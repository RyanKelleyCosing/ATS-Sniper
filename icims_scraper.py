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
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import httpx

from utils.state import load_config, load_state, save_state
from utils.filters import should_keep_job
from utils.http import httpx_get_with_retry
from utils.pipeline_telemetry import record_source_rejection_reason

# iCIMS endpoints - add more as discovered
DEFAULT_ENDPOINTS = {
    "western_southern": {
        "name": "Western & Southern Financial Group",
        "base_url": "https://careers-westernsouthern.icims.com",
        "json_url": "https://careers-westernsouthern.icims.com/jobs/search?mode=json",
        "priority": "HIGH",
        "keywords": ["devops", "cloud", "engineer", "infrastructure", "platform", "sre", "software"]
    },
    "medical_solutions": {
        "name": "Medical Solutions",
        "base_url": "https://careers-medicalsolutions.icims.com",
        "json_url": "https://careers-medicalsolutions.icims.com/jobs/search?mode=json",
        "search_url": "https://careers-medicalsolutions.icims.com/jobs/search?in_iframe=1",
        "priority": "HIGH",
        "keywords": ["devops", "cloud", "engineer", "infrastructure", "platform", "sre", "software"],
    }
}

# Mobile User-Agent helps bypass some iCIMS gates
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
DEFAULT_ICIMS_SEARCH_PATH = "/jobs/search?searchRelation=keyword_all"
ICIMS_REQUEST_RETRY_ATTEMPTS = 3


def get_icims_endpoints(config: Optional[dict] = None) -> Dict[str, dict]:
    """Return default iCIMS endpoints merged with config overrides."""
    config = config or load_config()
    configured_endpoints = config.get("icims_endpoints", {})
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


def build_icims_headers(session_cookie: str | None = None) -> dict[str, str]:
    """Build request headers for iCIMS endpoints."""
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if session_cookie:
        headers["Cookie"] = f"icims_session={session_cookie}"
    return headers


def canonicalize_icims_url(url: str) -> str:
    """Strip iframe-only parameters from iCIMS URLs for stable deduplication."""
    parts = urlsplit(url)
    cleaned_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() != "in_iframe"
    ]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(cleaned_query),
            "",
        )
    )


def extract_icims_location(row) -> str:
    """Extract location from the varying iCIMS search row header layouts."""
    if row is None:
        return "Unknown"

    header_containers = row.select("div.header.left, div.header.right")
    for header_container in header_containers:
        location_texts = [
            span.get_text(" ", strip=True)
            for span in header_container.find_all("span")
            if "sr-only" not in (span.get("class") or []) and span.get_text(" ", strip=True)
        ]
        if location_texts:
            return location_texts[0]

        fallback_text = header_container.get_text(" ", strip=True)
        if fallback_text:
            return fallback_text

    return "Unknown"


