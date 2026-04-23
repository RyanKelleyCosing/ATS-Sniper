"""Freshness helpers for discovery metadata, sorting, and reporting."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


DEFAULT_FRESHNESS_SETTINGS: dict[str, Any] = {
    "under_6_hours": 6,
    "under_24_hours": 24,
    "lightweight_run_types": ["lightweight"],
    "lightweight_custom_scrapers": [
        "activate_api",
        "successfactors_rss",
        "peopleadmin_atom",
        "phenom_search",
    ],
}

FRESHNESS_BUCKET_ORDER = {
    "fresh_under_6h": 0,
    "fresh_under_24h": 1,
    "stale_unknown": 2,
    "stale_over_24h": 3,
}

FRESHNESS_BADGE_LABELS = {
    "fresh_under_6h": "Fresh <6h",
    "fresh_under_24h": "Fresh <24h",
    "stale_unknown": "Freshness Unknown",
    "stale_over_24h": "Posted >24h",
}


def get_freshness_settings(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return freshness settings merged with defaults."""
    settings = deepcopy(DEFAULT_FRESHNESS_SETTINGS)
    if not config:
        return settings

    custom_settings = config.get("freshness", {})
    if not isinstance(custom_settings, dict):
        return settings

    for key, value in custom_settings.items():
        settings[key] = value
    return settings


