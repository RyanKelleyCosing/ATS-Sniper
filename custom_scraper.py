"""
Custom Scraper v1 - LLM-Assisted Job Extraction

Uses Playwright (headless browser) + GPT-4o-mini to extract jobs from
non-Workday ATS systems (SuccessFactors, iCIMS, Avature, Taleo, etc.)

Why this approach:
- Zero maintenance: LLM adapts to page redesigns
- Highly scalable: Just add URLs to config
- Cost-effective: ~$0.001 per page with GPT-4o-mini
"""

import asyncio
import json
import re
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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

# Load config
CONFIG_FILE = Path(__file__).parent / "config.json"
STATE_FILE = Path(__file__).parent / "job_state.json"

def load_config() -> dict:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"seen_jobs": {}, "jobs": {}}

def save_state(state: dict):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# Non-Workday targets with pre-loaded search URLs
NON_WORKDAY_TARGETS = {
    "Medpace": {
        "url": "https://careers.medpace.com/information-technology/jobs",
        "ats": "iCIMS/Jibe",
        "priority": "HIGH",
        "note": "C#/.NET/Azure - APPLY IMMEDIATELY"
    },
    "TQL": {
        "url": "https://careers.tql.com/en_US/TQLexternalcareers/SearchJobs/?3_87_3=%5B%22133%22%5D",
        "ats": "Avature",
        "priority": "MEDIUM",
        "note": "Large IT dept in Eastgate - filter=IT"
    },
    "Kroger": {
        "url": "https://www.krogerfamilycareers.com/en/sites/CX_2001/requisitions/list?keyword=engineer",
        "ats": "Eightfold",
        "priority": "MEDIUM",
        "note": "Technology division - new URL"
    },
    "LCS": {
        "url": "https://www.lcs.com/careers/",
        "ats": "Custom",
        "priority": "HIGH", 
        "note": "C#/.NET shop in Loveland"
    },
    "Cintas": {
        "url": "https://careers.cintas.com/us/en/search-results?keywords=engineer",
        "ats": "SuccessFactors",
        "priority": "MEDIUM",
        "note": "Corporate HQ Cincinnati"
    },
    "TriHealth": {
        "url": "https://www.trihealth.com/careers/search-jobs?keyword=IT",
        "ats": "Oracle Cloud",
        "priority": "LOW",
        "note": "Healthcare IT"
    },
    "Western_Southern": {
        "url": "https://careers-westernsouthern.icims.com/jobs/search?ss=1&searchKeyword=engineer",
        "ats": "iCIMS",
        "priority": "LOW",
        "note": "SPA - requires manual check: https://careers-westernsouthern.icims.com/jobs/search",
        "manual_check": True
    },
    "University_of_Cincinnati": {
        "url": "https://jobs.uc.edu/search/?searchby=location&createNewAlert=false&q=&locationsearch=Cincinnati&geolocation=&optionsFacetsDD_title=&optionsFacetsDD_location=Cincinnati&optionsFacetsDD_department=",
        "ats": "SuccessFactors",
        "priority": "MEDIUM",
        "note": "IT/Azure/DevOps roles in academic setting"
    },
    "UC_Health": {
        "url": "https://careers.uchealth.com/jobs",
        "ats": "Radancy",
        "priority": "LOW",
        "note": "SPA - requires manual check: https://careers.uchealth.com/search-jobs",
        "manual_check": True
    }
}
# NOTE: St_Elizabeth moved to Workday scraper (uses stelizabeth.wd5.myworkdayjobs.com)

# USAJobs API (Wright-Patterson AFB) - separate because it has a real API
USAJOBS_CONFIG = {
    "enabled": True,
    "keywords": ["DevOps", "Cloud Engineer", "Site Reliability", "Systems Administrator"],
    "location": "Dayton, Ohio",
    "radius": 25
}


