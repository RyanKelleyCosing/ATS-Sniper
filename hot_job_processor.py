#!/usr/bin/env python3
"""
Hot Job Processor - Automated Resume Tailoring for High-Match Jobs

The "Sniper" Logic:
- Score < 80%: Job goes to CSV for manual review
- Score >= 90%: Natural high-confidence automation lane
- Fresh exact-fit security, IAM, and platform roles can enter strong-fit backfill at 75%+

This is the bridge between job scoring and resume generation.
"""

import json
import csv
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Local imports
from job_scraper import fetch_job_description
from resume_tailor import (
    analyze_job_match,
    classify_job_for_screening,
    generate_cover_letter,
)
from generate_tailored_resume import generate_tailored_resume_for_job
from utils.application_packages import sanitize_file_component, write_cover_letter_docx
from utils.contacts import merge_contact_emails, primary_contact_email
from utils.filters import normalize_filter_text
from utils.pipeline_freshness import freshness_badge_label
from utils.runtime_paths import regular_jobs_csv_path
from utils.state import load_config, load_state, save_state

# Paths
SCRIPT_DIR = Path(__file__).parent
OUTPUTS_DIR = SCRIPT_DIR / "outputs"
CSV_PATH = regular_jobs_csv_path()

# Hot job threshold
HOT_JOB_THRESHOLD = 80
BOOSTED_THRESHOLD_DELTA = -5
PENALIZED_THRESHOLD_DELTA = 5
DEFAULT_DAILY_RELEVANT_JOBS_TARGET = 10
DEFAULT_MIN_GENERATED_PACKAGES_PER_DAY = 3
DEFAULT_HIGH_CONFIDENCE_MATCH_FLOOR = 90
DEFAULT_AUTO_PROMOTE_MATCH_FLOOR = HOT_JOB_THRESHOLD
DEFAULT_EXACT_FIT_STRONG_FIT_FLOOR = 75
DEFAULT_EXACT_FIT_AUTO_PROMOTE_MATCH_FLOOR = 75
DEFAULT_EXACT_FIT_SIGNAL_FLOOR = 5
DEFAULT_ADJACENT_STRONG_FIT_FLOOR = 82
DEFAULT_ADJACENT_AUTO_PROMOTE_MATCH_FLOOR = 88
DEFAULT_ADJACENT_SIGNAL_FLOOR = 5
DEFAULT_AUTO_PROMOTE_SCREENING_CONFIDENCE = 0.85
DEFAULT_AUTO_PROMOTE_DISCOVERY_CONFIDENCE = 70
DEFAULT_AUTO_PROMOTE_FRESHNESS_BUCKETS = ("fresh_under_6h", "fresh_under_24h")
AUTOMATION_STATUS_HOT = "hot"
AUTOMATION_STATUS_AUTO_PROMOTED = "auto_promoted"
GENERATION_LANE_HIGH_CONFIDENCE = "high_confidence"
GENERATION_LANE_STRONG_FIT = "strong_fit"
GENERATION_LANE_REVIEW = "review"
EXACT_FIT_SCREENING_CATEGORIES = frozenset({"SECURITY", "IAM", "DEVOPS_SRE_CLOUD"})
ADJACENT_SCREENING_CATEGORY = "ADJACENT_TECH"
ACTIONABLE_SCREENING_CATEGORIES = EXACT_FIT_SCREENING_CATEGORIES | {ADJACENT_SCREENING_CATEGORY}
ACTIONABLE_REVIEW_BUCKETS = frozenset(
    {
        "strong_fit_exact",
        "adjacent_strong_fit",
        "strong_fit_review",
        "exact_fit_review",
        "target_lane_fresh",
        "target_lane_review",
        "adjacent_fresh",
        "adjacent_review",
    }
)
LOW_PRACTICALITY_TITLE_MARKERS = (
    " architect ",
    " principal ",
    " staff ",
    " manager ",
    " director ",
    " head of ",
    " vice president ",
    " vp ",
    " chief ",
)


def get_cover_letter_model(config: Dict[str, Any]) -> str:
    """Return the preferred model for scheduled cover letter generation."""
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        return "gpt-4o-mini"

    return (
        str(settings.get("application_package_model", "")).strip()
        or str(settings.get("tailor_batch_model", "")).strip()
        or str(settings.get("openai_model", "gpt-4o-mini")).strip()
        or "gpt-4o-mini"
    )


