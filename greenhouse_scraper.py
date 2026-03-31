#!/usr/bin/env python3
"""
Greenhouse Scraper - Public JSON API Access for Greenhouse-powered job boards

API: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
No authentication required. Returns all published jobs for a given board.
Client-side filtering by title keywords (no server-side filtering available).
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE = Path(__file__).parent / "job_state.json"

API_BASE = "https://boards-api.greenhouse.io/v1/boards"

TITLE_KEYWORDS = [
    "devops", "cloud", "engineer", "infrastructure", "platform", "sre",
    "systems", "software", "reliability", "devsecops", "mlops", "architect",
    "automation", "security", "kubernetes", "azure", "aws", "site reliability",
]

EXCLUDE_TITLES = [
    "senior staff", "staff engineer", "principal", "director", "vp",
    "vice president", "lead architect", "head of", "chief",
    "manager", "management",
    "sales", "account executive", "account manager",
    "business development", "customer success", "recruiter", "marketing",
    "legal", "finance manager", "hr", "people operations",
]

EXCLUDE_LEAD_PREFIXES = ["lead "]


def should_exclude_title(title: str) -> bool:
    """Check if a job title should be excluded based on seniority/irrelevance."""
    title_lower = title.lower()
    if any(exc in title_lower for exc in EXCLUDE_TITLES):
        return True
    if any(title_lower.startswith(prefix) for prefix in EXCLUDE_LEAD_PREFIXES):
        return True
    return False

DEFAULT_ENDPOINTS = {
    "8451": {
        "name": "84.51 (Kroger)",
        "board_token": "8451",
        "priority": "HIGH"
    },
    "array": {
        "name": "Array",
        "board_token": "array",
        "priority": "MEDIUM"
    },
    "patientpoint": {
        "name": "PatientPoint",
        "board_token": "patientpoint",
        "priority": "MEDIUM"
    },
    "gitlab": {
        "name": "GitLab",
        "board_token": "gitlab",
        "priority": "MEDIUM"
    },
    "cloudflare": {
        "name": "Cloudflare",
        "board_token": "cloudflare",
        "priority": "MEDIUM"
    },
    "datadog": {
        "name": "Datadog",
        "board_token": "datadog",
        "priority": "MEDIUM"
    },
    "mongodb": {
        "name": "MongoDB",
        "board_token": "mongodb",
        "priority": "MEDIUM"
    },
    "cockroachlabs": {
        "name": "CockroachDB",
        "board_token": "cockroachlabs",
        "priority": "MEDIUM"
    },
    "hyland": {
        "name": "Hyland Software",
        "board_token": "haborasoftware",
        "priority": "MEDIUM"
    },
    "rootinsurance": {
        "name": "Root Insurance",
        "board_token": "rootinsurance",
        "priority": "MEDIUM"
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def load_config() -> dict:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"seen_jobs": {}}


def save_state(state: dict) -> None:
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def strip_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    if not html:
        return ""
    return BeautifulSoup(html, 'html.parser').get_text(separator='\n', strip=True)


async def fetch_greenhouse_jobs(board_token: str) -> Optional[List[dict]]:
    """Fetch all jobs from a Greenhouse board via public API."""
    url = f"{API_BASE}/{board_token}/jobs?content=true&pay_transparency=true"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=HEADERS)

            if response.status_code == 404:
                print(f"  -- Board '{board_token}' not found (404), skipping")
                return None

            response.raise_for_status()
            data = response.json()
            return data.get("jobs", [])

        except httpx.HTTPStatusError as e:
            print(f"  -- Greenhouse HTTP error for '{board_token}': {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            print(f"  -- Greenhouse request error for '{board_token}': {e}")
            return None


def parse_greenhouse_jobs(
    job_list: List[dict],
    company_name: str,
    priority: str
) -> List[Dict]:
    """Parse Greenhouse API response into standardized job format."""
    jobs = []

    for job_data in job_list:
        title = job_data.get("title", "")
        title_lower = title.lower()

        if not any(kw in title_lower for kw in TITLE_KEYWORDS):
            continue

        if should_exclude_title(title):
            continue

        job_id = str(job_data.get("id", ""))
        location = job_data.get("location", {}).get("name", "Unknown")
        content_html = job_data.get("content", "")
        description = strip_html(content_html)

        salary = ""
        pay_ranges = job_data.get("pay_input_ranges", [])
        if pay_ranges:
            pay = pay_ranges[0]
            min_cents = pay.get("min_cents", 0)
            max_cents = pay.get("max_cents", 0)
            if min_cents and max_cents:
                salary = f"${min_cents // 100:,} - ${max_cents // 100:,}"

        posted_date = job_data.get("first_published", job_data.get("updated_at", ""))

        departments = [d.get("name", "") for d in job_data.get("departments", [])]
        offices = [o.get("name", "") for o in job_data.get("offices", [])]

        jobs.append({
            "title": title,
            "company": company_name,
            "url": job_data.get("absolute_url", ""),
            "location": location,
            "posted_date": posted_date,
            "job_id": job_id,
            "source": "greenhouse_api",
            "ats": "Greenhouse",
            "priority": priority,
            "scraped_at": datetime.now().isoformat(),
            "salary": salary,
            "description": description,
            "departments": departments,
            "offices": offices,
        })

    return jobs


async def scrape_greenhouse_endpoint(
    endpoint_key: str,
    endpoint_config: dict
) -> List[Dict]:
    """Scrape a single Greenhouse board."""
    board_token = endpoint_config.get("board_token", endpoint_key)
    company_name = endpoint_config.get("name", endpoint_key)
    priority = endpoint_config.get("priority", "MEDIUM")

    print(f"  -> {company_name} ({board_token})...", end=" ")

    job_list = await fetch_greenhouse_jobs(board_token)
    if job_list is None:
        return []

    jobs = parse_greenhouse_jobs(job_list, company_name, priority)
    print(f"Found {len(jobs)} matching jobs (of {len(job_list)} total)")
    return jobs


async def run_greenhouse_scrape(dry_run: bool = False) -> List[Dict]:
    """
    Run scraper for all configured Greenhouse boards.

    Returns:
        List of new jobs found
    """
    print("=" * 60)
    print("GREENHOUSE SCRAPER - Public Board API")
    print("=" * 60)

    config = load_config()
    endpoints = config.get("greenhouse_endpoints", DEFAULT_ENDPOINTS)
    state = load_state()
    all_jobs = []
    new_jobs = []

    print(f"  Scanning {len(endpoints)} Greenhouse boards...")

    for endpoint_key, endpoint_config in endpoints.items():
        if dry_run:
            print(f"  [DRY RUN] Would scrape: {endpoint_config.get('name', endpoint_key)}")
            continue

        jobs = await scrape_greenhouse_endpoint(endpoint_key, endpoint_config)
        all_jobs.extend(jobs)

        for job in jobs:
            job_hash = f"greenhouse_{job['job_id']}"
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

    print(f"\n  Greenhouse Summary: {len(all_jobs)} total matching, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_greenhouse_scrape(dry_run=dry_run))

    if jobs:
        print("\nNew Greenhouse Jobs:")
        for job in jobs:
            salary_tag = f" [{job['salary']}]" if job.get('salary') else ""
            print(f"  * {job['title']} @ {job['company']}{salary_tag}")
            print(f"    {job['url']}")
