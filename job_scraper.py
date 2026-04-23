#!/usr/bin/env python3
"""
Job Description Scraper - Fetches full job descriptions from ATS platforms
Supports Workday, Greenhouse, Lever, Ashby, and Workable
"""

import json
import re
import requests
from bs4 import BeautifulSoup
from html import unescape
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from utils.contacts import (
    extract_contact_emails_from_links,
    extract_contact_emails_from_text,
    merge_contact_emails,
    primary_contact_email,
)
from utils.job_identity import build_workday_api_url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/json",
}


def _extract_ashby_app_data(html_text: str) -> dict[str, Any]:
    """Extract Ashby's embedded app payload from a board or posting page."""
    marker = "window.__appData = "
    marker_index = html_text.find(marker)
    if marker_index == -1:
        return {}

    start_index = marker_index + len(marker)
    brace_depth = 0
    end_index: int | None = None
    in_string = False
    is_escaped = False

    for index, character in enumerate(html_text[start_index:], start=start_index):
        if in_string:
            if is_escaped:
                is_escaped = False
            elif character == "\\":
                is_escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
            continue

        if character == "{":
            brace_depth += 1
            continue

        if character == "}":
            brace_depth -= 1
            if brace_depth == 0:
                end_index = index + 1
                break

    if end_index is None:
        return {}

    try:
        payload = json.loads(html_text[start_index:end_index])
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def _build_contact_fields(page_text: str, mailto_links: list[str] | None = None) -> Dict:
    contact_emails = merge_contact_emails(
        extract_contact_emails_from_text(page_text),
        extract_contact_emails_from_links(mailto_links or []),
    )
    return {
        "contact_email": primary_contact_email(contact_emails),
        "contact_emails": contact_emails,
    }


def _iter_jobposting_items(payload: Any) -> list[dict[str, Any]]:
    """Flatten structured-data payloads down to JobPosting dictionaries."""
    if isinstance(payload, list):
        items: list[dict[str, Any]] = []
        for entry in payload:
            items.extend(_iter_jobposting_items(entry))
        return items
    if not isinstance(payload, dict):
        return []

    items: list[dict[str, Any]] = []
    if str(payload.get("@type", "")).casefold() == "jobposting":
        items.append(payload)
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for entry in graph:
            items.extend(_iter_jobposting_items(entry))
    return items


def _extract_jobposting_structured_data(soup: BeautifulSoup) -> dict[str, Any] | None:
    """Return the first JobPosting structured-data object on the page."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        script_text = script.string or script.get_text(strip=True)
        if not script_text:
            continue
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError:
            continue
        jobposting_items = _iter_jobposting_items(payload)
        if jobposting_items:
            return jobposting_items[0]
    return None


def _format_jobposting_location(jobposting: dict[str, Any]) -> str:
    """Build a readable location string from JobPosting structured data."""
    job_location = jobposting.get("jobLocation")
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else {}
    address = job_location.get("address", {}) if isinstance(job_location, dict) else {}
    if not isinstance(address, dict):
        return ""

    location_parts: list[str] = []
    for key in ("addressLocality", "addressRegion", "postalCode", "addressCountry"):
        value = str(address.get(key, "")).strip()
        if value and value not in location_parts:
            location_parts.append(value)
    return ", ".join(location_parts)


def fetch_workday_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Fetch job description from Workday portal."""
    try:
        api_url = build_workday_api_url(url)
        if not api_url:
            return None
        
        response = requests.get(api_url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        job_posting = data.get("jobPostingInfo", {})
        page_text = "\n".join(
            str(value)
            for value in (
                job_posting.get("title", ""),
                job_posting.get("jobDescription", ""),
                job_posting.get("location", ""),
            )
            if value
        )
        
        return {
            "title": job_posting.get("title", ""),
            "description": job_posting.get("jobDescription", ""),
            "location": job_posting.get("location", ""),
            "posted_date": job_posting.get("postedOn", ""),
            "job_type": job_posting.get("timeType", ""),
            "requirements": extract_requirements(job_posting.get("jobDescription", "")),
            "source": "workday",
            **_build_contact_fields(page_text),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Workday fetch error: {e}")
        return None


def fetch_greenhouse_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Fetch job description from Greenhouse."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.find('h1', class_='app-title') or soup.find('h1')
        description = soup.find('div', id='content') or soup.find('div', class_='content')
        location = soup.find('div', class_='location')
        
        desc_text = description.get_text(separator='\n') if description else ""
        page_text = soup.get_text(separator='\n')
        mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]
        
        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text,
            "location": location.get_text(strip=True) if location else "",
            "requirements": extract_requirements(desc_text),
            "source": "greenhouse",
            **_build_contact_fields(page_text, mailto_links),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Greenhouse fetch error: {e}")
        return None


def fetch_lever_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Fetch job description from Lever."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.find('h2') or soup.find('h1')
        content = soup.find('div', class_='section-wrapper') or soup.find('div', class_='content')
        location = soup.find('div', class_='location')
        
        desc_text = content.get_text(separator='\n') if content else ""
        page_text = soup.get_text(separator='\n')
        mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]
        
        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text,
            "location": location.get_text(strip=True) if location else "",
            "requirements": extract_requirements(desc_text),
            "source": "lever",
            **_build_contact_fields(page_text, mailto_links),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Lever fetch error: {e}")
        return None


