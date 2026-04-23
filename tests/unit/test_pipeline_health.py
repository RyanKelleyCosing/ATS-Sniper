"""Unit tests for scheduled-run monitoring decisions."""

from datetime import datetime
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.pipeline_health import evaluate_run_health, get_monitoring_config


def test_get_monitoring_config_merges_defaults() -> None:
    config = {"monitoring": {"grace_minutes": {"morning": 30}}}

    monitoring = get_monitoring_config(config)

    assert monitoring["grace_minutes"]["morning"] == 30
    assert monitoring["grace_minutes"]["afternoon"] == 90
    assert monitoring["task_names"]["morning"] == "ATS_Sniper_OnLogin_CurrentUser"


def test_evaluate_run_health_skips_alert_for_successful_record() -> None:
    now = datetime(2026, 4, 2, 12, 0)
    record = {"status": "success", "completed_at": "2026-04-02T10:15:00"}

    result = evaluate_run_health(now, "09:30", 120, record, None)

    assert result["should_alert"] is False


def test_evaluate_run_health_skips_alert_for_successful_task_result() -> None:
    now = datetime(2026, 4, 2, 12, 0)
    task_info = {"state": "Ready", "last_run_time": "2026-04-02T09:35:00", "last_task_result": 0}

    result = evaluate_run_health(now, "09:30", 120, None, task_info)

    assert result == {
        "should_alert": False,
        "reason": "task-success",
        "due_at": "2026-04-02T11:30:00",
    }


def test_evaluate_run_health_skips_alert_for_running_record() -> None:
    now = datetime(2026, 4, 3, 18, 0)
    record = {"status": "running", "started_at": "2026-04-03T16:30:05"}

    result = evaluate_run_health(now, "16:30", 90, record, None)

    assert result == {
        "should_alert": False,
        "reason": "run-record-running",
        "due_at": "2026-04-03T18:00:00",
    }


def test_evaluate_run_health_flags_failed_task_after_grace_period() -> None:
    now = datetime(2026, 4, 2, 18, 0)
    task_info = {"state": "Ready", "last_run_time": "2026-04-02T16:30:00", "last_task_result": 1}

    result = evaluate_run_health(now, "16:30", 90, None, task_info)

    assert result["should_alert"] is True
    assert result["reason"] == "task-failed"