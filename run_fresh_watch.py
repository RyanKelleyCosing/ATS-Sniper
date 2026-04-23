#!/usr/bin/env python3
"""Run the lightweight fresh-watch discovery bridge."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from params.logging_config import setup_logging
from run_full_pipeline import annotate_jobs_with_freshness, capture_stage_output
from startup_discovery_scraper import run_web_discovery_scrape
from utils.notifications import send_status_email
from utils.pipeline_telemetry import (
    apply_feedback_signals,
    record_source_health_snapshot,
    sort_jobs_for_reporting,
    write_discovery_audit_report,
)
from utils.runtime_paths import reports_dir
from utils.state import load_config, load_state, save_state


logger = logging.getLogger(__name__)

FRESH_WATCH_RUN_TYPE = "fresh_watch"
FRESH_WATCH_STAGE_NAME = "web-discovery"
FRESH_WATCH_REPORT_BASENAME = "fresh_watch_latest"
FRESH_WATCH_CSV_NAME = "fresh_watch_latest.csv"
FRESH_WATCH_QUERY_PROFILE_ALLOWLIST = (
    "ats_board_pages",
    "ats_pages_extended",
    "company_career_domains",
    "remote_us_roles",
)


@dataclass(frozen=True)
class FreshWatchSettings:
    """Runtime settings for the 10-minute fresh-watch bridge."""

    enabled: bool = True
    max_queries: int = 4
    max_results_per_query: int = 8
    min_discovery_confidence: int = 72
    alert_min_discovery_confidence: int = 82
    alert_max_age_hours: float = 1.0
    send_email_alerts: bool = True
    max_alert_jobs: int = 5
    history_limit: int = 144
    enable_adjacent_jobspy: bool = False
    adjacent_jobspy_max_search_terms: int = 6
    adjacent_jobspy_results_per_search_term: int = 3


def load_fresh_watch_settings(config: Mapping[str, Any]) -> FreshWatchSettings:
    """Load fresh-watch settings from config.json."""
    raw_settings = dict(config.get("fresh_watch", {}))
    return FreshWatchSettings(
        enabled=bool(raw_settings.get("enabled", True)),
        max_queries=max(int(raw_settings.get("max_queries", 4) or 4), 1),
        max_results_per_query=max(int(raw_settings.get("max_results_per_query", 8) or 8), 1),
        min_discovery_confidence=min(max(int(raw_settings.get("min_discovery_confidence", 72) or 72), 0), 100),
        alert_min_discovery_confidence=min(
            max(int(raw_settings.get("alert_min_discovery_confidence", 82) or 82), 0),
            100,
        ),
        alert_max_age_hours=max(float(raw_settings.get("alert_max_age_hours", 1.0) or 1.0), 0.0),
        send_email_alerts=bool(raw_settings.get("send_email_alerts", True)),
        max_alert_jobs=max(int(raw_settings.get("max_alert_jobs", 5) or 5), 1),
        history_limit=max(int(raw_settings.get("history_limit", 144) or 144), 1),
        enable_adjacent_jobspy=bool(raw_settings.get("enable_adjacent_jobspy", False)),
        adjacent_jobspy_max_search_terms=max(int(raw_settings.get("adjacent_jobspy_max_search_terms", 6) or 6), 1),
        adjacent_jobspy_results_per_search_term=max(
            int(raw_settings.get("adjacent_jobspy_results_per_search_term", 3) or 3),
            1,
        ),
    )


def build_fresh_watch_runtime_config(
    config: Mapping[str, Any],
    settings: FreshWatchSettings,
) -> dict[str, Any]:
    """Build an in-memory config override for the web-only fresh-watch lane."""
    runtime_config = deepcopy(dict(config))
    startup_discovery = dict(runtime_config.get("startup_discovery", {}))
    allowed_run_types = [
        str(value).strip()
        for value in startup_discovery.get("allowed_run_types", [])
        if str(value).strip()
    ]
    if FRESH_WATCH_RUN_TYPE.casefold() not in {
        value.casefold() for value in allowed_run_types
    }:
        allowed_run_types.append(FRESH_WATCH_RUN_TYPE)

    startup_discovery["enabled"] = True
    startup_discovery["max_queries"] = settings.max_queries
    startup_discovery["max_results_per_query"] = settings.max_results_per_query
    startup_discovery["include_adjacent_roles"] = False
    startup_discovery["query_profile_allowlist"] = list(FRESH_WATCH_QUERY_PROFILE_ALLOWLIST)
    startup_discovery["allowed_run_types"] = allowed_run_types
    runtime_config["startup_discovery"] = startup_discovery

    jobspy_discovery = dict(runtime_config.get("jobspy_discovery", {}))
    existing_run_types = [
        str(value).strip()
        for value in jobspy_discovery.get("run_types", [])
        if str(value).strip()
    ]
    if settings.enable_adjacent_jobspy:
        fresh_watch_run_types = list(existing_run_types)
        if FRESH_WATCH_RUN_TYPE.casefold() not in {
            value.casefold() for value in fresh_watch_run_types
        }:
            fresh_watch_run_types.append(FRESH_WATCH_RUN_TYPE)

        configured_max_search_terms = max(
            int(
                jobspy_discovery.get(
                    "max_search_terms",
                    settings.adjacent_jobspy_max_search_terms,
                )
                or settings.adjacent_jobspy_max_search_terms
            ),
            1,
        )
        max_search_terms = min(
            configured_max_search_terms,
            settings.adjacent_jobspy_max_search_terms,
        )
        adjacent_max_search_terms = min(settings.adjacent_jobspy_max_search_terms, max_search_terms)
        if max_search_terms > 1:
            adjacent_max_search_terms = min(adjacent_max_search_terms, max_search_terms - 1)
        results_per_search_term = min(
            max(
                int(
                    jobspy_discovery.get(
                        "results_per_search_term",
                        settings.adjacent_jobspy_results_per_search_term,
                    )
                    or settings.adjacent_jobspy_results_per_search_term
                ),
                1,
            ),
            settings.adjacent_jobspy_results_per_search_term,
        )

        jobspy_discovery["enabled"] = True
        jobspy_discovery["run_types"] = fresh_watch_run_types
        jobspy_discovery["include_adjacent_roles"] = True
        jobspy_discovery["max_search_terms"] = max_search_terms
        jobspy_discovery["adjacent_max_search_terms"] = adjacent_max_search_terms
        jobspy_discovery["results_per_search_term"] = results_per_search_term
        jobspy_discovery["results_wanted"] = max(
            results_per_search_term,
            min(
                int(jobspy_discovery.get("results_wanted", results_per_search_term * max_search_terms) or (results_per_search_term * max_search_terms)),
                results_per_search_term * max_search_terms,
            ),
        )
    else:
        jobspy_discovery["enabled"] = False
        jobspy_discovery["run_types"] = [
            value
            for value in existing_run_types
            if value.casefold() != FRESH_WATCH_RUN_TYPE
        ]
    runtime_config["jobspy_discovery"] = jobspy_discovery
    return runtime_config


@contextmanager
def override_runtime_config(config: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily override config loading for the fresh-watch subprocess path."""
    previous_value = os.environ.get("ATS_SNIPER_CONFIG_JSON")
    os.environ["ATS_SNIPER_CONFIG_JSON"] = json.dumps(config)
    try:
        yield
    finally:
        if previous_value is None:
            os.environ.pop("ATS_SNIPER_CONFIG_JSON", None)
        else:
            os.environ["ATS_SNIPER_CONFIG_JSON"] = previous_value


