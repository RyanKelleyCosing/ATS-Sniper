#!/usr/bin/env python3
"""Check scheduled ATS Sniper runs and alert on missed or failed executions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from typing import Any

from utils.notifications import send_status_email
from utils.pipeline_health import evaluate_run_health, get_monitoring_config
from utils.state import (
    get_pipeline_run_record,
    load_config,
    load_state,
    make_pipeline_alert_key,
    mark_pipeline_alert_sent,
    pipeline_alert_sent,
    save_state,
)


def get_task_info(task_name: str) -> dict[str, Any] | None:
    """Read basic Windows Task Scheduler metadata for a task."""
    command = (
        "$task = Get-ScheduledTask -TaskName '{0}' -ErrorAction Stop;"
        "$info = Get-ScheduledTaskInfo -TaskName '{0}' -ErrorAction Stop;"
        "[PSCustomObject]@{{"
        "task_name = $task.TaskName;"
        "state = $task.State.ToString();"
        "last_run_time = $info.LastRunTime.ToString('o');"
        "last_task_result = $info.LastTaskResult;"
        "next_run_time = $info.NextRunTime.ToString('o')"
        "}} | ConvertTo-Json -Compress"
    ).format(task_name)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def build_alert_messages(
    run_type: str,
    evaluation: dict[str, Any],
    task_name: str,
    task_info: dict[str, Any] | None,
    record: dict[str, Any] | None,
) -> tuple[str, list[str], dict[str, Any]]:
    """Build a human-readable alert email for a missed or failed run."""
    reason = evaluation["reason"]
    heading = f"ATS Sniper {run_type.title()} Run Alert"
    message_lines = [
        f"The {run_type} ATS Sniper run appears to be {reason.replace('-', ' ')}.",
        f"Expected by: {evaluation['due_at']}",
        f"Scheduled task: {task_name}",
    ]
    stats = {
        "Alert Reason": reason,
        "Task State": task_info.get("state", "Unknown") if task_info else "Unknown",
        "Last Task Result": task_info.get("last_task_result", "Unknown") if task_info else "Unknown",
        "Last Task Run": task_info.get("last_run_time", "Never") if task_info else "Never",
        "Recorded Run Status": record.get("status", "Missing") if record else "Missing",
    }
    if record and record.get("error_message"):
        message_lines.append(f"Last recorded error: {record['error_message']}")
    return heading, message_lines, stats


def run_monitor(dry_run: bool = False) -> int:
    """Check expected runs and send alert emails when they are missing or failed."""
    config = load_config()
    monitoring_config = get_monitoring_config(config)
    if not monitoring_config.get("enabled", True):
        print("Pipeline monitor disabled in config")
        return 0

    now = datetime.now()
    state = load_state()
    schedules = config.get("schedules", {})
    task_names = monitoring_config.get("task_names", {})
    skip_task_scheduler = (
        os.getenv("ATS_SNIPER_SKIP_TASK_SCHEDULER_CHECKS", "").strip().lower()
        in {"1", "true", "yes", "on"}
        or bool(config.get("runtime", {}).get("skip_task_scheduler_checks", False))
    )
    alerts_sent = 0

    for run_type in ("morning", "afternoon"):
        schedule_time = schedules.get(run_type, {}).get("time")
        if not schedule_time:
            continue

        task_name = task_names.get(run_type, "")
        task_info = get_task_info(task_name) if task_name and not skip_task_scheduler else None
        record = get_pipeline_run_record(state, run_type, now)
        evaluation = evaluate_run_health(
            now,
            schedule_time,
            int(monitoring_config.get("grace_minutes", {}).get(run_type, 90)),
            record,
            task_info,
        )
        print(f"{run_type}: {evaluation['reason']}")
        if not evaluation.get("should_alert"):
            continue

        alert_key = make_pipeline_alert_key(run_type, evaluation["reason"], now)
        if pipeline_alert_sent(state, alert_key):
            print(f"  alert already sent for {alert_key}")
            continue

        if dry_run:
            print(f"  would alert for {run_type}: {evaluation['reason']}")
            continue

        heading, message_lines, stats = build_alert_messages(
            run_type,
            evaluation,
            task_name,
            task_info,
            record,
        )
        subject = f"ATS Sniper alert: {run_type} run {evaluation['reason']}"
        if send_status_email(config, subject, heading, message_lines, stats):
            mark_pipeline_alert_sent(
                state,
                alert_key,
                {
                    "sent_at": now.isoformat(),
                    "run_type": run_type,
                    "reason": evaluation["reason"],
                },
            )
            alerts_sent += 1

    if alerts_sent:
        save_state(state)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor ATS Sniper scheduled runs")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate alerts without sending email")
    args = parser.parse_args()
    raise SystemExit(run_monitor(dry_run=args.dry_run))