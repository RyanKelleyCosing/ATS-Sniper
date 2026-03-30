#!/usr/bin/env python3
"""
Job Description Scraper - Fetches full job descriptions from ATS platforms
Supports Workday, Greenhouse, Lever, Ashby, and Workable
"""

import re
import json
import requests
from bs4 import BeautifulSoup
from typing import Dict, Optional
from urllib.parse import urlparse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/json",
}


def fetch_workday_description(url: str) -> Optional[Dict]:
    """Fetch job description from Workday portal."""
    try:
        # Convert external URL to API endpoint
        # URL format: https://pg.wd5.myworkdayjobs.com/en-US/1000/job/LOCATION/TITLE_ID
        # API format: https://pg.wd5.myworkdayjobs.com/wday/cxs/pg/1000/job/LOCATION/TITLE_ID

        # Match both /en-US/ and /tenant/ patterns
        match = re.search(r'https://([^/]+)/en-US/([^/]+)/job/(.+)', url)
        if not match:
            # Try alternate pattern without en-US
            match = re.search(r'https://([^/]+)/([^/]+)/([^/]+)/job/(.+)', url)
            if not match:
                return None
            host, tenant, site, job_path = match.groups()
        else:
            host, site, job_path = match.groups()
            tenant = host.split('.')[0]

        api_url = f"https://{host}/wday/cxs/{tenant}/{site}/job/{job_path}"
        
        response = requests.get(api_url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        job_posting = data.get("jobPostingInfo", {})
        
        return {
            "title": job_posting.get("title", ""),
            "description": job_posting.get("jobDescription", ""),
            "location": job_posting.get("location", ""),
            "posted_date": job_posting.get("postedOn", ""),
            "job_type": job_posting.get("timeType", ""),
            "requirements": extract_requirements(job_posting.get("jobDescription", "")),
            "source": "workday"
        }
    except Exception as e:
        print(f"  ⚠️ Workday fetch error: {e}")
        return None


def fetch_greenhouse_description(url: str) -> Optional[Dict]:
    """Fetch job description from Greenhouse."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.find('h1', class_='app-title') or soup.find('h1')
        description = soup.find('div', id='content') or soup.find('div', class_='content')
        location = soup.find('div', class_='location')
        
        desc_text = description.get_text(separator='\n') if description else ""
        
        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text,
            "location": location.get_text(strip=True) if location else "",
            "requirements": extract_requirements(desc_text),
            "source": "greenhouse"
        }
    except Exception as e:
        print(f"  ⚠️ Greenhouse fetch error: {e}")
        return None


def fetch_lever_description(url: str) -> Optional[Dict]:
    """Fetch job description from Lever."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.find('h2') or soup.find('h1')
        content = soup.find('div', class_='section-wrapper') or soup.find('div', class_='content')
        location = soup.find('div', class_='location')
        
        desc_text = content.get_text(separator='\n') if content else ""
        
        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text,
            "location": location.get_text(strip=True) if location else "",
            "requirements": extract_requirements(desc_text),
            "source": "lever"
        }
    except Exception as e:
        print(f"  ⚠️ Lever fetch error: {e}")
        return None


def extract_requirements(text: str) -> Dict:
    """Extract skills, years of experience, and certifications from job description."""
    requirements = {
        "skills": [],
        "years_experience": None,
        "certifications": [],
        "keywords": []
    }
    
    # Common tech skills
    skill_patterns = [
        r'\b(AWS|Azure|GCP|Kubernetes|K8s|Docker|Terraform|Ansible|Jenkins|CI/CD)\b',
        r'\b(Python|Java|Go|Golang|JavaScript|TypeScript|Rust|C\+\+)\b',
        r'\b(Linux|Unix|Windows Server|SQL|PostgreSQL|MySQL|MongoDB)\b',
        r'\b(DevOps|SRE|MLOps|GitOps|Agile|Scrum)\b',
    ]
    
    for pattern in skill_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        requirements["skills"].extend([m for m in matches if m not in requirements["skills"]])
    
    # Years of experience
    exp_match = re.search(r'(\d+)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*experience', text, re.IGNORECASE)
    if exp_match:
        requirements["years_experience"] = int(exp_match.group(1))
    
    # Certifications
    cert_patterns = [
        r'(AWS Certified[^,\.]+)',
        r'(Azure[^,\.]*Certif[^,\.]+)',
        r'(CKA|CKAD|CKS)',
        r'(PMP|CISSP|CCNA)',
    ]
    
    for pattern in cert_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        requirements["certifications"].extend(matches)
    
    return requirements


def fetch_jibe_description(url: str) -> Optional[Dict]:
    """Fetch job description from Jibe/iCIMS (Medpace, etc.)."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Jibe uses schema.org markup
        title = soup.find('h1', {'itemprop': 'title'}) or soup.find('h1')
        description = soup.find('div', {'itemprop': 'description'}) or soup.find('div', class_='job-description')
        location = soup.find('span', {'itemprop': 'addressLocality'}) or soup.find('div', class_='job-location')

        # Fallback to any main content
        if not description:
            description = soup.find('div', class_='jibe-container') or soup.find('main')

        desc_text = description.get_text(separator='\n') if description else ""

        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text,
            "location": location.get_text(strip=True) if location else "",
            "requirements": extract_requirements(desc_text),
            "source": "jibe"
        }
    except Exception as e:
        print(f"  ⚠️ Jibe fetch error: {e}")
        return None


def fetch_generic_description(url: str) -> Optional[Dict]:
    """Generic job description fetcher for unknown ATS platforms."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Try common patterns
        title = soup.find('h1') or soup.find('title')
        description = (
            soup.find('div', class_=lambda x: x and 'description' in x.lower() if x else False) or
            soup.find('div', class_=lambda x: x and 'content' in x.lower() if x else False) or
            soup.find('main') or
            soup.find('article')
        )

        desc_text = description.get_text(separator='\n') if description else ""

        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text[:5000],  # Limit size
            "location": "",
            "requirements": extract_requirements(desc_text),
            "source": "generic"
        }
    except Exception as e:
        print(f"  ⚠️ Generic fetch error: {e}")
        return None


def fetch_job_description(url: str) -> Optional[Dict]:
    """Fetch job description from any supported ATS platform."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if 'myworkdayjobs.com' in domain:
        return fetch_workday_description(url)
    elif 'greenhouse.io' in domain:
        return fetch_greenhouse_description(url)
    elif 'lever.co' in domain:
        return fetch_lever_description(url)
    elif 'medpace.com' in domain or 'jibe' in domain:
        return fetch_jibe_description(url)
    else:
        # Try generic fallback
        return fetch_generic_description(url)


if __name__ == "__main__":
    # Test with a sample URL
    test_urls = [
        "https://pg.wd5.myworkdayjobs.com/en-US/1000/job/Senior-Data-Engineer_R000147123",
    ]
    
    for url in test_urls:
        print(f"\n📄 Fetching: {url}")
        result = fetch_job_description(url)
        if result:
            print(f"   Title: {result['title']}")
            print(f"   Skills: {result['requirements']['skills'][:5]}")