def clean_html_for_llm(html: str) -> str:
    """Strip scripts, styles, and excess whitespace to reduce tokens."""
    # Remove script and style tags
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    # Remove SVG and path elements
    html = re.sub(r'<svg[^>]*>.*?</svg>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Collapse whitespace
    html = re.sub(r'\s+', ' ', html)
    # Remove data attributes
    html = re.sub(r'\s+data-[a-z-]+="[^"]*"', '', html)
    return html.strip()


async def fetch_page_html(url: str, wait_seconds: int = 5) -> Optional[str]:
    """Use Playwright to render JavaScript and get final HTML."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    try:
        async with async_playwright() as p:
            # Launch with anti-detection
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                java_script_enabled=True,
            )
            page = await context.new_page()

            # Remove webdriver detection
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # Navigate and wait for network to be idle
            await page.goto(url, wait_until='networkidle', timeout=45000)

            # Wait for job listings to appear (common selectors)
            try:
                await page.wait_for_selector('a[href*="/jobs/"], .job-card, .job-listing, .job-title, [data-job-id], .search-result', timeout=10000)
            except:
                pass  # Continue even if selector not found

            await asyncio.sleep(wait_seconds)  # Extra wait for dynamic content
            html = await page.content()
            await browser.close()
            return html
    except Exception as e:
        print(f"  ⚠️ Error fetching {url}: {e}")
        return None


def extract_jobs_regex_fallback(html: str, base_url: str) -> List[Dict]:
    """Fallback: Extract job links using regex patterns."""
    jobs = []

    # Blacklist navigation/non-job text
    blacklist = ['read more', 'apply now', 'view job', 'learn more', 'login',
                 'candidates', 'employees', 'sign in', 'register', 'search']

    # Pattern 1: Jibe/Medpace - <a href="/xxx/jobs/12345"><span itemprop="title">Job Title</span></a>
    jibe_pattern = r'href="(/[^"]*?/jobs/(\d{4,6})[^"]*)"[^>]*><span[^>]*itemprop="title"[^>]*>([^<]+)</span>'
    matches = re.findall(jibe_pattern, html, re.IGNORECASE)
    for url, job_id, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 3:
            if not url.startswith('http'):
                url = base_url.rstrip('/') + url
            jobs.append({"title": title, "url": url, "job_id": job_id, "company": "Medpace"})

    # Pattern 2: Generic job-title-link with span
    generic_pattern = r'class="job-title[^"]*"[^>]*href="([^"]+)"[^>]*><span[^>]*>([^<]+)</span>'
    matches = re.findall(generic_pattern, html, re.IGNORECASE)
    for url, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 3 and title.lower() not in blacklist:
            if not url.startswith('http'):
                url = base_url.rstrip('/') + url
            # Extract job ID from URL
            job_id_match = re.search(r'/jobs/(\d+)', url)
            job_id = job_id_match.group(1) if job_id_match else None
            jobs.append({"title": title, "url": url, "job_id": job_id})

    # Pattern 3: iCIMS standard - /jobs/12345 with direct text
    icims_pattern = r'href="(/jobs/(\d{4,6})[^"]*)"[^>]*>([^<]+)'
    matches = re.findall(icims_pattern, html, re.IGNORECASE)
    for url, job_id, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 5 and title.lower() not in blacklist:
            if not url.startswith('http'):
                url = base_url.rstrip('/') + url
            jobs.append({"title": title, "url": url, "job_id": job_id})

    # Pattern 4: job cards with data attributes
    job_card_pattern = r'data-job-id="(\d+)"[^>]*title="([^"]+)"'
    matches = re.findall(job_card_pattern, html, re.IGNORECASE)
    for job_id, title in matches:
        url = f"{base_url}/jobs/{job_id}"
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 5:
            jobs.append({"title": title, "url": url, "job_id": job_id})

    # Pattern 5: SuccessFactors - jobId=12345
    sf_pattern = r'href="([^"]*jobId=\d+[^"]*)"[^>]*>([^<]+)'
    matches = re.findall(sf_pattern, html, re.IGNORECASE)
    for url, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 5 and title.lower() not in blacklist:
            if not url.startswith('http'):
                url = base_url + url
            jobs.append({"title": title, "url": url})

    # Pattern 6: UC SuccessFactors - /job/Title-Here/12345-en_US
    uc_pattern = r'href="(/job/([^/]+)/(\d+)-en_US[^"]*)"'
    matches = re.findall(uc_pattern, html, re.IGNORECASE)
    for url, title_slug, job_id in matches:
        # Convert URL-encoded title to readable
        title = title_slug.replace('-', ' ').replace('%2C', ',').replace('%28', '(').replace('%29', ')').replace('&amp;', '&')
        if len(title) > 5:
            full_url = f"https://jobs.uc.edu{url}"
            jobs.append({"title": title, "url": full_url, "job_id": job_id, "company": "University of Cincinnati"})

    # Pattern 7: Avature/TQL - /JobDetail/Location/jobId=XXXX
    avature_pattern = r'href="([^"]*JobDetail[^"]*)"[^>]*>\s*(?:<[^>]*>)*\s*([^<]+)'
    matches = re.findall(avature_pattern, html, re.IGNORECASE)
    for url, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 5 and title.lower() not in blacklist:
            if not url.startswith('http'):
                url = base_url.rstrip('/') + url
            job_id_match = re.search(r'jobId=(\d+)', url)
            job_id = job_id_match.group(1) if job_id_match else None
            jobs.append({"title": title, "url": url, "job_id": job_id, "company": "TQL"})

    # Pattern 8: Eightfold/Kroger - requisitions with title in JSON or data attr
    eightfold_pattern = r'"title"\s*:\s*"([^"]{10,80})"[^}]*"id"\s*:\s*"?(\d+)"?'
    matches = re.findall(eightfold_pattern, html, re.IGNORECASE)
    for title, job_id in matches:
        url = f"https://www.krogerfamilycareers.com/requisitions/{job_id}"
        jobs.append({"title": title, "url": url, "job_id": job_id, "company": "Kroger"})

    # Pattern 9: Radancy/UC Health - job cards with data-entity-id
    radancy_pattern = r'data-entity-id="(\d+)"[^>]*>.*?<[^>]*class="[^"]*job-title[^"]*"[^>]*>([^<]+)'
    matches = re.findall(radancy_pattern, html, re.IGNORECASE | re.DOTALL)
    for job_id, title in matches[:20]:  # Limit to prevent runaway
        title = re.sub(r'\s+', ' ', title).strip()
        if len(title) > 5:
            url = f"https://careers.uchealth.com/job/{job_id}"
            jobs.append({"title": title, "url": url, "job_id": job_id, "company": "UC Health"})

    # Pattern 10: Western & Southern iCIMS - uses /jobs/XXXXX/title
    ws_icims_pattern = r'href="(/jobs/(\d{4,7})/[^"]*)"[^>]*>([^<]+)'
    matches = re.findall(ws_icims_pattern, html, re.IGNORECASE)
    for url, job_id, title in matches:
        title = re.sub(r'\s+', ' ', title).strip()
        if title and len(title) > 5 and title.lower() not in blacklist:
            full_url = f"https://careers-westernsouthern.icims.com{url}"
            jobs.append({"title": title, "url": full_url, "job_id": job_id, "company": "Western & Southern"})

    # Dedupe by URL
    seen = set()
    unique_jobs = []
    for job in jobs:
        if job['url'] not in seen:
            seen.add(job['url'])
            unique_jobs.append(job)

    return unique_jobs


def extract_jobs_with_llm(html: str, company: str, base_url: str) -> List[Dict]:
    """Use GPT-4o-mini to extract job listings from HTML."""
    from openai import OpenAI

    config = load_config()
    client = OpenAI(api_key=config.get('openai_key'))

    cleaned_html = clean_html_for_llm(html)

    # Debug: print HTML length
    print(f"(HTML: {len(html)} chars, cleaned: {len(cleaned_html)} chars)", end=" ", flush=True)

    # Truncate to ~20k chars to stay under token limits
    if len(cleaned_html) > 20000:
        cleaned_html = cleaned_html[:20000]

    # If HTML is too small, something went wrong
    if len(cleaned_html) < 500:
        print("⚠️ HTML too small, using regex fallback...", end=" ", flush=True)
        return extract_jobs_regex_fallback(html, base_url)

    prompt = f"""You are a data extraction API. Parse this HTML from {company}'s careers page.
Extract every job posting visible. Return ONLY a valid JSON array.

Each object must have:
- "title": Job title
- "url": Full URL (if relative, prepend: {base_url})
- "location": Location if visible
- "department": Department if visible

Look for job titles in links, cards, list items. Common patterns:
- <a href="/jobs/12345">Job Title</a>
- Links containing "job" or job IDs
- Elements with job-related classes

HTML:
{cleaned_html}

Return ONLY the JSON array, no markdown, no explanation. If no jobs found, return []."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
            temperature=0
        )
        content = response.choices[0].message.content.strip()
        # Clean up response
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        jobs = json.loads(content)

        # If LLM found nothing, try regex fallback
        if not jobs:
            print("LLM found nothing, trying regex...", end=" ", flush=True)
            jobs = extract_jobs_regex_fallback(html, base_url)

        return jobs
    except Exception as e:
        print(f"  ⚠️ LLM error: {e}, trying regex...", end=" ", flush=True)
        return extract_jobs_regex_fallback(html, base_url)


