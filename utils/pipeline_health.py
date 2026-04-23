"""Helpers for evaluating scheduled pipeline run health."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

DEFAULT_MONITORING_CONFIG: dict[str, Any] = {
    "enabled": True,
    "send_no_jobs_email": True,
    "send_failure_email": True,
    "send_issue_email": True,
    "send_monitor_alerts": True,
    "grace_minutes": {"morning": 120, "afternoon": 90},
    "alert_times": {"morning": "11:15", "afternoon": "17:45"},
    "task_names": {
        "morning": "ATS_Sniper_OnLogin_CurrentUser",
        "afternoon": "ATS_Sniper_Afternoon_CurrentUser",
    },
}
SUCCESS_RUN_STATUSES = {"success", "no_jobs"}


def get_monitoring_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return monitoring settings merged with defaults."""
    merged = {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in DEFAULT_MONITORING_CONFIG.items()
    }
    custom_config = config.get("monitoring", {})
    for key, value in custom_config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def parse_schedule_time(value: str) -> time:
    """Parse a HH:MM or HH:MMPM schedule string."""
    normalized_value = value.strip().upper().replace(" ", "")
    for fmt in ("%H:%M", "%I:%M%p"):
        try:
            return datetime.strptime(normalized_value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Unsupported schedule time: {value}")


def parse_optional_datetime(value: Any) -> datetime | None:
    """Parse a datetime value if possible."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_run_successful(record: Mapping[str, Any] | None, now: datetime) -> bool:
    """Return True when a run record marks the run successful for today."""
    if not record:
        return False
    if record.get("status") not in SUCCESS_RUN_STATUSES:
        return False
    completed_at = parse_optional_datetime(record.get("completed_at"))
    if completed_at:
        return completed_at.date() == now.date()
    started_at = parse_optional_datetime(record.get("started_at"))
    return bool(started_at and started_at.date() == now.date())


def is_run_in_progress(record: Mapping[str, Any] | None, now: datetime) -> bool:
    """Return True when a run record shows an in-progress run for today."""
    if not record or record.get("status") != "running":
        return False
    started_at = parse_optional_datetime(record.get("started_at"))
    return bool(started_at and started_at.date() == now.date())


def evaluate_run_health(
    now: datetime,
    schedule_time: str,
    grace_minutes: int,
    record: Mapping[str, Any] | None,
    task_info: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate whether a scheduled run should trigger an alert."""
    due_at = datetime.combine(now.date(), parse_schedule_time(schedule_time)) + timedelta(minutes=grace_minutes)
    if now < due_at:
        return {"should_alert": False, "reason": "not-due", "due_at": due_at.isoformat()}

    if is_run_successful(record, now):
        return {"should_alert": False, "reason": "run-record-success", "due_at": due_at.isoformat()}

    if is_run_in_progress(record, now):
        return {"should_alert": False, "reason": "run-record-running", "due_at": due_at.isoformat()}

    if task_info:
        if str(task_info.get("state", "")).casefold() == "running":
            return {"should_alert": False, "reason": "task-running", "due_at": due_at.isoformat()}

        last_run_time = parse_optional_datetime(task_info.get("last_run_time"))
        last_task_result = int(task_info.get("last_task_result", -1))
        if last_run_time and last_run_time.date() == now.date() and last_task_result == 0:
            return {"should_alert": False, "reason": "task-success", "due_at": due_at.isoformat()}
        if record and record.get("status") == "failed":
            return {"should_alert": True, "reason": "pipeline-failed", "due_at": due_at.isoformat()}
        if last_run_time and last_run_time.date() == now.date() and last_task_result != 0:
            return {"should_alert": True, "reason": "task-failed", "due_at": due_at.isoformat()}

    return {"should_alert": True, "reason": "missed-run", "due_at": due_at.isoformat()}