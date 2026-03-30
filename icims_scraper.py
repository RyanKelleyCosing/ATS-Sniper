#!/usr/bin/env python3
"""
iCIMS Scraper - Fast JSON API Access for iCIMS-powered career sites

The "Sniper Move": Append ?mode=json to bypass the React SPA and get structured data.
Uses mobile User-Agent to bypass some gating. Falls back to Playwright for cookie-gated sites.

Target employers: Western & Southern Financial Group
"""

import json
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

# Config paths
CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE = Path(__file__).parent / "job_state.json"

# iCIMS endpoints - add more as discovered
ICIMS_ENDPOINTS = {
    "western_southern": {
        "name": "Western & Southern Financial Group",
        "base_url": "https://careers-westernsouthern.icims.com",
        "json_url": "https://careers-westernsouthern.icims.com/jobs/search?mode=json",
        "priority": "HIGH",
        "keywords": ["devops", "cloud", "engineer", "infrastructure", "platform", "sre", "software"]
    }
}

# Mobile User-Agent helps bypass some iCIMS gates
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"


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


async def fetch_icims_json(endpoint_config: dict, session_cookie: str = None) -> Optional[dict]:
    """
    Fetch job listings from iCIMS JSON endpoint.
    
    Args:
        endpoint_config: Dict with base_url, json_url, keywords
        session_cookie: Optional icims_session cookie for gated sites
    
    Returns:
        Dict with job listings or None on failure
    """
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    if session_cookie:
        headers["Cookie"] = f"icims_session={session_cookie}"
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            # Try JSON mode first
            response = await client.get(endpoint_config["json_url"], headers=headers)
            
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    return response.json()
                else:
                    # Might have gotten HTML instead - need cookie auth
                    print(f"  ⚠️ Got HTML instead of JSON, may need cookie auth")
                    return None
            else:
                print(f"  ⚠️ HTTP {response.status_code} from iCIMS")
                return None
                
        except Exception as e:
            print(f"  ❌ iCIMS fetch error: {e}")
            return None


async def fetch_icims_with_playwright_fallback(endpoint_config: dict) -> Optional[dict]:
    """
    Fallback: Use Playwright to grab session cookie, then use fast httpx.
    """
    try:
        from playwright.async_api import async_playwright
        
        print(f"  🔄 Using Playwright fallback for {endpoint_config['name']}...")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=MOBILE_UA)
            page = await context.new_page()
            
            # Load main page to get cookie
            await page.goto(endpoint_config["base_url"], wait_until="networkidle")
            await asyncio.sleep(2)
            
            # Extract cookies
            cookies = await context.cookies()
            session_cookie = None
            for cookie in cookies:
                if "icims" in cookie["name"].lower():
                    session_cookie = cookie["value"]
                    break
            
            await browser.close()
            
            if session_cookie:
                return await fetch_icims_json(endpoint_config, session_cookie)
            else:
                print(f"  ⚠️ No iCIMS session cookie found")
                return None
                
    except ImportError:
        print("  ⚠️ Playwright not installed. Run: pip install playwright && playwright install")
        return None
    except Exception as e:
        print(f"  ❌ Playwright fallback error: {e}")
        return None


def parse_icims_jobs(data: dict, endpoint_config: dict, keywords: List[str]) -> List[Dict]:
    """
    Parse iCIMS JSON response into standardized job format.

    iCIMS JSON structure varies, but commonly:
    - jobDetails or jobs array
    - Each job has: id, title, location, posted_date, etc.
    """
    jobs = []

    # Try common iCIMS JSON structures
    job_list = data.get("jobDetails", data.get("jobs", data.get("results", [])))

    if not isinstance(job_list, list):
        print(f"  ⚠️ Unexpected iCIMS structure, keys: {list(data.keys())}")
        return jobs

    for job_data in job_list:
        title = job_data.get("title", job_data.get("jobTitle", ""))
        job_id = job_data.get("id", job_data.get("jobId", ""))

        # Filter by keywords
        title_lower = title.lower()
        if not any(kw in title_lower for kw in keywords):
            continue

        job_url = f"{endpoint_config['base_url']}/jobs/{job_id}"

        jobs.append({
            "title": title,
            "company": endpoint_config["name"],
            "url": job_url,
            "location": job_data.get("location", job_data.get("city", "Unknown")),
            "posted_date": job_data.get("postedDate", job_data.get("posted_date", "")),
            "job_id": str(job_id),
            "source": "icims_api",
            "ats": "iCIMS",
            "priority": endpoint_config.get("priority", "MEDIUM"),
            "scraped_at": datetime.now().isoformat()
        })

    return jobs


async def scrape_icims_endpoint(endpoint_key: str, endpoint_config: dict) -> List[Dict]:
    """Scrape a single iCIMS endpoint."""
    print(f"\n🏢 Scraping {endpoint_config['name']} (iCIMS)...")

    # Try direct JSON first
    data = await fetch_icims_json(endpoint_config)

    # Fallback to Playwright if needed
    if data is None:
        data = await fetch_icims_with_playwright_fallback(endpoint_config)

    if data is None:
        print(f"  ❌ Failed to fetch data from {endpoint_config['name']}")
        return []

    keywords = endpoint_config.get("keywords", ["engineer", "devops", "cloud"])
    jobs = parse_icims_jobs(data, endpoint_config, keywords)

    print(f"  ✅ Found {len(jobs)} matching jobs")
    return jobs


async def run_icims_scrape(dry_run: bool = False) -> List[Dict]:
    """
    Run scraper for all configured iCIMS endpoints.

    Returns:
        List of new jobs found
    """
    print("=" * 60)
    print("🎯 iCIMS SCRAPER - JSON API Mode")
    print("=" * 60)

    state = load_state()
    all_jobs = []
    new_jobs = []

    for endpoint_key, endpoint_config in ICIMS_ENDPOINTS.items():
        if dry_run:
            print(f"  [DRY RUN] Would scrape: {endpoint_config['name']}")
            continue

        jobs = await scrape_icims_endpoint(endpoint_key, endpoint_config)
        all_jobs.extend(jobs)

        # Check for new jobs
        for job in jobs:
            job_hash = f"icims_{job['job_id']}"
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

    print(f"\n📊 iCIMS Summary: {len(all_jobs)} total, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_icims_scrape(dry_run=dry_run))

    if jobs:
        print("\n🆕 New Jobs Found:")
        for job in jobs:
            print(f"  • {job['title']} @ {job['company']}")
            print(f"    {job['url']}")