def parse_icims_html_jobs(
    html_text: str,
    endpoint_config: dict,
    keywords: List[str],
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse server-rendered iCIMS search HTML into standardized job rows."""
    soup = BeautifulSoup(html_text, "html.parser")
    jobs: list[dict] = []
    seen_job_ids: set[str] = set()

    for anchor in soup.select("div.title a.iCIMS_Anchor[href*='/jobs/']"):
        href = str(anchor.get("href", "")).strip()
        title_tag = anchor.find("h3")
        title = title_tag.get_text(" ", strip=True) if title_tag else anchor.get_text(" ", strip=True)
        job_id_match = re.search(r"/jobs/(\d+)/", href)

        if not href or not title or not job_id_match:
            continue

        job_id = job_id_match.group(1)
        if job_id in seen_job_ids:
            continue

        row = anchor.find_parent("div", class_="row")
        location = extract_icims_location(row)
        description = ""
        if row is not None:
            description_container = row.select_one("div.description")
            if description_container is not None:
                description = description_container.get_text(" ", strip=True)

        title_lower = title.lower()
        description_lower = description.lower()
        if not any(keyword in title_lower or keyword in description_lower for keyword in keywords):
            record_source_rejection_reason(telemetry, "icims", "non_target_title")
            continue

        if not should_keep_job(
            title,
            location=location,
            description=description,
            telemetry=telemetry,
            telemetry_source="icims",
        ):
            continue

        jobs.append(
            {
                "title": title,
                "company": endpoint_config["name"],
                "url": canonicalize_icims_url(
                    href if href.startswith("http") else urljoin(endpoint_config["base_url"], href)
                ),
                "location": location,
                "posted_date": "",
                "job_id": job_id,
                "source": "icims_html",
                "ats": "iCIMS",
                "priority": endpoint_config.get("priority", "MEDIUM"),
                "scraped_at": datetime.now().isoformat(),
            }
        )
        seen_job_ids.add(job_id)

    return jobs


async def fetch_icims_html_jobs(
    endpoint_config: dict,
    keywords: List[str],
    *,
    initial_html: str | None = None,
    initial_url: str | None = None,
    session_cookie: str | None = None,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Follow iCIMS search pages and parse server-rendered job listings."""
    headers = build_icims_headers(session_cookie)
    search_url = str(
        endpoint_config.get("search_url")
        or urljoin(endpoint_config["base_url"], DEFAULT_ICIMS_SEARCH_PATH)
    )
    next_url = initial_url or search_url
    html_text = initial_html
    seen_pages: set[str] = set()
    jobs: list[dict] = []
    seen_job_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while next_url and next_url not in seen_pages:
            seen_pages.add(next_url)

            if html_text is None:
                try:
                    response = await httpx_get_with_retry(
                        client,
                        next_url,
                        headers=headers,
                        max_retries=ICIMS_REQUEST_RETRY_ATTEMPTS,
                        retry_label=f"iCIMS HTML '{endpoint_config['name']}'",
                    )
                except httpx.RequestError as exc:
                    print(f"  [WARN] iCIMS HTML fetch error for {endpoint_config['name']}: {exc}")
                    break
                if response.status_code != 200:
                    break
                html_text = response.text

            page_jobs = parse_icims_html_jobs(
                html_text,
                endpoint_config,
                keywords,
                telemetry=telemetry,
            )
            for job in page_jobs:
                if job["job_id"] in seen_job_ids:
                    continue
                seen_job_ids.add(job["job_id"])
                jobs.append(job)

            soup = BeautifulSoup(html_text, "html.parser")
            next_link = soup.find("link", rel="next")
            next_href = str(next_link.get("href", "")).strip() if next_link else ""
            next_url = urljoin(endpoint_config["base_url"], next_href) if next_href else None
            html_text = None

    return jobs


async def fetch_icims_json(
    endpoint_config: dict,
    session_cookie: str | None = None,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Fetch job listings from iCIMS JSON endpoint.
    
    Args:
        endpoint_config: Dict with base_url, json_url, keywords
        session_cookie: Optional icims_session cookie for gated sites
    
    Returns:
        Dict with job listings or None on failure
    """
    headers = build_icims_headers(session_cookie)
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            # Try JSON mode first
            response = await httpx_get_with_retry(
                client,
                endpoint_config["json_url"],
                headers=headers,
                max_retries=ICIMS_REQUEST_RETRY_ATTEMPTS,
                retry_label=f"iCIMS JSON '{endpoint_config['name']}'",
            )
            
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    return response.json(), None
                else:
                    print("  [INFO] Got HTML instead of JSON, parsing search page directly")
                    return None, response.text
            else:
                print(f"  [WARN] HTTP {response.status_code} from iCIMS")
                return None, None
                
        except Exception as e:
            print(f"  [ERROR] iCIMS fetch error: {e}")
            return None, None


async def fetch_icims_with_playwright_fallback(
    endpoint_config: dict,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Fallback: Use Playwright to grab session cookie, then use fast httpx.
    """
    try:
        from playwright.async_api import async_playwright
        
        print(f"  [FALLBACK] Using Playwright for {endpoint_config['name']}...")
        
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
                print("  [WARN] No iCIMS session cookie found")
                return None, None
                
    except ImportError:
        print("  [WARN] Playwright not installed. Run: pip install playwright && playwright install")
        return None, None
    except Exception as e:
        print(f"  [ERROR] Playwright fallback error: {e}")
        return None, None


def parse_icims_jobs(
    data: dict,
    endpoint_config: dict,
    keywords: List[str],
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
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
        print(f"  [WARN] Unexpected iCIMS structure, keys: {list(data.keys())}")
        return jobs

    for job_data in job_list:
        title = job_data.get("title", job_data.get("jobTitle", ""))
        job_id = job_data.get("id", job_data.get("jobId", ""))
        location = job_data.get("location", job_data.get("city", "Unknown"))

        # Filter by keywords
        title_lower = title.lower()
        if not any(kw in title_lower for kw in keywords):
            record_source_rejection_reason(telemetry, "icims", "non_target_title")
            continue

        if not should_keep_job(
            title,
            location=location,
            telemetry=telemetry,
            telemetry_source="icims",
        ):
            continue

        job_url = f"{endpoint_config['base_url']}/jobs/{job_id}"

        jobs.append({
            "title": title,
            "company": endpoint_config["name"],
            "url": job_url,
            "location": location,
            "posted_date": job_data.get("postedDate", job_data.get("posted_date", "")),
            "job_id": str(job_id),
            "source": "icims_api",
            "ats": "iCIMS",
            "priority": endpoint_config.get("priority", "MEDIUM"),
            "scraped_at": datetime.now().isoformat()
        })

    return jobs


async def scrape_icims_endpoint(
    endpoint_key: str,
    endpoint_config: dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Scrape a single iCIMS endpoint."""
    print(f"\n[iCIMS] Scraping {endpoint_config['name']}...")

    keywords = endpoint_config.get("keywords", ["engineer", "devops", "cloud"])

    # Try direct JSON first
    data, html_text = await fetch_icims_json(endpoint_config)

    if html_text:
        jobs = await fetch_icims_html_jobs(
            endpoint_config,
            keywords,
            initial_html=html_text,
            initial_url=endpoint_config["json_url"],
            telemetry=telemetry,
        )
        if jobs:
            print(f"  [OK] Found {len(jobs)} matching jobs")
            return jobs

    jobs = await fetch_icims_html_jobs(
        endpoint_config,
        keywords,
        telemetry=telemetry,
    )
    if jobs:
        print(f"  [OK] Found {len(jobs)} matching jobs")
        return jobs

    # Fallback to Playwright if needed
    if data is None:
        data, html_text = await fetch_icims_with_playwright_fallback(endpoint_config)
        if html_text:
            jobs = await fetch_icims_html_jobs(
                endpoint_config,
                keywords,
                initial_html=html_text,
                initial_url=endpoint_config["json_url"],
                telemetry=telemetry,
            )
            if jobs:
                print(f"  [OK] Found {len(jobs)} matching jobs")
                return jobs

    if data is None:
        print(f"  [ERROR] Failed to fetch data from {endpoint_config['name']}")
        return []

    jobs = parse_icims_jobs(data, endpoint_config, keywords, telemetry=telemetry)

    print(f"  [OK] Found {len(jobs)} matching jobs")
    return jobs


async def run_icims_scrape(
    dry_run: bool = False,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """
    Run scraper for all configured iCIMS endpoints.

    Returns:
        List of new jobs found
    """
    print("=" * 60)
    print("iCIMS SCRAPER - HTML/JSON MODE")
    print("=" * 60)

    config = load_config()
    endpoints = get_icims_endpoints(config)
    state = load_state()
    all_jobs = []
    new_jobs = []

    for endpoint_key, endpoint_config in endpoints.items():
        if dry_run:
            print(f"  [DRY RUN] Scraping without state write: {endpoint_config['name']}")

        jobs = await scrape_icims_endpoint(endpoint_key, endpoint_config, telemetry=telemetry)
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

    print(f"\n[SUMMARY] iCIMS: {len(all_jobs)} total, {len(new_jobs)} new")
    return new_jobs


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    jobs = asyncio.run(run_icims_scrape(dry_run=dry_run))

    if jobs:
        print("\n[NEW JOBS]")
        for job in jobs:
            print(f"  - {job['title']} @ {job['company']}")
            print(f"    {job['url']}")