def filter_fresh_watch_jobs(
    jobs: Sequence[Mapping[str, Any]],
    settings: FreshWatchSettings,
) -> list[dict[str, Any]]:
    """Keep only discovery results that meet the configured confidence floor."""
    return [
        dict(job)
        for job in jobs
        if int(job.get("discovery_confidence", 0) or 0) >= settings.min_discovery_confidence
    ]


def select_alert_jobs(
    jobs: Sequence[Mapping[str, Any]],
    settings: FreshWatchSettings,
) -> list[dict[str, Any]]:
    """Select the freshest high-confidence jobs eligible for optional alerts."""
    selected_jobs: list[dict[str, Any]] = []

    for job in jobs:
        if int(job.get("discovery_confidence", 0) or 0) < settings.alert_min_discovery_confidence:
            continue

        freshness_age = _coerce_freshness_age_hours(job)
        freshness_bucket = str(job.get("freshness_bucket", "")).strip()
        if freshness_age is None and freshness_bucket != "fresh_under_6h":
            continue
        if freshness_age is not None and freshness_age > settings.alert_max_age_hours:
            continue

        selected_jobs.append(dict(job))
        if len(selected_jobs) >= settings.max_alert_jobs:
            break

    return selected_jobs


def build_fresh_watch_stats(
    *,
    new_jobs: Sequence[Mapping[str, Any]],
    watch_jobs: Sequence[Mapping[str, Any]],
    alert_jobs: Sequence[Mapping[str, Any]],
    freshness_counts: Mapping[str, int],
    feedback_summary: Mapping[str, int],
) -> dict[str, Any]:
    """Build compact metrics for fresh-watch reporting and history."""
    stats = dict(freshness_counts)
    stats.update(
        {
            "web_discovery": len(new_jobs),
            "fresh_watch_candidates": len(watch_jobs),
            "fresh_watch_alert_candidates": len(alert_jobs),
            "fresh_watch_low_confidence_filtered": max(len(new_jobs) - len(watch_jobs), 0),
            "feedback_boosted": int(feedback_summary.get("boosted", 0) or 0),
            "feedback_neutral": int(feedback_summary.get("neutral", 0) or 0),
            "feedback_penalized": int(feedback_summary.get("penalized", 0) or 0),
        }
    )
    return stats


