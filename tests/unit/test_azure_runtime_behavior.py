"""Unit tests for Azure-specific runtime behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import monitor_pipeline_runs as monitor_module
import run_full_pipeline as pipeline_module


def test_extract_stage_issues_collects_warning_and_error_lines() -> None:
    issues = pipeline_module.extract_stage_issues(
        "workday",
        "All good\n⚠️ Timed out reaching endpoint\nEmail failed for job digest\nStatus email sent successfully\n",
    )

    assert issues == [
        "[workday] ⚠️ Timed out reaching endpoint",
        "[workday] Email failed for job digest",
    ]


def test_async_scrapers_skip_custom_scraper_when_disabled(monkeypatch) -> None:
    calls = {"custom": 0}

    async def fake_custom_scraper() -> list[dict[str, str]]:
        calls["custom"] += 1
        return [{"url": "https://example.test/job"}]

    monkeypatch.setenv("ATS_SNIPER_DISABLE_CUSTOM_SCRAPER", "true")
    monkeypatch.setattr(pipeline_module, "run_custom_scraper", fake_custom_scraper)
    monkeypatch.setattr(pipeline_module, "load_state", lambda: {"seen_jobs": {}})

    results = asyncio.run(
        pipeline_module._run_all_async_scrapers(
            dry_run=True,
            v3_mode=False,
            run_type="full",
        )
    )

    assert calls["custom"] == 0
    assert results["custom"] == []
    assert results["_new_custom_urls"] == set()


def test_record_pipeline_run_clears_stale_error_message(monkeypatch) -> None:
    state = {}
    saved_record: dict[str, object] = {
        "status": "failed",
        "error_message": "old failure",
    }

    monkeypatch.setattr(pipeline_module, "load_state", lambda: state)
    monkeypatch.setattr(pipeline_module, "save_state", lambda updated_state: None)
    monkeypatch.setattr(
        pipeline_module,
        "upsert_pipeline_run_record",
        lambda _state, _run_type, updates: saved_record.update(updates) or saved_record,
    )

    pipeline_module.record_pipeline_run(
        "morning",
        "success",
        stats={"icims": 3},
        error_message="",
        email_sent=True,
        total_new_jobs=3,
        started_at="2026-04-07T09:18:32",
        completed_at="2026-04-07T09:28:03",
    )

    assert saved_record["status"] == "success"
    assert saved_record["error_message"] == ""
    assert saved_record["stats"] == {"icims": 3}
    assert saved_record["email_sent"] is True
    assert saved_record["total_new_jobs"] == 3


def test_monitor_skips_task_scheduler_queries_in_azure_mode(monkeypatch) -> None:
    monkeypatch.setenv("ATS_SNIPER_SKIP_TASK_SCHEDULER_CHECKS", "true")
    monkeypatch.setattr(
        monitor_module,
        "load_config",
        lambda: {
            "schedules": {
                "morning": {"time": "09:30"},
                "afternoon": {"time": "16:30"},
            }
        },
    )
    monkeypatch.setattr(
        monitor_module,
        "get_monitoring_config",
        lambda config: {
            "enabled": True,
            "grace_minutes": {"morning": 120, "afternoon": 90},
            "task_names": {"morning": "Morning ATS Sniper", "afternoon": "Afternoon ATS Sniper"},
        },
    )
    monkeypatch.setattr(monitor_module, "load_state", lambda: {})
    monkeypatch.setattr(monitor_module, "get_pipeline_run_record", lambda state, run_type, now: None)
    monkeypatch.setattr(
        monitor_module,
        "evaluate_run_health",
        lambda *args, **kwargs: {"reason": "success", "should_alert": False, "due_at": "2026-04-03T11:15:00"},
    )
    monkeypatch.setattr(
        monitor_module,
        "get_task_info",
        lambda task_name: (_ for _ in ()).throw(AssertionError("Task Scheduler should be skipped in Azure mode")),
    )

    assert monitor_module.run_monitor(dry_run=True) == 0
