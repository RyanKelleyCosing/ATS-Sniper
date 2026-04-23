#!/usr/bin/env python3
"""Workable scraper for public job board widgets.

API: GET https://apply.workable.com/api/v3/accounts/{subdomain}/jobs
The widget endpoint returns published jobs without authentication.
Falls back to a paginated POST search when the cursor list is too small.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from utils.filters import get_board_keyword_markers, should_keep_job
from utils.http import httpx_get_with_retry
from utils.pipeline_telemetry import record_source_rejection_reason
from utils.state import load_config, load_state, save_state

API_BASE = "https://apply.workable.com/api/v3/accounts"

# Verified 2026-04-22: the public widget endpoint
# `POST https://apply.workable.com/api/v3/accounts/{subdomain}/jobs` returns 200 but
# `total=0` for accounts that have not provisioned a public widget token. Until a
# token-based or HTML-scrape fallback is implemented, leave defaults empty so the
# scraper short-circuits cleanly. Real seeds can be added through `workable_endpoints`
# in `config.json` when an account is verified to publish via the widget.
DEFAULT_ENDPOINTS: Dict[str, Dict[str, Any]] = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
WORKABLE_REQUEST_RETRY_ATTEMPTS = 3
PAGE_LIMIT = 100


def get_workable_endpoints(config: Optional[dict] = None) -> Dict[str, dict]:
    """Return default Workable subdomains merged with config overrides."""
    config = config or load_config()
    configured = config.get("workable_endpoints", {})
    endpoints = {key: dict(value) for key, value in DEFAULT_ENDPOINTS.items()}
    if not isinstance(configured, dict):
        return endpoints

    for key, value in configured.items():
        if not isinstance(value, dict):
            continue
        endpoints[key] = {**endpoints.get(key, {}), **value}
    return endpoints


async def fetch_workable_jobs(subdomain: str) -> Optional[List[dict]]:
    """Fetch all published jobs for one Workable account."""
    jobs: list[dict] = []
    url = f"{API_BASE}/{subdomain}/jobs?limit={PAGE_LIMIT}"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        cursor: Optional[str] = None
        for _ in range(20):  # hard page cap to avoid runaway loops
            page_url = url if cursor is None else f"{url}&since_id={cursor}"
            try:
                response = await httpx_get_with_retry(
                    client,
                    page_url,
                    headers=HEADERS,
                    max_retries=WORKABLE_REQUEST_RETRY_ATTEMPTS,
                    retry_label=f"Workable '{subdomain}'",
                )
                if response.status_code == 404:
                    print(f"  -- Account '{subdomain}' not found (404), skipping")
                    return None
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as error:
                print(
                    f"  -- Workable HTTP {error.response.status_code} for '{subdomain}', skipping"
                )
                return None
            except httpx.RequestError as error:
                print(f"  -- Workable request error for '{subdomain}': {error}")
                return None

            page = payload.get("results", payload.get("jobs", []))
            if not isinstance(page, list) or not page:
                break

            jobs.extend(page)
            paging = payload.get("paging", {}) if isinstance(payload, dict) else {}
            next_cursor = paging.get("next") if isinstance(paging, dict) else None
            if not next_cursor:
                break
            cursor = str(next_cursor)

    return jobs


def _format_workable_location(job: dict) -> str:
    """Build a readable location string from Workable's location object."""
    location = job.get("location") or {}
    if not isinstance(location, dict):
        return ""
    parts = [
        str(location.get("city", "")).strip(),
        str(location.get("region", "")).strip(),
        str(location.get("country", "")).strip(),
    ]
    cleaned = [part for part in parts if part]
    if location.get("workplace") == "remote" or location.get("telecommuting"):
        cleaned.append("Remote")
    return ", ".join(cleaned)


