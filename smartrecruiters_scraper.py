#!/usr/bin/env python3
"""SmartRecruiters scraper for public posting boards.

API: GET https://api.smartrecruiters.com/v1/companies/{company}/postings
No authentication required for the public job feed. Returns paginated postings.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from utils.filters import get_board_keyword_markers, should_keep_job
from utils.http import httpx_get_with_retry
from utils.pipeline_telemetry import record_source_rejection_reason
from utils.state import load_config, load_state, save_state

API_BASE = "https://api.smartrecruiters.com/v1/companies"

# Verified 2026-04-22: only a small subset of company slugs return non-empty postings via
# the public v1/companies/{slug}/postings endpoint. Most enterprises gate the feed behind
# the partner API. Add new seeds only after confirming totalFound > 0 for the slug.
DEFAULT_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "bosch": {
        "name": "Bosch",
        "company_slug": "BoschGroup",
        "priority": "MEDIUM",
    },
    "visa": {
        "name": "Visa",
        "company_slug": "Visa",
        "priority": "MEDIUM",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
SMARTRECRUITERS_REQUEST_RETRY_ATTEMPTS = 3
PAGE_LIMIT = 100


def get_smartrecruiters_endpoints(config: Optional[dict] = None) -> Dict[str, dict]:
    """Return default SmartRecruiters companies merged with config overrides."""
    config = config or load_config()
    configured = config.get("smartrecruiters_endpoints", {})
    endpoints = {key: dict(value) for key, value in DEFAULT_ENDPOINTS.items()}
    if not isinstance(configured, dict):
        return endpoints

    for key, value in configured.items():
        if not isinstance(value, dict):
            continue
        endpoints[key] = {**endpoints.get(key, {}), **value}
    return endpoints


async def fetch_smartrecruiters_jobs(company_slug: str) -> Optional[List[dict]]:
    """Fetch all postings for one SmartRecruiters company via the public API."""
    postings: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while True:
            url = (
                f"{API_BASE}/{company_slug}/postings"
                f"?limit={PAGE_LIMIT}&offset={offset}"
            )
            try:
                response = await httpx_get_with_retry(
                    client,
                    url,
                    headers=HEADERS,
                    max_retries=SMARTRECRUITERS_REQUEST_RETRY_ATTEMPTS,
                    retry_label=f"SmartRecruiters '{company_slug}'",
                )
                if response.status_code == 404:
                    print(f"  -- Company '{company_slug}' not found (404), skipping")
                    return None
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as error:
                print(
                    f"  -- SmartRecruiters HTTP {error.response.status_code} for '{company_slug}', skipping"
                )
                return None
            except httpx.RequestError as error:
                print(f"  -- SmartRecruiters request error for '{company_slug}': {error}")
                return None

            page = payload.get("content", []) if isinstance(payload, dict) else []
            if not isinstance(page, list) or not page:
                break

            postings.extend(page)
            total = int(payload.get("totalFound", 0) or 0)
            offset += len(page)
            if total and offset >= total:
                break
            if len(page) < PAGE_LIMIT:
                break

    return postings


def _format_location(location: Any) -> str:
    """Build a readable location string from the SmartRecruiters location object."""
    if not isinstance(location, dict):
        return ""
    parts = [
        str(location.get("city", "")).strip(),
        str(location.get("region", "")).strip(),
        str(location.get("country", "")).strip(),
    ]
    cleaned = [part for part in parts if part]
    if location.get("remote"):
        cleaned.append("Remote")
    return ", ".join(cleaned)


def parse_smartrecruiters_jobs(
    postings: List[dict],
    company_name: str,
    priority: str,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse SmartRecruiters API response into standardized job records."""
    jobs: list[dict] = []
    keyword_markers = get_board_keyword_markers()

    for posting in postings:
        if not isinstance(posting, dict):
            continue

        title = str(posting.get("name", "")).strip()
        if not title:
            continue

        title_lower = title.casefold()
        if not any(marker in title_lower for marker in keyword_markers):
            record_source_rejection_reason(telemetry, "smartrecruiters", "non_target_title")
            continue

        location = _format_location(posting.get("location"))
        workplace_type = ""
        if isinstance(posting.get("location"), dict) and posting["location"].get("remote"):
            workplace_type = "remote"

        description = ""
        ad = posting.get("jobAd")
        if isinstance(ad, dict):
            sections = ad.get("sections", {})
            if isinstance(sections, dict):
                description = "\n\n".join(
                    str(section.get("text", "")).strip()
                    for section in sections.values()
                    if isinstance(section, dict) and str(section.get("text", "")).strip()
                )

        if not should_keep_job(
            title,
            location=location,
            workplace_type=workplace_type,
            description=description,
            telemetry=telemetry,
            telemetry_source="smartrecruiters",
        ):
            continue

        posting_id = str(posting.get("id", "")).strip()
        company_slug = str(posting.get("company", {}).get("identifier", "")).strip()
        ref_url = str(posting.get("ref", "")).strip()
        if not ref_url and company_slug and posting_id:
            ref_url = f"https://jobs.smartrecruiters.com/{company_slug}/{posting_id}"

        jobs.append(
            {
                "title": title,
                "company": company_name,
                "url": ref_url,
                "location": location,
                "posted_date": str(posting.get("releasedDate", "")).strip(),
                "job_id": posting_id,
                "source": "smartrecruiters_api",
                "ats": "SmartRecruiters",
                "priority": priority,
                "scraped_at": datetime.now().isoformat(),
                "salary": "",
                "description": description,
                "workplace_type": workplace_type,
            }
        )

    return jobs