def write_fresh_watch_csv(jobs: Sequence[Mapping[str, Any]], output_path: Path) -> Path:
    """Write the latest high-signal fresh-watch leads to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(
            [
                "Company",
                "Title",
                "Location",
                "Source",
                "Source Family",
                "Query Profile",
                "Discovery Confidence",
                "Freshness Bucket",
                "Freshness Age Hours",
                "Posted Date",
                "First Seen At",
                "URL",
            ]
        )
        for job in jobs:
            freshness_age = _coerce_freshness_age_hours(job)
            writer.writerow(
                [
                    str(job.get("company", "Unknown")),
                    str(job.get("title", "Unknown")),
                    str(job.get("location", "")),
                    str(job.get("source", "")),
                    str(job.get("source_family", "")),
                    str(job.get("query_profile", "")),
                    int(job.get("discovery_confidence", 0) or 0),
                    str(job.get("freshness_bucket", "")),
                    "" if freshness_age is None else round(freshness_age, 2),
                    str(job.get("posted_date", job.get("date_posted", ""))),
                    str(job.get("first_seen_at", "")),
                    str(job.get("url", "")),
                ]
            )
    return output_path


def record_fresh_watch_history(
    state: dict[str, Any],
    *,
    status: str,
    new_jobs: Sequence[Mapping[str, Any]],
    watch_jobs: Sequence[Mapping[str, Any]],
    alert_jobs: Sequence[Mapping[str, Any]],
    issues: Sequence[str],
    report_paths: Mapping[str, str],
    csv_path: Path,
    settings: FreshWatchSettings,
    email_sent: bool,
    error_message: str = "",
) -> None:
    """Append a compact fresh-watch run summary to job state."""
    history = list(state.get("fresh_watch_history", []))
    history.append(
        {
            "generated_at": datetime.now().isoformat(),
            "status": status,
            "new_jobs": len(new_jobs),
            "watch_candidates": len(watch_jobs),
            "alert_jobs": len(alert_jobs),
            "issue_count": len(issues),
            "issues": list(issues)[:5],
            "report_paths": dict(report_paths),
            "csv_path": str(csv_path),
            "email_sent": email_sent,
            "max_queries": settings.max_queries,
            "max_results_per_query": settings.max_results_per_query,
            "error_message": error_message,
            "top_jobs": [_summarize_job(job) for job in list(watch_jobs)[:5]],
        }
    )
    state["fresh_watch_history"] = history[-settings.history_limit :]


def send_fresh_watch_email_alert(
    config: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    *,
    report_paths: Mapping[str, str],
    csv_path: Path,
    threshold_label: str,
) -> bool:
    """Send an optional alert email for the freshest, highest-confidence leads."""
    heading = "ATS Sniper Fresh Watch Alert"
    subject = f"ATS Sniper fresh watch: {len(jobs)} lead(s)"
    message_lines = [
        f"{len(jobs)} lead(s) cleared the fresh-watch {threshold_label} threshold.",
        f"Latest audit report: {report_paths.get('markdown_path', '')}",
        f"Latest CSV: {csv_path}",
    ]
    for job in jobs:
        message_lines.append(
            " | ".join(
                part
                for part in (
                    str(job.get("company", "Unknown")),
                    str(job.get("title", "Unknown")),
                    f"confidence {int(job.get('discovery_confidence', 0) or 0)}",
                    str(job.get("freshness_bucket", "")),
                    str(job.get("url", "")),
                )
                if part
            )
        )

    return send_status_email(
        config,
        subject,
        heading,
        message_lines,
        stats={
            "Alert Leads": len(jobs),
            "Audit Report": report_paths.get("markdown_path", ""),
            "CSV": str(csv_path),
        },
    )


def select_email_jobs(
    watch_jobs: Sequence[Mapping[str, Any]],
    alert_jobs: Sequence[Mapping[str, Any]],
    settings: FreshWatchSettings,
) -> tuple[list[dict[str, Any]], str]:
    """Select the jobs that should trigger a fresh-watch email notification."""
    if alert_jobs:
        return [dict(job) for job in list(alert_jobs)[: settings.max_alert_jobs]], "alert"
    return [dict(job) for job in list(watch_jobs)[: settings.max_alert_jobs]], "watch"


def run_fresh_watch(*, dry_run: bool = False) -> int:
    """Run the web-only fresh-watch lane and persist the latest monitoring artifacts."""
    config = load_config()
    settings = load_fresh_watch_settings(config)
    if not settings.enabled:
        logger.info("Fresh watch is disabled in config")
        return 0

    runtime_config = build_fresh_watch_runtime_config(config, settings)
    telemetry: dict[str, Any] = {"query_profile_yield": {}, "rejected_reasons": {}}
    reports_root = reports_dir()
    csv_path = reports_root / FRESH_WATCH_CSV_NAME

    try:
        with override_runtime_config(runtime_config):
            new_jobs, pipeline_issues = capture_stage_output(
                FRESH_WATCH_STAGE_NAME,
                lambda: run_web_discovery_scrape(
                    dry_run=dry_run,
                    run_type=FRESH_WATCH_RUN_TYPE,
                    telemetry=telemetry,
                ),
            )
        freshness_counts = annotate_jobs_with_freshness(new_jobs, runtime_config, persist_state=not dry_run)
        feedback_summary = apply_feedback_signals(new_jobs)
        new_jobs[:] = sort_jobs_for_reporting(new_jobs)
        watch_jobs = filter_fresh_watch_jobs(new_jobs, settings)
        alert_jobs = select_alert_jobs(watch_jobs, settings)
        stats = build_fresh_watch_stats(
            new_jobs=new_jobs,
            watch_jobs=watch_jobs,
            alert_jobs=alert_jobs,
            freshness_counts=freshness_counts,
            feedback_summary=feedback_summary,
        )
        status = "issues" if pipeline_issues else "ok" if new_jobs else "ok_no_results"

        if dry_run:
            logger.info(
                "Fresh watch dry run complete: %s raw jobs, %s candidates, %s alert jobs",
                len(new_jobs),
                len(watch_jobs),
                len(alert_jobs),
            )
            return 0

        record_source_health_snapshot(
            run_type=FRESH_WATCH_RUN_TYPE,
            stats=stats,
            pipeline_issues=pipeline_issues,
            stage_attempts={"web_discovery": True},
        )
        report_paths = write_discovery_audit_report(
            run_type=FRESH_WATCH_RUN_TYPE,
            stats=stats,
            all_new_jobs=new_jobs,
            hot_job_results={"hot_jobs": [], "regular_jobs": [], "screened_out_jobs": []},
            pipeline_issues=pipeline_issues,
            web_discovery_telemetry=telemetry,
            stage_attempts={"web_discovery": True},
            base_name=FRESH_WATCH_REPORT_BASENAME,
        )
        write_fresh_watch_csv(watch_jobs, csv_path)
        email_sent = False
        if settings.send_email_alerts and watch_jobs:
            email_jobs, threshold_label = select_email_jobs(watch_jobs, alert_jobs, settings)
            email_sent = send_fresh_watch_email_alert(
                config,
                email_jobs,
                report_paths=report_paths,
                csv_path=csv_path,
                threshold_label=threshold_label,
            )

        state = load_state()
        record_fresh_watch_history(
            state,
            status=status,
            new_jobs=new_jobs,
            watch_jobs=watch_jobs,
            alert_jobs=alert_jobs,
            issues=pipeline_issues,
            report_paths=report_paths,
            csv_path=csv_path,
            settings=settings,
            email_sent=email_sent,
        )
        save_state(state)

        logger.info(
            "Fresh watch complete: %s raw jobs, %s candidates, %s alert jobs",
            len(new_jobs),
            len(watch_jobs),
            len(alert_jobs),
        )
        return 0
    except Exception as exc:
        logger.exception("Fresh watch run failed")
        if dry_run:
            return 1

        failure_issue = f"[{FRESH_WATCH_STAGE_NAME}] Fresh watch failed: {exc}"
        record_source_health_snapshot(
            run_type=FRESH_WATCH_RUN_TYPE,
            stats={"web_discovery": 0},
            pipeline_issues=[failure_issue],
            stage_attempts={"web_discovery": True},
        )
        state = load_state()
        record_fresh_watch_history(
            state,
            status="failed",
            new_jobs=[],
            watch_jobs=[],
            alert_jobs=[],
            issues=[failure_issue],
            report_paths={},
            csv_path=csv_path,
            settings=settings,
            email_sent=False,
            error_message=str(exc),
        )
        save_state(state)
        return 1


def _coerce_freshness_age_hours(job: Mapping[str, Any]) -> float | None:
    """Return a normalized freshness age when present."""
    raw_value = job.get("freshness_age_hours")
    if raw_value in (None, ""):
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def _summarize_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact state-safe job summary for fresh-watch history."""
    return {
        "company": str(job.get("company", "Unknown")),
        "title": str(job.get("title", "Unknown")),
        "source": str(job.get("source", "")),
        "freshness_bucket": str(job.get("freshness_bucket", "")),
        "discovery_confidence": int(job.get("discovery_confidence", 0) or 0),
        "url": str(job.get("url", "")),
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the fresh-watch entrypoint."""
    parser = argparse.ArgumentParser(description="Run the ATS Sniper fresh-watch bridge")
    parser.add_argument("--dry-run", action="store_true", help="Run discovery without saving state or reports")
    return parser.parse_args()


def main() -> int:
    """Program entrypoint."""
    setup_logging()
    args = parse_args()
    return run_fresh_watch(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())