def parse_workable_jobs(
    job_list: List[dict],
    company_name: str,
    subdomain: str,
    priority: str,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse Workable API response into standardized job records."""
    jobs: list[dict] = []
    keyword_markers = get_board_keyword_markers()

    for job_data in job_list:
        if not isinstance(job_data, dict):
            continue

        title = str(job_data.get("title", "")).strip()
        if not title:
            continue

        title_lower = title.casefold()
        if not any(marker in title_lower for marker in keyword_markers):
            record_source_rejection_reason(telemetry, "workable", "non_target_title")
            continue

        location = _format_workable_location(job_data)
        workplace_type = ""
        location_obj = job_data.get("location")
        if isinstance(location_obj, dict):
            workplace_type = str(location_obj.get("workplace", "")).strip()

        description = str(job_data.get("description", "")).strip()
        if not description:
            description = str(job_data.get("requirements", "")).strip()

        if not should_keep_job(
            title,
            location=location,
            workplace_type=workplace_type,
            description=description,
            telemetry=telemetry,
            telemetry_source="workable",
        ):
            continue

        shortcode = str(job_data.get("shortcode", "")).strip()
        url = str(job_data.get("url", "")).strip()
        if not url and shortcode:
            url = f"https://apply.workable.com/{subdomain}/j/{shortcode}/"

        jobs.append(
            {
                "title": title,
                "company": company_name,
                "url": url,
                "location": location,
                "posted_date": str(job_data.get("published_on", job_data.get("created_at", ""))).strip(),
                "job_id": shortcode or str(job_data.get("id", "")).strip(),
                "source": "workable_api",
                "ats": "Workable",
                "priority": priority,
                "scraped_at": datetime.now().isoformat(),
                "salary": "",
                "description": description,
                "workplace_type": workplace_type,
                "department": str(job_data.get("department", "")).strip(),
            }
        )

    return jobs


async def scrape_workable_endpoint(
    endpoint_key: str,
    endpoint_config: dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Scrape a single Workable account."""
    subdomain = endpoint_config.get("subdomain", endpoint_key)
    company_name = endpoint_config.get("name", endpoint_key)
    priority = endpoint_config.get("priority", "MEDIUM")

    print(f"  -> {company_name} ({subdomain})...", end=" ")

    job_list = await fetch_workable_jobs(subdomain)
    if job_list is None:
        return []

    jobs = parse_workable_jobs(job_list, company_name, subdomain, priority, telemetry=telemetry)
    print(f"Found {len(jobs)} matching jobs (of {len(job_list)} total)")
    return jobs


async def run_workable_scrape(
    dry_run: bool = False,
    endpoints: Optional[Dict[str, dict]] = None,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Run scraper for all configured Workable accounts."""
    print("=" * 60)
    print("WORKABLE SCRAPER - Public Widget API")
    print("=" * 60)

    config = load_config()
    if endpoints is None:
        endpoints = get_workable_endpoints(config)
    state = load_state()
    new_jobs: list[dict] = []
    total_seen = 0

    print(f"  Scanning {len(endpoints)} Workable accounts...")

    for endpoint_key, endpoint_config in endpoints.items():
        if dry_run:
            print(
                f"  [DRY RUN] Scraping without state write: {endpoint_config.get('name', endpoint_key)}"
            )
        jobs = await scrape_workable_endpoint(endpoint_key, endpoint_config, telemetry=telemetry)
        total_seen += len(jobs)

        for job in jobs:
            job_hash = f"workable_{job['job_id']}"
            if job_hash not in state.get("seen_jobs", {}):
                new_jobs.append(job)
                state.setdefault("seen_jobs", {})[job_hash] = {
                    "url": job["url"],
                    "title": job["title"],
                    "company": job["company"],
                    "found_at": job["scraped_at"],
                    "tier": "Startup",
                }

    if not dry_run:
        save_state(state)

    print(f"\n  Workable Summary: {total_seen} total matching, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_workable_scrape(dry_run=dry_run))
    if jobs:
        print("\nNew Workable Jobs:")
        for job in jobs:
            print(f"  * {job['title']} @ {job['company']}")
            print(f"    {job['url']}")
