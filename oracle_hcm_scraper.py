#!/usr/bin/env python3
"""
Oracle HCM Scraper - Direct REST API Access for Oracle Fusion Cloud HCM

Target employers: UC Health (migrated from Radancy to Oracle HCM)
API Pattern: /hcmRestApi/resources/{version}/recruitingJobSitePostedJobs

This reuses the pattern from Kroger's Oracle instance.
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

# Config paths
CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE = Path(__file__).parent / "job_state.json"

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

# Oracle HCM endpoints - Cincinnati employers
ORACLE_HCM_ENDPOINTS = {
    "uc_health": {
        "name": "UC Health",
        "base_url": "https://eswt.fa.us6.oraclecloud.com",
        "api_path": "/hcmRestApi/resources/11.13.18.05/recruitingJobSitePostedJobs",
        "site_id": "CX_1001",  # UC Health candidate experience site
        "priority": "HIGH",
        "keywords": ["devops", "cloud", "engineer", "infrastructure", "platform", "sre", 
                     "software", "systems", "network", "security", "data"],
        "location_filter": "Cincinnati"  # Filter for Cincinnati-area jobs
    },
    "kroger": {
        "name": "Kroger",
        "base_url": "https://kroger.wd5.myworkdayjobs.com",  # Actually Workday, reference only
        "note": "Kroger uses Workday - handled by workday_scraper.py",
        "skip": True
    }
}

# Headers for Oracle HCM REST API
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def load_config() -> dict:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"seen_jobs": {}}


def save_state(state: dict):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


async def fetch_oracle_hcm_jobs(endpoint_config: dict) -> Optional[List[dict]]:
    """
    Fetch jobs from Oracle HCM REST API.
    
    Oracle HCM uses a REST API pattern:
    GET /hcmRestApi/resources/{version}/recruitingJobSitePostedJobs
    
    Headers:
    - ora-cx-site-id: The specific career site ID
    
    Can also POST with search payload for filtered results.
    """
    base_url = endpoint_config["base_url"]
    api_path = endpoint_config["api_path"]
    site_id = endpoint_config.get("site_id", "")
    
    headers = {
        **DEFAULT_HEADERS,
        "ora-cx-site-id": site_id
    }
    
    # Build API URL
    api_url = f"{base_url}{api_path}"
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            # Try GET first (lists all jobs)
            response = await client.get(api_url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("items", data.get("jobs", []))
            
            # Try POST with search payload if GET fails
            if response.status_code in [401, 403, 404]:
                print(f"  🔄 GET failed ({response.status_code}), trying POST search...")
                
                keywords = endpoint_config.get("keywords", ["engineer"])
                search_payload = {
                    "searchTerm": " OR ".join(keywords[:5]),
                    "limit": 100,
                    "offset": 0
                }
                
                response = await client.post(api_url, headers=headers, json=search_payload)
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("items", data.get("jobs", []))
            
            print(f"  ⚠️ Oracle HCM API returned {response.status_code}")
            return None
            
        except Exception as e:
            print(f"  ❌ Oracle HCM fetch error: {e}")
            return None


async def search_oracle_hcm(endpoint_config: dict, search_term: str) -> Optional[List[dict]]:
    """
    Search Oracle HCM with specific term.
    """
    base_url = endpoint_config["base_url"]
    api_path = endpoint_config["api_path"]
    site_id = endpoint_config.get("site_id", "")

    headers = {
        **DEFAULT_HEADERS,
        "ora-cx-site-id": site_id
    }

    api_url = f"{base_url}{api_path}"
    search_payload = {"searchTerm": search_term}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(api_url, headers=headers, json=search_payload)
            if response.status_code == 200:
                data = response.json()
                return data.get("items", data.get("jobs", []))
            return None
        except Exception as e:
            print(f"  ❌ Search error: {e}")
            return None


def parse_oracle_jobs(job_list: List[dict], endpoint_config: dict) -> List[Dict]:
    """
    Parse Oracle HCM job response into standardized format.

    Oracle HCM job objects typically have:
    - RequisitionId, Title, PrimaryLocation, PostedDate, etc.
    """
    jobs = []
    keywords = [kw.lower() for kw in endpoint_config.get("keywords", [])]
    location_filter = endpoint_config.get("location_filter", "").lower()

    for job_data in job_list:
        # Extract fields (Oracle uses various field names)
        title = job_data.get("Title", job_data.get("title", job_data.get("JobTitle", "")))
        job_id = job_data.get("RequisitionId", job_data.get("Id", job_data.get("id", "")))
        location = job_data.get("PrimaryLocation", job_data.get("location", ""))

        # Filter by keywords in title
        title_lower = title.lower()
        if keywords and not any(kw in title_lower for kw in keywords):
            continue

        if should_exclude_title(title):
            continue

        # Filter by location if specified
        if location_filter and location_filter not in location.lower():
            continue

        # Build job URL
        base_url = endpoint_config["base_url"]
        job_url = f"{base_url}/hcmUI/CandidateExperience/en/sites/{endpoint_config.get('site_id', 'CX_1001')}/job/{job_id}"

        jobs.append({
            "title": title,
            "company": endpoint_config["name"],
            "url": job_url,
            "location": location,
            "posted_date": job_data.get("PostedDate", job_data.get("postedDate", "")),
            "job_id": str(job_id),
            "source": "oracle_hcm_api",
            "ats": "Oracle HCM",
            "priority": endpoint_config.get("priority", "MEDIUM"),
            "scraped_at": datetime.now().isoformat()
        })

    return jobs


async def scrape_oracle_endpoint(endpoint_key: str, endpoint_config: dict) -> List[Dict]:
    """Scrape a single Oracle HCM endpoint."""
    if endpoint_config.get("skip"):
        return []

    print(f"\n🏥 Scraping {endpoint_config['name']} (Oracle HCM)...")

    job_list = await fetch_oracle_hcm_jobs(endpoint_config)

    if job_list is None:
        print(f"  ❌ Failed to fetch data from {endpoint_config['name']}")
        return []

    jobs = parse_oracle_jobs(job_list, endpoint_config)
    print(f"  ✅ Found {len(jobs)} matching jobs")
    return jobs


async def run_oracle_hcm_scrape(dry_run: bool = False) -> List[Dict]:
    """
    Run scraper for all configured Oracle HCM endpoints.

    Returns:
        List of new jobs found
    """
    print("=" * 60)
    print("🎯 ORACLE HCM SCRAPER - REST API Mode")
    print("=" * 60)

    state = load_state()
    all_jobs = []
    new_jobs = []

    for endpoint_key, endpoint_config in ORACLE_HCM_ENDPOINTS.items():
        if endpoint_config.get("skip"):
            continue

        if dry_run:
            print(f"  [DRY RUN] Would scrape: {endpoint_config['name']}")
            continue

        jobs = await scrape_oracle_endpoint(endpoint_key, endpoint_config)
        all_jobs.extend(jobs)

        # Check for new jobs
        for job in jobs:
            job_hash = f"oracle_{job['job_id']}"
            if job_hash not in state.get("seen_jobs", {}):
                new_jobs.append(job)
                state.setdefault("seen_jobs", {})[job_hash] = {
                    "url": job["url"],
                    "title": job["title"],
                    "company": job["company"],
                    "found_at": job["scraped_at"],
                    "tier": "Enterprise"
                }

    # Save state
    if not dry_run:
        save_state(state)

    print(f"\n📊 Oracle HCM Summary: {len(all_jobs)} total, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_oracle_hcm_scrape(dry_run=dry_run))

    if jobs:
        print("\n🆕 New Jobs Found:")
        for job in jobs:
            print(f"  • {job['title']} @ {job['company']}")
            print(f"    {job['url']}")

