#!/usr/bin/env python3
"""Ashby scraper for public job boards hosted on jobs.ashbyhq.com."""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from utils.filters import get_board_keyword_markers, should_keep_job
from utils.http import httpx_get_with_retry
from utils.pipeline_telemetry import record_source_rejection_reason
from utils.state import load_config, load_state, save_state

DEFAULT_ENDPOINTS = {
    "homevision": {
        "name": "HomeVision",
        "company_slug": "homevision",
        "priority": "HIGH",
    },
    "leantechniques": {
        "name": "Lean TECHniques",
        "company_slug": "leantechniques",
        "priority": "HIGH",
        "allow_global_remote": True,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/json",
}
ASHBY_REQUEST_RETRY_ATTEMPTS = 3


def get_ashby_endpoints(config: Optional[dict] = None) -> Dict[str, dict]:
    """Return default Ashby boards merged with config overrides."""
    config = config or load_config()
    configured_endpoints = config.get("ashby_endpoints", {})
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


def extract_ashby_app_data(html_text: str) -> dict:
    """Extract the embedded Ashby app payload from a public board page."""
    marker = "window.__appData = "
    marker_index = html_text.find(marker)
    if marker_index == -1:
        return {}

    start_index = marker_index + len(marker)
    brace_depth = 0
    end_index: int | None = None
    in_string = False
    is_escaped = False

    for index, character in enumerate(html_text[start_index:], start=start_index):
        if in_string:
            if is_escaped:
                is_escaped = False
            elif character == "\\":
                is_escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
            continue

        if character == "{":
            brace_depth += 1
            continue

        if character == "}":
            brace_depth -= 1
            if brace_depth == 0:
                end_index = index + 1
                break

    if end_index is None:
        return {}

    try:
        payload = json.loads(html_text[start_index:end_index])
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


async def fetch_ashby_board_html(company_slug: str) -> str | None:
    """Fetch a public Ashby board page."""
    board_url = f"https://jobs.ashbyhq.com/{company_slug}"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await httpx_get_with_retry(
                client,
                board_url,
                headers=HEADERS,
                max_retries=ASHBY_REQUEST_RETRY_ATTEMPTS,
                retry_label=f"Ashby board '{company_slug}'",
            )
            if response.status_code == 404:
                print(f"  -- Board '{company_slug}' not found (404), skipping")
                return None
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as error:
            print(f"  -- Ashby HTTP error for '{company_slug}': {error.response.status_code}")
            return None
        except httpx.RequestError as error:
            print(f"  -- Ashby request error for '{company_slug}': {error}")
            return None


def parse_ashby_jobs(
    app_data: dict,
    company_name: str,
    base_url: str,
    priority: str,
    *,
    allow_global_remote: bool = False,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse the embedded Ashby job board payload into ATS Sniper job records."""
    job_board = app_data.get("jobBoard", {})
    job_postings = job_board.get("jobPostings", []) if isinstance(job_board, dict) else []
    if not isinstance(job_postings, list):
        return []

    jobs: list[dict] = []
    keyword_markers = get_board_keyword_markers()

    for job_posting in job_postings:
        if not isinstance(job_posting, dict) or not job_posting.get("isListed", True):
            continue

        title = str(job_posting.get("title", "")).strip()
        if not title:
            continue

        title_lower = title.casefold()
        if not any(keyword in title_lower for keyword in keyword_markers):
            record_source_rejection_reason(telemetry, "ashby", "non_target_title")
            continue

        location = str(
            job_posting.get("locationExternalName")
            or job_posting.get("locationName")
            or "Unknown"
        ).strip()
        workplace_type = str(job_posting.get("workplaceType", "")).strip()
        description = str(job_posting.get("descriptionPlainText", "")).strip()

        keep_job = should_keep_job(
            title,
            location=location,
            workplace_type=workplace_type,
            description=description,
            telemetry=telemetry,
            telemetry_source="ashby",
        )
        if not keep_job and not (
            allow_global_remote
            and workplace_type.casefold() == "remote"
            and location.casefold() == "remote"
        ):
            continue

        job_id = str(job_posting.get("id", "")).strip()
        if not job_id:
            continue

        jobs.append(
            {
                "title": title,
                "company": company_name,
                "url": f"{base_url.rstrip('/')}/{job_id}",
                "location": location,
                "posted_date": str(
                    job_posting.get("publishedDate") or job_posting.get("updatedAt") or ""
                ).strip(),
                "job_id": job_id,
                "source": "ashby_board",
                "ats": "Ashby",
                "priority": priority,
                "scraped_at": datetime.now().isoformat(),
                "description": description,
                "workplace_type": workplace_type,
                "employment_type": str(job_posting.get("employmentType", "")).strip(),
                "department": str(
                    job_posting.get("departmentExternalName")
                    or job_posting.get("departmentName")
                    or ""
                ).strip(),
                "team": str(job_posting.get("teamExternalName") or job_posting.get("teamName") or "").strip(),
                "team_names": job_posting.get("teamNames", []),
            }
        )

    return jobs


async def scrape_ashby_endpoint(
    endpoint_key: str,
    endpoint_config: dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Scrape a single Ashby board."""
    company_slug = endpoint_config.get("company_slug", endpoint_key)
    company_name = endpoint_config.get("name", endpoint_key)
    priority = endpoint_config.get("priority", "MEDIUM")
    base_url = endpoint_config.get("base_url", f"https://jobs.ashbyhq.com/{company_slug}")

    print(f"  -> {company_name} ({company_slug})...", end=" ")

    html_text = await fetch_ashby_board_html(company_slug)
    if html_text is None:
        return []

    app_data = extract_ashby_app_data(html_text)
    jobs = parse_ashby_jobs(
        app_data,
        company_name,
        base_url,
        priority,
        allow_global_remote=bool(endpoint_config.get("allow_global_remote")),
        telemetry=telemetry,
    )
    total_jobs = (
        len(app_data.get("jobBoard", {}).get("jobPostings", []))
        if isinstance(app_data.get("jobBoard", {}), dict)
        else 0
    )
    print(f"Found {len(jobs)} matching jobs (of {total_jobs} total)")
    return jobs


async def run_ashby_scrape(
    dry_run: bool = False,
    endpoints: Optional[Dict[str, dict]] = None,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Run scraper for all configured Ashby boards."""
    print("=" * 60)
    print("ASHBY SCRAPER - Public Job Boards")
    print("=" * 60)

    config = load_config()
    if endpoints is None:
        endpoints = get_ashby_endpoints(config)
    state = load_state()
    all_jobs = []
    new_jobs = []

    print(f"  Scanning {len(endpoints)} Ashby boards...")

    for endpoint_key, endpoint_config in endpoints.items():
        if dry_run:
            print(
                "  [DRY RUN] Scraping without state write: "
                f"{endpoint_config.get('name', endpoint_key)}"
            )

        jobs = await scrape_ashby_endpoint(endpoint_key, endpoint_config, telemetry=telemetry)
        all_jobs.extend(jobs)

        for job in jobs:
            job_hash = f"ashby_{job['job_id']}"
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

    print(f"\n  Ashby Summary: {len(all_jobs)} total matching, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_ashby_scrape(dry_run=dry_run))

    if jobs:
        print("\nNew Ashby Jobs:")
        for job in jobs:
            workplace = f" ({job['workplace_type']})" if job.get("workplace_type") else ""
            print(f"  * {job['title']} @ {job['company']}{workplace}")
            print(f"    {job['url']}")