"""
USAJobs API Scraper - Wright-Patterson AFB and Dayton area federal jobs.

Requires API key from https://developer.usajobs.gov/APIRequest/Index
Add to config.json as "usajobs_api_key" and "usajobs_email"
"""
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from utils.state import load_config, load_state, save_state
from utils.filters import should_keep_job

# IT-related job category codes (2200 series)
IT_JOB_CATEGORIES = [
    "2210",  # IT Management
    "2210",  # IT Specialist
    "1550",  # Computer Scientist
]


def search_usajobs(
    api_key: str,
    email: str,
    location: str = "Dayton, Ohio",
    radius: int = 50,
    keywords: str = "Software Engineer Developer IT",
    results_per_page: int = 50,
    telemetry: dict[str, Any] | None = None,
) -> List[Dict]:
    """Search USAJobs API for IT positions near specified location."""
    
    base_url = "https://data.usajobs.gov/api/search"
    
    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": email,
        "Authorization-Key": api_key,
    }
    
    params = {
        "LocationName": location,
        "Radius": radius,
        "Keyword": keywords,
        "ResultsPerPage": results_per_page,
        "WhoMayApply": "public",  # Open to all US citizens
        "Fields": "full",
    }
    
    try:
        response = requests.get(base_url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("SearchResult", {})
        count = results.get("SearchResultCount", 0)
        items = results.get("SearchResultItems", [])
        
        print(f"  Found {count} jobs (showing {len(items)})")
        
        jobs = []
        for item in items:
            desc = item.get("MatchedObjectDescriptor", {})
            
            # Get salary info
            salary_info = desc.get("PositionRemuneration", [{}])[0]
            salary_min = salary_info.get("MinimumRange", "")
            salary_max = salary_info.get("MaximumRange", "")
            
            # Get location
            locations = desc.get("PositionLocation", [])
            location_str = ", ".join(loc.get("LocationName", "") for loc in locations)
            
            job = {
                "title": desc.get("PositionTitle", ""),
                "url": desc.get("PositionURI", ""),
                "apply_url": desc.get("ApplyURI", [""])[0],
                "company": desc.get("OrganizationName", "Federal Government"),
                "department": desc.get("DepartmentName", ""),
                "location": location_str,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "close_date": desc.get("ApplicationCloseDate", ""),
                "job_id": item.get("MatchedObjectId", ""),
                "ats": "USAJobs",
                "scraped_at": datetime.now().isoformat(),
            }
            
            # Add job description if available
            details = desc.get("UserArea", {}).get("Details", {})
            job["description"] = details.get("JobSummary", "")
            job["requirements"] = details.get("Requirements", "")

            if not should_keep_job(
                job["title"],
                location=location_str,
                description=f"{job['description']}\n{job['requirements']}",
                telemetry=telemetry,
                telemetry_source="usajobs",
            ):
                continue
            
            jobs.append(job)
        
        return jobs
        
    except requests.exceptions.RequestException as e:
        print(f"  ⚠️ API Error: {e}")
        return []


def run_usajobs_scraper(telemetry: dict[str, Any] | None = None):
    """Main scraper entry point."""
    print("=" * 60)
    print("🏛️  USAJOBS SCRAPER - Federal IT Positions")
    print("=" * 60)
    
    config = load_config()
    api_key = config.get("usajobs_api_key", "")
    email = config.get("usajobs_email", "")
    
    if not api_key or not email:
        print("\n⚠️  Missing USAJobs API credentials!")
        print("   1. Register at: https://developer.usajobs.gov/APIRequest/Index")
        print("   2. Add to config.json:")
        print('      "usajobs_api_key": "YOUR_API_KEY",')
        print('      "usajobs_email": "your@email.com"')
        return
    
    state = load_state()
    new_jobs = 0
    
    # Search locations
    searches = [
        {
            "location": "Cincinnati, Ohio",
            "radius": 35,
            "keywords": "DevOps Cloud Infrastructure Site Reliability Azure IT",
        },
    ]
    
    all_jobs = []
    for search in searches:
        print(f"\n🔍 Searching {search['location']} (radius: {search['radius']} mi)...")
        jobs = search_usajobs(api_key, email, telemetry=telemetry, **search)
        all_jobs.extend(jobs)
    
    # Dedupe and save
    for job in all_jobs:
        url = job.get("url", "")
        if url and url not in state.get("seen_jobs", {}):
            state.setdefault("seen_jobs", {})[url] = datetime.now().isoformat()
            state.setdefault("jobs", {})[url] = job
            new_jobs += 1
    
    save_state(state)
    
    print(f"\n📊 Results:")
    print(f"   Total jobs found: {len(all_jobs)}")
    print(f"   New jobs: {new_jobs}")
    
    if new_jobs > 0:
        print(f"\n🆕 NEW FEDERAL JOBS:")
        for job in all_jobs[:10]:
            if job.get("url") in state.get("seen_jobs", {}):
                salary = f"${job.get('salary_min', '?')}-${job.get('salary_max', '?')}"
                print(f"   • {job['company']}: {job['title']} ({salary})")


if __name__ == "__main__":
    run_usajobs_scraper()

