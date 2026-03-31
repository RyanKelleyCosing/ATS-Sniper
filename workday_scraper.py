#!/usr/bin/env python3
"""
Workday API Scraper - Direct API access to Workday career portals
Bypasses SerpApi for enterprise tier jobs - gets fresh postings instantly!
"""

import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Load config
CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "job_state.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Search keywords for DevOps/Cloud/Infra roles
SEARCH_TERMS = [
    "DevOps", "SRE", "Site Reliability", "Cloud Engineer",
    "Infrastructure Engineer", "Platform Engineer", "Cloud Architect",
    "Systems Engineer", "DevSecOps", "MLOps"
]

EXCLUDE_TITLES = [
    "senior staff", "staff engineer", "principal", "director", "vp",
    "vice president", "lead architect", "head of", "chief",
    "manager", "management",
    "sales", "account executive", "account manager",
    "business development", "customer success", "recruiter", "marketing",
    "legal", "finance manager", "hr", "people operations",
]

# Standalone "lead" — exclude "Lead" as a prefix (e.g. "Lead DevOps Engineer")
# but allow "lead" as part of compound words (e.g. "Leadership")
EXCLUDE_LEAD_PREFIXES = ["lead "]


def should_exclude_title(title: str) -> bool:
    """Check if a job title should be excluded based on seniority/irrelevance."""
    title_lower = title.lower()
    if any(exc in title_lower for exc in EXCLUDE_TITLES):
        return True
    if any(title_lower.startswith(prefix) for prefix in EXCLUDE_LEAD_PREFIXES):
        return True
    return False


def load_config() -> dict:
    """Load configuration from config.json"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state() -> dict:
    """Load existing job state"""
    if STATE_PATH.exists():
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"jobs": {}, "last_run": None}


def save_state(state: dict):
    """Save job state"""
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


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
        response = requests.post(url, headers=HEADERS, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        job_postings = data.get("jobPostings", [])
        
        for job in job_postings:
            title = job.get("title", "Unknown Title")
            external_path = job.get("externalPath", "")
            posted_on = job.get("postedOn", "")
            
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
                "posted_on": posted_on,
                "source": "workday_api",
                "tier": "enterprise"
            })
            
    except requests.RequestException as e:
        print(f"  ⚠️ Error fetching {company_name}: {e}")
    except json.JSONDecodeError:
        print(f"  ⚠️ Invalid JSON from {company_name}")
    
    return jobs


def scrape_all_workday(config: dict, verbose: bool = True) -> List[dict]:
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
        company_jobs = []
        
        for i, term in enumerate(SEARCH_TERMS):
            if i > 0:
                import time
                time.sleep(0.5)
            jobs = fetch_workday_jobs(endpoint, term)
            for job in jobs:
                if job["url"] not in seen_urls and not should_exclude_title(job["title"]):
                    seen_urls.add(job["url"])
                    company_jobs.append(job)
        
        if verbose:
            print(f"Found {len(company_jobs)} jobs")
        
        all_jobs.extend(company_jobs)
    
    return all_jobs


def run_workday_scrape(dry_run: bool = False) -> List[dict]:
    """
    Run a full Workday scrape and update job state.
    
    Args:
        dry_run: If True, don't update state
    
    Returns:
        List of NEW jobs found
    """
    config = load_config()
    state = load_state()
    
    print("=" * 60)
    print("🏢 WORKDAY DIRECT SCRAPER - Enterprise Jobs")
    print("=" * 60)
    
    all_jobs = scrape_all_workday(config)
    
    # Find new jobs
    existing_urls = set(state.get("jobs", {}).keys())
    new_jobs = [j for j in all_jobs if j["url"] not in existing_urls]
    
    print(f"\n📊 Results:")
    print(f"   Total jobs found: {len(all_jobs)}")
    print(f"   New jobs: {len(new_jobs)}")
    
    if not dry_run and new_jobs:
        # Ensure jobs dict exists
        if "jobs" not in state:
            state["jobs"] = {}
        # Add new jobs to state
        for job in new_jobs:
            state["jobs"][job["url"]] = {
                "title": job["title"],
                "company": job["company"],
                "first_seen": datetime.now().isoformat(),
                "source": "workday_api",
                "tier": "enterprise"
            }
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