def fetch_ashby_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Fetch job description from Ashby."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        app_data = _extract_ashby_app_data(response.text)
        posting = app_data.get("posting", {}) if isinstance(app_data, dict) else {}

        if not isinstance(posting, dict) or not posting:
            return fetch_generic_description(url, log_warnings=log_warnings)

        description = str(
            posting.get("descriptionPlainText")
            or BeautifulSoup(str(posting.get("descriptionHtml", "")), 'html.parser').get_text(separator='\n')
        ).strip()
        location = str(
            posting.get("locationExternalName")
            or posting.get("locationName")
            or ""
        ).strip()
        workplace_type = str(posting.get("workplaceType", "")).strip()
        if workplace_type and workplace_type.casefold() not in location.casefold():
            location = f"{location} ({workplace_type})" if location else workplace_type

        page_text = soup.get_text(separator='\n')
        mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]

        return {
            "title": str(posting.get("title", "")).strip(),
            "description": description,
            "location": location,
            "posted_date": str(posting.get("updatedAt", "")).strip(),
            "job_type": str(posting.get("employmentType", "")).strip(),
            "requirements": extract_requirements(description),
            "source": "ashby",
            **_build_contact_fields(page_text, mailto_links),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Ashby fetch error: {e}")
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


def fetch_jibe_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
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
        page_text = soup.get_text(separator='\n')
        mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]

        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text,
            "location": location.get_text(strip=True) if location else "",
            "requirements": extract_requirements(desc_text),
            "source": "jibe",
            **_build_contact_fields(page_text, mailto_links),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Jibe fetch error: {e}")
        return None


def fetch_phenom_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Fetch job description from a Phenom-hosted careers page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        structured_job = _extract_jobposting_structured_data(soup)
        if not structured_job:
            return fetch_generic_description(url, log_warnings=log_warnings)

        title = str(structured_job.get("title", "")).strip()
        description_html = unescape(str(structured_job.get("description", "")))
        description = BeautifulSoup(description_html, 'html.parser').get_text(separator='\n').strip()
        location = _format_jobposting_location(structured_job)
        employment_type = structured_job.get("employmentType", "")
        if isinstance(employment_type, list):
            employment_type = ", ".join(str(value).strip() for value in employment_type if str(value).strip())

        page_text = soup.get_text(separator='\n')
        mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]

        return {
            "title": title,
            "description": description,
            "location": location,
            "posted_date": str(structured_job.get("datePosted", "")).strip(),
            "job_type": str(employment_type).strip(),
            "requirements": extract_requirements(description),
            "source": "phenom",
            **_build_contact_fields(page_text, mailto_links),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Phenom fetch error: {e}")
        return None


def fetch_generic_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Generic job description fetcher for unknown ATS platforms."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        structured_job = _extract_jobposting_structured_data(soup)
        if structured_job:
            title = str(structured_job.get("title", "")).strip()
            description_html = unescape(str(structured_job.get("description", "")))
            description = BeautifulSoup(description_html, 'html.parser').get_text(separator='\n').strip()
            page_text = soup.get_text(separator='\n')
            mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]
            employment_type = structured_job.get("employmentType", "")
            if isinstance(employment_type, list):
                employment_type = ", ".join(
                    str(value).strip() for value in employment_type if str(value).strip()
                )

            return {
                "title": title,
                "description": description,
                "location": _format_jobposting_location(structured_job),
                "posted_date": str(structured_job.get("datePosted", "")).strip(),
                "job_type": str(employment_type).strip(),
                "requirements": extract_requirements(description),
                "source": "generic",
                **_build_contact_fields(page_text, mailto_links),
            }

        # Try common patterns
        title = soup.find('h1') or soup.find('title')
        description = (
            soup.find('div', class_=lambda x: x and 'description' in x.lower() if x else False) or
            soup.find('div', class_=lambda x: x and 'content' in x.lower() if x else False) or
            soup.find('main') or
            soup.find('article')
        )

        desc_text = description.get_text(separator='\n') if description else ""
        page_text = soup.get_text(separator='\n')
        mailto_links = [link.get('href', '') for link in soup.select('a[href^="mailto:"]')]

        return {
            "title": title.get_text(strip=True) if title else "",
            "description": desc_text[:5000],  # Limit size
            "location": "",
            "requirements": extract_requirements(desc_text),
            "source": "generic",
            **_build_contact_fields(page_text, mailto_links),
        }
    except Exception as e:
        if log_warnings:
            print(f"  [WARN] Generic fetch error: {e}")
        return None


def fetch_job_description(url: str, *, log_warnings: bool = True) -> Optional[Dict]:
    """Fetch job description from any supported ATS platform."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if 'myworkdayjobs.com' in domain:
        return fetch_workday_description(url, log_warnings=log_warnings)
    elif 'greenhouse.io' in domain:
        return fetch_greenhouse_description(url, log_warnings=log_warnings)
    elif 'lever.co' in domain:
        return fetch_lever_description(url, log_warnings=log_warnings)
    elif 'ashbyhq.com' in domain:
        return fetch_ashby_description(url, log_warnings=log_warnings)
    elif 'medpace.com' in domain or 'jibe' in domain:
        return fetch_jibe_description(url, log_warnings=log_warnings)
    elif 'careers.atsginc.com' in domain:
        return fetch_phenom_description(url, log_warnings=log_warnings)
    else:
        # Try generic fallback
        return fetch_generic_description(url, log_warnings=log_warnings)


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