def _normalize_datetime(value: datetime) -> datetime:
    """Normalize timezone-aware datetimes to local naive datetimes."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def parse_job_datetime(value: Any, *, reference_time: datetime | None = None) -> datetime | None:
    """Parse a variety of ATS timestamp formats into a datetime."""
    if isinstance(value, datetime):
        return _normalize_datetime(value)

    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    now = _normalize_datetime(reference_time or datetime.now())
    normalized_text = text.casefold()
    normalized_text = re.sub(r"^posted\s+", "", normalized_text)

    if "just posted" in normalized_text or normalized_text == "today":
        return now
    if "yesterday" in normalized_text:
        return now - timedelta(days=1)

    hours_ago_match = re.search(r"(\d+)\s*(?:hour|hours|hr|hrs)\s+ago", normalized_text)
    if hours_ago_match:
        return now - timedelta(hours=int(hours_ago_match.group(1)))

    days_ago_match = re.search(r"(\d+)\s*(?:day|days)\s+ago", normalized_text)
    if days_ago_match:
        return now - timedelta(days=int(days_ago_match.group(1)))

    parse_candidates = [text, text.replace("Z", "+00:00")]
    parse_formats = (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d %H:%M:%S",
    )

    for candidate in parse_candidates:
        try:
            return _normalize_datetime(datetime.fromisoformat(candidate))
        except ValueError:
            pass

        for fmt in parse_formats:
            try:
                return _normalize_datetime(datetime.strptime(candidate, fmt))
            except ValueError:
                continue

    return None


def infer_source_family(job: Mapping[str, Any]) -> str:
    """Infer a broad source family when one is not already set."""
    current_family = str(job.get("source_family", "")).strip()
    if current_family:
        return current_family

    source = str(job.get("source", "")).casefold()
    url = str(job.get("url") or job.get("source_url") or "").casefold()
    hostname = urlparse(url).netloc.casefold()

    if source.startswith("jobspy_"):
        return "job_board"
    if "greenhouse" in source or "greenhouse.io" in hostname:
        return "greenhouse_board"
    if "lever" in source or "lever.co" in hostname:
        return "lever_board"
    if "workday" in source or "myworkdayjobs.com" in hostname:
        return "workday"
    if "smartrecruiters" in source or "smartrecruiters.com" in hostname:
        return "smartrecruiters"
    if "workable" in source or "workable.com" in hostname:
        return "workable"
    if "jobvite" in source or "jobvite.com" in hostname:
        return "jobvite"
    if "builtin" in source or "builtin.com" in hostname:
        return "builtin"
    if "usajobs" in source or "usajobs.gov" in hostname:
        return "usajobs"
    if "icims" in source or "icims.com" in hostname:
        return "icims"
    if "oracle" in source or "oraclecloud.com" in hostname:
        return "oracle_hcm"
    if "governmentjobs.com" in hostname:
        return "neogov"
    if "cardinalhealth.com" in hostname:
        return "activate"
    if "atsginc.com" in hostname:
        return "phenom"
    if source == "custom_scraper":
        return "custom_direct"
    if source.startswith("web_"):
        return "company_career_site"
    return "direct_api"


def infer_query_profile(job: Mapping[str, Any]) -> str:
    """Infer a query profile for direct-source jobs when missing."""
    current_profile = str(job.get("query_profile", "")).strip()
    if current_profile:
        return current_profile

    family = infer_source_family(job)
    if family in {"greenhouse_board", "lever_board", "smartrecruiters", "workable"}:
        return "direct_board_api"
    if family == "workday":
        return "workday_api"
    if family in {"activate", "phenom", "neogov", "custom_direct"}:
        return "direct_custom_api"
    if family in {"icims", "oracle_hcm"}:
        return "direct_ats_api"
    if family == "job_board":
        return "jobspy_pilot"
    if family == "usajobs":
        return "usajobs_api"
    return "web_discovery"


def _coalesce_non_empty(values: Sequence[Any]) -> str:
    """Return the first non-empty string representation from a sequence."""
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.casefold() != "none":
            return text
    return ""


def _compute_freshness_bucket(
    posted_date: str,
    source_detected_at: str,
    first_seen_at: str,
    settings: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[str, str, float | None]:
    """Compute a freshness bucket, basis, and age in hours."""
    posted_at = parse_job_datetime(posted_date, reference_time=now)
    detected_at = parse_job_datetime(source_detected_at, reference_time=now)
    first_seen = parse_job_datetime(first_seen_at, reference_time=now)

    freshness_basis = "unknown"
    reference_timestamp = None
    if posted_at is not None:
        freshness_basis = "posted_date"
        reference_timestamp = posted_at
    elif detected_at is not None:
        freshness_basis = "source_detected_at"
        reference_timestamp = detected_at
    elif first_seen is not None:
        freshness_basis = "first_seen_at"
        reference_timestamp = first_seen

    if reference_timestamp is None:
        return "stale_unknown", freshness_basis, None

    age_hours = max(0.0, (now - reference_timestamp).total_seconds() / 3600)
    under_6_hours = float(settings.get("under_6_hours", 6))
    under_24_hours = float(settings.get("under_24_hours", 24))

    if age_hours <= under_6_hours:
        return "fresh_under_6h", freshness_basis, age_hours
    if age_hours <= under_24_hours:
        return "fresh_under_24h", freshness_basis, age_hours
    if freshness_basis == "posted_date":
        return "stale_over_24h", freshness_basis, age_hours
    return "stale_unknown", freshness_basis, age_hours


def apply_freshness_metadata(
    job: Mapping[str, Any],
    existing_record: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a job dict enriched with normalized freshness metadata."""
    current_time = _normalize_datetime(now or datetime.now())
    settings = get_freshness_settings(config)
    existing = dict(existing_record or {})
    enriched = dict(job)

    source_detected_at = _coalesce_non_empty(
        (
            enriched.get("source_detected_at"),
            existing.get("source_detected_at"),
            enriched.get("scraped_at"),
            existing.get("scraped_at"),
            current_time.isoformat(),
        )
    )
    first_seen_at = _coalesce_non_empty(
        (
            existing.get("first_seen_at"),
            existing.get("first_seen"),
            enriched.get("first_seen_at"),
            enriched.get("first_seen"),
            source_detected_at,
        )
    )
    posted_date = _coalesce_non_empty(
        (
            enriched.get("posted_date"),
            enriched.get("posted_on"),
            enriched.get("date_posted"),
            existing.get("posted_date"),
            existing.get("posted_on"),
            existing.get("date_posted"),
        )
    )

    enriched["source_family"] = infer_source_family({**existing, **enriched})
    enriched["query_profile"] = infer_query_profile({**existing, **enriched})
    enriched["source_detected_at"] = source_detected_at
    enriched["first_seen_at"] = first_seen_at
    if posted_date:
        enriched["posted_date"] = posted_date
        enriched.setdefault("date_posted", posted_date)

    freshness_bucket, freshness_basis, age_hours = _compute_freshness_bucket(
        posted_date,
        source_detected_at,
        first_seen_at,
        settings,
        now=current_time,
    )
    enriched["freshness_bucket"] = freshness_bucket
    enriched["freshness_basis"] = freshness_basis
    enriched["freshness_age_hours"] = round(age_hours, 2) if age_hours is not None else None
    return enriched


def freshness_badge_label(bucket: str) -> str:
    """Return a human-readable badge label for a freshness bucket."""
    return FRESHNESS_BADGE_LABELS.get(str(bucket).strip(), "")


def sort_jobs_by_freshness(jobs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return jobs sorted with the freshest items first."""

    def sort_key(job: Mapping[str, Any]) -> tuple[int, float, str, str]:
        bucket = str(job.get("freshness_bucket", "stale_unknown"))
        order = FRESHNESS_BUCKET_ORDER.get(bucket, 99)
        best_timestamp = parse_job_datetime(
            job.get("posted_date") or job.get("source_detected_at") or job.get("first_seen_at")
        )
        timestamp_value = best_timestamp.timestamp() if best_timestamp else 0.0
        match_score = float(job.get("match_score", 0) or 0)
        return (order, -timestamp_value, -match_score, str(job.get("title", "")))

    return [dict(job) for job in sorted(jobs, key=sort_key)]


def count_jobs_by_freshness_bucket(jobs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count jobs by freshness bucket for summary stats."""
    counts = {bucket: 0 for bucket in FRESHNESS_BUCKET_ORDER}
    for job in jobs:
        bucket = str(job.get("freshness_bucket", "stale_unknown"))
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts