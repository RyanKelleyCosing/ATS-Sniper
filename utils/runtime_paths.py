"""Central runtime paths for ATS Sniper local and cloud execution."""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """Return the repository root."""
    return _PROJECT_ROOT


def runtime_root() -> Path:
    """Return the writable runtime root for generated artifacts."""
    override = os.getenv("ATS_SNIPER_RUNTIME_DIR", "").strip()
    return Path(override) if override else _PROJECT_ROOT


def outputs_dir() -> Path:
    """Return the directory used for generated resumes and logs."""
    override = os.getenv("ATS_SNIPER_OUTPUTS_DIR", "").strip()
    return Path(override) if override else runtime_root() / "outputs"


def reports_dir() -> Path:
    """Return the directory used for generated reports and CSV exports."""
    override = os.getenv("ATS_SNIPER_REPORTS_DIR", "").strip()
    return Path(override) if override else runtime_root() / "reports"


def config_path() -> Path:
    """Return the on-disk config path when config is not supplied via env."""
    override = os.getenv("ATS_SNIPER_CONFIG_PATH", "").strip()
    return Path(override) if override else _PROJECT_ROOT / "config.json"


def state_path() -> Path:
    """Return the job-state path."""
    override = os.getenv("ATS_SNIPER_STATE_PATH", "").strip()
    return Path(override) if override else _PROJECT_ROOT / "job_state.json"


def state_backup_dir() -> Path:
    """Return the directory used for state backups."""
    override = os.getenv("ATS_SNIPER_STATE_BACKUP_DIR", "").strip()
    return Path(override) if override else outputs_dir() / "state"


def log_file_path() -> Path:
    """Return the structured log file path."""
    return outputs_dir() / "sniper.log"


def master_jobs_csv_path() -> Path:
    """Return the master export path for all tracked jobs."""
    return reports_dir() / "jobs_export.csv"


def regular_jobs_csv_path() -> Path:
    """Return the export path for non-hot jobs reviewed manually."""
    return reports_dir() / "regular_jobs_export.csv"