def fetch_kroger_api() -> List[Dict]:
    """Fetch jobs from Kroger's Oracle HCM REST API."""
    import requests

    url = 'https://eluq.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions'
    jobs = []

    # Search for DevOps/Cloud/Engineer keywords
    keywords = ['DevOps', 'Cloud', 'SRE', 'Infrastructure', 'Engineer']
    seen_ids = set()

    for keyword in keywords:
        params = {
            'onlyData': 'true',
            'expand': 'requisitionList',
            'finder': f'findReqs;siteNumber=CX_2001,limit=25,keyword={keyword},sortBy=POSTING_DATES_DESC'
        }
        try:
            r = requests.get(url, params=params, headers={'Accept': 'application/json'}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                reqs = data.get('items', [{}])[0].get('requisitionList', [])
                for req in reqs:
                    job_id = str(req.get('Id', ''))
                    if job_id and job_id not in seen_ids:
                        seen_ids.add(job_id)
                        title = req.get('Title', 'Unknown')
                        location = req.get('PrimaryLocation', '')
                        # Filter for Cincinnati area
                        if any(loc in location for loc in ['Blue Ash', 'Cincinnati', 'OH', 'Ohio', 'Remote']):
                            jobs.append({
                                'title': title,
                                'url': f'https://jobs.kroger.com/kroger/job/{job_id}',
                                'job_id': job_id,
                                'location': location,
                                'company': 'Kroger',
                                'ats': 'Oracle HCM'
                            })
        except Exception as e:
            print(f"  ⚠️ Kroger API error for '{keyword}': {e}")

    return jobs


def fetch_western_southern_api() -> List[Dict]:
    """Try iCIMS API for Western & Southern."""
    import requests

    # iCIMS uses a JSON endpoint for search
    jobs = []
    base_url = 'https://careers-westernsouthern.icims.com'

    # Try direct API call
    api_url = f'{base_url}/jobs/search'
    params = {'ss': 1, 'searchKeyword': 'engineer', 'searchLocation': '', 'mobile': 'false'}
    headers = {
        'Accept': 'application/json, text/html',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
    }

    try:
        r = requests.get(api_url, params=params, headers=headers, timeout=15)
        # iCIMS returns HTML, need to parse it
        if r.status_code == 200:
            html = r.text
            # Look for job cards in the HTML
            import re
            pattern = r'href="(/jobs/(\d+)/[^"]+)"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, html, re.IGNORECASE)
            seen = set()
            for url_path, job_id, title in matches:
                title = title.strip()
                if job_id not in seen and len(title) > 5:
                    seen.add(job_id)
                    jobs.append({
                        'title': title,
                        'url': f'{base_url}{url_path}',
                        'job_id': job_id,
                        'company': 'Western & Southern',
                        'ats': 'iCIMS'
                    })
    except Exception as e:
        print(f"  ⚠️ W&S API error: {e}")

    return jobs


async def scrape_company(company: str, config: Dict) -> List[Dict]:
    """Scrape a single company's career page."""

    # Use direct API for specific companies
    if company == "Kroger":
        print(f"  → {company} (Oracle HCM API)...", end=" ", flush=True)
        jobs = fetch_kroger_api()
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    if company == "Western_Southern":
        print(f"  → {company} (iCIMS API)...", end=" ", flush=True)
        jobs = fetch_western_southern_api()
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    url = config["url"]
    base_url = "/".join(url.split("/")[:3])  # Extract base domain

    print(f"  → {company} ({config['ats']})...", end=" ", flush=True)

    html = await fetch_page_html(url)
    if not html:
        print("❌ Failed to load")
        return []

    jobs = extract_jobs_with_llm(html, company, base_url)
    print(f"Found {len(jobs)} jobs")

    # Add metadata
    for job in jobs:
        job["company"] = company
        job["ats"] = config["ats"]
        job["priority"] = config.get("priority", "MEDIUM")
        job["scraped_at"] = datetime.now().isoformat()

    return jobs


async def run_custom_scraper(targets: List[str] = None):
    """Main scraper loop for non-Workday sites."""
    print("=" * 60)
    print("🔧 CUSTOM SCRAPER - LLM-Assisted Job Extraction")
    print("=" * 60)

    state = load_state()
    all_jobs = []
    new_jobs = 0

    # Filter targets if specified
    companies = targets if targets else list(NON_WORKDAY_TARGETS.keys())

    print(f"\n🔍 Scraping {len(companies)} non-Workday sites...")

    for company in companies:
        if company not in NON_WORKDAY_TARGETS:
            print(f"  ⚠️ Unknown company: {company}")
            continue

        config = NON_WORKDAY_TARGETS[company]

        # Skip manual check sites
        if config.get("manual_check"):
            print(f"  → {company} (MANUAL CHECK) - {config.get('note', '')}")
            continue

        jobs = await scrape_company(company, config)

        for job in jobs:
            url = job.get("url", "")
            title = job.get("title", "")
            if url and url not in state.get("seen_jobs", {}) and not should_exclude_title(title):
                state.setdefault("seen_jobs", {})[url] = datetime.now().isoformat()
                state.setdefault("jobs", {})[url] = job
                new_jobs += 1

        all_jobs.extend(jobs)

    save_state(state)

    print(f"\n📊 Results:")
    print(f"   Total jobs found: {len(all_jobs)}")
    print(f"   New jobs: {new_jobs}")
    print(f"   ✅ State updated!")

    if new_jobs > 0:
        print(f"\n🆕 NEW JOBS FROM CUSTOM SCRAPER:")
        for job in all_jobs[:10]:
            title = job.get("title", "Unknown")[:60]
            company = job.get("company", "Unknown")
            print(f"   • {company}: {title}")

    return all_jobs


def main():
    """CLI interface for custom scraper."""
    import argparse

    parser = argparse.ArgumentParser(description="LLM-Assisted Custom Scraper")
    parser.add_argument("--targets", nargs="+", help="Specific companies to scrape")
    parser.add_argument("--list", action="store_true", help="List available targets")
    parser.add_argument("--test", help="Test single company")

    args = parser.parse_args()

    if args.list:
        print("\n📋 Available Non-Workday Targets:")
        print("-" * 60)
        for name, cfg in NON_WORKDAY_TARGETS.items():
            print(f"  {name:20} | {cfg['ats']:20} | {cfg['priority']}")
            print(f"    └─ {cfg['url'][:60]}...")
        print()
        return

    targets = [args.test] if args.test else args.targets
    asyncio.run(run_custom_scraper(targets))


if __name__ == "__main__":
    main()

