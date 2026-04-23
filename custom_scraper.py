"""
Custom Scraper v2 - mixed extraction for non-Workday ATS systems.

Uses vendor-specific APIs or feeds where available and falls back to
Playwright + GPT-4o-mini for harder sites.
"""

import asyncio
from copy import deepcopy
import json
import re
from datetime import datetime
from html import unescape
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from utils.state import load_config, load_state, save_state
from utils.filters import should_keep_job
from utils.job_identity import ensure_job_identity_index, find_existing_job_url, store_job_identity_record


DEFAULT_REQUEST_HEADERS = {
    "Accept": "application/json, application/rss+xml, text/html;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}
ACTIVATE_PAGE_SIZE = 100
PHENOM_PAGE_SIZE = 10
ACTIVATE_SEARCH_TERMS = (
    "information technology",
    "cloud",
    "devops",
    "platform",
    "infrastructure",
    "security",
    "identity",
    "access management",
    "systems administrator",
    "network",
)
UC_SEARCH_TERMS = ACTIVATE_SEARCH_TERMS + ("digital technology solutions",)
NKU_SEARCH_TERMS = (
    "security",
    "information technology",
    "identity",
    "access management",
    "systems administrator",
    "cloud",
    "devops",
)


def build_custom_telemetry_source(config: Dict) -> str:
    """Return a stable telemetry source name for a custom scraper target."""
    source_name = str(config.get("scraper") or config.get("ats") or "custom").strip().casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", source_name).strip("_")
    return f"custom_{normalized or 'custom'}"


def get_custom_target_ats_name(config: Dict) -> str:
    """Return a safe ATS label for custom targets and config-only overrides."""
    return str(config.get("ats") or config.get("type") or "Custom").strip() or "Custom"

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
        "url": "https://careers.trihealth.com/search/searchjobs",
        "ats": "Activate",
        "scraper": "activate_api",
        "search_api": "https://careers.trihealth.com/Search/SearchResults",
        "search_keywords": ACTIVATE_SEARCH_TERMS,
        "priority": "LOW",
        "note": "Healthcare IT"
    },
    "Western_Southern": {
        "url": "https://careers-westernsouthern.icims.com/jobs/search?ss=1&searchKeyword=engineer",
        "ats": "iCIMS",
        "priority": "LOW",
        "display_name": "Western & Southern",
        "note": "Handled by the dedicated iCIMS scraper in the main pipeline",
        "managed_by_pipeline": "iCIMS scraper"
    },
    "University_of_Cincinnati": {
        "url": "https://jobs.uc.edu/search/?createNewAlert=false&locationsearch=Cincinnati",
        "ats": "SuccessFactors RSS",
        "display_name": "University of Cincinnati",
        "scraper": "successfactors_rss",
        "rss_url": "https://jobs.uc.edu/services/rss/job/",
        "search_keywords": UC_SEARCH_TERMS,
        "location_search": "Cincinnati",
        "priority": "MEDIUM",
        "note": "IT/Azure/DevOps roles in academic setting"
    },
    "NKU": {
        "url": "https://jobs.nku.edu/postings/search",
        "ats": "PeopleAdmin Atom",
        "display_name": "Northern Kentucky University",
        "scraper": "peopleadmin_atom",
        "atom_url": "https://jobs.nku.edu/postings/search.atom",
        "search_keywords": NKU_SEARCH_TERMS,
        "priority": "MEDIUM",
        "note": "IT/security roles via the NKU applicant portal"
    },
    "UC_Health": {
        "url": "https://careers.uchealth.com/search/searchjobs",
        "ats": "Activate",
        "display_name": "UC Health",
        "scraper": "activate_api",
        "search_api": "https://careers.uchealth.com/Search/SearchResults",
        "search_keywords": ACTIVATE_SEARCH_TERMS,
        "priority": "MEDIUM",
        "note": "Healthcare IT roles via Activate search results"
    },
    "Christ_Hospital": {
        "url": "https://careers.thechristhospital.com/search/searchjobs",
        "ats": "Activate",
        "display_name": "The Christ Hospital",
        "scraper": "activate_api",
        "search_api": "https://careers.thechristhospital.com/Search/SearchResults",
        "search_keywords": ACTIVATE_SEARCH_TERMS,
        "priority": "LOW",
        "note": "Regional healthcare IT roles"
    },
    "Cardinal_Health": {
        "url": "https://jobs.cardinalhealth.com/search/jobs",
        "ats": "Activate",
        "display_name": "Cardinal Health",
        "scraper": "activate_api",
        "search_api": "https://jobs.cardinalhealth.com/Search/SearchResults",
        "search_keywords": ACTIVATE_SEARCH_TERMS + (
            "data protection",
            "resiliency",
            "identity and access",
            "ai",
        ),
        "priority": "HIGH",
        "note": "Activate SearchResults API for remote US identity, cloud, security, and adjacent platform roles"
    },
    "ATSG": {
        "url": "https://careers.atsginc.com/us/en/search-results",
        "ats": "Phenom",
        "display_name": "ATSG",
        "scraper": "phenom_search",
        "search_results_url": "https://careers.atsginc.com/us/en/search-results",
        "page_size": PHENOM_PAGE_SIZE,
        "priority": "HIGH",
        "note": "Phenom-hosted ATSG board for regional security, IT, and systems roles"
    },
    "City_of_Cincinnati": {
        "url": "https://www.governmentjobs.com/careers/cincinnati",
        "ats": "NEOGOV",
        "display_name": "City of Cincinnati",
        "scraper": "neogov_rendered",
        "location": "Cincinnati, OH",
        "max_pages": 9,
        "priority": "LOW",
        "note": "Municipal IT and infrastructure roles via the NEOGOV careers board"
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


def normalize_target_key(value: str) -> str:
    """Normalize company keys across code defaults and config.json overrides."""
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def infer_custom_target_settings(target_key: str, target_config: Dict) -> Dict:
    """Infer scraper-specific settings from lightweight config entries when possible."""
    normalized_config = dict(target_config)
    target_type = str(normalized_config.get("type", "")).strip().casefold()
    target_url = str(normalized_config.get("url", "")).strip()
    split_url = urlsplit(target_url)
    target_root = f"{split_url.scheme}://{split_url.netloc}" if split_url.scheme and split_url.netloc else ""

    if normalized_config.get("name") and not normalized_config.get("display_name"):
        normalized_config["display_name"] = str(normalized_config["name"])

    if target_type in {"", "custom"}:
        normalized_config.setdefault("ats", "Custom")

    if target_type == "activate":
        normalized_config.setdefault("ats", "Activate")
        normalized_config.setdefault("scraper", "activate_api")
        if target_root:
            normalized_config.setdefault("search_api", f"{target_root}/Search/SearchResults")
        normalized_config.setdefault("search_keywords", ACTIVATE_SEARCH_TERMS)
    elif target_type == "peopleadmin_atom":
        normalized_config.setdefault("ats", "PeopleAdmin Atom")
        normalized_config.setdefault("scraper", "peopleadmin_atom")
        if target_root:
            normalized_config.setdefault("atom_url", f"{target_root}/postings/search.atom")
    elif target_type == "successfactors_rss":
        normalized_config.setdefault("ats", "SuccessFactors RSS")
        normalized_config.setdefault("scraper", "successfactors_rss")
        if target_root:
            normalized_config.setdefault("rss_url", f"{target_root}/services/rss/job/")
        normalized_config.setdefault("location_search", "Cincinnati")
        normalized_config.setdefault("search_keywords", UC_SEARCH_TERMS)
    elif target_type == "neogov_rendered":
        normalized_config.setdefault("ats", "NEOGOV")
        normalized_config.setdefault("scraper", "neogov_rendered")
    elif target_type == "phenom":
        normalized_config.setdefault("ats", "Phenom")
        normalized_config.setdefault("scraper", "phenom_search")
        normalized_config.setdefault("search_results_url", target_url)
        normalized_config.setdefault("page_size", PHENOM_PAGE_SIZE)

    if target_key == "Cardinal_Health":
        normalized_config.setdefault("ats", "Activate")
        normalized_config.setdefault("scraper", "activate_api")
        normalized_config.setdefault("search_api", "https://jobs.cardinalhealth.com/Search/SearchResults")
        normalized_config.setdefault(
            "search_keywords",
            ACTIVATE_SEARCH_TERMS + ("data protection", "resiliency", "identity and access", "ai"),
        )

    if target_key == "ATSG":
        normalized_config.setdefault("ats", "Phenom")
        normalized_config.setdefault("scraper", "phenom_search")
        normalized_config.setdefault("search_results_url", "https://careers.atsginc.com/us/en/search-results")
        normalized_config.setdefault("page_size", PHENOM_PAGE_SIZE)

    return normalized_config


def get_non_workday_targets() -> Dict[str, Dict]:
    """Return custom-scraper targets merged with any config.json overrides."""
    targets = {key: deepcopy(value) for key, value in NON_WORKDAY_TARGETS.items()}
    target_key_lookup = {normalize_target_key(key): key for key in targets}

    try:
        config = load_config()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return targets

    configured_targets = config.get("custom_ats", {})
    if not isinstance(configured_targets, dict):
        return targets

    for configured_key, configured_target in configured_targets.items():
        if not isinstance(configured_target, dict):
            continue

        normalized_key = normalize_target_key(configured_key)
        target_key = target_key_lookup.get(normalized_key, configured_key)
        merged_target = deepcopy(targets.get(target_key, {}))
        merged_target.update(configured_target)
        targets[target_key] = infer_custom_target_settings(target_key, merged_target)
        target_key_lookup[normalized_key] = target_key

    return targets


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


def strip_html_fragment(value: str | None) -> str:
    """Remove markup and normalize whitespace from HTML fragments."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def slugify_job_title(title: str) -> str:
    """Convert a job title into the slug format used by Activate pages."""
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")


def canonicalize_job_url(url: str) -> str:
    """Strip tracking parameters from job URLs for stable deduplication."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def slugify_phenom_job_title(title: str) -> str:
    """Convert a Phenom title into the slug format used in ATSG job URLs."""
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")


def normalize_page_text(text: str) -> str:
    """Collapse freeform page text into a compact single string."""
    return re.sub(r"\s+", " ", text).strip()


def extract_phenom_ddo(html: str) -> Dict:
    """Extract the inline `phApp.ddo` JSON blob from a Phenom page."""
    match = re.search(r"phApp\.ddo\s*=\s*(\{.*?\})\s*;\s*phApp\.experimentData", html, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_phenom_search_payload(html: str) -> Dict:
    """Extract the search-results payload from a Phenom search page."""
    ddo_payload = extract_phenom_ddo(html)
    search_payload = ddo_payload.get("eagerLoadRefineSearch", {}).get("data", {})
    return search_payload if isinstance(search_payload, dict) else {}


def parse_phenom_search_results(
    html: str,
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse Phenom search-results HTML into ATS Sniper job dictionaries."""
    search_payload = extract_phenom_search_payload(html)
    job_records = search_payload.get("jobs", [])
    if not isinstance(job_records, list):
        return []

    jobs: List[Dict] = []
    seen_urls: set[str] = set()
    detail_base_url = str(config.get("detail_base_url") or config.get("url") or "").strip()
    telemetry_source = build_custom_telemetry_source(config)
    detail_base_url = re.sub(r"/search-results/?$", "", detail_base_url).rstrip("/")

    for job_record in job_records:
        if not isinstance(job_record, dict):
            continue

        raw_title = str(
            job_record.get("ml_title")
            or job_record.get("title")
            or job_record.get("ml_job_parser", {}).get("actual_title")
            or ""
        ).strip()
        title = re.sub(r"\s+", " ", raw_title).strip()
        job_id = str(job_record.get("jobId") or job_record.get("reqId") or "").strip()
        if not title or not job_id:
            continue

        location = re.sub(
            r"\s+",
            " ",
            str(
                job_record.get("location")
                or job_record.get("cityStateCountry")
                or job_record.get("cityState")
                or ""
            ),
        ).strip()
        description = normalize_page_text(
            str(
                job_record.get("descriptionTeaser_keyword")
                or job_record.get("descriptionTeaser")
                or job_record.get("ml_job_parser", {}).get("descriptionTeaser_first200")
                or ""
            )
        )
        if not should_keep_job(
            title,
            location=location,
            description=description,
            telemetry=telemetry,
            telemetry_source=telemetry_source,
        ):
            continue

        job_slug = slugify_phenom_job_title(title)
        detail_url = canonicalize_job_url(
            urljoin(f"{detail_base_url}/", f"job/{job_id}/{job_slug}") if detail_base_url else ""
        )
        if not detail_url or detail_url in seen_urls:
            continue

        seen_urls.add(detail_url)
        jobs.append(
            {
                "title": title,
                "url": detail_url,
                "job_id": job_id,
                "location": location,
                "description": description,
                "posted_date": str(job_record.get("postedDate") or job_record.get("dateCreated") or "").strip(),
                "apply_url": str(job_record.get("applyUrl") or "").strip(),
                "category": str(job_record.get("category") or "").strip(),
                "company": company,
                "ats": config.get("ats", "Phenom"),
            }
        )

    return jobs


def split_rss_title_and_location(raw_title: str) -> tuple[str, str]:
    """Separate a SuccessFactors RSS title from its trailing location text."""
    title = raw_title.strip()
    if title.endswith(")") and " (" in title:
        possible_title, possible_location = title.rsplit(" (", 1)
        possible_location = possible_location[:-1].strip()
        if possible_location:
            return possible_title.strip(), possible_location
    return title, ""


def parse_successfactors_rss_feed(
    rss_text: str,
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse SuccessFactors RSS results into ATS Sniper job dictionaries."""
    jobs: List[Dict] = []
    telemetry_source = build_custom_telemetry_source(config)

    try:
        root = ElementTree.fromstring(rss_text)
    except ElementTree.ParseError:
        return jobs

    for item in root.findall(".//item"):
        raw_title = (item.findtext("title") or "").strip()
        raw_link = (item.findtext("link") or "").strip()
        description = strip_html_fragment(item.findtext("description") or "")
        title, location = split_rss_title_and_location(raw_title)
        url = canonicalize_job_url(raw_link)

        if not title or not url:
            continue

        if not should_keep_job(
            title,
            location=location,
            description=description,
            telemetry=telemetry,
            telemetry_source=telemetry_source,
        ):
            continue

        job_id_match = re.search(r"/(\d+)/?$", url)
        job_id = job_id_match.group(1) if job_id_match else ""
        jobs.append(
            {
                "title": title,
                "url": url,
                "job_id": job_id,
                "location": location,
                "description": description,
                "company": company,
                "ats": config["ats"],
            }
        )

    return jobs


def parse_peopleadmin_atom_feed(feed_text: str) -> List[Dict[str, str]]:
    """Parse a PeopleAdmin Atom feed into a lightweight entry list."""
    soup = BeautifulSoup(feed_text, "xml")
    entries: List[Dict[str, str]] = []

    for entry in soup.find_all("entry"):
        title_node = entry.find("title")
        link_node = entry.find("link")
        summary_node = entry.find("summary")
        updated_node = entry.find("updated")

        title = title_node.get_text(strip=True) if title_node else ""
        url = ""
        if link_node:
            url = link_node.get("href") or link_node.get_text(strip=True)

        if not title or not url:
            continue

        entries.append(
            {
                "title": title,
                "url": canonicalize_job_url(url),
                "summary": summary_node.get_text(" ", strip=True) if summary_node else "",
                "updated": updated_node.get_text(strip=True) if updated_node else "",
            }
        )

    return entries


def extract_peopleadmin_location(page_text: str) -> str:
    """Infer the PeopleAdmin posting location from the rendered page text."""
    lowered_text = page_text.casefold()
    if "highland heights" in lowered_text:
        return "Highland Heights, Kentucky"
    if "northern kentucky / greater cincinnati" in lowered_text:
        return "Northern Kentucky / Greater Cincinnati"
    return ""


def parse_neogov_listing_html(
    html: str,
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse rendered NEOGOV listings into ATS Sniper job dictionaries."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: List[Dict] = []
    base_url = "/".join(config["url"].split("/")[:3])
    fallback_location = config.get("location", "")
    telemetry_source = build_custom_telemetry_source(config)

    for row in soup.select("li.list-item[data-job-id]"):
        anchor = row.select_one("a.item-details-link[href]")
        if anchor is None:
            continue

        title = anchor.get_text(" ", strip=True)
        if not title:
            continue

        description = normalize_page_text(row.get_text(" ", strip=True))
        if not should_keep_job(
            title,
            location=fallback_location,
            description=description,
            telemetry=telemetry,
            telemetry_source=telemetry_source,
        ):
            continue

        href = anchor.get("href", "")
        jobs.append(
            {
                "title": title,
                "url": canonicalize_job_url(urljoin(base_url, href)),
                "job_id": str(row.get("data-job-id") or "").strip(),
                "location": fallback_location,
                "department": anchor.get("data-department-name", ""),
                "description": description,
                "company": company,
                "ats": config["ats"],
            }
        )

    return jobs


def get_neogov_page_count(html: str) -> int:
    """Extract the page count from a rendered NEOGOV listing page."""
    soup = BeautifulSoup(html, "html.parser")
    page_numbers = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        match = re.search(r"page=(\d+)", href, flags=re.IGNORECASE)
        if match:
            page_numbers.append(int(match.group(1)))
    return max(page_numbers, default=1)


def parse_activate_search_results(
    payload: Dict,
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Parse Activate SearchResults JSON into ATS Sniper job dictionaries."""
    jobs: List[Dict] = []
    if not isinstance(payload, dict):
        return jobs

    records = payload.get("Records", [])
    if not isinstance(records, list):
        return jobs

    base_url = "/".join(config["url"].split("/")[:3])
    telemetry_source = build_custom_telemetry_source(config)
    for record in records:
        if not isinstance(record, dict):
            continue

        tracking = record.get("TrackingObject", {})
        if not isinstance(tracking, dict):
            tracking = {}

        title = strip_html_fragment(tracking.get("TitleJson") or record.get("Title"))
        job_id = str(record.get("ID") or "").strip()
        location = strip_html_fragment(
            record.get("CityStateDataAbbrev")
            or record.get("CityStateData")
            or record.get("LocationName")
        )
        workplace_type = strip_html_fragment(record.get("LocationName"))
        description = " ".join(
            part
            for part in (
                strip_html_fragment(record.get("DepartmentName")),
                strip_html_fragment(record.get("ScheduleName")),
                strip_html_fragment(record.get("TypeName")),
            )
            if part
        )

        if not title or not job_id:
            continue

        if not should_keep_job(
            title,
            location=location,
            workplace_type=workplace_type,
            description=description,
            telemetry=telemetry,
            telemetry_source=telemetry_source,
        ):
            continue

        jobs.append(
            {
                "title": title,
                "url": f"{base_url}/search/jobdetails/{slugify_job_title(title)}/{job_id}",
                "job_id": job_id,
                "location": location or workplace_type,
                "workplace_type": workplace_type,
                "department": strip_html_fragment(record.get("DepartmentName")),
                "schedule": strip_html_fragment(record.get("ScheduleName")),
                "description": description,
                "posted_date": strip_html_fragment(record.get("PostedDate"))
                or str(record.get("PostedDateRaw") or ""),
                "company": company,
                "ats": config["ats"],
            }
        )

    return jobs


async def fetch_peopleadmin_posting_details(
    client: httpx.AsyncClient,
    posting_url: str,
) -> Dict[str, str]:
    """Fetch PeopleAdmin posting details used for filtering and enrichment."""
    try:
        response = await client.get(posting_url)
        response.raise_for_status()
    except Exception as exc:
        print(f"  [WARN] PeopleAdmin detail fetch error for '{posting_url}': {exc}")
        return {"location": "", "description": ""}

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text("\n", strip=True)
    normalized_text = normalize_page_text(page_text)
    screening_text = re.sub(
        r"\bnot a remote position\b",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    ).strip()
    return {
        "location": extract_peopleadmin_location(page_text),
        "description": screening_text,
    }


async def fetch_peopleadmin_atom_jobs(
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Fetch PeopleAdmin-hosted jobs through the public Atom search feed."""
    atom_url = config.get("atom_url")
    if not atom_url:
        return []

    jobs: List[Dict] = []
    seen_urls: set[str] = set()
    search_terms = config.get("search_keywords", NKU_SEARCH_TERMS)
    telemetry_source = build_custom_telemetry_source(config)

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=DEFAULT_REQUEST_HEADERS,
    ) as client:
        for search_term in search_terms:
            try:
                response = await client.get(
                    atom_url,
                    params={"query": search_term, "commit": "Search"},
                )
                response.raise_for_status()
            except Exception as exc:
                print(f"  [WARN] Atom feed error for '{search_term}': {exc}")
                continue

            for entry in parse_peopleadmin_atom_feed(response.text):
                if entry["url"] in seen_urls:
                    continue

                detail = await fetch_peopleadmin_posting_details(client, entry["url"])
                if not should_keep_job(
                    entry["title"],
                    location=detail.get("location", ""),
                    description=detail.get("description", entry.get("summary", "")),
                    telemetry=telemetry,
                    telemetry_source=telemetry_source,
                ):
                    continue

                seen_urls.add(entry["url"])
                job_id_match = re.search(r"/postings/(\d+)", entry["url"])
                jobs.append(
                    {
                        "title": entry["title"],
                        "url": entry["url"],
                        "job_id": job_id_match.group(1) if job_id_match else "",
                        "location": detail.get("location", ""),
                        "description": detail.get("description", entry.get("summary", "")),
                        "company": company,
                        "ats": config["ats"],
                        "posted_date": entry.get("updated", ""),
                    }
                )

    return jobs


async def fetch_neogov_jobs(
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Fetch NEOGOV listings from rendered page content across all visible pages."""
    base_url = config.get("url")
    if not base_url:
        return []

    first_page_html = await fetch_page_html(base_url, wait_seconds=2)
    if not first_page_html:
        return []

    total_pages = min(get_neogov_page_count(first_page_html), int(config.get("max_pages", 1)))
    jobs: List[Dict] = []
    seen_urls: set[str] = set()

    for page_number in range(1, total_pages + 1):
        if page_number == 1:
            html = first_page_html
        else:
            html = await fetch_page_html(f"{base_url}?page={page_number}", wait_seconds=2)
            if not html:
                continue

        for job in parse_neogov_listing_html(html, company, config, telemetry=telemetry):
            if job["url"] in seen_urls:
                continue
            seen_urls.add(job["url"])
            jobs.append(job)

    return jobs


async def fetch_successfactors_rss_jobs(
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Fetch SuccessFactors jobs through the query-specific RSS feed."""
    rss_url = config.get("rss_url")
    if not rss_url:
        return []

    jobs: List[Dict] = []
    seen_urls: set[str] = set()
    search_terms = config.get("search_keywords", UC_SEARCH_TERMS)
    location_search = config.get("location_search", "Cincinnati")

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=DEFAULT_REQUEST_HEADERS,
    ) as client:
        for search_term in search_terms:
            params = {
                "locale": "en_US",
                "keywords": f"({search_term}) AND locationSearch:({location_search})",
            }
            try:
                response = await client.get(rss_url, params=params)
                response.raise_for_status()
            except Exception as exc:
                print(f"  [WARN] RSS fetch error for '{search_term}': {exc}")
                continue

            for job in parse_successfactors_rss_feed(response.text, company, config, telemetry=telemetry):
                if job["url"] in seen_urls:
                    continue
                seen_urls.add(job["url"])
                jobs.append(job)

    return jobs


async def fetch_activate_jobs(
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Fetch Activate-hosted job listings through the shared JSON endpoint."""
    search_api = config.get("search_api")
    if not search_api:
        return []

    jobs: List[Dict] = []
    seen_urls: set[str] = set()
    page_size = int(config.get("page_size", ACTIVATE_PAGE_SIZE))
    search_terms = config.get("search_keywords", ACTIVATE_SEARCH_TERMS)

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=DEFAULT_REQUEST_HEADERS,
    ) as client:
        for search_term in search_terms:
            start_index = 0

            while True:
                params = {
                    "keyword": search_term,
                    "jtStartIndex": start_index,
                    "jtPageSize": page_size,
                }
                try:
                    response = await client.get(search_api, params=params)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    print(f"  [WARN] Activate search error for '{search_term}': {exc}")
                    break

                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        print(
                            f"  [WARN] Activate search returned a non-object string "
                            f"for '{search_term}'"
                        )
                        break

                if not isinstance(payload, dict):
                    print(
                        f"  [WARN] Activate search returned an unexpected payload "
                        f"for '{search_term}': {type(payload).__name__}"
                    )
                    break

                for job in parse_activate_search_results(payload, company, config, telemetry=telemetry):
                    if job["url"] in seen_urls:
                        continue
                    seen_urls.add(job["url"])
                    jobs.append(job)

                records = payload.get("Records", [])
                record_count = len(records) if isinstance(records, list) else 0
                total_records = int(payload.get("TotalRecordCount", 0) or 0)
                if record_count == 0 or start_index + record_count >= total_records:
                    break
                start_index += page_size

    return jobs


async def fetch_phenom_jobs(
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Fetch Phenom-hosted search results pages and parse direct job cards."""
    search_url = str(config.get("search_results_url") or config.get("url") or "").strip()
    if not search_url:
        return []

    jobs: List[Dict] = []
    seen_urls: set[str] = set()
    offset = 0
    page_size = int(config.get("page_size", PHENOM_PAGE_SIZE))
    max_pages = int(config.get("max_pages", 20))
    page_number = 0

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=DEFAULT_REQUEST_HEADERS,
    ) as client:
        while page_number < max_pages:
            params = {"from": offset} if offset else None
            try:
                response = await client.get(search_url, params=params)
                response.raise_for_status()
            except Exception as exc:
                print(f"  [WARN] Phenom search error at offset {offset}: {exc}")
                break

            page_payload = extract_phenom_search_payload(response.text)
            raw_job_records = page_payload.get("jobs", []) if isinstance(page_payload, dict) else []
            if not isinstance(raw_job_records, list):
                raw_job_records = []

            page_jobs = parse_phenom_search_results(response.text, company, config, telemetry=telemetry)
            for job in page_jobs:
                if job["url"] in seen_urls:
                    continue
                seen_urls.add(job["url"])
                jobs.append(job)

            raw_job_count = len(raw_job_records)
            if page_payload.get("size"):
                try:
                    page_size = int(page_payload["size"])
                except (TypeError, ValueError):
                    page_size = page_size or PHENOM_PAGE_SIZE
            elif raw_job_count:
                page_size = raw_job_count

            has_next_page = False
            if page_size > 0:
                next_offset = offset + page_size
                has_next_page = f"from={next_offset}" in response.text

            page_number += 1
            if raw_job_count == 0 or page_size <= 0 or not has_next_page:
                break
            offset += page_size

    return jobs


async def fetch_page_html(url: str, wait_seconds: int = 5) -> Optional[str]:
    """Use Playwright to render JavaScript and get final HTML."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[ERROR] Playwright not installed. Run: pip install playwright && playwright install chromium")
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
        print(f"  [WARN] Error fetching {url}: {e}")
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
        print("[WARN] HTML too small, using regex fallback...", end=" ", flush=True)
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
        print(f"  [WARN] LLM error: {e}, trying regex...", end=" ", flush=True)
        return extract_jobs_regex_fallback(html, base_url)


def fetch_kroger_api(telemetry: Optional[Dict[str, dict]] = None) -> List[Dict]:
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
                        if should_keep_job(
                            title,
                            location=location,
                            telemetry=telemetry,
                            telemetry_source="custom_oracle_hcm",
                        ):
                            jobs.append({
                                'title': title,
                                'url': f'https://jobs.kroger.com/kroger/job/{job_id}',
                                'job_id': job_id,
                                'location': location,
                                'company': 'Kroger',
                                'ats': 'Oracle HCM'
                            })
        except Exception as e:
            print(f"  [WARN] Kroger API error for '{keyword}': {e}")

    return jobs


def fetch_western_southern_api(telemetry: Optional[Dict[str, dict]] = None) -> List[Dict]:
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
                    if should_keep_job(
                        title,
                        telemetry=telemetry,
                        telemetry_source="custom_icims",
                    ):
                        jobs.append({
                            'title': title,
                            'url': f'{base_url}{url_path}',
                            'job_id': job_id,
                            'company': 'Western & Southern',
                            'ats': 'iCIMS'
                        })
    except Exception as e:
        print(f"  [WARN] W&S API error: {e}")

    return jobs


async def scrape_company(
    company: str,
    config: Dict,
    telemetry: Optional[Dict[str, dict]] = None,
) -> List[Dict]:
    """Scrape a single company's career page."""
    company_name = config.get("display_name", company.replace("_", " "))
    ats_name = get_custom_target_ats_name(config)

    if config.get("scraper") == "successfactors_rss":
        print(f"  -> {company_name} ({ats_name})...", end=" ", flush=True)
        jobs = await fetch_successfactors_rss_jobs(company_name, config, telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    if config.get("scraper") == "activate_api":
        print(f"  -> {company_name} ({ats_name})...", end=" ", flush=True)
        jobs = await fetch_activate_jobs(company_name, config, telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    if config.get("scraper") == "peopleadmin_atom":
        print(f"  -> {company_name} ({ats_name})...", end=" ", flush=True)
        jobs = await fetch_peopleadmin_atom_jobs(company_name, config, telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    if config.get("scraper") == "neogov_rendered":
        print(f"  -> {company_name} ({ats_name})...", end=" ", flush=True)
        jobs = await fetch_neogov_jobs(company_name, config, telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    if config.get("scraper") == "phenom_search":
        print(f"  -> {company_name} ({ats_name})...", end=" ", flush=True)
        jobs = await fetch_phenom_jobs(company_name, config, telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    # Use direct API for specific companies
    if company == "Kroger":
        print(f"  -> {company} (Oracle HCM API)...", end=" ", flush=True)
        jobs = fetch_kroger_api(telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    if company == "Western_Southern":
        print(f"  -> {company} (iCIMS API)...", end=" ", flush=True)
        jobs = fetch_western_southern_api(telemetry=telemetry)
        print(f"Found {len(jobs)} jobs")
        for job in jobs:
            job["priority"] = config.get("priority", "MEDIUM")
            job["scraped_at"] = datetime.now().isoformat()
        return jobs

    url = config["url"]
    base_url = "/".join(url.split("/")[:3])  # Extract base domain

    print(f"  -> {company_name} ({ats_name})...", end=" ", flush=True)

    html = await fetch_page_html(url)
    if not html:
        print("[ERROR] Failed to load")
        return []

    jobs = extract_jobs_with_llm(html, company, base_url)
    print(f"Found {len(jobs)} jobs")

    # Add metadata
    for job in jobs:
        job["company"] = company_name
        job["ats"] = ats_name
        job["priority"] = config.get("priority", "MEDIUM")
        job["scraped_at"] = datetime.now().isoformat()

    return jobs


async def run_custom_scraper(
    targets: List[str] = None,
    telemetry: Optional[Dict[str, dict]] = None,
    dry_run: bool = False,
):
    """Main scraper loop for non-Workday sites."""
    print("=" * 60)
    print("CUSTOM SCRAPER - LLM-Assisted Job Extraction")
    print("=" * 60)

    state = load_state()
    ensure_job_identity_index(state)
    all_jobs = []
    kept_jobs = []
    new_jobs = 0

    available_targets = get_non_workday_targets()

    # Filter targets if specified
    companies = targets if targets else list(available_targets.keys())

    print(f"\nScanning {len(companies)} non-Workday sites...")

    for company in companies:
        if company not in available_targets:
            print(f"  [WARN] Unknown company: {company}")
            continue

        config = available_targets[company]

        if config.get("managed_by_pipeline"):
            print(f"  -> {config.get('display_name', company)} ({config['managed_by_pipeline']})")
            continue

        # Skip manual check sites
        if config.get("manual_check"):
            print(f"  -> {company} (MANUAL CHECK) - {config.get('note', '')}")
            continue

        jobs = await scrape_company(company, config, telemetry=telemetry)
        telemetry_source = build_custom_telemetry_source(config)
        needs_final_filter_telemetry = not config.get("scraper") and company != "Kroger"

        for job in jobs:
            url = job.get("url", "")
            title = job.get("title", "")
            keep_job = should_keep_job(
                title,
                location=job.get("location", ""),
                description=job.get("description", job.get("job_description", "")),
                telemetry=telemetry if needs_final_filter_telemetry else None,
                telemetry_source=telemetry_source if needs_final_filter_telemetry else "",
            )
            if keep_job:
                kept_jobs.append(job)
            existing_url = find_existing_job_url(state, job) if keep_job else ""
            if keep_job and existing_url:
                store_job_identity_record(state, job, stored_url=existing_url)
            if url and url not in state.get("seen_jobs", {}) and keep_job and not existing_url:
                if not dry_run:
                    state.setdefault("seen_jobs", {})[url] = datetime.now().isoformat()
                    store_job_identity_record(state, job)
                new_jobs += 1

        all_jobs.extend(jobs)

    if not dry_run:
        save_state(state)

    print("\nResults:")
    print(f"   Total jobs found: {len(all_jobs)}")
    print(f"   New jobs: {new_jobs}")
    print("   [DRY RUN] State not updated" if dry_run else "   [OK] State updated")

    if new_jobs > 0:
        print("\nNew jobs from custom scraper:")
        for job in all_jobs[:10]:
            title = job.get("title", "Unknown")[:60]
            company = job.get("company", "Unknown")
            print(f"   - {company}: {title}")

    return kept_jobs


def main():
    """CLI interface for custom scraper."""
    import argparse

    parser = argparse.ArgumentParser(description="LLM-Assisted Custom Scraper")
    parser.add_argument("--targets", nargs="+", help="Specific companies to scrape")
    parser.add_argument("--list", action="store_true", help="List available targets")
    parser.add_argument("--test", help="Test single company")

    args = parser.parse_args()

    if args.list:
        print("\nAvailable Non-Workday Targets:")
        print("-" * 60)
        for name, cfg in get_non_workday_targets().items():
            print(f"  {name:20} | {get_custom_target_ats_name(cfg):20} | {cfg['priority']}")
            print(f"    └─ {cfg['url'][:60]}...")
        print()
        return

    targets = [args.test] if args.test else args.targets
    asyncio.run(run_custom_scraper(targets))


if __name__ == "__main__":
    main()

