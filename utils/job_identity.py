"""Helpers for stable job identity across URL variants and ATS families."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from utils.pipeline_freshness import infer_source_family

TRACKING_QUERY_PARAMS = {
    "gh_jid",
    "gh_src",
    "in_iframe",
    "lever-origin",
    "lever-source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
DIRECT_SOURCE_FAMILIES = {
    "activate",
    "ashby",
    "custom_direct",
    "direct_api",
    "greenhouse_board",
    "icims",
    "lever_board",
    "oracle_hcm",
    "phenom",
    "usajobs",
    "workday",
}
LOCALE_SEGMENT_PATTERN = re.compile(r"[a-z]{2}(?:-[a-z]{2})?", re.IGNORECASE)


def normalize_company_key(company: str) -> str:
    """Return a normalized company key safe for identity comparisons."""
    return re.sub(r"[^a-z0-9]+", "", str(company).casefold())


def canonicalize_job_url(url: str) -> str:
    """Remove tracking parameters and apply-only suffixes for stable identity checks."""
    raw_url = str(url).strip()
    if not raw_url:
        return ""

    parts = urlsplit(raw_url)
    cleaned_path = re.sub(r"/apply(?:/.*)?$", "", parts.path).rstrip("/")
    cleaned_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_PARAMS
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            cleaned_path,
            urlencode(cleaned_query),
            "",
        )
    ).rstrip("/")


def parse_workday_identity(url: str) -> dict[str, str]:
    """Parse a Workday public or API URL into a stable identity tuple."""
    parts = urlsplit(str(url).strip())
    segments = [segment for segment in parts.path.split("/") if segment]
    if segments and LOCALE_SEGMENT_PATTERN.fullmatch(segments[0]):
        segments = segments[1:]

    site = ""
    job_segments: list[str] = []
    if len(segments) >= 6 and segments[0] == "wday" and segments[1] == "cxs" and segments[4] == "job":
        site = segments[3]
        job_segments = segments[5:]
    elif len(segments) >= 3 and segments[1] == "job":
        site = segments[0]
        job_segments = segments[2:]
    elif len(segments) >= 2 and segments[0] == "job":
        site = "external"
        job_segments = segments[1:]

    if not site or not job_segments:
        return {}

    last_segment = job_segments[-1]
    requisition_id = ""
    if "_" in last_segment:
        requisition_id = last_segment.rsplit("_", 1)[-1]
    elif re.search(r"\d", last_segment):
        requisition_id = last_segment

    return {
        "host": parts.netloc.casefold(),
        "site": site.casefold(),
        "job_path": "/".join(segment.casefold() for segment in job_segments),
        "requisition_id": requisition_id.casefold(),
    }


def build_job_identity_aliases(job: Mapping[str, Any]) -> tuple[str, ...]:
    """Build identity aliases for one job so URL variants can collapse together."""
    url = str(job.get("url") or job.get("source_url") or "").strip()
    company_key = normalize_company_key(str(job.get("company", "")))
    source_family = infer_source_family(job)
    url_family = infer_source_family({"url": url, "source": ""}) if url else ""
    job_id = str(
        job.get("job_id")
        or job.get("jobId")
        or job.get("reqId")
        or job.get("MatchedObjectId")
        or ""
    ).strip()

    aliases: list[str] = []
    canonical_url = canonicalize_job_url(url)
    if canonical_url:
        aliases.append(f"url::{canonical_url}")

    if source_family == "workday" or url_family == "workday":
        workday_identity = parse_workday_identity(url)
        if workday_identity:
            identity_suffix = workday_identity.get("requisition_id") or workday_identity.get("job_path")
            if identity_suffix:
                aliases.append(
                    "::".join(
                        (
                            "workday",
                            workday_identity.get("host", ""),
                            workday_identity.get("site", ""),
                            identity_suffix,
                        )
                    )
                )

    stable_family = source_family if source_family in DIRECT_SOURCE_FAMILIES else url_family
    if stable_family and company_key and job_id:
        aliases.append(f"{stable_family}::{company_key}::{job_id.casefold()}")

    deduped_aliases: list[str] = []
    seen_aliases: set[str] = set()
    for alias in aliases:
        alias_key = alias.casefold()
        if not alias_key or alias_key in seen_aliases:
            continue
        seen_aliases.add(alias_key)
        deduped_aliases.append(alias_key)
    return tuple(deduped_aliases)


def ensure_job_identity_index(state: dict[str, Any]) -> bool:
    """Backfill the in-memory job identity index for persisted job records."""
    jobs = state.setdefault("jobs", {})
    job_identities = state.setdefault("job_identities", {})
    updated = False

    for stored_url, record in jobs.items():
        job = dict(record)
        job.setdefault("url", stored_url)
        for alias in build_job_identity_aliases(job):
            if job_identities.get(alias) == stored_url:
                continue
            if alias not in job_identities:
                job_identities[alias] = stored_url
                updated = True
    return updated


def find_existing_job_url(state: Mapping[str, Any], job: Mapping[str, Any]) -> str:
    """Return the stored job URL for the same identity if one already exists."""
    jobs = state.get("jobs", {})
    job_identities = state.get("job_identities", {})
    if not isinstance(jobs, Mapping) or not isinstance(job_identities, Mapping):
        return ""

    for alias in build_job_identity_aliases(job):
        stored_url = str(job_identities.get(alias, "")).strip()
        if stored_url and stored_url in jobs:
            return stored_url

    raw_url = str(job.get("url", "")).strip()
    if raw_url and raw_url in jobs:
        return raw_url

    canonical_url = canonicalize_job_url(raw_url)
    if canonical_url and canonical_url in jobs:
        return canonical_url
    return ""


def _prefer_earliest_timestamp(existing_value: Any, incoming_value: Any) -> str:
    """Prefer the earliest non-empty ISO-like timestamp when merging job records."""
    existing_text = str(existing_value).strip()
    incoming_text = str(incoming_value).strip()
    if not existing_text:
        return incoming_text
    if not incoming_text:
        return existing_text

    try:
        existing_dt = datetime.fromisoformat(existing_text.replace("Z", "+00:00"))
        incoming_dt = datetime.fromisoformat(incoming_text.replace("Z", "+00:00"))
    except ValueError:
        return existing_text

    return existing_text if existing_dt <= incoming_dt else incoming_text


def store_job_identity_record(
    state: dict[str, Any],
    job: Mapping[str, Any],
    *,
    stored_url: str | None = None,
) -> str:
    """Store or update one job record and register all of its identity aliases."""
    raw_url = str(job.get("url", "")).strip()
    target_url = str(stored_url or raw_url).strip()
    if not target_url:
        return ""

    jobs = state.setdefault("jobs", {})
    job_identities = state.setdefault("job_identities", {})
    existing_record = dict(jobs.get(target_url, {}))
    merged_record = {**existing_record, **job, "url": target_url}
    for timestamp_field in ("source_detected_at", "first_seen_at"):
        merged_timestamp = _prefer_earliest_timestamp(
            existing_record.get(timestamp_field, ""),
            job.get(timestamp_field, ""),
        )
        if merged_timestamp:
            merged_record[timestamp_field] = merged_timestamp

    if raw_url and raw_url != target_url:
        alternate_urls = list(existing_record.get("alternate_urls", []))
        if raw_url not in alternate_urls:
            alternate_urls.append(raw_url)
        merged_record["alternate_urls"] = alternate_urls

    jobs[target_url] = merged_record
    for alias in build_job_identity_aliases(merged_record):
        job_identities[alias] = target_url
    if raw_url and raw_url != target_url:
        alias_job = dict(merged_record)
        alias_job["url"] = raw_url
        for alias in build_job_identity_aliases(alias_job):
            job_identities[alias] = target_url
    return target_url


def merge_duplicate_jobs(primary_job: Mapping[str, Any], secondary_job: Mapping[str, Any]) -> dict[str, Any]:
    """Merge two duplicate job records while preferring the stronger source."""
    primary = dict(primary_job)
    secondary = dict(secondary_job)

    primary_family = infer_source_family(primary)
    secondary_family = infer_source_family(secondary)
    if primary_family not in DIRECT_SOURCE_FAMILIES and secondary_family in DIRECT_SOURCE_FAMILIES:
        primary, secondary = secondary, primary

    merged = dict(primary)
    for key, value in secondary.items():
        if key not in merged or merged.get(key) in ("", None, [], {}):
            merged[key] = value

    alternate_urls = list(primary.get("alternate_urls", []))
    for candidate in (str(primary.get("url", "")).strip(), str(secondary.get("url", "")).strip()):
        if candidate and candidate != str(merged.get("url", "")).strip() and candidate not in alternate_urls:
            alternate_urls.append(candidate)
    if alternate_urls:
        merged["alternate_urls"] = alternate_urls
    return merged


def deduplicate_jobs_by_identity(jobs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-identity jobs within a single run batch."""
    deduped_jobs: list[dict[str, Any]] = []
    seen_aliases: dict[str, int] = {}

    for job in jobs:
        aliases = build_job_identity_aliases(job)
        matched_index = next((seen_aliases[alias] for alias in aliases if alias in seen_aliases), None)
        if matched_index is None:
            deduped_jobs.append(dict(job))
            matched_index = len(deduped_jobs) - 1
        else:
            deduped_jobs[matched_index] = merge_duplicate_jobs(deduped_jobs[matched_index], job)

        for alias in build_job_identity_aliases(deduped_jobs[matched_index]):
            seen_aliases[alias] = matched_index

    return deduped_jobs


def build_workday_api_url(url: str) -> str:
    """Convert a Workday public URL variant into the CXS API endpoint URL."""
    identity = parse_workday_identity(url)
    if not identity:
        return ""

    host = identity.get("host", "")
    site = identity.get("site", "")
    job_path = identity.get("job_path", "")
    if not host or not site or not job_path:
        return ""

    tenant = host.split(".", 1)[0]
    return f"https://{host}/wday/cxs/{tenant}/{site}/job/{job_path}"