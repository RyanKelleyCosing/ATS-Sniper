"""Shared Azure Function helpers for ATS Sniper."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import azure.functions as func

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logger = logging.getLogger(__name__)


def default_skip_tailor() -> bool:
    """Return the default skip-tailor setting for hosted environments."""
    return coerce_bool(os.getenv("ATS_SNIPER_SKIP_TAILOR"), default=False)


def load_pipeline_handlers():
    """Load pipeline callables lazily for Azure function execution."""
    from run_full_pipeline import run_pipeline, summarize_jobs_for_response

    return run_pipeline, summarize_jobs_for_response


def load_monitor_handler():
    """Load the pipeline monitor lazily for Azure function execution."""
    from monitor_pipeline_runs import run_monitor

    return run_monitor


def load_state_handler():
    """Load state helpers lazily for Azure function execution."""
    from utils.state import load_state

    return load_state


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Convert query/body values into booleans."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def normalize_trigger_options(
    query_params: Mapping[str, Any] | None,
    body_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize request options for the HTTP trigger."""
    query_params = query_params or {}
    body_payload = body_payload or {}

    def read_value(name: str, default: Any = None) -> Any:
        if name in body_payload:
            return body_payload[name]
        return query_params.get(name, default)

    run_type = str(read_value("run_type", "full")).strip().casefold()
    if run_type not in {"morning", "afternoon", "full"}:
        run_type = "full"

    return {
        "dry_run": coerce_bool(read_value("dry_run"), default=False),
        "skip_tailor": coerce_bool(read_value("skip_tailor"), default=default_skip_tailor()),
        "v3_mode": not coerce_bool(read_value("v2"), default=False),
        "run_type": run_type,
    }


def parse_http_request(req: func.HttpRequest) -> dict[str, Any]:
    """Extract trigger options from query string and optional JSON body."""
    try:
        body_payload = req.get_json()
        if not isinstance(body_payload, dict):
            body_payload = {}
    except ValueError:
        body_payload = {}

    return normalize_trigger_options(req.params, body_payload)


def run_pipeline_from_trigger(options: dict[str, Any]) -> dict[str, Any]:
    """Execute the pipeline and return a structured response payload."""
    run_pipeline, summarize_jobs_for_response = load_pipeline_handlers()
    jobs = run_pipeline(
        dry_run=options["dry_run"],
        skip_tailor=options["skip_tailor"],
        v3_mode=options["v3_mode"],
        run_type=options["run_type"],
    )
    return {
        "status": "success",
        "options": options,
        "job_count": len(jobs),
        "jobs": summarize_jobs_for_response(jobs),
    }


def health(req: func.HttpRequest) -> func.HttpResponse:
    """Return a simple health response for platform checks."""
    payload = {"status": "ok", "service": "ats-sniper"}
    return func.HttpResponse(json.dumps(payload), mimetype="application/json", status_code=200)


def run_ats_sniper(req: func.HttpRequest) -> func.HttpResponse:
    """Trigger the ATS Sniper pipeline over HTTP."""
    options = parse_http_request(req)
    logger.info("ATS Sniper trigger received: %s", options)

    try:
        response_payload = run_pipeline_from_trigger(options)
    except Exception as exc:
        logger.exception("ATS Sniper function run failed")
        error_payload = {
            "status": "error",
            "message": str(exc),
            "options": options,
        }
        return func.HttpResponse(
            json.dumps(error_payload, indent=2),
            mimetype="application/json",
            status_code=500,
        )

    return func.HttpResponse(
        json.dumps(response_payload, indent=2),
        mimetype="application/json",
        status_code=200,
    )


def run_status(req: func.HttpRequest) -> func.HttpResponse:
    """Return recent pipeline run records from persisted state."""
    load_state = load_state_handler()
    try:
        requested_days = int(req.params.get("days", "3"))
    except ValueError:
        requested_days = 3

    days = max(1, min(requested_days, 7))
    state = load_state()
    pipeline_runs = state.get("pipeline_runs", {})
    recent_dates = sorted(pipeline_runs.keys(), reverse=True)[:days]
    recent_runs = {date_key: pipeline_runs.get(date_key, {}) for date_key in recent_dates}
    payload = {
        "status": "success",
        "days": days,
        "pipeline_runs": recent_runs,
    }
    return func.HttpResponse(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        status_code=200,
    )


def scheduled_options(run_type: str) -> dict[str, Any]:
    """Build default options for timer-triggered runs."""
    return {
        "dry_run": False,
        "skip_tailor": default_skip_tailor(),
        "v3_mode": True,
        "run_type": run_type,
    }


def morning_scheduled_run(timer: func.TimerRequest) -> None:
    """Run the morning pipeline schedule in Azure Functions."""
    logger.info("Morning timer fired. Past due=%s", timer.past_due)
    run_pipeline_from_trigger(scheduled_options("morning"))


def afternoon_scheduled_run(timer: func.TimerRequest) -> None:
    """Run the afternoon pipeline schedule in Azure Functions."""
    logger.info("Afternoon timer fired. Past due=%s", timer.past_due)
    run_pipeline_from_trigger(scheduled_options("afternoon"))


def run_monitor_timer(timer: func.TimerRequest, window: str) -> None:
    """Run the pipeline monitor for the requested window."""
    logger.info("Pipeline monitor timer fired for %s window. Past due=%s", window, timer.past_due)
    run_monitor = load_monitor_handler()
    run_monitor(dry_run=False)


def morning_run_monitor(timer: func.TimerRequest) -> None:
    """Check whether the morning pipeline run completed successfully."""
    run_monitor_timer(timer, "morning")


def afternoon_run_monitor(timer: func.TimerRequest) -> None:
    """Check whether the afternoon pipeline run completed successfully."""
    run_monitor_timer(timer, "afternoon")