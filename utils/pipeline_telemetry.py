"""Discovery audit reporting and lightweight telemetry helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from utils.filters import explain_job_targeting, infer_reporting_role_clusters
from utils.pipeline_freshness import FRESHNESS_BUCKET_ORDER, parse_job_datetime
from utils.runtime_paths import reports_dir
from utils.state import load_state, save_state

DISCOVERY_FEEDBACK_FILENAME = "discovery_feedback.csv"
DISCOVERY_FEEDBACK_HEADER = ["url", "decision", "notes", "reviewed_at"]
SOURCE_HEALTH_HISTORY_KEY = "source_health_history"
SOURCE_HEALTH_HISTORY_LIMIT = 40
POSITIVE_FEEDBACK_DECISIONS = {"good lead", "good", "keep", "apply", "hot"}
NEGATIVE_FEEDBACK_DECISIONS = {"noise", "bad lead", "reject", "skip"}
SOURCE_STAGE_FIELDS = (
    ("workday", "workday"),
    ("custom", "custom"),
    ("icims", "icims"),
    ("oracle", "oracle"),
    ("greenhouse", "greenhouse"),
    ("lever", "lever"),
    ("web_discovery", "web-discovery"),
    ("usajobs", "usajobs"),
)
ACTIONABLE_REVIEW_BUCKETS = frozenset(
    {
        "strong_fit_exact",
        "strong_fit_review",
        "exact_fit_review",
        "target_lane_fresh",
        "target_lane_review",
    }
)
MAX_ISSUE_SUMMARY_ITEMS = 10
HTTP_STATUS_PATTERN = re.compile(r"\b([45]\d\d)\b")
URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")
ISSUE_TARGET_PATTERNS = (
    re.compile(r"'([^']{2,120})'"),
    re.compile(r'"([^\"]{2,120})"'),
    re.compile(r"\[([^\]]+\|[^\]]+)\]"),
    re.compile(r"\bfetching ([^:]{2,120}):", re.IGNORECASE),
    re.compile(r"\bfrom ([A-Za-z0-9&().,\- /]{2,120})$", re.IGNORECASE),
    re.compile(r"\bfor ([^:]{2,120}):", re.IGNORECASE),
)


def _ensure_source_bucket(telemetry: dict[str, Any] | None, source: str) -> dict[str, dict[str, int]] | None:
    """Ensure a source-level telemetry bucket exists and return it."""
    if telemetry is None:
        return None
    source_key = str(source).strip() or "unknown"
    bucket = telemetry.setdefault(source_key, {})
    bucket.setdefault("kept_reasons", {})
    bucket.setdefault("rejected_reasons", {})
    return bucket


def record_source_rejection_reason(
    telemetry: dict[str, Any] | None,
    source: str,
    reason: str,
) -> None:
    """Increment a source-scoped rejection reason counter."""
    bucket = _ensure_source_bucket(telemetry, source)
    if bucket is None:
        return
    reason_key = str(reason).strip() or "unknown"
    bucket["rejected_reasons"][reason_key] = int(bucket["rejected_reasons"].get(reason_key, 0) or 0) + 1


def record_source_filter_decision(
    telemetry: dict[str, Any] | None,
    source: str,
    explanation: Mapping[str, Any],
) -> None:
    """Record a keep or reject decision from explain_job_targeting for a direct source."""
    bucket = _ensure_source_bucket(telemetry, source)
    if bucket is None:
        return

    if explanation.get("keep"):
        reason = str(explanation.get("decision_reason", "kept_target_role")).strip() or "kept_target_role"
        bucket["kept_reasons"][reason] = int(bucket["kept_reasons"].get(reason, 0) or 0) + 1
        return

    rejection_reasons = explanation.get("rejection_reasons", [])
    if not isinstance(rejection_reasons, list):
        rejection_reasons = [str(explanation.get("decision_reason", "unknown"))]
    for reason in rejection_reasons:
        record_source_rejection_reason(telemetry, source, str(reason))


def _feedback_polarity(decision: str) -> int:
    """Map manual feedback decisions to positive, negative, or neutral polarity."""
    normalized = str(decision).strip().casefold()
    if normalized in POSITIVE_FEEDBACK_DECISIONS:
        return 1
    if normalized in NEGATIVE_FEEDBACK_DECISIONS:
        return -1
    return 0


def discovery_feedback_csv_path() -> Path:
    """Return the CSV path used for manual lead/noise feedback."""
    return reports_dir() / DISCOVERY_FEEDBACK_FILENAME


def ensure_discovery_feedback_csv() -> Path:
    """Ensure the manual discovery feedback CSV exists with a header."""
    feedback_path = discovery_feedback_csv_path()
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    if feedback_path.exists():
        return feedback_path

    with feedback_path.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(DISCOVERY_FEEDBACK_HEADER)
    return feedback_path


def load_feedback_summary() -> dict[str, Any]:
    """Load lightweight manual-review telemetry from the feedback CSV."""
    feedback_path = ensure_discovery_feedback_csv()
    decision_counts: Counter[str] = Counter()
    total_rows = 0

    with feedback_path.open("r", newline="", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            total_rows += 1
            decision = str(row.get("decision", "")).strip().casefold()
            if decision:
                decision_counts[decision] += 1

    return {
        "path": str(feedback_path),
        "total_feedback_rows": total_rows,
        "decision_counts": dict(sorted(decision_counts.items())),
        "false_positive_count": decision_counts.get("noise", 0),
    }


def _load_feedback_examples() -> list[dict[str, Any]]:
    """Load feedback rows enriched with job metadata from persisted state."""
    feedback_path = ensure_discovery_feedback_csv()
    state_jobs = load_state().get("jobs", {})
    examples: list[dict[str, Any]] = []

    with feedback_path.open("r", newline="", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            decision = str(row.get("decision", "")).strip()
            polarity = _feedback_polarity(decision)
            if polarity == 0:
                continue

            url = str(row.get("url", "")).strip()
            job_record = state_jobs.get(url, {}) if url else {}
            title = str(job_record.get("title", ""))
            description = str(job_record.get("job_description") or job_record.get("description") or "")
            examples.append(
                {
                    "url": url,
                    "decision": decision,
                    "polarity": polarity,
                    "company": str(job_record.get("company", "")).strip().casefold(),
                    "source_family": str(job_record.get("source_family", "")).strip().casefold(),
                    "query_profile": str(job_record.get("query_profile", "")).strip().casefold(),
                    "role_clusters": set(infer_reporting_role_clusters(title, description=description)),
                }
            )

    return examples


def _summarize_feedback_signal(score: int) -> str:
    """Convert a numeric feedback score into a compact label."""
    if score > 0:
        return "boosted"
    if score < 0:
        return "penalized"
    return "neutral"


def build_feedback_signal(job: Mapping[str, Any], feedback_examples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a lightweight ranking signal from past manual good lead or noise decisions."""
    job_url = str(job.get("url", "")).strip()
    job_company = str(job.get("company", "")).strip().casefold()
    job_source_family = str(job.get("source_family", "")).strip().casefold()
    job_query_profile = str(job.get("query_profile", "")).strip().casefold()
    title = str(job.get("title", ""))
    description = str(job.get("job_description") or job.get("description") or "")
    job_clusters = set(infer_reporting_role_clusters(title, description=description))

    score = 0
    reasons: list[str] = []
    for example in feedback_examples:
        polarity = int(example.get("polarity", 0) or 0)
        if polarity == 0:
            continue

        weight = 0
        matched_signals: list[str] = []
        if job_url and job_url == str(example.get("url", "")).strip():
            weight += 30
            matched_signals.append("exact_url")
        if job_company and job_company == str(example.get("company", "")).strip().casefold():
            weight += 14
            matched_signals.append("company")
        if job_source_family and job_source_family == str(example.get("source_family", "")).strip().casefold():
            weight += 6
            matched_signals.append("source_family")
        if job_query_profile and job_query_profile == str(example.get("query_profile", "")).strip().casefold():
            weight += 8
            matched_signals.append("query_profile")

        overlap = job_clusters & set(example.get("role_clusters", set()))
        if overlap:
            weight += min(8, 4 * len(overlap))
            matched_signals.append("role_cluster")

        if not weight:
            continue

        score += polarity * weight
        reasons.append(
            f"{example.get('decision', 'feedback')}:" + "+".join(matched_signals)
        )

    clamped_score = max(-100, min(100, score))
    return {
        "feedback_signal_score": clamped_score,
        "feedback_signal_label": _summarize_feedback_signal(clamped_score),
        "feedback_signal_reasons": reasons[:6],
    }


