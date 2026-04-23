#!/usr/bin/env python3
"""
Workday API Scraper - Direct API access to Workday career portals
Bypasses SerpApi for enterprise tier jobs - gets fresh postings instantly!
"""

import json
import random
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from utils.state import load_config, load_state, save_state
from utils.filters import get_workday_search_terms, should_keep_job
from utils.job_identity import ensure_job_identity_index, find_existing_job_url, store_job_identity_record

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

WORKDAY_RETRY_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
WORKDAY_MAX_ATTEMPTS = 3
WORKDAY_BACKOFF_BASE_SECONDS = 1.0
WORKDAY_BACKOFF_MAX_SECONDS = 8.0


def _workday_post_with_retry(url: str, payload: dict, timeout: int = 15) -> requests.Response:
    """POST to a Workday CXS endpoint with jittered backoff on transient 5xx/429."""
    last_error: Optional[Exception] = None
    for attempt in range(1, WORKDAY_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, headers=HEADERS, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == WORKDAY_MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in WORKDAY_RETRY_STATUS_CODES:
                return response
            last_error = requests.HTTPError(
                f"{response.status_code} transient response from {url}",
                response=response,
            )
            if attempt == WORKDAY_MAX_ATTEMPTS:
                return response
        sleep_for = min(
            WORKDAY_BACKOFF_MAX_SECONDS,
            WORKDAY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
        ) + random.uniform(0.0, 0.5)
        time.sleep(sleep_for)
    # Unreachable, but keep the type checker happy.
    raise last_error if last_error else RuntimeError(f"Workday POST failed for {url}")

def fetch_workday_jobs(endpoint: dict, search_text: str = "") -> List[dict]:
    """
    Fetch jobs from a Workday CXS API endpoint.
    
    Args:
        endpoint: Dict with 'name' and 'url' keys
        search_text: Optional search term to filter jobs
    
    Returns:
        List of job dictionaries
    """
    url = endpoint["url"]
    company_name = endpoint["name"]
    
    payload = {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": search_text
    }
    
    jobs = []
    
    try:
        response = _workday_post_with_retry(url, payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        job_postings = data.get("jobPostings", [])
        
        for job in job_postings:
            title = job.get("title", "Unknown Title")
            external_path = job.get("externalPath", "")
            posted_on = job.get("postedOn", "")
            location = job.get("locationsText") or job.get("location", "")
            if not location:
                bullet_fields = job.get("bulletFields", [])
                if isinstance(bullet_fields, list):
                    location = " | ".join(
                        str(field).strip()
                        for field in bullet_fields
                        if str(field).strip()
                    )
            
            # Build the full job URL
            # API format: https://pg.wd5.myworkdayjobs.com/wday/cxs/pg/1000/jobs
            # Public format: https://pg.wd5.myworkdayjobs.com/en-US/1000/job/LOCATION/TITLE_ID
            # Replace /wday/cxs/tenant/ with /en-US/
            import re as re_mod
            base_url = re_mod.sub(r'/wday/cxs/[^/]+/', '/en-US/', url).replace("/jobs", "")
            job_url = f"{base_url}{external_path}"
            
            jobs.append({
                "title": title,
                "company": company_name,
                "url": job_url,
                "location": location,
                "posted_on": posted_on,
                "source": "workday_api",
                "tier": "enterprise"
            })
            
    except requests.RequestException as e:
        print(f"  ⚠️ Error fetching {company_name}: {e}")
    except json.JSONDecodeError:
        print(f"  ⚠️ Invalid JSON from {company_name}")
    
    return jobs


def scrape_all_workday(
    config: dict,
    verbose: bool = True,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[dict]:
    """
    Scrape all configured Workday endpoints.
    
    Returns:
        List of all jobs found
    """
    endpoints = config.get("workday_endpoints", {})
    all_jobs = []
    
    if verbose:
        print(f"\n🔍 Scraping {len(endpoints)} Workday portals...")
    
    for key, endpoint in endpoints.items():
        if verbose:
            print(f"  → {endpoint['name']}...", end=" ")
        
        # Try multiple search terms to catch different job titles
        seen_urls = set()
        evaluated_urls = set()
        company_jobs = []
        
        for i, term in enumerate(get_workday_search_terms()):
            if i > 0:
                import time
                time.sleep(0.5)
            jobs = fetch_workday_jobs(endpoint, term)
            for job in jobs:
                if job["url"] in evaluated_urls:
                    continue
                evaluated_urls.add(job["url"])
                if job["url"] not in seen_urls and should_keep_job(
                    job["title"],
                    location=job.get("location", ""),
                    telemetry=telemetry,
                    telemetry_source="workday",
                ):
                    seen_urls.add(job["url"])
                    company_jobs.append(job)
        
        if verbose:
            print(f"Found {len(company_jobs)} jobs")
        
        all_jobs.extend(company_jobs)
    
    return all_jobs


def run_workday_scrape(
    dry_run: bool = False,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[dict]:
    """
    Run a full Workday scrape and update job state.
    
    Args:
        dry_run: If True, don't update state
    
    Returns:
        List of NEW jobs found
    """
    config = load_config()
    state = load_state()
    ensure_job_identity_index(state)
    
    print("=" * 60)
    print("🏢 WORKDAY DIRECT SCRAPER - Enterprise Jobs")
    print("=" * 60)
    
    all_jobs = scrape_all_workday(config, telemetry=telemetry)
    
    # Find new jobs
    new_jobs = []
    for job in all_jobs:
        existing_url = find_existing_job_url(state, job)
        if existing_url:
            store_job_identity_record(state, job, stored_url=existing_url)
            continue
        new_jobs.append(job)

    if telemetry is not None:
        seen_counters = telemetry.setdefault("_already_seen", {})
        seen_counters["workday"] = max(0, len(all_jobs) - len(new_jobs))

    print(f"\n📊 Results:")
    print(f"   Total jobs found: {len(all_jobs)}")
    print(f"   New jobs: {len(new_jobs)}")
    
    if not dry_run and new_jobs:
        # Ensure jobs dict exists
        if "jobs" not in state:
            state["jobs"] = {}
        # Add new jobs to state
        for job in new_jobs:
            store_job_identity_record(state, {
                "title": job["title"],
                "company": job["company"],
                "url": job["url"],
                "location": job.get("location", ""),
                "first_seen": datetime.now().isoformat(),
                "source": "workday_api",
                "tier": "enterprise"
            })
        state["last_workday_run"] = datetime.now().isoformat()
        save_state(state)
        print(f"   ✅ State updated!")
    
    return new_jobs


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    jobs = run_workday_scrape(dry_run=dry_run)
    
    if jobs:
        print("\n🆕 NEW ENTERPRISE JOBS:")
        for job in jobs[:10]:  # Show first 10
            print(f"   • {job['company']}: {job['title']}")

