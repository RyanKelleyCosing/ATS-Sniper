"""Shared state and configuration I/O for ATS Sniper."""

import os
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.runtime_paths import config_path, project_root, state_backup_dir, state_path

_PROJECT_ROOT = project_root()
CONFIG_PATH = config_path()
STATE_PATH = state_path()

_DEFAULT_STATE: dict[str, Any] = {
    "jobs": {},
    "seen_jobs": {},
    "job_identities": {},
    "board_health": {},
    "last_run": None,
    "pipeline_runs": {},
    "pipeline_alerts": {},
}
_TERMINAL_PIPELINE_RUN_STATUSES = {"success", "no_jobs", "failed"}
_PREVIOUS_COMPLETED_RUN_KEY = "previous_completed_run"
_STALE_RUNNING_RUN_FIELDS = (
    "benchmark_summary",
    "completed_at",
    "email_sent",
    "total_new_jobs",
)


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Ensure expected top-level state structures are always present."""
    normalized_state = {**_DEFAULT_STATE, **state}
    for key in ("jobs", "seen_jobs", "job_identities", "board_health", "pipeline_runs", "pipeline_alerts"):
        if not isinstance(normalized_state.get(key), dict):
            normalized_state[key] = {}
    return normalized_state


def load_config() -> dict:
    """Load configuration from env override or config.json."""
    config_json = os.getenv("ATS_SNIPER_CONFIG_JSON", "").strip()
    if config_json:
        return json.loads(config_json)

    with open(config_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    """Load job state, returning a default dict if the file does not exist."""
    current_state_path = state_path()
    if current_state_path.exists():
        with open(current_state_path, "r", encoding="utf-8") as f:
            return _normalize_state(json.load(f))
    return {**_DEFAULT_STATE}


def save_state(state: dict) -> None:
    """Atomically save job state with a backup of the previous version."""
    state = _normalize_state(state)
    current_state_path = state_path()
    current_state_path.parent.mkdir(parents=True, exist_ok=True)
    if current_state_path.exists():
        backup_dir = state_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "job_state_backup.json"
        shutil.copy2(current_state_path, backup_path)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=current_state_path.parent, suffix=".tmp", prefix="job_state_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        Path(tmp_path).replace(current_state_path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def get_pipeline_run_record(
    state: dict[str, Any],
    run_type: str,
    run_date: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the pipeline-run record for a run type on a given date."""
    normalized_state = _normalize_state(state)
    date_key = (run_date or datetime.now()).date().isoformat()
    day_runs = normalized_state.get("pipeline_runs", {}).get(date_key, {})
    record = day_runs.get(run_type)
    return record if isinstance(record, dict) else None


def upsert_pipeline_run_record(
    state: dict[str, Any],
    run_type: str,
    updates: dict[str, Any],
    run_date: datetime | None = None,
) -> dict[str, Any]:
    """Create or update the pipeline-run record for a run type on a given date."""
    normalized_state = _normalize_state(state)
    date_key = (run_date or datetime.now()).date().isoformat()
    day_runs = normalized_state.setdefault("pipeline_runs", {}).setdefault(date_key, {})
    current_record = day_runs.get(run_type, {})
    if not isinstance(current_record, dict):
        current_record = {}

    next_status = str(updates.get("status", "")).strip()
    current_status = str(current_record.get("status", "")).strip()
    if next_status == "running":
        if current_status in _TERMINAL_PIPELINE_RUN_STATUSES and current_record.get("completed_at"):
            current_record[_PREVIOUS_COMPLETED_RUN_KEY] = {
                key: value
                for key, value in current_record.items()
                if key != _PREVIOUS_COMPLETED_RUN_KEY
            }
        for field_name in _STALE_RUNNING_RUN_FIELDS:
            current_record.pop(field_name, None)
    elif next_status in _TERMINAL_PIPELINE_RUN_STATUSES:
        current_record.pop(_PREVIOUS_COMPLETED_RUN_KEY, None)

    current_record.update(updates)
    day_runs[run_type] = current_record
    return current_record


def make_pipeline_alert_key(
    run_type: str,
    reason: str,
    run_date: datetime | None = None,
) -> str:
    """Build a stable key for deduping pipeline-monitor alert emails."""
    date_key = (run_date or datetime.now()).date().isoformat()
    return f"{date_key}:{run_type}:{reason}"


def pipeline_alert_sent(state: dict[str, Any], alert_key: str) -> bool:
    """Return True when a monitor alert has already been sent for the key."""
    normalized_state = _normalize_state(state)
    return alert_key in normalized_state.get("pipeline_alerts", {})


def mark_pipeline_alert_sent(
    state: dict[str, Any],
    alert_key: str,
    details: dict[str, Any],
) -> None:
    """Record that a monitor alert has been sent."""
    normalized_state = _normalize_state(state)
    normalized_state.setdefault("pipeline_alerts", {})[alert_key] = details