def apply_feedback_signals(jobs: list[dict[str, Any]]) -> dict[str, int]:
    """Annotate jobs with manual-feedback-derived ranking signals."""
    feedback_examples = _load_feedback_examples()
    counts: Counter[str] = Counter()
    for job in jobs:
        signal = build_feedback_signal(job, feedback_examples)
        job.update(signal)
        counts[str(signal["feedback_signal_label"])] += 1
    return {
        "boosted": counts.get("boosted", 0),
        "neutral": counts.get("neutral", 0),
        "penalized": counts.get("penalized", 0),
    }


def sort_jobs_for_reporting(jobs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sort jobs by freshness first, then by manual feedback signal."""

    def sort_key(job: Mapping[str, Any]) -> tuple[int, int, float, str]:
        bucket = str(job.get("freshness_bucket", "stale_unknown")).strip()
        bucket_order = FRESHNESS_BUCKET_ORDER.get(bucket, 99)
        feedback_score = int(job.get("feedback_signal_score", 0) or 0)
        timestamp = parse_job_datetime(
            job.get("posted_date")
            or job.get("source_detected_at")
            or job.get("first_seen_at")
            or job.get("date_posted")
        )
        timestamp_value = timestamp.timestamp() if timestamp else 0.0
        return (bucket_order, -feedback_score, -timestamp_value, str(job.get("title", "")))

    return [dict(job) for job in sorted(jobs, key=sort_key)]


def _count_values(jobs: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    """Count non-empty string values for a job field."""
    counts: Counter[str] = Counter()
    for job in jobs:
        value = str(job.get(key, "")).strip()
        counts[value or "unknown"] += 1
    return dict(sorted(counts.items()))


def _count_role_clusters(jobs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count jobs by reporting-friendly role clusters."""
    counts: Counter[str] = Counter()
    for job in jobs:
        clusters = infer_reporting_role_clusters(
            str(job.get("title", "")),
            description=str(job.get("job_description") or job.get("description") or ""),
        )
        if not clusters:
            counts["unclassified"] += 1
            continue
        for cluster in clusters:
            counts[cluster] += 1
    return dict(sorted(counts.items()))


def _count_filter_reasons(jobs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count decision reasons for kept jobs using the shared filter explainer."""
    counts: Counter[str] = Counter()
    for job in jobs:
        explanation = explain_job_targeting(
            str(job.get("title", "")),
            location=str(job.get("location", "")),
            workplace_type=str(job.get("workplace_type", "")),
            description=str(job.get("job_description") or job.get("description") or ""),
        )
        counts[str(explanation.get("decision_reason", "unknown"))] += 1
    return dict(sorted(counts.items()))


def _count_screening_reasons(jobs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count early-classifier noise reasons for screened-out jobs."""
    counts: Counter[str] = Counter()
    for job in jobs:
        category = str(job.get("screening_category", "NOISE")).strip() or "NOISE"
        reason = str(job.get("screening_reason", "")).strip()
        key = f"{category}: {reason}" if reason else category
        counts[key] += 1
    return dict(sorted(counts.items()))


def _count_non_empty_values(jobs: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    """Count non-empty string values for a job field."""
    counts: Counter[str] = Counter()
    for job in jobs:
        value = str(job.get(key, "")).strip()
        if value:
            counts[value] += 1
    return dict(sorted(counts.items()))


def _is_actionable_regular_job(job: Mapping[str, Any]) -> bool:
    """Return True when a regular job reached a meaningful review lane."""
    if bool(job.get("actionable_review", False)):
        return True
    if str(job.get("export_priority", "standard_review")).strip() == "deprioritized_review":
        return False
    review_bucket = str(job.get("review_bucket", "")).strip()
    return review_bucket in ACTIONABLE_REVIEW_BUCKETS


def _build_found_vs_actionable_by_source_family(
    *,
    all_new_jobs: Sequence[Mapping[str, Any]],
    hot_jobs: Sequence[Mapping[str, Any]],
    actionable_regular_jobs: Sequence[Mapping[str, Any]],
    non_actionable_review_jobs: Sequence[Mapping[str, Any]],
    screened_out_jobs: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    """Compare raw discovery volume with the subset that stayed actionable."""
    found_counts = Counter(str(job.get("source_family", "")).strip() or "unknown" for job in all_new_jobs)
    hot_counts = Counter(str(job.get("source_family", "")).strip() or "unknown" for job in hot_jobs)
    actionable_review_counts = Counter(
        str(job.get("source_family", "")).strip() or "unknown"
        for job in actionable_regular_jobs
    )
    non_actionable_counts = Counter(
        str(job.get("source_family", "")).strip() or "unknown"
        for job in non_actionable_review_jobs
    )
    screened_out_counts = Counter(
        str(job.get("source_family", "")).strip() or "unknown"
        for job in screened_out_jobs
    )

    families = sorted(
        set(found_counts)
        | set(hot_counts)
        | set(actionable_review_counts)
        | set(non_actionable_counts)
        | set(screened_out_counts)
    )
    return {
        family: {
            "found": int(found_counts.get(family, 0) or 0),
            "actionable": int(hot_counts.get(family, 0) or 0)
            + int(actionable_review_counts.get(family, 0) or 0),
            "hot": int(hot_counts.get(family, 0) or 0),
            "actionable_review": int(actionable_review_counts.get(family, 0) or 0),
            "non_actionable_review": int(non_actionable_counts.get(family, 0) or 0),
            "screened_out_noise": int(screened_out_counts.get(family, 0) or 0),
        }
        for family in families
    }


def _enrich_jobs_from_discovery_context(
    jobs: Sequence[Mapping[str, Any]],
    all_new_jobs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Backfill reporting fields on scored jobs using the original discovery records."""
    jobs_by_url = {
        str(job.get("url", "")).strip(): dict(job)
        for job in all_new_jobs
        if str(job.get("url", "")).strip()
    }
    jobs_by_identity = {
        (
            str(job.get("company", "")).strip().casefold(),
            str(job.get("title", "")).strip().casefold(),
        ): dict(job)
        for job in all_new_jobs
        if str(job.get("company", "")).strip() or str(job.get("title", "")).strip()
    }
    jobs_by_title = {
        str(job.get("title", "")).strip().casefold(): dict(job)
        for job in all_new_jobs
        if str(job.get("title", "")).strip()
    }

    enriched_jobs: list[dict[str, Any]] = []
    for job in jobs:
        enriched_job = dict(job)
        lookup_job = jobs_by_url.get(str(job.get("url", "")).strip())
        if lookup_job is None:
            lookup_job = jobs_by_identity.get(
                (
                    str(job.get("company", "")).strip().casefold(),
                    str(job.get("title", "")).strip().casefold(),
                )
            )
        if lookup_job is None:
            lookup_job = jobs_by_title.get(str(job.get("title", "")).strip().casefold())
        if lookup_job is not None:
            merged_job = dict(lookup_job)
            merged_job.update(enriched_job)
            enriched_job = merged_job
        enriched_jobs.append(enriched_job)
    return enriched_jobs


def _group_pipeline_issues(pipeline_issues: Sequence[str]) -> dict[str, list[str]]:
    """Group pipeline issues by extracted stage label."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for issue in pipeline_issues:
        issue_text = str(issue).strip()
        stage = "pipeline"
        if issue_text.startswith("[") and "]" in issue_text:
            stage, _, remaining = issue_text[1:].partition("]")
            stage = stage.strip() or "pipeline"
            issue_text = remaining.strip() or issue_text
        grouped[stage].append(issue_text)
    return dict(sorted(grouped.items()))


def _normalize_issue_stage(stage: str) -> str:
    """Normalize stage labels so drift and issue summaries stay consistent."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(stage).strip().casefold()).strip("_")
    return normalized or "pipeline"


def _extract_issue_target(issue_message: str) -> str:
    """Extract the board, slug, company, or URL most associated with an issue line."""
    url_match = URL_PATTERN.search(issue_message)
    if url_match:
        return url_match.group(0).rstrip(".,)")

    for pattern in ISSUE_TARGET_PATTERNS:
        match = pattern.search(issue_message)
        if match:
            return match.group(1).strip().rstrip(".,)")
    return ""


def _classify_issue_signature(issue_message: str) -> str:
    """Collapse similar warning lines into stable issue signatures."""
    normalized = str(issue_message).strip().casefold()
    if not normalized:
        return "unknown_issue"

    if "timeout" in normalized or "timed out" in normalized:
        return "timeout"

    http_status_match = HTTP_STATUS_PATTERN.search(normalized)
    if "not found" in normalized or (http_status_match and http_status_match.group(1) == "404"):
        return "http_404"
    if http_status_match:
        return f"http_{http_status_match.group(1)}"

    if "request error" in normalized:
        return "request_error"
    if "fetch error" in normalized:
        return "fetch_error"
    if "failed to fetch data" in normalized:
        return "fetch_failed"
    if "search error" in normalized:
        return "search_error"
    if "api error" in normalized:
        return "api_error"
    if "playwright fallback error" in normalized:
        return "playwright_fallback_error"
    if "playwright not installed" in normalized:
        return "playwright_missing"
    if "wrapper" in normalized:
        return "wrapper_response"
    if "iframe" in normalized:
        return "iframe_fallback"
    if "email failed" in normalized:
        return "email_failed"
    if "traceback" in normalized:
        return "traceback"

    compact_signature = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return compact_signature[:60] or "unknown_issue"


def _parse_pipeline_issue(issue: str) -> dict[str, str]:
    """Parse one pipeline issue line into a normalized structured record."""
    raw_issue = str(issue).strip()
    stage = "pipeline"
    issue_message = raw_issue
    if raw_issue.startswith("[") and "]" in raw_issue:
        stage_label, _, remaining = raw_issue[1:].partition("]")
        stage = _normalize_issue_stage(stage_label)
        issue_message = remaining.strip() or raw_issue

    return {
        "stage": stage,
        "target": _extract_issue_target(issue_message),
        "signature": _classify_issue_signature(issue_message),
        "message": issue_message,
    }


def _build_issue_records(pipeline_issues: Sequence[str]) -> list[dict[str, str]]:
    """Normalize pipeline issue lines into structured issue records."""
    return [_parse_pipeline_issue(issue) for issue in pipeline_issues if str(issue).strip()]


def _aggregate_issue_records(
    issue_records: Sequence[Mapping[str, Any]],
    *,
    require_target: bool = False,
) -> list[dict[str, Any]]:
    """Group normalized issue records into compact summary rows."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    for record in issue_records:
        stage = str(record.get("stage", "pipeline")).strip() or "pipeline"
        target = str(record.get("target", "")).strip()
        signature = str(record.get("signature", "unknown_issue")).strip() or "unknown_issue"
        if require_target and not target:
            continue

        key = (stage, target, signature)
        entry = grouped.setdefault(
            key,
            {
                "stage": stage,
                "target": target,
                "signature": signature,
                "count": 0,
                "sample_message": str(record.get("message", "")).strip(),
            },
        )
        entry["count"] = int(entry["count"] or 0) + 1
        if not entry["sample_message"]:
            entry["sample_message"] = str(record.get("message", "")).strip()

    return sorted(
        grouped.values(),
        key=lambda item: (
            -int(item.get("count", 0) or 0),
            str(item.get("stage", "")),
            str(item.get("target", "")),
            str(item.get("signature", "")),
        ),
    )[:MAX_ISSUE_SUMMARY_ITEMS]


def _history_without_current_snapshot(
    source_health_history: Sequence[Mapping[str, Any]],
    *,
    run_type: str,
    current_stage_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Drop the just-recorded current snapshot when the report runs after persistence."""
    history = [dict(snapshot) for snapshot in source_health_history if isinstance(snapshot, Mapping)]
    if not history:
        return []

    latest_snapshot = history[-1]
    if (
        str(latest_snapshot.get("run_type", "")).strip() == str(run_type).strip()
        and latest_snapshot.get("stage_summary") == current_stage_summary
    ):
        return history[:-1]
    return history


def _build_stage_drift_summary(
    run_type: str,
    current_stage_summary: Mapping[str, Any],
    source_health_history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare the current stage summary against the previous run of the same type."""
    comparison_history = _history_without_current_snapshot(
        source_health_history,
        run_type=run_type,
        current_stage_summary=current_stage_summary,
    )
    previous_snapshots = [
        snapshot
        for snapshot in comparison_history
        if str(snapshot.get("run_type", "")).strip() == str(run_type).strip()
        and isinstance(snapshot.get("stage_summary"), Mapping)
    ]
    if not previous_snapshots:
        return {
            "available": False,
            "message": f"No previous {run_type} run snapshot is available for stage drift comparison.",
        }

    baseline_snapshot = previous_snapshots[-1]
    baseline_stage_summary = baseline_snapshot.get("stage_summary", {})
    changed_stages: dict[str, Any] = {}
    regression_count = 0
    improvement_count = 0

    for stage in sorted(set(current_stage_summary) | set(baseline_stage_summary)):
        current = current_stage_summary.get(stage, {}) if isinstance(current_stage_summary, Mapping) else {}
        previous = baseline_stage_summary.get(stage, {}) if isinstance(baseline_stage_summary, Mapping) else {}
        if not isinstance(current, Mapping):
            current = {}
        if not isinstance(previous, Mapping):
            previous = {}

        current_status = str(current.get("status", "unknown")).strip() or "unknown"
        previous_status = str(previous.get("status", "unknown")).strip() or "unknown"
        delta_new_jobs = int(current.get("new_jobs", 0) or 0) - int(previous.get("new_jobs", 0) or 0)
        delta_issue_count = int(current.get("issue_count", 0) or 0) - int(previous.get("issue_count", 0) or 0)
        if current_status == previous_status and delta_new_jobs == 0 and delta_issue_count == 0:
            continue

        regression = bool(
            (current_status == "issues" and previous_status != "issues")
            or delta_issue_count > 0
            or (
                delta_new_jobs < 0
                and current_status in {"issues", "ok_no_results"}
                and int(previous.get("new_jobs", 0) or 0) > 0
            )
        )
        improvement = bool(
            (previous_status == "issues" and current_status != "issues")
            or delta_issue_count < 0
            or (delta_new_jobs > 0 and current_status != "issues")
        )
        if regression:
            regression_count += 1
        if improvement:
            improvement_count += 1

        changed_stages[stage] = {
            "previous_status": previous_status,
            "current_status": current_status,
            "delta_new_jobs": delta_new_jobs,
            "delta_issue_count": delta_issue_count,
            "regression": regression,
            "improvement": improvement,
        }

    return {
        "available": True,
        "baseline_generated_at": str(baseline_snapshot.get("generated_at", "")).strip(),
        "regression_count": regression_count,
        "improvement_count": improvement_count,
        "message": (
            "No stage-level drift detected versus the previous run."
            if not changed_stages
            else ""
        ),
        "changed_stages": dict(sorted(changed_stages.items())),
    }


def _build_recurring_edge_case_summary(
    current_issue_records: Sequence[Mapping[str, Any]],
    *,
    run_type: str,
    current_stage_summary: Mapping[str, Any],
    source_health_history: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Identify current-run issues that have shown up in prior run history."""
    current_issue_summary = _aggregate_issue_records(current_issue_records)
    if not current_issue_summary:
        return []

    comparison_history = _history_without_current_snapshot(
        source_health_history,
        run_type=run_type,
        current_stage_summary=current_stage_summary,
    )
    historical_matches: dict[tuple[str, str, str], dict[str, Any]] = {}
    for snapshot in comparison_history:
        snapshot_generated_at = str(snapshot.get("generated_at", "")).strip()
        for record in snapshot.get("issue_records", []):
            if not isinstance(record, Mapping):
                continue
            key = (
                str(record.get("stage", "pipeline")).strip() or "pipeline",
                str(record.get("target", "")).strip(),
                str(record.get("signature", "unknown_issue")).strip() or "unknown_issue",
            )
            entry = historical_matches.setdefault(
                key,
                {
                    "prior_occurrences": 0,
                    "prior_runs": set(),
                    "last_seen_at": "",
                },
            )
            entry["prior_occurrences"] = int(entry["prior_occurrences"] or 0) + 1
            if snapshot_generated_at:
                entry["prior_runs"].add(snapshot_generated_at)
                if snapshot_generated_at > str(entry.get("last_seen_at", "")):
                    entry["last_seen_at"] = snapshot_generated_at

    recurring_entries: list[dict[str, Any]] = []
    for current_entry in current_issue_summary:
        key = (
            str(current_entry.get("stage", "pipeline")).strip() or "pipeline",
            str(current_entry.get("target", "")).strip(),
            str(current_entry.get("signature", "unknown_issue")).strip() or "unknown_issue",
        )
        history_entry = historical_matches.get(key)
        if history_entry is None:
            continue

        recurring_entries.append(
            {
                **current_entry,
                "prior_occurrences": int(history_entry.get("prior_occurrences", 0) or 0),
                "prior_runs": len(history_entry.get("prior_runs", set())),
                "last_seen_at": str(history_entry.get("last_seen_at", "")).strip(),
            }
        )

    return sorted(
        recurring_entries,
        key=lambda item: (
            -int(item.get("prior_occurrences", 0) or 0),
            -int(item.get("count", 0) or 0),
            str(item.get("stage", "")),
            str(item.get("target", "")),
            str(item.get("signature", "")),
        ),
    )[:MAX_ISSUE_SUMMARY_ITEMS]


def _aggregate_source_rejections(source_telemetry: Mapping[str, Any] | None) -> dict[str, int]:
    """Aggregate rejection reasons across direct-source telemetry buckets."""
    counts: Counter[str] = Counter()
    for source_bucket in (source_telemetry or {}).values():
        rejected_reasons = source_bucket.get("rejected_reasons", {}) if isinstance(source_bucket, dict) else {}
        for reason, count in rejected_reasons.items():
            counts[str(reason)] += int(count or 0)
    return dict(sorted(counts.items()))


def _extract_ignored_reason_summary(
    web_discovery_telemetry: Mapping[str, Any] | None,
    direct_scraper_telemetry: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Extract seniority and location rejection counts across discovery and direct sources."""
    rejected_reasons = Counter(dict((web_discovery_telemetry or {}).get("rejected_reasons", {})))
    rejected_reasons.update(_aggregate_source_rejections(direct_scraper_telemetry))
    relevant_reasons = {
        str(reason): int(count or 0)
        for reason, count in rejected_reasons.items()
        if str(reason).startswith("excluded_seniority_")
        or str(reason) in {"blocked_location", "unpreferred_location"}
    }
    return dict(sorted(relevant_reasons.items()))


def _default_llm_stage_usage(model: str = "") -> dict[str, Any]:
    """Return the default shape for one LLM stage in the audit payload."""
    return {
        "model": model,
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "estimated_cost_usd": 0.0,
        "pricing_available": False,
    }


def _coerce_llm_stage_usage(
    stage_data: Mapping[str, Any] | None,
    *,
    fallback_model: str = "",
    fallback_calls: int = 0,
) -> dict[str, Any]:
    """Normalize one LLM stage for audit serialization."""
    normalized = _default_llm_stage_usage(fallback_model)
    if isinstance(stage_data, Mapping):
        normalized["model"] = str(stage_data.get("model", fallback_model)).strip()
        normalized["calls"] = int(stage_data.get("calls", fallback_calls) or 0)
        normalized["prompt_tokens"] = int(stage_data.get("prompt_tokens", 0) or 0)
        normalized["completion_tokens"] = int(stage_data.get("completion_tokens", 0) or 0)
        normalized["total_tokens"] = int(stage_data.get("total_tokens", 0) or 0)
        normalized["cached_tokens"] = int(stage_data.get("cached_tokens", 0) or 0)
        normalized["estimated_cost_usd"] = round(float(stage_data.get("estimated_cost_usd", 0.0) or 0.0), 6)
        normalized["pricing_available"] = bool(stage_data.get("pricing_available", False))
    else:
        normalized["calls"] = fallback_calls

    if normalized["total_tokens"] <= 0:
        normalized["total_tokens"] = normalized["prompt_tokens"] + normalized["completion_tokens"]

    return normalized


def _build_llm_usage_summary(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Build the audit-ready LLM usage summary from pipeline stats."""
    raw_usage = stats.get("llm_usage", {})
    if not isinstance(raw_usage, Mapping):
        raw_usage = {}

    early_classifier = _coerce_llm_stage_usage(
        raw_usage.get("early_classifier") if isinstance(raw_usage.get("early_classifier"), Mapping) else None,
        fallback_calls=int(stats.get("early_classifier_calls", 0) or 0),
    )
    full_scoring = _coerce_llm_stage_usage(
        raw_usage.get("full_scoring") if isinstance(raw_usage.get("full_scoring"), Mapping) else None,
        fallback_calls=int(stats.get("full_scoring_calls", 0) or 0),
    )
    totals = _coerce_llm_stage_usage(
        raw_usage.get("totals") if isinstance(raw_usage.get("totals"), Mapping) else None,
        fallback_model="multiple",
    )

    if totals["calls"] <= 0:
        totals["calls"] = early_classifier["calls"] + full_scoring["calls"]
    if totals["prompt_tokens"] <= 0:
        totals["prompt_tokens"] = early_classifier["prompt_tokens"] + full_scoring["prompt_tokens"]
    if totals["completion_tokens"] <= 0:
        totals["completion_tokens"] = early_classifier["completion_tokens"] + full_scoring["completion_tokens"]
    if totals["total_tokens"] <= 0:
        totals["total_tokens"] = early_classifier["total_tokens"] + full_scoring["total_tokens"]
    if totals["cached_tokens"] <= 0:
        totals["cached_tokens"] = early_classifier["cached_tokens"] + full_scoring["cached_tokens"]
    if totals["estimated_cost_usd"] <= 0:
        totals["estimated_cost_usd"] = round(
            float(early_classifier["estimated_cost_usd"] or 0.0)
            + float(full_scoring["estimated_cost_usd"] or 0.0),
            6,
        )
    totals["pricing_available"] = bool(
        totals["pricing_available"]
        or early_classifier["pricing_available"]
        or full_scoring["pricing_available"]
    )

    return {
        "jobs_analyzed": int(stats.get("analyzed", 0) or 0),
        "screened_out_noise": int(stats.get("screened_out_noise", 0) or 0),
        "early_classifier_calls": early_classifier["calls"],
        "full_scoring_calls": full_scoring["calls"],
        "early_classifier": early_classifier,
        "full_scoring": full_scoring,
        "totals": totals,
    }


def _format_cost_value(value: Any) -> str:
    """Format a cost value for human-readable markdown output."""
    try:
        return f"${float(value or 0.0):.6f}"
    except (TypeError, ValueError):
        return "$0.000000"


def _build_stage_summary(
    stats: Mapping[str, Any],
    pipeline_issues: Sequence[str],
    stage_attempts: Mapping[str, bool] | None,
) -> dict[str, Any]:
    """Build per-stage discovery counts and issue summaries."""
    issues_by_stage = _group_pipeline_issues(pipeline_issues)
    stage_summary: dict[str, Any] = {}
    for stats_key, stage_name in SOURCE_STAGE_FIELDS:
        stage_issues = list(
            dict.fromkeys(
                [
                    *issues_by_stage.get(stage_name, []),
                    *issues_by_stage.get(stats_key, []),
                ]
            )
        )
        attempted = bool(stage_attempts.get(stats_key, False)) if stage_attempts else True
        new_jobs = int(stats.get(stats_key, 0) or 0)
        already_seen_not_new = int(stats.get(f"{stats_key}_already_seen", 0) or 0)
        if not attempted:
            status = "skipped"
        elif stage_issues:
            status = "issues"
        elif new_jobs > 0:
            status = "ok"
        elif already_seen_not_new > 0:
            status = "ok_all_already_seen"
        else:
            status = "ok_no_results"
        stage_summary[stats_key] = {
            "attempted": attempted,
            "new_jobs": new_jobs,
            "already_seen_not_new": already_seen_not_new,
            "issue_count": len(stage_issues),
            "issues": stage_issues[:10],
            "status": status,
        }
    return stage_summary


def record_source_health_snapshot(
    *,
    run_type: str,
    stats: Mapping[str, Any],
    pipeline_issues: Sequence[str],
    stage_attempts: Mapping[str, bool] | None,
) -> None:
    """Persist a rolling source-health snapshot for recent-rate reporting."""
    stage_summary = _build_stage_summary(stats, pipeline_issues, stage_attempts)
    snapshot = {
        "generated_at": datetime.now().isoformat(),
        "run_type": run_type,
        "stage_summary": stage_summary,
        "issue_records": _build_issue_records(pipeline_issues),
    }
    state = load_state()
    history = list(state.get(SOURCE_HEALTH_HISTORY_KEY, []))
    history.append(snapshot)
    state[SOURCE_HEALTH_HISTORY_KEY] = history[-SOURCE_HEALTH_HISTORY_LIMIT:]
    save_state(state)


def _build_source_health_rates(source_health_history: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Build recent success and failure rates by source from persisted run history."""
    history = list(source_health_history or load_state().get(SOURCE_HEALTH_HISTORY_KEY, []))
    aggregates: dict[str, dict[str, Any]] = {
        stats_key: {
            "attempted_runs": 0,
            "successful_runs": 0,
            "issue_runs": 0,
            "avg_new_jobs": 0.0,
            "_total_new_jobs": 0,
        }
        for stats_key, _stage_name in SOURCE_STAGE_FIELDS
    }

    for snapshot in history:
        stage_summary = snapshot.get("stage_summary", {}) if isinstance(snapshot, dict) else {}
        for stats_key, _stage_name in SOURCE_STAGE_FIELDS:
            stage = stage_summary.get(stats_key, {}) if isinstance(stage_summary, dict) else {}
            if not stage or not stage.get("attempted"):
                continue
            aggregates[stats_key]["attempted_runs"] += 1
            aggregates[stats_key]["_total_new_jobs"] += int(stage.get("new_jobs", 0) or 0)
            if str(stage.get("status", "")).strip() in {"ok", "ok_no_results", "ok_all_already_seen"}:
                aggregates[stats_key]["successful_runs"] += 1
            elif str(stage.get("status", "")).strip() == "issues":
                aggregates[stats_key]["issue_runs"] += 1

    output: dict[str, Any] = {}
    for stats_key, aggregate in aggregates.items():
        attempted_runs = int(aggregate["attempted_runs"])
        total_new_jobs = int(aggregate.pop("_total_new_jobs"))
        aggregate["avg_new_jobs"] = round(total_new_jobs / attempted_runs, 2) if attempted_runs else 0.0
        aggregate["success_rate"] = round(aggregate["successful_runs"] / attempted_runs, 2) if attempted_runs else 0.0
        aggregate["failure_rate"] = round(aggregate["issue_runs"] / attempted_runs, 2) if attempted_runs else 0.0
        output[stats_key] = aggregate
    return dict(sorted(output.items()))


def build_discovery_audit_payload(
    *,
    run_type: str,
    stats: Mapping[str, Any],
    all_new_jobs: Sequence[Mapping[str, Any]],
    hot_job_results: Mapping[str, Any] | None,
    pipeline_issues: Sequence[str],
    web_discovery_telemetry: Mapping[str, Any] | None,
    direct_scraper_telemetry: Mapping[str, Any] | None = None,
    stage_attempts: Mapping[str, bool] | None = None,
    benchmark_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured discovery audit payload for the completed run."""
    hot_job_results = dict(hot_job_results or {})
    source_health_history = list(load_state().get(SOURCE_HEALTH_HISTORY_KEY, []))
    stage_summary = _build_stage_summary(stats, pipeline_issues, stage_attempts)
    current_issue_records = _build_issue_records(pipeline_issues)
    hot_jobs = _enrich_jobs_from_discovery_context(
        hot_job_results.get("hot_jobs", []),
        all_new_jobs,
    )
    regular_jobs = _enrich_jobs_from_discovery_context(
        hot_job_results.get("regular_jobs", []),
        all_new_jobs,
    )
    screened_out_jobs = _enrich_jobs_from_discovery_context(
        hot_job_results.get("screened_out_jobs", []),
        all_new_jobs,
    )
    actionable_regular_jobs = [job for job in regular_jobs if _is_actionable_regular_job(job)]
    non_actionable_review_jobs = [job for job in regular_jobs if not _is_actionable_regular_job(job)]

    feedback_summary = load_feedback_summary()
    feedback_signal_summary = _count_values(all_new_jobs, "feedback_signal_label")
    source_health_rates = _build_source_health_rates(source_health_history)
    llm_usage = _build_llm_usage_summary(stats)
    direct_scraper_rejections = _aggregate_source_rejections(direct_scraper_telemetry)
    web_discovery_rejections = dict(
        sorted(dict((web_discovery_telemetry or {}).get("rejected_reasons", {})).items())
    )
    found_vs_actionable = _build_found_vs_actionable_by_source_family(
        all_new_jobs=all_new_jobs,
        hot_jobs=hot_jobs,
        actionable_regular_jobs=actionable_regular_jobs,
        non_actionable_review_jobs=non_actionable_review_jobs,
        screened_out_jobs=screened_out_jobs,
    )
    stage_drift_summary = _build_stage_drift_summary(
        run_type,
        stage_summary,
        source_health_history,
    )
    board_specific_failures = _aggregate_issue_records(current_issue_records, require_target=True)
    recurring_edge_cases = _build_recurring_edge_case_summary(
        current_issue_records,
        run_type=run_type,
        current_stage_summary=stage_summary,
        source_health_history=source_health_history,
    )
    return {
        "generated_at": datetime.now().isoformat(),
        "run_type": run_type,
        "phase4_verification": {
            "lightweight_run_type_available": run_type == "lightweight" or "fresh_under_6h" in stats,
            "freshness_buckets_recorded": any(
                key in stats for key in ("fresh_under_6h", "fresh_under_24h", "stale_unknown")
            ),
            "query_profile_tracking_available": any(job.get("query_profile") for job in all_new_jobs),
            "source_family_tracking_available": any(job.get("source_family") for job in all_new_jobs),
            "stage_drift_summary_available": bool(stage_drift_summary.get("available", False)),
            "board_specific_failure_tracking_available": True,
        },
        "counts": {
            "total_new_jobs": len(all_new_jobs),
            "hot_jobs": len(hot_jobs),
            "regular_jobs": len(regular_jobs),
            "actionable_jobs": len(hot_jobs) + len(actionable_regular_jobs),
            "non_actionable_review_jobs": len(non_actionable_review_jobs),
            "screened_out_noise": len(screened_out_jobs),
            "issues": len(pipeline_issues),
        },
        "jobs_by_source": _count_values(all_new_jobs, "source"),
        "jobs_by_source_family": _count_values(all_new_jobs, "source_family"),
        "jobs_by_source_board": _count_values(all_new_jobs, "source_board"),
        "actionable_jobs_by_source_family": _count_values(
            [*hot_jobs, *actionable_regular_jobs],
            "source_family",
        ),
        "found_vs_actionable_by_source_family": found_vs_actionable,
        "jobs_by_query_profile": _count_values(all_new_jobs, "query_profile"),
        "jobs_by_freshness_bucket": _count_values(all_new_jobs, "freshness_bucket"),
        "jobs_by_role_cluster": _count_role_clusters(all_new_jobs),
        "hot_jobs_by_role_cluster": _count_role_clusters(hot_jobs),
        "decision_reasons_for_kept_jobs": _count_filter_reasons(all_new_jobs),
        "screened_out_noise_reasons": _count_screening_reasons(screened_out_jobs),
        "review_deprioritization_reasons": _count_non_empty_values(
            non_actionable_review_jobs,
            "review_deprioritization_reason",
        ),
        "feedback_signal_summary": feedback_signal_summary,
        "stage_summary": stage_summary,
        "stage_drift_summary": stage_drift_summary,
        "source_health_rates": source_health_rates,
        "board_specific_failures": board_specific_failures,
        "recurring_edge_cases": recurring_edge_cases,
        "web_discovery_telemetry": dict(web_discovery_telemetry or {}),
        "direct_scraper_telemetry": dict(direct_scraper_telemetry or {}),
        "direct_scraper_rejections_by_reason": direct_scraper_rejections,
        "web_discovery_rejections_by_reason": web_discovery_rejections,
        "ignored_due_seniority_or_location": _extract_ignored_reason_summary(
            web_discovery_telemetry,
            direct_scraper_telemetry,
        ),
        "llm_usage": llm_usage,
        "manual_feedback": feedback_summary,
        "benchmark_summary": dict(benchmark_summary or {}),
        "pipeline_issues": list(pipeline_issues),
        "sample_jobs": [
            {
                "company": str(job.get("company", "Unknown")),
                "title": str(job.get("title", "Unknown")),
                "source": str(job.get("source", "unknown")),
                "freshness_bucket": str(job.get("freshness_bucket", "")),
                "query_profile": str(job.get("query_profile", "")),
                "feedback_signal_label": str(job.get("feedback_signal_label", "neutral")),
                "feedback_signal_score": int(job.get("feedback_signal_score", 0) or 0),
                "url": str(job.get("url", "")),
            }
            for job in list(all_new_jobs)[:15]
        ],
    }


def _render_audit_markdown(payload: Mapping[str, Any]) -> str:
    """Render a compact Markdown version of the discovery audit report."""
    counts = payload.get("counts", {})
    benchmark_summary = payload.get("benchmark_summary", {})
    lines = [
        "# ATS Sniper Discovery Audit",
        "",
        f"Generated: {payload.get('generated_at', '')}",
        f"Run type: {payload.get('run_type', '')}",
        "",
        "## Summary",
        f"- New jobs: {counts.get('total_new_jobs', 0)}",
        f"- Hot jobs: {counts.get('hot_jobs', 0)}",
        f"- Regular jobs: {counts.get('regular_jobs', 0)}",
        f"- Actionable jobs: {counts.get('actionable_jobs', 0)}",
        f"- Non-actionable review jobs: {counts.get('non_actionable_review_jobs', 0)}",
        f"- Screened noise: {counts.get('screened_out_noise', 0)}",
        f"- Issues: {counts.get('issues', 0)}",
        "",
    ]

    if benchmark_summary:
        primary_scope_name = str(benchmark_summary.get("scope_name", "")).strip()
        lines.extend(
            [
                "## Benchmark Overlap",
                f"- Benchmark set: {benchmark_summary.get('benchmark_name', 'Discovery benchmark')}",
                f"- Source report: {benchmark_summary.get('source_report', '')}",
                f"- Match strategy: {benchmark_summary.get('match_strategy', 'normalized_url_only')}",
                f"- Targets: {benchmark_summary.get('target_count', 0)}",
            ]
        )
        if primary_scope_name:
            lines.append(f"- Primary scope: {primary_scope_name}")
        if "candidate_job_count" in benchmark_summary:
            lines.append(
                f"- Candidate jobs in primary scope: {benchmark_summary.get('candidate_job_count', 0)}"
            )
        lines.extend(
            [
                f"- Hits: {benchmark_summary.get('hit_count', 0)}",
                f"- Misses: {benchmark_summary.get('miss_count', 0)}",
                f"- Extras: {benchmark_summary.get('extra_count', 0)}",
                f"- Overlap rate: {benchmark_summary.get('overlap_rate', 0.0)}",
                "",
                "### Benchmark Coverage By Source Family",
            ]
        )
        for family, summary in benchmark_summary.get("source_family_summary", {}).items():
            lines.append(
                f"- {family}: targets={summary.get('targets', 0)}, hits={summary.get('hits', 0)}, misses={summary.get('misses', 0)}, overlap_rate={summary.get('overlap_rate', 0.0)}"
            )
        if not benchmark_summary.get("source_family_summary"):
            lines.append("- none")
        lines.append("")

        comparison_scopes = benchmark_summary.get("comparison_scopes", {})
        if isinstance(comparison_scopes, Mapping) and comparison_scopes:
            lines.append("### Benchmark Scope Comparison")
            for scope_name, scope_summary in comparison_scopes.items():
                lines.append(
                    f"- {scope_name}: candidates={scope_summary.get('candidate_job_count', 0)}, hits={scope_summary.get('hit_count', 0)}, misses={scope_summary.get('miss_count', 0)}, extras={scope_summary.get('extra_count', 0)}, overlap_rate={scope_summary.get('overlap_rate', 0.0)}"
                )
            lines.append("")

        drift = benchmark_summary.get("drift", {})
        lines.append("### Benchmark Drift")
        if isinstance(drift, dict) and drift.get("available"):
            lines.append(f"- Delta hits: {drift.get('delta_hit_count', 0)}")
            lines.append(f"- Delta misses: {drift.get('delta_miss_count', 0)}")
            lines.append(f"- Delta extras: {drift.get('delta_extra_count', 0)}")
            lines.append(f"- Delta overlap rate: {drift.get('delta_overlap_rate', 0.0)}")
            if drift.get("previous_completed_at"):
                lines.append(f"- Previous completed at: {drift.get('previous_completed_at', '')}")
        else:
            lines.append(f"- {drift.get('message', 'No prior benchmark comparison is available.')}")
        lines.append("")

        lines.append("### Benchmark Misses")
        for miss in benchmark_summary.get("misses", []):
            lines.append(
                f"- {miss.get('company', 'Unknown')} | {miss.get('title', 'Unknown')} | {miss.get('source_family', 'unknown')}"
            )
        if not benchmark_summary.get("misses"):
            lines.append("- none")
        lines.append("")

        lines.append("### Benchmark Limitations")
        for limitation in benchmark_summary.get("limitations", []):
            lines.append(f"- {limitation}")
        if not benchmark_summary.get("limitations"):
            lines.append("- none")
        lines.append("")

    lines.extend(
        [
        "## Source Yield",
        ]
    )

    lines.append("## Found Vs Actionable")
    for family, summary in payload.get("found_vs_actionable_by_source_family", {}).items():
        lines.append(
            "- "
            f"{family}: found={summary.get('found', 0)}, actionable={summary.get('actionable', 0)}, "
            f"hot={summary.get('hot', 0)}, actionable_review={summary.get('actionable_review', 0)}, "
            f"non_actionable_review={summary.get('non_actionable_review', 0)}, "
            f"screened_out_noise={summary.get('screened_out_noise', 0)}"
        )
    if not payload.get("found_vs_actionable_by_source_family"):
        lines.append("- none")
    lines.append("")

    for key in ("jobs_by_source", "jobs_by_source_board", "jobs_by_query_profile", "jobs_by_role_cluster", "jobs_by_freshness_bucket"):
        lines.append(f"### {key.replace('_', ' ').title()}")
        for label, count in payload.get(key, {}).items():
            lines.append(f"- {label}: {count}")
        lines.append("")

    lines.append("## Feedback Ranking")
    for label, count in payload.get("feedback_signal_summary", {}).items():
        lines.append(f"- {label}: {count}")
    if not payload.get("feedback_signal_summary"):
        lines.append("- neutral: 0")
    lines.append("")

    lines.append("## Rejection Signals")
    for label, count in payload.get("screened_out_noise_reasons", {}).items():
        lines.append(f"- {label}: {count}")
    if not payload.get("screened_out_noise_reasons"):
        lines.append("- none")
    lines.append("")

    lines.append("### Review Deprioritization Signals")
    for label, count in payload.get("review_deprioritization_reasons", {}).items():
        lines.append(f"- {label}: {count}")
    if not payload.get("review_deprioritization_reasons"):
        lines.append("- none")
    lines.append("")

    lines.append("### Web Discovery Rejections")
    for label, count in payload.get("web_discovery_rejections_by_reason", {}).items():
        lines.append(f"- {label}: {count}")
    if not payload.get("web_discovery_rejections_by_reason"):
        lines.append("- none")
    lines.append("")

    lines.append("### Direct Scraper Rejections")
    for label, count in payload.get("direct_scraper_rejections_by_reason", {}).items():
        lines.append(f"- {label}: {count}")
    if not payload.get("direct_scraper_rejections_by_reason"):
        lines.append("- none")
    lines.append("")

    lines.append("### Ignored Due Seniority Or Location")
    for label, count in payload.get("ignored_due_seniority_or_location", {}).items():
        lines.append(f"- {label}: {count}")
    if not payload.get("ignored_due_seniority_or_location"):
        lines.append("- none")
    lines.append("")

    lines.append("## Stage Summary")
    for stage, summary in payload.get("stage_summary", {}).items():
        already_seen = int(summary.get('already_seen_not_new', 0) or 0)
        already_seen_suffix = f", already_seen={already_seen}" if already_seen else ""
        lines.append(
            f"- {stage}: attempted={summary.get('attempted', False)}, {summary.get('new_jobs', 0)} new jobs{already_seen_suffix}, {summary.get('issue_count', 0)} issues, status={summary.get('status', 'ok')}"
        )
    lines.append("")

    lines.append("## Stage Drift")
    stage_drift_summary = payload.get("stage_drift_summary", {})
    if stage_drift_summary.get("available"):
        if stage_drift_summary.get("baseline_generated_at"):
            lines.append(f"- Baseline snapshot: {stage_drift_summary.get('baseline_generated_at', '')}")
        changed_stages = stage_drift_summary.get("changed_stages", {})
        if changed_stages:
            for stage, summary in changed_stages.items():
                lines.append(
                    f"- {stage}: {summary.get('previous_status', 'unknown')} -> {summary.get('current_status', 'unknown')}, delta_new_jobs={summary.get('delta_new_jobs', 0)}, delta_issue_count={summary.get('delta_issue_count', 0)}, regression={'yes' if summary.get('regression') else 'no'}, improvement={'yes' if summary.get('improvement') else 'no'}"
                )
        else:
            lines.append(f"- {stage_drift_summary.get('message', 'No stage-level drift detected versus the previous run.')}")
    else:
        lines.append(
            f"- {stage_drift_summary.get('message', 'No previous run snapshot is available for stage drift comparison.')}")
    lines.append("")

    lines.append("## Board-Specific Failures")
    for entry in payload.get("board_specific_failures", []):
        lines.append(
            f"- {entry.get('stage', 'unknown')} | {entry.get('target', 'stage-wide')} | {entry.get('signature', 'unknown_issue')} | count={entry.get('count', 0)} | {entry.get('sample_message', '')}"
        )
    if not payload.get("board_specific_failures"):
        lines.append("- none")
    lines.append("")

    lines.append("## Recurring Edge Cases")
    for entry in payload.get("recurring_edge_cases", []):
        lines.append(
            f"- {entry.get('stage', 'unknown')} | {entry.get('target', 'stage-wide') or 'stage-wide'} | {entry.get('signature', 'unknown_issue')} | current={entry.get('count', 0)}, previous_occurrences={entry.get('prior_occurrences', 0)}, prior_runs={entry.get('prior_runs', 0)}, last_seen={entry.get('last_seen_at', '')}"
        )
    if not payload.get("recurring_edge_cases"):
        lines.append("- none")
    lines.append("")

    lines.append("## Source Health Rates")
    for stage, summary in payload.get("source_health_rates", {}).items():
        lines.append(
            f"- {stage}: runs={summary.get('attempted_runs', 0)}, success_rate={summary.get('success_rate', 0)}, failure_rate={summary.get('failure_rate', 0)}, avg_new_jobs={summary.get('avg_new_jobs', 0)}"
        )
    lines.append("")

    llm_usage = payload.get("llm_usage", {})
    lines.append("## LLM Usage")
    lines.append(f"- Jobs analyzed: {llm_usage.get('jobs_analyzed', 0)}")
    lines.append(f"- Noise screened before scoring: {llm_usage.get('screened_out_noise', 0)}")
    lines.append(f"- Estimated total cost: {_format_cost_value(llm_usage.get('totals', {}).get('estimated_cost_usd', 0.0))}")
    lines.append("")

    for section_label, section_key in (("Early Classifier", "early_classifier"), ("Full Scoring", "full_scoring")):
        summary = llm_usage.get(section_key, {})
        calls = int(summary.get('calls', 0) or 0)
        lines.append(f"### {section_label}")
        lines.append(f"- model: {summary.get('model', '') or 'unknown'}")
        lines.append(f"- calls: {calls}")
        if calls == 0:
            lines.append("- status: no_candidates_to_score")
        lines.append(f"- prompt_tokens: {summary.get('prompt_tokens', 0)}")
        lines.append(f"- completion_tokens: {summary.get('completion_tokens', 0)}")
        lines.append(f"- total_tokens: {summary.get('total_tokens', 0)}")
        lines.append(f"- cached_tokens: {summary.get('cached_tokens', 0)}")
        lines.append(f"- estimated_cost_usd: {_format_cost_value(summary.get('estimated_cost_usd', 0.0))}")
        lines.append("")

    feedback = payload.get("manual_feedback", {})
    lines.append("## Manual Feedback")
    lines.append(f"- Feedback file: {feedback.get('path', '')}")
    lines.append(f"- False positives marked noise: {feedback.get('false_positive_count', 0)}")
    for label, count in feedback.get("decision_counts", {}).items():
        lines.append(f"- {label}: {count}")
    lines.append("")

    if payload.get("pipeline_issues"):
        lines.append("## Pipeline Issues")
        for issue in payload.get("pipeline_issues", [])[:12]:
            lines.append(f"- {issue}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_discovery_audit_report(
    *,
    run_type: str,
    stats: Mapping[str, Any],
    all_new_jobs: Sequence[Mapping[str, Any]],
    hot_job_results: Mapping[str, Any] | None,
    pipeline_issues: Sequence[str],
    web_discovery_telemetry: Mapping[str, Any] | None,
    direct_scraper_telemetry: Mapping[str, Any] | None = None,
    stage_attempts: Mapping[str, bool] | None = None,
    benchmark_summary: Mapping[str, Any] | None = None,
    base_name: str | None = None,
) -> dict[str, str]:
    """Write JSON and Markdown discovery audit reports for the completed run."""
    payload = build_discovery_audit_payload(
        run_type=run_type,
        stats=stats,
        all_new_jobs=all_new_jobs,
        hot_job_results=hot_job_results,
        pipeline_issues=pipeline_issues,
        web_discovery_telemetry=web_discovery_telemetry,
        direct_scraper_telemetry=direct_scraper_telemetry,
        stage_attempts=stage_attempts,
        benchmark_summary=benchmark_summary,
    )
    reports_root = reports_dir()
    reports_root.mkdir(parents=True, exist_ok=True)
    if not base_name:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        base_name = f"discovery_audit_{timestamp}_{run_type}"
    json_path = reports_root / f"{base_name}.json"
    markdown_path = reports_root / f"{base_name}.md"

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_audit_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(markdown_path)}