async def scrape_smartrecruiters_endpoint(
    endpoint_key: str,
    endpoint_config: dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Scrape a single SmartRecruiters company board."""
    company_slug = endpoint_config.get("company_slug", endpoint_key)
    company_name = endpoint_config.get("name", endpoint_key)
    priority = endpoint_config.get("priority", "MEDIUM")

    print(f"  -> {company_name} ({company_slug})...", end=" ")

    postings = await fetch_smartrecruiters_jobs(company_slug)
    if postings is None:
        return []

    jobs = parse_smartrecruiters_jobs(postings, company_name, priority, telemetry=telemetry)
    print(f"Found {len(jobs)} matching jobs (of {len(postings)} total)")
    return jobs


async def run_smartrecruiters_scrape(
    dry_run: bool = False,
    endpoints: Optional[Dict[str, dict]] = None,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Run scraper for all configured SmartRecruiters companies."""
    print("=" * 60)
    print("SMARTRECRUITERS SCRAPER - Public Postings API")
    print("=" * 60)

    config = load_config()
    if endpoints is None:
        endpoints = get_smartrecruiters_endpoints(config)
    state = load_state()
    new_jobs: list[dict] = []
    total_seen = 0

    print(f"  Scanning {len(endpoints)} SmartRecruiters companies...")

    for endpoint_key, endpoint_config in endpoints.items():
        if dry_run:
            print(
                f"  [DRY RUN] Scraping without state write: {endpoint_config.get('name', endpoint_key)}"
            )
        jobs = await scrape_smartrecruiters_endpoint(
            endpoint_key, endpoint_config, telemetry=telemetry
        )
        total_seen += len(jobs)

        for job in jobs:
            job_hash = f"smartrecruiters_{job['job_id']}"
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

    print(
        f"\n  SmartRecruiters Summary: {total_seen} total matching, {len(new_jobs)} new"
    )
    return new_jobs


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_smartrecruiters_scrape(dry_run=dry_run))
    if jobs:
        print("\nNew SmartRecruiters Jobs:")
        for job in jobs:
            print(f"  * {job['title']} @ {job['company']}")
            print(f"    {job['url']}")
