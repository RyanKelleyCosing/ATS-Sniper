"""Unit tests for shared config and state path behavior."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils import state as state_module


def test_load_config_prefers_env_json(monkeypatch) -> None:
    monkeypatch.setenv(
        "ATS_SNIPER_CONFIG_JSON",
        json.dumps({"openai_key": "test-key", "settings": {"openai_model": "gpt-4o-mini"}}),
    )

    config = state_module.load_config()

    assert config["openai_key"] == "test-key"
    assert config["settings"]["openai_model"] == "gpt-4o-mini"


def test_save_state_writes_backup_to_runtime_backup_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ATS_SNIPER_CONFIG_JSON", raising=False)
    monkeypatch.setenv("ATS_SNIPER_STATE_PATH", str(tmp_path / "job_state.json"))
    monkeypatch.setenv("ATS_SNIPER_STATE_BACKUP_DIR", str(tmp_path / "state_backups"))

    state_module.save_state({"jobs": {"first": {}}, "seen_jobs": {}})
    state_module.save_state({"jobs": {"second": {}}, "seen_jobs": {}})

    backup_path = tmp_path / "state_backups" / "job_state_backup.json"
    current_state = json.loads((tmp_path / "job_state.json").read_text(encoding="utf-8"))
    backup_state = json.loads(backup_path.read_text(encoding="utf-8"))

    assert backup_path.exists()
    assert "second" in current_state["jobs"]
    assert "first" in backup_state["jobs"]


def test_upsert_pipeline_run_record_preserves_previous_completed_run_when_rerun_starts() -> None:
    state = {
        "pipeline_runs": {
            "2026-04-21": {
                "morning": {
                    "status": "success",
                    "started_at": "2026-04-21T09:49:38.000000",
                    "completed_at": "2026-04-21T10:17:44.000000",
                    "stats": {"web_discovery": 19, "analyzed": 19},
                    "email_sent": True,
                    "total_new_jobs": 19,
                    "benchmark_summary": {"hit_count": 0, "target_count": 10},
                }
            }
        }
    }

    record = state_module.upsert_pipeline_run_record(
        state,
        "morning",
        {
            "status": "running",
            "started_at": "2026-04-21T13:47:05.000000",
            "stats": {"phase": "workday"},
        },
        run_date=datetime(2026, 4, 21),
    )

    assert record["status"] == "running"
    assert record["started_at"] == "2026-04-21T13:47:05.000000"
    assert record["stats"] == {"phase": "workday"}
    assert "completed_at" not in record
    assert "email_sent" not in record
    assert "total_new_jobs" not in record
    assert "benchmark_summary" not in record
    assert record["previous_completed_run"] == {
        "status": "success",
        "started_at": "2026-04-21T09:49:38.000000",
        "completed_at": "2026-04-21T10:17:44.000000",
        "stats": {"web_discovery": 19, "analyzed": 19},
        "email_sent": True,
        "total_new_jobs": 19,
        "benchmark_summary": {"hit_count": 0, "target_count": 10},
    }