def load_master_resume() -> str:
    """Load master resume content for scoring."""
    resume_path = SCRIPT_DIR.parent / "resume_devops.md"
    if resume_path.exists():
        with open(resume_path, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


def get_early_classifier_settings(config: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether the early screener is enabled and which model it should use."""
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        return True, "gpt-4o-mini"

    model = str(settings.get("early_classifier_model", "")).strip()
    if not model:
        model = str(settings.get("openai_model", "gpt-4o-mini")).strip() or "gpt-4o-mini"

    return bool(settings.get("early_classifier_enabled", True)), model


def update_state_job_record(
    state: Dict[str, Any],
    job: Dict[str, Any],
    contact_emails: List[str],
    match_score: int,
    screening_result: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist the latest analysis details for a job into job state."""
    job_url = job.get("url", "")
    if not job_url:
        return

    job_record = state.setdefault("jobs", {}).setdefault(job_url, {})
    job_description = str(job.get("job_description", "")).strip()
    job_record.update(
        {
            "title": job.get("title", "Unknown"),
            "company": job.get("company", "Unknown"),
            "location": job.get("location", ""),
            "source": job.get("source", "unknown"),
            "tier": job.get("tier", job_record.get("tier", "")),
            "source_board": job.get("source_board", job_record.get("source_board", "")),
            "scraped_at": job.get("scraped_at", job_record.get("scraped_at", "")),
            "contact_email": job.get("contact_email", ""),
            "contact_emails": contact_emails,
            "match_score": match_score,
            "exact_fit_lane": job.get("exact_fit_lane", ""),
            "exact_fit_title_match": bool(job.get("exact_fit_title_match", False)),
            "exact_fit_signal_score": int(job.get("exact_fit_signal_score", 0) or 0),
            "exact_fit_bonus": int(job.get("exact_fit_bonus", 0) or 0),
            "adjacent_fit_lane": job.get("adjacent_fit_lane", ""),
            "adjacent_fit_title_match": bool(job.get("adjacent_fit_title_match", False)),
            "adjacent_fit_signal_score": int(job.get("adjacent_fit_signal_score", 0) or 0),
            "adjacent_fit_bonus": int(job.get("adjacent_fit_bonus", 0) or 0),
            "generation_lane": str(job.get("generation_lane", "")).strip(),
            "export_priority": str(job.get("export_priority", "")).strip(),
            "review_bucket": str(job.get("review_bucket", "")).strip(),
            "actionable_review": bool(job.get("actionable_review", False)),
            "review_deprioritization_reason": str(
                job.get("review_deprioritization_reason", "")
            ).strip(),
            "last_analyzed_at": datetime.now().isoformat(),
        }
    )
    if job_description:
        job_record["job_description"] = job_description
        job_record["description"] = job_description

    if screening_result is not None:
        job_record.update(
            {
                "screening_category": screening_result.get("category", ""),
                "screening_confidence": screening_result.get("confidence", 0.0),
                "screening_reason": screening_result.get("reason", ""),
                "screened_out": screening_result.get("category") == "NOISE",
            }
        )


def generate_cover_letter_artifacts(
    client: Any,
    model: str,
    job: Dict[str, Any],
    resume_source_path: Path,
    output_dir: Path,
) -> Dict[str, str]:
    """Generate cover letter text and DOCX artifacts for a hot job."""
    cover_letter_text = generate_cover_letter(
        client,
        job.get("job_description", ""),
        resume_source_path.read_text(encoding="utf-8"),
        job.get("company", "Unknown"),
        job.get("title", "Unknown"),
        model,
    ).strip()
    if not cover_letter_text:
        return {}

    safe_company = sanitize_file_component(str(job.get("company", "Unknown")))[:40]
    safe_role = sanitize_file_component(str(job.get("title", "Unknown")))[:60]
    cover_letter_basename = f"cover_letter_{safe_company}_{safe_role}"
    cover_letter_txt_path = output_dir / f"{cover_letter_basename}.txt"
    cover_letter_docx_path = output_dir / f"{cover_letter_basename}.docx"

    cover_letter_txt_path.write_text(cover_letter_text + "\n", encoding="utf-8")
    write_cover_letter_docx(cover_letter_text, cover_letter_docx_path)
    return {
        "cover_letter_text": cover_letter_text,
        "cover_letter_txt": str(cover_letter_txt_path),
        "cover_letter_docx": str(cover_letter_docx_path),
    }


def get_hot_threshold_for_job(job: Dict[str, Any]) -> int:
    """Return the hot-job threshold after applying manual feedback signals."""
    feedback_label = str(job.get("feedback_signal_label", "neutral")).strip().casefold()
    if feedback_label == "boosted":
        return max(HOT_JOB_THRESHOLD + BOOSTED_THRESHOLD_DELTA, 70)
    if feedback_label == "penalized":
        return HOT_JOB_THRESHOLD + PENALIZED_THRESHOLD_DELTA
    return HOT_JOB_THRESHOLD


def _has_low_practicality_title_scope(title: str) -> bool:
    """Return True when a title reads like a stretch or management-heavy role."""
    normalized_title = f" {normalize_filter_text(title)} "
    if not normalized_title.strip():
        return False
    return any(marker in normalized_title for marker in LOW_PRACTICALITY_TITLE_MARKERS)


def get_review_deprioritization_reason(job: Mapping[str, Any]) -> str | None:
    """Explain why a scored review job should sink below the actionable queue."""
    feedback_label = str(job.get("feedback_signal_label", "neutral")).strip().casefold()
    if feedback_label == "boosted":
        return None
    if feedback_label == "penalized":
        return "manual_feedback_penalty"

    if str(job.get("generation_lane", "")).strip() == GENERATION_LANE_HIGH_CONFIDENCE:
        return None
    if bool(job.get("exact_fit_title_match", False)):
        return None
    if _has_low_practicality_title_scope(str(job.get("title", ""))):
        return "stretch_title_scope"

    screening_category = str(job.get("screening_category", "")).strip().upper()
    match_score = int(job.get("match_score", 0) or 0)
    if screening_category and screening_category not in ACTIONABLE_SCREENING_CATEGORIES and match_score < HOT_JOB_THRESHOLD:
        return "off_lane_review"

    freshness_bucket = str(job.get("freshness_bucket", "")).strip()
    screening_confidence = float(job.get("screening_confidence", 0.0) or 0.0)
    raw_discovery_confidence = job.get("discovery_confidence")
    discovery_confidence = (
        int(raw_discovery_confidence or 0)
        if raw_discovery_confidence not in (None, "")
        else None
    )
    if (
        str(job.get("generation_lane", "")).strip() == GENERATION_LANE_REVIEW
        and freshness_bucket in {"stale_unknown", "stale_over_24h"}
        and match_score < HOT_JOB_THRESHOLD
        and screening_confidence < 0.75
        and (
            discovery_confidence is None
            or discovery_confidence < DEFAULT_AUTO_PROMOTE_DISCOVERY_CONFIDENCE
        )
    ):
        return "stale_low_signal_review"
    return None


def get_export_priority(
    job: Dict[str, Any],
    *,
    review_deprioritization_reason: str | None = None,
) -> str:
    """Return the review priority label for a non-hot job export row."""
    feedback_label = str(job.get("feedback_signal_label", "neutral")).strip().casefold()
    if feedback_label == "boosted":
        return "priority_review"
    deprioritization_reason = review_deprioritization_reason
    if deprioritization_reason is None:
        deprioritization_reason = get_review_deprioritization_reason(job)
    if deprioritization_reason:
        return "deprioritized_review"
    if str(job.get("generation_lane", "")).strip() == GENERATION_LANE_STRONG_FIT:
        return "priority_review"
    if bool(job.get("exact_fit_title_match", False)) and int(job.get("match_score", 0) or 0) >= 70:
        return "priority_review"
    if bool(job.get("adjacent_fit_title_match", False)) and int(job.get("match_score", 0) or 0) >= 82:
        return "priority_review"
    return "standard_review"


def is_actionable_review_job(job: Mapping[str, Any]) -> bool:
    """Return True when a job survived scoring into an actionable lane."""
    if str(job.get("generation_lane", "")).strip() == GENERATION_LANE_HIGH_CONFIDENCE:
        return True
    if str(job.get("export_priority", "standard_review")).strip() == "deprioritized_review":
        return False
    review_bucket = str(job.get("review_bucket", "")).strip() or get_regular_review_bucket(job)
    return review_bucket in ACTIONABLE_REVIEW_BUCKETS


def get_regular_review_bucket(job: Mapping[str, Any]) -> str:
    """Classify exported review jobs into queue-oriented buckets."""
    generation_lane = str(job.get("generation_lane", "")).strip()
    screening_category = str(job.get("screening_category", "")).strip().upper()
    freshness_bucket = str(job.get("freshness_bucket", "")).strip()
    export_priority = str(job.get("export_priority", "standard_review")).strip()
    exact_fit = bool(job.get("exact_fit_title_match", False))
    adjacent_fit = bool(job.get("adjacent_fit_title_match", False))
    match_score = int(job.get("match_score", 0) or 0)

    if export_priority == "deprioritized_review":
        return "deprioritized_review"
    if generation_lane == GENERATION_LANE_STRONG_FIT and exact_fit:
        return "strong_fit_exact"
    if generation_lane == GENERATION_LANE_STRONG_FIT and adjacent_fit:
        return "adjacent_strong_fit"
    if generation_lane == GENERATION_LANE_STRONG_FIT:
        return "strong_fit_review"
    if exact_fit and screening_category in EXACT_FIT_SCREENING_CATEGORIES and match_score >= 70:
        return "exact_fit_review"
    if screening_category == ADJACENT_SCREENING_CATEGORY and freshness_bucket in {"fresh_under_6h", "fresh_under_24h"}:
        return "adjacent_fresh"
    if screening_category == ADJACENT_SCREENING_CATEGORY:
        return "adjacent_review"
    if screening_category in EXACT_FIT_SCREENING_CATEGORIES and freshness_bucket in {"fresh_under_6h", "fresh_under_24h"}:
        return "target_lane_fresh"
    if screening_category in EXACT_FIT_SCREENING_CATEGORIES:
        return "target_lane_review"
    return "broad_review"


def get_regular_job_queue_score(job: Mapping[str, Any]) -> int:
    """Score exported regular jobs so the CSV reads like an apply queue."""
    queue_score = int(job.get("match_score", 0) or 0)

    if str(job.get("generation_lane", "")).strip() == GENERATION_LANE_STRONG_FIT:
        queue_score += 25
    if bool(job.get("exact_fit_title_match", False)):
        queue_score += 20
    if bool(job.get("adjacent_fit_title_match", False)):
        queue_score += 14
    queue_score += min(int(job.get("exact_fit_signal_score", 0) or 0), 10) * 2
    queue_score += min(int(job.get("adjacent_fit_signal_score", 0) or 0), 10) * 2

    freshness_bucket = str(job.get("freshness_bucket", "")).strip()
    if freshness_bucket == "fresh_under_6h":
        queue_score += 18
    elif freshness_bucket == "fresh_under_24h":
        queue_score += 10

    screening_category = str(job.get("screening_category", "")).strip().upper()
    if screening_category in ACTIONABLE_SCREENING_CATEGORIES:
        queue_score += 8

    export_priority = str(job.get("export_priority", "standard_review")).strip()
    if export_priority == "priority_review":
        queue_score += 12
    elif export_priority == "deprioritized_review":
        queue_score -= 20

    queue_score += int(float(job.get("screening_confidence", 0.0) or 0.0) * 10)
    queue_score += int(job.get("discovery_confidence", 0) or 0) // 10
    return queue_score


def get_phase6_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the Phase 6 daily-goal automation settings."""
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}

    freshness_buckets = settings.get("phase6_auto_promote_freshness_buckets", DEFAULT_AUTO_PROMOTE_FRESHNESS_BUCKETS)
    if not isinstance(freshness_buckets, list | tuple):
        freshness_buckets = DEFAULT_AUTO_PROMOTE_FRESHNESS_BUCKETS

    return {
        "daily_relevant_jobs_target": int(
            settings.get("phase6_daily_relevant_jobs_target", DEFAULT_DAILY_RELEVANT_JOBS_TARGET) or 0
        ),
        "min_generated_packages_per_day": int(
            settings.get(
                "phase6_min_generated_packages_per_day",
                DEFAULT_MIN_GENERATED_PACKAGES_PER_DAY,
            )
            or 0
        ),
        "high_confidence_match_floor": int(
            settings.get(
                "phase6_high_confidence_match_floor",
                DEFAULT_HIGH_CONFIDENCE_MATCH_FLOOR,
            )
            or 0
        ),
        "exact_fit_strong_fit_floor": int(
            settings.get(
                "phase6_exact_fit_strong_fit_floor",
                DEFAULT_EXACT_FIT_STRONG_FIT_FLOOR,
            )
            or 0
        ),
        "adjacent_strong_fit_floor": int(
            settings.get(
                "phase6_adjacent_strong_fit_floor",
                DEFAULT_ADJACENT_STRONG_FIT_FLOOR,
            )
            or 0
        ),
        "auto_promote_enabled": bool(settings.get("phase6_auto_promote_enabled", True)),
        "auto_promote_match_floor": int(
            settings.get("phase6_auto_promote_match_floor", DEFAULT_AUTO_PROMOTE_MATCH_FLOOR) or 0
        ),
        "exact_fit_auto_promote_match_floor": int(
            settings.get(
                "phase6_exact_fit_auto_promote_match_floor",
                DEFAULT_EXACT_FIT_AUTO_PROMOTE_MATCH_FLOOR,
            )
            or 0
        ),
        "adjacent_auto_promote_match_floor": int(
            settings.get(
                "phase6_adjacent_auto_promote_match_floor",
                DEFAULT_ADJACENT_AUTO_PROMOTE_MATCH_FLOOR,
            )
            or 0
        ),
        "exact_fit_signal_floor": int(
            settings.get("phase6_exact_fit_signal_floor", DEFAULT_EXACT_FIT_SIGNAL_FLOOR) or 0
        ),
        "adjacent_signal_floor": int(
            settings.get("phase6_adjacent_signal_floor", DEFAULT_ADJACENT_SIGNAL_FLOOR) or 0
        ),
        "auto_promote_screening_confidence": float(
            settings.get(
                "phase6_auto_promote_screening_confidence",
                DEFAULT_AUTO_PROMOTE_SCREENING_CONFIDENCE,
            )
            or 0.0
        ),
        "auto_promote_discovery_confidence": int(
            settings.get(
                "phase6_auto_promote_discovery_confidence",
                DEFAULT_AUTO_PROMOTE_DISCOVERY_CONFIDENCE,
            )
            or 0
        ),
        "auto_promote_freshness_buckets": tuple(str(bucket).strip() for bucket in freshness_buckets if str(bucket).strip()),
    }


def is_exact_fit_priority_job(job: Mapping[str, Any], settings: Mapping[str, Any]) -> bool:
    """Return True for exact-title target roles with strong lane evidence."""
    exact_fit_lane = str(job.get("exact_fit_lane", "")).strip().upper()
    screening_category = str(job.get("screening_category", "")).strip().upper()
    if exact_fit_lane not in EXACT_FIT_SCREENING_CATEGORIES:
        return False
    if not bool(job.get("exact_fit_title_match", False)):
        return False

    signal_floor = int(settings.get("exact_fit_signal_floor", DEFAULT_EXACT_FIT_SIGNAL_FLOOR) or 0)
    if int(job.get("exact_fit_signal_score", 0) or 0) < signal_floor:
        return False
    if screening_category and screening_category != exact_fit_lane:
        return False

    screening_confidence = float(job.get("screening_confidence", 0.0) or 0.0)
    required_confidence = float(
        settings.get(
            "auto_promote_screening_confidence",
            DEFAULT_AUTO_PROMOTE_SCREENING_CONFIDENCE,
        )
        or 0.0
    )
    return screening_confidence >= required_confidence


def is_adjacent_priority_job(job: Mapping[str, Any], settings: Mapping[str, Any]) -> bool:
    """Return True for strong adjacent-tech roles that should remain actionable."""
    screening_category = str(job.get("screening_category", "")).strip().upper()
    if screening_category != ADJACENT_SCREENING_CATEGORY:
        return False
    if not bool(job.get("adjacent_fit_title_match", False)):
        return False

    signal_floor = int(settings.get("adjacent_signal_floor", DEFAULT_ADJACENT_SIGNAL_FLOOR) or 0)
    if int(job.get("adjacent_fit_signal_score", 0) or 0) < signal_floor:
        return False

    screening_confidence = float(job.get("screening_confidence", 0.0) or 0.0)
    required_confidence = float(
        settings.get(
            "auto_promote_screening_confidence",
            DEFAULT_AUTO_PROMOTE_SCREENING_CONFIDENCE,
        )
        or 0.0
    )
    return screening_confidence >= required_confidence


def get_strong_fit_threshold_for_job(
    job: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> int:
    """Allow exact-fit roles into the strong-fit lane at a lower floor."""
    if is_exact_fit_priority_job(job, settings):
        return min(
            HOT_JOB_THRESHOLD,
            int(
                settings.get(
                    "exact_fit_strong_fit_floor",
                    DEFAULT_EXACT_FIT_STRONG_FIT_FLOOR,
                )
                or HOT_JOB_THRESHOLD
            ),
        )
    if is_adjacent_priority_job(job, settings):
        return min(
            HOT_JOB_THRESHOLD,
            int(
                settings.get(
                    "adjacent_strong_fit_floor",
                    DEFAULT_ADJACENT_STRONG_FIT_FLOOR,
                )
                or HOT_JOB_THRESHOLD
            ),
        )
    return HOT_JOB_THRESHOLD


def get_auto_promote_match_floor_for_job(
    job: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> int:
    """Use a lower daily-backfill floor for fresh exact-fit roles."""
    if is_exact_fit_priority_job(job, settings):
        return min(
            HOT_JOB_THRESHOLD,
            int(
                settings.get(
                    "exact_fit_auto_promote_match_floor",
                    DEFAULT_EXACT_FIT_AUTO_PROMOTE_MATCH_FLOOR,
                )
                or HOT_JOB_THRESHOLD
            ),
        )
    if is_adjacent_priority_job(job, settings):
        return min(
            HOT_JOB_THRESHOLD,
            int(
                settings.get(
                    "adjacent_auto_promote_match_floor",
                    DEFAULT_ADJACENT_AUTO_PROMOTE_MATCH_FLOOR,
                )
                or HOT_JOB_THRESHOLD
            ),
        )
    return int(settings.get("auto_promote_match_floor", DEFAULT_AUTO_PROMOTE_MATCH_FLOOR) or 0)


def determine_generation_lane(
    match_score: int,
    *,
    high_confidence_threshold: int,
    strong_fit_threshold: int,
) -> str:
    """Map a scored job into the high-confidence, strong-fit, or review lane."""
    if match_score >= high_confidence_threshold:
        return GENERATION_LANE_HIGH_CONFIDENCE
    if match_score >= strong_fit_threshold:
        return GENERATION_LANE_STRONG_FIT
    return GENERATION_LANE_REVIEW


def count_automated_jobs_for_today(state: Dict[str, Any], run_date: datetime | None = None) -> int:
    """Return how many jobs already entered the automation lane today."""
    date_key = (run_date or datetime.now()).date().isoformat()
    count = 0
    for record in state.get("jobs", {}).values():
        if not isinstance(record, dict):
            continue
        automation_status = str(record.get("automation_status", "")).strip()
        last_automated_at = str(record.get("last_automated_at", "")).strip()
        if automation_status not in {AUTOMATION_STATUS_HOT, AUTOMATION_STATUS_AUTO_PROMOTED}:
            continue
        if last_automated_at[:10] != date_key:
            continue
        count += 1
    return count


def evaluate_auto_promotion(job: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Determine whether a regular job should be promoted into the automation lane."""
    reasons: List[str] = []
    if str(job.get("feedback_signal_label", "neutral")).strip().casefold() == "penalized":
        return False, reasons

    if str(job.get("generation_lane", GENERATION_LANE_REVIEW)).strip() not in {
        GENERATION_LANE_STRONG_FIT,
        GENERATION_LANE_HIGH_CONFIDENCE,
    }:
        return False, reasons

    freshness_bucket = str(job.get("freshness_bucket", "")).strip()
    if freshness_bucket not in set(settings.get("auto_promote_freshness_buckets", ())):
        return False, reasons
    reasons.append(freshness_bucket)

    match_score = int(job.get("match_score", 0) or 0)
    exact_fit_priority = is_exact_fit_priority_job(job, settings)
    match_floor = get_auto_promote_match_floor_for_job(job, settings)
    if match_score < match_floor:
        return False, reasons
    reasons.append(f"match>={match_floor}")

    screening_category = str(job.get("screening_category", "UNKNOWN")).strip().upper()
    screening_confidence = float(job.get("screening_confidence", 0.0) or 0.0)
    if screening_category not in ACTIONABLE_SCREENING_CATEGORIES:
        return False, reasons
    if screening_confidence < float(
        settings.get("auto_promote_screening_confidence", DEFAULT_AUTO_PROMOTE_SCREENING_CONFIDENCE) or 0.0
    ):
        return False, reasons
    reasons.append(f"screening:{screening_category.lower()}")

    raw_discovery_confidence = job.get("discovery_confidence")
    if raw_discovery_confidence not in (None, ""):
        discovery_confidence = int(raw_discovery_confidence or 0)
        discovery_floor = int(
            settings.get("auto_promote_discovery_confidence", DEFAULT_AUTO_PROMOTE_DISCOVERY_CONFIDENCE) or 0
        )
        if discovery_confidence < discovery_floor:
            return False, reasons
        reasons.append(f"discovery>={discovery_floor}")

    if str(job.get("export_priority", "standard_review")).strip() == "deprioritized_review":
        return False, reasons

    if exact_fit_priority:
        reasons.append("exact_fit")
    elif is_adjacent_priority_job(job, settings):
        reasons.append("adjacent_tech")

    return True, reasons


def _auto_promotion_sort_key(job: Dict[str, Any]) -> tuple[int, int, int, int, int, int, str, str]:
    freshness_rank = {
        "fresh_under_6h": 0,
        "fresh_under_24h": 1,
        "stale_unknown": 2,
        "stale_over_24h": 3,
    }
    feedback_rank = {"boosted": 0, "neutral": 1, "penalized": 2}
    return (
        freshness_rank.get(str(job.get("freshness_bucket", "stale_unknown")), 2),
        0
        if bool(job.get("exact_fit_title_match", False))
        else 1
        if bool(job.get("adjacent_fit_title_match", False))
        else 2,
        feedback_rank.get(str(job.get("feedback_signal_label", "neutral")).strip().casefold(), 1),
        -int(job.get("exact_fit_signal_score", 0) or 0),
        -int(job.get("adjacent_fit_signal_score", 0) or 0),
        -int(job.get("match_score", 0) or 0),
        str(job.get("company", "")),
        str(job.get("title", "")),
    )


def apply_daily_goal_promotions(
    hot_jobs: List[Dict[str, Any]],
    regular_jobs: List[Dict[str, Any]],
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Promote fresh, high-confidence regular jobs until the daily target is met."""
    settings = get_phase6_settings(config)
    already_automated_today = count_automated_jobs_for_today(state)
    daily_target = max(int(settings.get("min_generated_packages_per_day", DEFAULT_MIN_GENERATED_PACKAGES_PER_DAY) or 0), 0)
    promotion_summary: Dict[str, Any] = {
        "daily_target": daily_target,
        "already_automated_today": already_automated_today,
        "high_confidence_match_floor": int(
            settings.get("high_confidence_match_floor", DEFAULT_HIGH_CONFIDENCE_MATCH_FLOOR) or 0
        ),
        "natural_hot_jobs": len(hot_jobs),
        "auto_promoted_count": 0,
        "eligible_for_promotion": 0,
        "remaining_after_run": max(daily_target - already_automated_today - len(hot_jobs), 0),
        "auto_promote_enabled": bool(settings.get("auto_promote_enabled", True)),
    }
    if not promotion_summary["auto_promote_enabled"] or daily_target <= 0:
        return hot_jobs, regular_jobs, promotion_summary

    remaining_slots = max(daily_target - already_automated_today - len(hot_jobs), 0)
    if remaining_slots <= 0:
        promotion_summary["remaining_after_run"] = 0
        return hot_jobs, regular_jobs, promotion_summary

    eligible_jobs: List[Dict[str, Any]] = []
    ineligible_jobs: List[Dict[str, Any]] = []
    for job in regular_jobs:
        should_promote, reasons = evaluate_auto_promotion(job, settings)
        if should_promote:
            candidate = dict(job)
            candidate["automation_status"] = AUTOMATION_STATUS_AUTO_PROMOTED
            candidate["phase6_auto_promoted"] = True
            candidate["generation_lane"] = GENERATION_LANE_STRONG_FIT
            candidate["auto_promotion_reason"] = ", ".join(reasons)
            eligible_jobs.append(candidate)
        else:
            ineligible_jobs.append(job)

    promotion_summary["eligible_for_promotion"] = len(eligible_jobs)
    eligible_jobs = sorted(eligible_jobs, key=_auto_promotion_sort_key)
    promoted_jobs = eligible_jobs[:remaining_slots]
    promoted_urls = {job.get("url", "") for job in promoted_jobs if job.get("url")}
    remaining_regular_jobs = [job for job in ineligible_jobs]
    remaining_regular_jobs.extend(job for job in eligible_jobs[remaining_slots:])
    remaining_regular_jobs = sort_regular_jobs_for_export(remaining_regular_jobs)

    promotion_summary["auto_promoted_count"] = len(promoted_jobs)
    promotion_summary["remaining_after_run"] = max(
        daily_target - already_automated_today - len(hot_jobs) - len(promoted_jobs),
        0,
    )
    if promoted_urls:
        hot_jobs = list(hot_jobs) + promoted_jobs
    return hot_jobs, remaining_regular_jobs, promotion_summary


def persist_automation_results(state: Dict[str, Any], jobs: List[Dict[str, Any]]) -> None:
    """Persist automation routing outcomes for hot and auto-promoted jobs."""
    automated_at = datetime.now().isoformat()
    for job in jobs:
        job_url = str(job.get("url", "")).strip()
        if not job_url:
            continue
        job_record = state.setdefault("jobs", {}).setdefault(job_url, {})
        automation_status = (
            AUTOMATION_STATUS_AUTO_PROMOTED if job.get("phase6_auto_promoted") else AUTOMATION_STATUS_HOT
        )
        job_record.update(
            {
                "automation_status": automation_status,
                "phase6_auto_promoted": bool(job.get("phase6_auto_promoted", False)),
                "auto_promotion_reason": str(job.get("auto_promotion_reason", "")).strip(),
                "last_automated_at": automated_at,
                "match_score": int(job.get("match_score", 0) or 0),
            }
        )
        for field in (
            "resume_pdf",
            "resume_docx",
            "resume_ats_docx",
            "analysis_report",
            "cover_letter_txt",
            "cover_letter_docx",
            "output_dir",
        ):
            if job.get(field):
                job_record[field] = job.get(field)


def sort_regular_jobs_for_export(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort regular jobs into an apply-queue style export order."""
    feedback_rank = {"priority_review": 0, "standard_review": 1, "deprioritized_review": 2}
    review_bucket_rank = {
        "strong_fit_exact": 0,
        "strong_fit_review": 1,
        "exact_fit_review": 2,
        "target_lane_fresh": 3,
        "target_lane_review": 4,
        "broad_review": 5,
        "deprioritized_review": 6,
    }
    freshness_rank = {
        "fresh_under_6h": 0,
        "fresh_under_24h": 1,
        "stale_unknown": 2,
        "stale_over_24h": 3,
    }

    return sorted(
        jobs,
        key=lambda job: (
            review_bucket_rank.get(get_regular_review_bucket(job), 5),
            feedback_rank.get(str(job.get("export_priority", "standard_review")), 1),
            freshness_rank.get(str(job.get("freshness_bucket", "stale_unknown")), 2),
            -get_regular_job_queue_score(job),
            -int(job.get("match_score", 0) or 0),
            str(job.get("company", "")),
            str(job.get("title", "")),
        ),
    )


def empty_llm_usage_summary(
    *,
    early_classifier_model: str = "",
    full_scoring_model: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Build the default nested LLM usage summary for pipeline telemetry."""
    def _stage(model_name: str) -> Dict[str, Any]:
        return {
            "model": model_name,
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "estimated_cost_usd": 0.0,
            "pricing_available": False,
        }

    return {
        "early_classifier": _stage(early_classifier_model),
        "full_scoring": _stage(full_scoring_model),
        "totals": _stage("multiple"),
    }


def _accumulate_llm_usage(
    stage_summary: Dict[str, Any],
    usage: Mapping[str, Any] | None,
) -> None:
    """Merge a single OpenAI response usage record into a stage summary."""
    stage_summary["calls"] = int(stage_summary.get("calls", 0) or 0) + 1
    if not usage:
        return

    usage_model = str(usage.get("model", "")).strip()
    if usage_model:
        current_model = str(stage_summary.get("model", "")).strip()
        if not current_model:
            stage_summary["model"] = usage_model
        elif current_model != usage_model and current_model != "mixed":
            stage_summary["model"] = "mixed"

    for field in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
        stage_summary[field] = int(stage_summary.get(field, 0) or 0) + int(usage.get(field, 0) or 0)

    stage_summary["estimated_cost_usd"] = round(
        float(stage_summary.get("estimated_cost_usd", 0.0) or 0.0)
        + float(usage.get("estimated_cost_usd", 0.0) or 0.0),
        6,
    )
    stage_summary["pricing_available"] = bool(
        stage_summary.get("pricing_available", False) or usage.get("pricing_available", False)
    )


def _finalize_llm_usage_summary(summary: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Recompute total LLM usage across early-classifier and full-scoring stages."""
    totals = empty_llm_usage_summary()["totals"]
    totals["calls"] = int(summary["early_classifier"].get("calls", 0) or 0) + int(
        summary["full_scoring"].get("calls", 0) or 0
    )
    for field in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
        totals[field] = int(summary["early_classifier"].get(field, 0) or 0) + int(
            summary["full_scoring"].get(field, 0) or 0
        )
    totals["estimated_cost_usd"] = round(
        float(summary["early_classifier"].get("estimated_cost_usd", 0.0) or 0.0)
        + float(summary["full_scoring"].get("estimated_cost_usd", 0.0) or 0.0),
        6,
    )
    totals["pricing_available"] = bool(
        summary["early_classifier"].get("pricing_available", False)
        or summary["full_scoring"].get("pricing_available", False)
    )
    summary["totals"] = totals
    return summary


def categorize_jobs_by_score(
    jobs: List[Dict],
    resume: str,
    config: dict,
    *,
    return_usage: bool = False,
) -> Tuple[List[Dict], List[Dict], List[Dict]] | Tuple[List[Dict], List[Dict], List[Dict], Dict[str, Any]]:
    """
    Analyze jobs and split into hot, regular, and screened-out buckets.
    
    Returns:
        Tuple of (hot_jobs, regular_jobs, screened_out_jobs) with analysis attached
    """
    from openai import OpenAI
    
    client = OpenAI(api_key=config.get("openai_key"))
    model = config.get("settings", {}).get("openai_model", "gpt-4o-mini")
    screening_enabled, screening_model = get_early_classifier_settings(config)
    phase6_settings = get_phase6_settings(config)
    high_confidence_threshold = int(
        phase6_settings.get("high_confidence_match_floor", DEFAULT_HIGH_CONFIDENCE_MATCH_FLOOR) or 0
    )
    state = load_state()
    
    hot_jobs = []
    regular_jobs = []
    screened_out_jobs = []
    early_classifier_calls = 0
    full_scoring_calls = 0
    llm_usage = empty_llm_usage_summary(
        early_classifier_model=screening_model if screening_enabled else "",
        full_scoring_model=str(model),
    )
    
    for job in jobs:
        print(f"\n[ANALYZE] {job.get('title', 'Unknown')} @ {job.get('company', 'Unknown')}")
        
        prefetched_description = str(job.get("job_description", "")).strip()
        source_name = str(job.get("source", "")).strip().casefold()
        can_reuse_prefetched_description = bool(prefetched_description) and (
            source_name.startswith("jobspy_") or len(prefetched_description) >= 400
        )

        if can_reuse_prefetched_description:
            job_data = {
                "description": prefetched_description,
                "location": job.get("location", ""),
                "contact_email": job.get("contact_email", ""),
                "contact_emails": job.get("contact_emails", []),
            }
        else:
            fetch_url = job.get("source_url", "") or job.get("url", "")
            job_data = fetch_job_description(fetch_url)
            if not job_data and prefetched_description:
                job_data = {
                    "description": prefetched_description,
                    "location": job.get("location", ""),
                    "contact_email": job.get("contact_email", ""),
                    "contact_emails": job.get("contact_emails", []),
                }

        if not job_data:
            print("   [WARN] Could not fetch job description, skipping")
            job["match_score"] = 0
            regular_jobs.append(job)
            continue
        
        job_desc = job_data.get("description", "")
        contact_emails = merge_contact_emails(
            job.get("contact_emails", []),
            job_data.get("contact_emails", []),
            [job.get("contact_email", "")],
            [job_data.get("contact_email", "")],
        )
        job["job_description"] = job_desc
        job["location"] = job.get("location") or job_data.get("location", "")
        job["contact_emails"] = contact_emails
        job["contact_email"] = primary_contact_email(contact_emails)

        screening_result: Optional[Dict[str, Any]] = None
        if screening_enabled:
            early_classifier_calls += 1
            screening_result = classify_job_for_screening(
                client,
                job,
                job_desc,
                screening_model,
                include_usage=True,
            )
            _accumulate_llm_usage(llm_usage["early_classifier"], screening_result.get("llm_usage"))
            job["screening_category"] = screening_result.get("category", "UNKNOWN")
            job["screening_confidence"] = screening_result.get("confidence", 0.0)
            job["screening_reason"] = screening_result.get("reason", "")
            if screening_result.get("should_skip"):
                job["match_score"] = 0
                job["gap_analysis"] = screening_result.get(
                    "reason",
                    "Filtered by early classifier before full scoring.",
                )
                update_state_job_record(
                    state,
                    job,
                    contact_emails,
                    0,
                    screening_result,
                )
                print(
                    "   [SKIP] Early classifier routed to NOISE: "
                    f"{job.get('screening_reason', 'outside target lane')}"
                )
                screened_out_jobs.append(job)
                continue

        # Analyze match
        full_scoring_calls += 1
        analysis = analyze_job_match(
            client,
            job_desc,
            resume,
            model,
            job_title=job.get("title", ""),
            include_usage=True,
        )
        _accumulate_llm_usage(llm_usage["full_scoring"], analysis.get("llm_usage"))
        match_score = analysis.get("match_score", 0)
        for field in (
            "exact_fit_lane",
            "exact_fit_title_match",
            "exact_fit_signal_score",
            "exact_fit_bonus",
            "exact_fit_matched_terms",
            "adjacent_fit_lane",
            "adjacent_fit_title_match",
            "adjacent_fit_signal_score",
            "adjacent_fit_bonus",
            "adjacent_fit_matched_terms",
        ):
            if field in analysis:
                job[field] = analysis.get(field)

        strong_fit_threshold = get_strong_fit_threshold_for_job(job, phase6_settings)
        generation_lane = determine_generation_lane(
            match_score,
            high_confidence_threshold=high_confidence_threshold,
            strong_fit_threshold=strong_fit_threshold,
        )

        # Attach analysis to job
        job["match_score"] = match_score
        job["gap_analysis"] = analysis.get("gap_analysis", "")
        job["suggested_achievements"] = analysis.get("suggested_achievements", [])
        job["hot_job_threshold"] = strong_fit_threshold
        job["strong_fit_threshold"] = strong_fit_threshold
        job["high_confidence_threshold"] = high_confidence_threshold
        job["generation_lane"] = generation_lane
        review_deprioritization_reason = get_review_deprioritization_reason(job)
        job["review_deprioritization_reason"] = review_deprioritization_reason or ""
        job["export_priority"] = get_export_priority(
            job,
            review_deprioritization_reason=review_deprioritization_reason,
        )
        job["review_bucket"] = get_regular_review_bucket(job)
        job["actionable_review"] = is_actionable_review_job(job)
        job["queue_score"] = get_regular_job_queue_score(job)
        job["automation_status"] = (
            AUTOMATION_STATUS_HOT
            if generation_lane == GENERATION_LANE_HIGH_CONFIDENCE
            else "review"
        )
        job["phase6_auto_promoted"] = False
        job.setdefault("auto_promotion_reason", "")

        update_state_job_record(
            state,
            job,
            contact_emails,
            match_score,
            screening_result,
        )
        
        score_label = (
            "HIGH_CONFIDENCE"
            if generation_lane == GENERATION_LANE_HIGH_CONFIDENCE
            else "STRONG_FIT"
            if generation_lane == GENERATION_LANE_STRONG_FIT
            else "REGULAR"
        )
        print(f"   [{score_label}] Match Score: {match_score}%")
        
        if generation_lane == GENERATION_LANE_HIGH_CONFIDENCE:
            hot_jobs.append(job)
        else:
            regular_jobs.append(job)

    save_state(state)

    llm_usage = _finalize_llm_usage_summary(llm_usage)
    llm_usage.update({
        "early_classifier_calls": early_classifier_calls,
        "full_scoring_calls": full_scoring_calls,
    })
    if return_usage:
        return hot_jobs, regular_jobs, screened_out_jobs, llm_usage
    return hot_jobs, regular_jobs, screened_out_jobs


def process_hot_jobs(
    hot_jobs: List[Dict],
    config: Dict[str, Any],
    dry_run: bool = False,
) -> List[Dict]:
    """
    Process hot jobs: generate tailored resumes and cover letters for each.
    
    Returns:
        List of job dicts with resume paths attached
    """
    from openai import OpenAI

    results = []
    cover_letter_model = get_cover_letter_model(config)
    cover_letter_client = None if dry_run else OpenAI(api_key=config.get("openai_key"))

    processing_queue = sorted(
        hot_jobs,
        key=lambda job: (
            0 if job.get("generation_lane") == GENERATION_LANE_HIGH_CONFIDENCE else 1,
            -int(job.get("match_score", 0) or 0),
            str(job.get("company", "")),
            str(job.get("title", "")),
        ),
    )

    for job in processing_queue:
        print(f"\n[HOT] Processing: {job.get('title')} @ {job.get('company')}")
        
        result = generate_tailored_resume_for_job(
            job_url=job.get("url", ""),
            job_description=job.get("job_description", ""),
            company=job.get("company", "Unknown"),
            role=job.get("title", "Unknown"),
            match_score=job.get("match_score", 80),
            dry_run=dry_run
        )
        
        if result and result.get("status") == "success":
            job["resume_pdf"] = result.get("resume_pdf")
            job["resume_docx"] = result.get("resume_docx")
            job["resume_ats_docx"] = result.get("resume_ats_docx")
            job["analysis_report"] = result.get("analysis_report")
            job["resume_source"] = result.get("resume_source")
            job["output_dir"] = result.get("output_dir")

            resume_source = result.get("resume_source")
            output_dir = result.get("output_dir")
            if cover_letter_client and resume_source and output_dir:
                try:
                    cover_letter_artifacts = generate_cover_letter_artifacts(
                        cover_letter_client,
                        cover_letter_model,
                        job,
                        Path(str(resume_source)),
                        Path(str(output_dir)),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"   [WARN] Failed to generate cover letter for {job.get('company')}: {exc}")
                else:
                    job.update(cover_letter_artifacts)
                    if cover_letter_artifacts:
                        print("   [OK] Cover letter generated")
            results.append(job)
        elif result and result.get("status") == "dry_run":
            job["resume_pdf"] = None
            job["resume_docx"] = None
            job["resume_ats_docx"] = None
            job["analysis_report"] = None
            job["resume_source"] = None
            job["output_dir"] = None
            job["automation_status"] = "dry_run"
            print(f"   [DRY RUN] Resume generation skipped for {job.get('company')}")
            results.append(job)
        else:
            company_name = job.get("company")
            status = str(result.get("status", "")).strip() if isinstance(result, dict) else ""
            if status == "rejected_company_keyword_gate":
                print(f"   [WARN] Resume export blocked by company keyword gate for {company_name}")
            elif status == "rejected_low_relevance":
                print(f"   [WARN] Resume export blocked by relevance gate for {company_name}")
            elif status == "analysis_error":
                error_text = str(result.get("error", "")).strip() if isinstance(result, dict) else ""
                suffix = f": {error_text}" if error_text else ""
                print(f"   [WARN] Resume analysis blocked for {company_name}{suffix}")
            else:
                print(f"   [WARN] Failed to generate resume for {company_name}")
    
    return results


def export_regular_jobs_to_csv(jobs: List[Dict]):
    """Export non-hot jobs to CSV for manual tracking."""
    if not jobs:
        return

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs = sort_regular_jobs_for_export(jobs)
    
    csv_data = []
    for queue_rank, job in enumerate(jobs, start=1):
        csv_data.append({
            "Queue Rank": queue_rank,
            "Review Bucket": job.get("review_bucket", get_regular_review_bucket(job)),
            "Queue Score": job.get("queue_score", get_regular_job_queue_score(job)),
            "Title": job.get("title", "Unknown"),
            "Company": job.get("company", "Unknown"),
            "URL": job.get("url", ""),
            "Contact Email": job.get("contact_email", ""),
            "Match Score": job.get("match_score", 0),
            "Hot Threshold": job.get("hot_job_threshold", HOT_JOB_THRESHOLD),
            "Exact Fit": "yes" if job.get("exact_fit_title_match") else "",
            "Exact Fit Lane": job.get("exact_fit_lane", ""),
            "Exact Fit Signal": job.get("exact_fit_signal_score", 0),
            "Screening Category": job.get("screening_category", ""),
            "Screening Confidence": job.get("screening_confidence", 0),
            "Discovery Confidence": job.get("discovery_confidence", 0),
            "Feedback Signal": job.get("feedback_signal_label", "neutral"),
            "Export Priority": job.get("export_priority", "standard_review"),
            "Actionable Review": "yes" if job.get("actionable_review") else "",
            "Deprioritization Reason": job.get("review_deprioritization_reason", ""),
            "Freshness": freshness_badge_label(job.get("freshness_bucket", "")),
            "Posted Date": job.get("posted_date", job.get("date_posted", "")),
            "Freshness Basis": job.get("freshness_basis", ""),
            "Source Family": job.get("source_family", ""),
            "Source Board": job.get("source_board", ""),
            "Query Profile": job.get("query_profile", ""),
            "Job Description Snapshot": job.get("job_description", ""),
            "Gap Analysis": job.get("gap_analysis", "")[:200],
            "Found Date": datetime.now().strftime("%Y-%m-%d"),
            "Source": job.get("source", "unknown"),
            "Applied": "",
            "Status": "",
            "Notes": ""
        })
    
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_data[0].keys())
        writer.writeheader()
        writer.writerows(csv_data)
    
    print(f"[EXPORT] Exported {len(jobs)} regular jobs to {CSV_PATH.name}")


def run_hot_job_pipeline(jobs: List[Dict], dry_run: bool = False) -> Dict:
    """
    Main pipeline: Score jobs, generate resumes for hot ones, export rest to CSV.

    Args:
        jobs: List of job dicts from scrapers
        dry_run: If True, don't generate files

    Returns:
        Dict with hot_jobs (with generated artifact paths) and regular_jobs
    """
    print("=" * 60)
    print("HOT JOB PROCESSOR - v3")
    print("=" * 60)
    print(f"Processing {len(jobs)} jobs...")

    config = load_config()
    phase6_settings = get_phase6_settings(config)
    print(
        "Generation Lanes: "
        f"high-confidence>={phase6_settings.get('high_confidence_match_floor', DEFAULT_HIGH_CONFIDENCE_MATCH_FLOOR)}%, "
        f"standard strong-fit>={HOT_JOB_THRESHOLD}%, "
        f"exact-fit strong-fit>={phase6_settings.get('exact_fit_strong_fit_floor', DEFAULT_EXACT_FIT_STRONG_FIT_FLOOR)}%, "
        f"daily backfill floor>={phase6_settings.get('auto_promote_match_floor', DEFAULT_AUTO_PROMOTE_MATCH_FLOOR)}%, "
        f"daily target={phase6_settings.get('min_generated_packages_per_day', DEFAULT_MIN_GENERATED_PACKAGES_PER_DAY)}"
    )
    resume = load_master_resume()

    if not resume:
        print("[WARN] No master resume found, using basic scoring")

    # Step 1: Categorize by score
    print("\n[PHASE 1] Scoring jobs...")
    try:
        categorized_jobs = categorize_jobs_by_score(
            jobs,
            resume,
            config,
            return_usage=True,
        )
    except TypeError:
        categorized_jobs = categorize_jobs_by_score(jobs, resume, config)

    if len(categorized_jobs) == 4:
        hot_jobs, regular_jobs, screened_out_jobs, llm_usage = categorized_jobs
    else:
        hot_jobs, regular_jobs, screened_out_jobs = categorized_jobs
        llm_usage = empty_llm_usage_summary()
        llm_usage.update({"early_classifier_calls": 0, "full_scoring_calls": 0})
    state = load_state()
    hot_jobs, regular_jobs, promotion_summary = apply_daily_goal_promotions(
        hot_jobs,
        regular_jobs,
        state,
        config,
    )
    regular_jobs = sort_regular_jobs_for_export(regular_jobs)
    high_confidence_jobs = len([job for job in hot_jobs if job.get("generation_lane") == GENERATION_LANE_HIGH_CONFIDENCE])
    strong_fit_review_jobs = len([job for job in regular_jobs if job.get("generation_lane") == GENERATION_LANE_STRONG_FIT])
    actionable_review_jobs = len([job for job in regular_jobs if job.get("actionable_review")])
    deprioritized_review_jobs = len(
        [job for job in regular_jobs if job.get("export_priority") == "deprioritized_review"]
    )

    print("\n[RESULTS] Scoring Results:")
    print(f"   Auto-Generate Queue: {len(hot_jobs)}")
    print(f"   Natural 90+ Jobs: {high_confidence_jobs}")
    print(f"   Promoted Strong-Fit Jobs: {promotion_summary.get('auto_promoted_count', 0)}")
    print(f"   Strong-Fit Review Jobs: {strong_fit_review_jobs}")
    print(f"   Actionable Review Jobs: {actionable_review_jobs}")
    print(f"   Deprioritized Review Jobs: {deprioritized_review_jobs}")
    print(f"   Other Review Jobs: {len(regular_jobs) - actionable_review_jobs - deprioritized_review_jobs}")
    print(f"   Noise Screened Before Scoring: {len(screened_out_jobs)}")
    print(
        "   Daily Goal: "
        f"target={promotion_summary.get('daily_target', 0)}, "
        f"already automated today={promotion_summary.get('already_automated_today', 0)}, "
        f"auto-promoted this run={promotion_summary.get('auto_promoted_count', 0)}"
    )

    # Step 2: Process hot jobs
    processed_hot_jobs = []
    if hot_jobs:
        print("\n[PHASE 2] Generating tailored resumes and cover letters for hot jobs...")
        processed_hot_jobs = process_hot_jobs(hot_jobs, config, dry_run=dry_run)
        if not dry_run and processed_hot_jobs:
            state = load_state()
            persist_automation_results(state, processed_hot_jobs)
            save_state(state)

    # Step 3: Export regular jobs to CSV
    if regular_jobs:
        print("\n[PHASE 3] Exporting regular jobs to CSV...")
        export_regular_jobs_to_csv(regular_jobs)

    return {
        "hot_jobs": processed_hot_jobs,
        "regular_jobs": regular_jobs,
        "screened_out_jobs": screened_out_jobs,
        "stats": {
            "total_processed": len(jobs),
            "hot_count": len(processed_hot_jobs),
            "regular_count": len(regular_jobs),
            "screened_out_noise": len(screened_out_jobs),
            "high_confidence_jobs": high_confidence_jobs,
            "strong_fit_review_jobs": strong_fit_review_jobs,
            "actionable_review_jobs": actionable_review_jobs,
            "deprioritized_review_jobs": deprioritized_review_jobs,
            "daily_relevant_jobs_target": promotion_summary.get("daily_target", 0),
            "already_automated_today": promotion_summary.get("already_automated_today", 0),
            "auto_promoted_count": promotion_summary.get("auto_promoted_count", 0),
            "daily_goal_remaining": promotion_summary.get("remaining_after_run", 0),
            "eligible_for_promotion": promotion_summary.get("eligible_for_promotion", 0),
            "early_classifier_calls": llm_usage.get("early_classifier_calls", 0),
            "full_scoring_calls": llm_usage.get("full_scoring_calls", 0),
            "llm_usage": llm_usage,
            "estimated_llm_cost_usd": llm_usage.get("totals", {}).get("estimated_cost_usd", 0.0),
            "resumes_generated": len([j for j in processed_hot_jobs if j.get("resume_pdf")]),
            "cover_letters_generated": len([j for j in processed_hot_jobs if j.get("cover_letter_docx")]),
        }
    }


def get_hot_job_attachments(hot_jobs: List[Dict]) -> List[Dict]:
    """
    Get list of resume attachments for email.

    Returns:
        List of dicts with 'path' and 'filename' for each attachment
    """
    attachments = []

    for job in hot_jobs:
        if job.get("resume_ats_docx") and Path(job["resume_ats_docx"]).exists():
            attachments.append({
                "path": job["resume_ats_docx"],
                "filename": Path(job["resume_ats_docx"]).name,
                "company": job.get("company", "Unknown"),
                "role": job.get("title", "Unknown"),
                "match_score": job.get("match_score", 0)
            })
        elif job.get("resume_docx") and Path(job["resume_docx"]).exists():
            attachments.append({
                "path": job["resume_docx"],
                "filename": Path(job["resume_docx"]).name,
                "company": job.get("company", "Unknown"),
                "role": job.get("title", "Unknown"),
                "match_score": job.get("match_score", 0)
            })

        if job.get("cover_letter_docx") and Path(job["cover_letter_docx"]).exists():
            attachments.append({
                "path": job["cover_letter_docx"],
                "filename": Path(job["cover_letter_docx"]).name,
                "company": job.get("company", "Unknown"),
                "role": job.get("title", "Unknown"),
                "match_score": job.get("match_score", 0),
            })

        if job.get("resume_pdf") and Path(job["resume_pdf"]).exists():
            attachments.append({
                "path": job["resume_pdf"],
                "filename": Path(job["resume_pdf"]).name,
                "company": job.get("company", "Unknown"),
                "role": job.get("title", "Unknown"),
                "match_score": job.get("match_score", 0)
            })

    return attachments


if __name__ == "__main__":
    import sys

    # Test with sample jobs
    test_jobs = [
        {
            "title": "Senior DevOps Engineer",
            "company": "Test Company",
            "url": "https://example.com/job/1",
            "source": "test"
        }
    ]

    dry_run = "--dry-run" in sys.argv
    result = run_hot_job_pipeline(test_jobs, dry_run=dry_run)

    print("\n[RESULTS] Pipeline Results:")
    print(json.dumps(result["stats"], indent=2))

