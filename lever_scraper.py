#!/usr/bin/env python3
"""
Lever Scraper - Public JSON API Access for Lever-powered job boards

API: GET https://api.lever.co/v0/postings/{company}?mode=json
No authentication required. Returns all published postings.
Supports server-side filtering by location, department, team, and commitment.
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE = Path(__file__).parent / "job_state.json"

API_BASE = "https://api.lever.co/v0/postings"

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


async def fetch_lever_jobs(company_slug: str) -> Optional[List[dict]]:
    """Fetch all postings from a Lever company board via public API."""
    url = f"{API_BASE}/{company_slug}?mode=json"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=HEADERS)

            if response.status_code == 404:
                print(f"  -- Company '{company_slug}' not found (404), skipping")
                return None

            response.raise_for_status()
            data = response.json()

            # Lever returns a plain JSON array, not a wrapped object
            if isinstance(data, list):
                return data
            return data.get("postings", [])

        except httpx.HTTPStatusError as e:
            print(f"  -- Lever HTTP error for '{company_slug}': {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            print(f"  -- Lever request error for '{company_slug}': {e}")
            return None


def parse_lever_jobs(
    job_list: List[dict],
    company_name: str,
    priority: str
) -> List[Dict]:
    """Parse Lever API response into standardized job format."""
    jobs = []

    for job_data in job_list:
        title = job_data.get("text", "")
        title_lower = title.lower()

        if not any(kw in title_lower for kw in TITLE_KEYWORDS):
            continue

        if should_exclude_title(title):
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
    endpoint_config: dict
) -> List[Dict]:
    """Scrape a single Lever company board."""
    company_slug = endpoint_config.get("company_slug", endpoint_key)
    company_name = endpoint_config.get("name", endpoint_key)
    priority = endpoint_config.get("priority", "MEDIUM")

    print(f"  -> {company_name} ({company_slug})...", end=" ")

    job_list = await fetch_lever_jobs(company_slug)
    if job_list is None:
        return []

    jobs = parse_lever_jobs(job_list, company_name, priority)
    print(f"Found {len(jobs)} matching jobs (of {len(job_list)} total)")
    return jobs


async def run_lever_scrape(dry_run: bool = False) -> List[Dict]:
    """
    Run scraper for all configured Lever company boards.

    Returns:
        List of new jobs found
    """
    print("=" * 60)
    print("LEVER SCRAPER - Public Postings API")
    print("=" * 60)

    config = load_config()
    endpoints = config.get("lever_endpoints", DEFAULT_ENDPOINTS)
    state = load_state()
    all_jobs = []
    new_jobs = []

    print(f"  Scanning {len(endpoints)} Lever boards...")

    for endpoint_key, endpoint_config in endpoints.items():
        if dry_run:
            print(f"  [DRY RUN] Would scrape: {endpoint_config.get('name', endpoint_key)}")
            continue

        jobs = await scrape_lever_endpoint(endpoint_key, endpoint_config)
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
