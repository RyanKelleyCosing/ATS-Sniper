#!/usr/bin/env python3
"""
Lever Scraper - Public JSON API Access for Lever-powered job boards

API: GET https://api.lever.co/v0/postings/{company}?mode=json
No authentication required. Returns all published postings.
Supports server-side filtering by location, department, team, and commitment.
"""

import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from utils.state import load_config, load_state, save_state
from utils.filters import get_board_keyword_markers, should_keep_job
from utils.pipeline_telemetry import record_source_rejection_reason

API_BASE = "https://api.lever.co/v0/postings"

DEFAULT_ENDPOINTS = {
    "restaurant365": {
        "name": "Restaurant365",
        "company_slug": "restaurant365",
        "priority": "MEDIUM"
    },
    "h1": {
        "name": "H1",
        "company_slug": "h1insights",
        "priority": "MEDIUM"
    },
    "spotify": {
        "name": "Spotify",
        "company_slug": "spotify",
        "priority": "MEDIUM"
    },
    "workwave": {
        "name": "WorkWave",
        "company_slug": "workwave",
        "priority": "HIGH"
    },
    "palantir": {
        "name": "Palantir",
        "company_slug": "palantir",
        "priority": "MEDIUM"
    },
    "moonpay": {
        "name": "MoonPay",
        "company_slug": "moonpay",
        "priority": "HIGH"
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
LEVER_REQUEST_RETRY_ATTEMPTS = 2
LEVER_RETRY_DELAY_SECONDS = 1.0
LEVER_BOARD_HEALTH_SCOPE = "lever"
DEFAULT_LEVER_BOARD_HEALTH_ENABLED = True
DEFAULT_LEVER_BOARD_HEALTH_DEGRADE_AFTER = 2
DEFAULT_LEVER_BOARD_HEALTH_DISABLE_AFTER = 3
DEFAULT_LEVER_BOARD_HEALTH_DISABLE_HOURS = 168
DEGRADED_LEVER_PRIORITY = "LOW"


def get_lever_endpoints(config: Optional[dict] = None) -> Dict[str, dict]:
    """Return default Lever boards merged with config overrides."""
    config = config or load_config()
    configured_endpoints = config.get("lever_endpoints", {})
    endpoints = {
        endpoint_key: dict(endpoint_config)
        for endpoint_key, endpoint_config in DEFAULT_ENDPOINTS.items()
    }
    if not isinstance(configured_endpoints, dict):
        return endpoints

    for endpoint_key, endpoint_config in configured_endpoints.items():
        if not isinstance(endpoint_config, dict):
            continue
        endpoints[endpoint_key] = {
            **endpoints.get(endpoint_key, {}),
            **endpoint_config,
        }

    return endpoints


def get_lever_board_health_settings(config: Optional[dict] = None) -> Dict[str, Any]:
    """Return Lever board health settings with sane defaults."""
    config = config or load_config()
    settings = config.get("settings", {}) if isinstance(config, dict) else {}
    if not isinstance(settings, dict):
        settings = {}

    degrade_after = int(
        settings.get(
            "lever_board_health_downgrade_after_failures",
            DEFAULT_LEVER_BOARD_HEALTH_DEGRADE_AFTER,
        )
        or 0
    )
    disable_after = int(
        settings.get(
            "lever_board_health_disable_after_failures",
            DEFAULT_LEVER_BOARD_HEALTH_DISABLE_AFTER,
        )
        or 0
    )
    if disable_after and disable_after < degrade_after:
        disable_after = degrade_after

    return {
        "enabled": bool(
            settings.get("lever_board_health_enabled", DEFAULT_LEVER_BOARD_HEALTH_ENABLED)
        ),
        "downgrade_after_failures": max(degrade_after, 1),
        "disable_after_failures": max(disable_after, 1),
        "disable_hours": max(
            int(
                settings.get(
                    "lever_board_health_disable_hours",
                    DEFAULT_LEVER_BOARD_HEALTH_DISABLE_HOURS,
                )
                or 0
            ),
            1,
        ),
    }


def get_lever_board_health_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the mutable Lever board health section of state."""
    board_health = state.setdefault("board_health", {})
    lever_health = board_health.setdefault(LEVER_BOARD_HEALTH_SCOPE, {})
    if not isinstance(lever_health, dict):
        lever_health = {}
        board_health[LEVER_BOARD_HEALTH_SCOPE] = lever_health
    return lever_health


def get_lever_board_health_record(
    state: dict[str, Any],
    company_slug: str,
) -> dict[str, Any]:
    """Return the current health record for a Lever company slug."""
    lever_health = get_lever_board_health_map(state)
    record = lever_health.get(company_slug, {})
    return record if isinstance(record, dict) else {}


def get_lever_board_disabled_until(record: dict[str, Any]) -> datetime | None:
    """Parse the disabled-until timestamp from a board-health record."""
    raw_value = str(record.get("disabled_until", "")).strip()
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


def is_lever_board_disabled(record: dict[str, Any], now: datetime | None = None) -> bool:
    """Return True when the board is temporarily disabled."""
    disabled_until = get_lever_board_disabled_until(record)
    if disabled_until is None:
        return False
    return disabled_until > (now or datetime.now())


def get_effective_lever_priority(
    endpoint_config: dict[str, Any],
    record: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    """Lower the effective board priority after repeated failures."""
    base_priority = str(endpoint_config.get("priority", "MEDIUM")).strip() or "MEDIUM"
    if int(record.get("consecutive_failures", 0) or 0) >= int(
        settings.get("downgrade_after_failures", DEFAULT_LEVER_BOARD_HEALTH_DEGRADE_AFTER) or 0
    ):
        return DEGRADED_LEVER_PRIORITY
    return base_priority


def update_lever_board_health(
    state: dict[str, Any],
    company_slug: str,
    *,
    success: bool,
    failure_kind: str = "",
    failure_message: str = "",
    settings: Optional[dict[str, Any]] = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update consecutive-failure health tracking for a Lever board."""
    settings = settings or get_lever_board_health_settings()
    current_time = now or datetime.now()
    lever_health = get_lever_board_health_map(state)
    record = dict(lever_health.get(company_slug, {}))

    if success:
        record.update(
            {
                "consecutive_failures": 0,
                "last_status": "healthy",
                "last_success_at": current_time.isoformat(),
                "last_failure_kind": "",
                "last_failure_message": "",
                "disabled_until": "",
            }
        )
        lever_health[company_slug] = record
        return record

    consecutive_failures = int(record.get("consecutive_failures", 0) or 0) + 1
    record.update(
        {
            "consecutive_failures": consecutive_failures,
            "last_status": "degraded",
            "last_failure_at": current_time.isoformat(),
            "last_failure_kind": failure_kind,
            "last_failure_message": failure_message,
        }
    )

    if consecutive_failures >= int(
        settings.get("disable_after_failures", DEFAULT_LEVER_BOARD_HEALTH_DISABLE_AFTER) or 0
    ):
        record["last_status"] = "disabled"
        record["disabled_until"] = (
            current_time
            + timedelta(hours=int(settings.get("disable_hours", DEFAULT_LEVER_BOARD_HEALTH_DISABLE_HOURS) or 0))
        ).isoformat()

    lever_health[company_slug] = record
    return record


def get_lever_fetch_status(endpoint_config: dict[str, Any]) -> dict[str, Any]:
    """Return the last fetch status recorded by scrape_lever_endpoint."""
    status = endpoint_config.get("_last_fetch_status", {})
    return status if isinstance(status, dict) else {}


async def fetch_lever_jobs(company_slug: str) -> Optional[List[dict]]:
    """Fetch all postings from a Lever company board via public API."""
    jobs, _status = await fetch_lever_jobs_with_status(company_slug)
    return jobs


async def fetch_lever_jobs_with_status(company_slug: str) -> tuple[Optional[List[dict]], dict[str, Any]]:
    """Fetch Lever jobs and return structured status for board-health tracking."""
    url = f"{API_BASE}/{company_slug}?mode=json"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for attempt in range(1, LEVER_REQUEST_RETRY_ATTEMPTS + 1):
            try:
                response = await client.get(url, headers=HEADERS)

                if response.status_code == 404:
                    print(f"  -- Company '{company_slug}' not found (404), skipping")
                    return None, {
                        "status": "not_found",
                        "failure_kind": "not_found",
                        "message": "Company not found (404)",
                    }

                response.raise_for_status()
                data = response.json()

                # Lever returns a plain JSON array, not a wrapped object
                if isinstance(data, list):
                    return data, {"status": "success"}
                return data.get("postings", []), {"status": "success"}

            except httpx.HTTPStatusError as exc:
                print(f"  -- Lever HTTP {exc.response.status_code} for '{company_slug}', skipping")
                return None, {
                    "status": "http_error",
                    "failure_kind": "http_error",
                    "message": f"HTTP {exc.response.status_code}",
                    "status_code": exc.response.status_code,
                }
            except httpx.RequestError as exc:
                issue_text = str(exc).strip() or repr(exc)
                issue_label = f"{type(exc).__name__}: {issue_text}"
                if attempt < LEVER_REQUEST_RETRY_ATTEMPTS:
                    print(
                        f"  -- Lever request issue for '{company_slug}' ({issue_label}); retrying"
                    )
                    await asyncio.sleep(LEVER_RETRY_DELAY_SECONDS * attempt)
                    continue

                print(
                    f"  -- Lever board unavailable for '{company_slug}', skipping ({issue_label})"
                )
                return None, {
                    "status": "unavailable",
                    "failure_kind": "request_error",
                    "message": issue_label,
                }

    return None, {
        "status": "unavailable",
        "failure_kind": "request_error",
        "message": "Unknown request issue",
    }


def parse_lever_jobs(
    job_list: List[dict],
    company_name: str,
    priority: str,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse Lever API response into standardized job format."""
    jobs = []

    for job_data in job_list:
        title = job_data.get("text", "")
        title_lower = title.lower()

        if not any(kw in title_lower for kw in get_board_keyword_markers()):
            record_source_rejection_reason(telemetry, "lever", "non_target_title")
            continue

        job_id = job_data.get("id", "")
        categories = job_data.get("categories", {})
        location = categories.get("location", "Unknown")
        workplace_type = job_data.get("workplaceType", "unspecified")

        # Convert Unix ms timestamp to ISO format
        created_at_ms = job_data.get("createdAt", 0)
        posted_date = ""
        if created_at_ms:
            posted_date = datetime.fromtimestamp(created_at_ms / 1000).isoformat()

        # Extract salary if available
        salary = ""
        salary_range = job_data.get("salaryRange")
        if salary_range:
            min_sal = salary_range.get("min", 0)
            max_sal = salary_range.get("max", 0)
            currency = salary_range.get("currency", "USD")
            if min_sal and max_sal:
                salary = f"${min_sal:,} - ${max_sal:,} {currency}"

        # Build description from available fields
        description = job_data.get("descriptionPlain", "")
        lists_data = job_data.get("lists", [])
        for section in lists_data:
            section_title = section.get("text", "")
            section_content = section.get("content", "")
            if section_title and section_content:
                description += f"\n\n{section_title}:\n{section_content}"

        if not should_keep_job(
            title,
            location=location,
            workplace_type=workplace_type,
            description=description,
            telemetry=telemetry,
            telemetry_source="lever",
        ):
            continue

        jobs.append({
            "title": title,
            "company": company_name,
            "url": job_data.get("hostedUrl", ""),
            "location": location,
            "posted_date": posted_date,
            "job_id": job_id,
            "source": "lever_api",
            "ats": "Lever",
            "priority": priority,
            "scraped_at": datetime.now().isoformat(),
            "salary": salary,
            "description": description,
            "workplace_type": workplace_type,
            "department": categories.get("department", ""),
            "team": categories.get("team", ""),
            "commitment": categories.get("commitment", ""),
        })

    return jobs


async def scrape_lever_endpoint(
    endpoint_key: str,
    endpoint_config: dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Scrape a single Lever company board."""
    company_slug = endpoint_config.get("company_slug", endpoint_key)
    company_name = endpoint_config.get("name", endpoint_key)
    priority = endpoint_config.get("effective_priority", endpoint_config.get("priority", "MEDIUM"))

    print(f"  -> {company_name} ({company_slug})...", end=" ")

    job_list, fetch_status = await fetch_lever_jobs_with_status(company_slug)
    endpoint_config["_last_fetch_status"] = fetch_status
    if job_list is None:
        return []

    jobs = parse_lever_jobs(job_list, company_name, priority, telemetry=telemetry)
    print(f"Found {len(jobs)} matching jobs (of {len(job_list)} total)")
    return jobs


async def run_lever_scrape(
    dry_run: bool = False,
    endpoints: Optional[Dict[str, dict]] = None,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """
    Run scraper for all configured Lever company boards.

    Returns:
        List of new jobs found
    """
    print("=" * 60)
    print("LEVER SCRAPER - Public Postings API")
    print("=" * 60)

    config = load_config()
    if endpoints is None:
        endpoints = get_lever_endpoints(config)
    state = load_state()
    health_settings = get_lever_board_health_settings(config)
    all_jobs = []
    new_jobs = []

    print(f"  Scanning {len(endpoints)} Lever boards...")

    for endpoint_key, endpoint_config in endpoints.items():
        company_slug = str(endpoint_config.get("company_slug", endpoint_key)).strip()
        company_name = str(endpoint_config.get("name", endpoint_key)).strip() or endpoint_key
        board_health = get_lever_board_health_record(state, company_slug)
        endpoint_config["effective_priority"] = get_effective_lever_priority(
            endpoint_config,
            board_health,
            health_settings,
        )

        if health_settings.get("enabled", True) and is_lever_board_disabled(board_health):
            disabled_until = get_lever_board_disabled_until(board_health)
            disabled_label = disabled_until.isoformat(timespec="minutes") if disabled_until else "later"
            print(
                f"  -> {company_name} ({company_slug})... skipped; board disabled until {disabled_label}"
            )
            continue

        if dry_run:
            print(f"  [DRY RUN] Scraping without state write: {endpoint_config.get('name', endpoint_key)}")

        jobs = await scrape_lever_endpoint(endpoint_key, endpoint_config, telemetry=telemetry)
        fetch_status = get_lever_fetch_status(endpoint_config)
        fetch_state = str(fetch_status.get("status", "")).strip().casefold()
        if health_settings.get("enabled", True) and not dry_run:
            if fetch_state == "success":
                update_lever_board_health(
                    state,
                    company_slug,
                    success=True,
                    settings=health_settings,
                )
            elif fetch_state:
                record = update_lever_board_health(
                    state,
                    company_slug,
                    success=False,
                    failure_kind=str(fetch_status.get("failure_kind", fetch_state)),
                    failure_message=str(fetch_status.get("message", "")).strip(),
                    settings=health_settings,
                )
                if record.get("last_status") == "disabled":
                    disabled_until = get_lever_board_disabled_until(record)
                    disabled_label = disabled_until.isoformat(timespec="minutes") if disabled_until else "later"
                    print(
                        f"  -- Lever board '{company_slug}' temporarily disabled until {disabled_label} after repeated {record.get('last_failure_kind', 'board')} issues"
                    )

        all_jobs.extend(jobs)

        for job in jobs:
            job_hash = f"lever_{job['job_id']}"
            if job_hash not in state.get("seen_jobs", {}):
                new_jobs.append(job)
                state.setdefault("seen_jobs", {})[job_hash] = {
                    "url": job["url"],
                    "title": job["title"],
                    "company": job["company"],
                    "found_at": job["scraped_at"],
                    "tier": "Startup"
                }

    if not dry_run:
        save_state(state)

    print(f"\n  Lever Summary: {len(all_jobs)} total matching, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_lever_scrape(dry_run=dry_run))

    if jobs:
        print("\nNew Lever Jobs:")
        for job in jobs:
            salary_tag = f" [{job['salary']}]" if job.get('salary') else ""
            wt = f" ({job['workplace_type']})" if job.get('workplace_type', 'unspecified') != 'unspecified' else ""
            print(f"  * {job['title']} @ {job['company']}{salary_tag}{wt}")
            print(f"    {job['url']}")
