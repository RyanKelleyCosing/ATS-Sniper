#!/usr/bin/env python3
"""Discover ATS-hosted pages and optional job-board leads for web discovery."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Any, Sequence
from urllib.parse import urlparse, urlsplit, urlunsplit

import requests

try:
    from jobspy import scrape_jobs as scrape_jobspy_jobs
except ImportError:
    scrape_jobspy_jobs = None

from job_scraper import fetch_job_description
from utils.job_identity import ensure_job_identity_index, find_existing_job_url, store_job_identity_record
from utils.filters import (
    explain_job_targeting,
    get_target_role_clusters,
    get_web_discovery_settings,
    matches_preferred_location,
    should_exclude_title,
    should_keep_job,
)
from utils.state import load_config, load_state, save_state


def _bump_nested_counter(
    telemetry: dict[str, Any] | None,
    bucket: str,
    key: str,
) -> None:
    """Increment a nested telemetry counter if telemetry collection is enabled."""
    if telemetry is None:
        return
    counters = telemetry.setdefault(bucket, {})
    counters[key] = int(counters.get(key, 0) or 0) + 1

DEFAULT_QUERY_TERMS = (
    "careers at",
    "careers",
    "open roles",
    "open role",
    "open positions",
    "We're hiring",
)
DEFAULT_MAX_QUERIES = 3
DEFAULT_MAX_RESULTS = 6
STARTUP_DISCOVERY_EXCLUDED_ROLE_MARKERS = ("staff", "principal", "head", "director")
DEFAULT_QUERY_KEYWORD_GROUPS = (
    ("security engineer", "cloud security engineer", "identity and access management", "iam"),
    ("devops engineer", "site reliability engineer", "sre", "platform engineer"),
    ("cloud engineer", "infrastructure engineer", "terraform", "kubernetes"),
)
DISCOVERY_FOCUSED_TITLE_TERMS = (
    "application security engineer",
    "product security engineer",
    "infrastructure security engineer",
    "cloud security engineer",
    "security platform engineer",
    "security operations engineer",
    "secops engineer",
    "devsecops engineer",
    "iam engineer",
    "identity engineer",
    "identity and access management engineer",
    "identity and access management analyst",
    "customer identity engineer",
    "ciam engineer",
    "azure iam engineer",
    "platform reliability engineer",
    "production engineer",
    "devops engineer",
    "site reliability engineer",
    "cloud sre",
    "platform engineer",
    "azure platform engineer",
    "cloud platform engineer",
    "cloud engineer",
    "azure cloud engineer",
    "azure devops engineer",
    "azure infrastructure engineer",
    "infrastructure engineer",
    "aks engineer",
    "infrastructure automation engineer",
    "cloud operations engineer",
    "cloud automation engineer",
    "security engineer",
    "identity and access management",
)
ADJACENT_DISCOVERY_ROLE_MARKERS = (
    "implementation",
    "integration",
    "automation",
    "support",
    "workflow",
    "internal tool",
    "tooling",
    "developer productivity",
    "agentic",
)
ADJACENT_DISCOVERY_FOCUSED_TITLE_TERMS = (
    "implementation engineer",
    "technical implementation engineer",
    "integration engineer",
    "systems integration engineer",
    "automation engineer",
    "workflow automation engineer",
    "software support engineer",
    "system software support engineer",
    "application support engineer",
    "platform support engineer",
    "developer productivity engineer",
    "internal tools engineer",
    "agentic engineer",
)
DEFAULT_COMPANY_SITE_FILTERS = (
    "site:jobs.cardinalhealth.com",
    "site:careers.atsginc.com",
    "site:careers.peraton.com",
    "site:jobs.medixteam.com",
)
JOBSPY_RUNNER_SCRIPT = Path(__file__).resolve().with_name("jobspy_process_runner.py")
JOBSPY_RUNNER_ENV_VAR = "ATS_SNIPER_JOBSPY_PYTHON"
JOBSPY_RUNNER_TIMEOUT_SECONDS = 300
DEFAULT_JOBSPY_SITES = ("indeed", "google", "zip_recruiter")
JOBSPY_TITLE_SEARCH_ANCHORS = (
    "engineer",
    "analyst",
    "administrator",
    "specialist",
    "architect",
)
JOBSPY_SEARCH_TERM_EXPANSIONS = (
    (
        "security",
        (
            "application security engineer",
            "product security engineer",
            "infrastructure security engineer",
            "cloud security engineer",
            "security operations engineer",
            "devsecops engineer",
        ),
    ),
    ("iam", ("iam engineer", "identity engineer", "azure iam engineer")),
    ("devops", ("devops engineer",)),
    ("site reliability", ("site reliability engineer",)),
    (
        "platform",
        (
            "platform reliability engineer",
            "platform engineer",
            "cloud platform engineer",
            "production engineer",
        ),
    ),
    (
        "cloud",
        (
            "cloud automation engineer",
            "cloud platform engineer",
            "cloud sre",
            "azure cloud engineer",
            "azure platform engineer",
            "azure infrastructure engineer",
            "azure devops engineer",
            "cloud operations engineer",
            "aks engineer",
            "cloud engineer",
        ),
    ),
    (
        "identity",
        (
            "customer identity engineer",
            "ciam engineer",
            "identity and access management engineer",
            "identity and access management analyst",
        ),
    ),
    ("customer identity", ("customer identity engineer", "ciam engineer")),
    ("ciam", ("ciam engineer", "customer identity engineer")),
    ("azure", ("azure iam engineer", "azure security engineer")),
    ("infrastructure", ("infrastructure automation engineer", "infrastructure engineer")),
    ("systems", ("systems engineer",)),
)
JOBSPY_ADJACENT_SEARCH_TERM_EXPANSIONS = (
    ("implementation", ("implementation engineer", "technical implementation engineer")),
    ("integration", ("integration engineer", "systems integration engineer")),
    ("automation", ("automation engineer", "workflow automation engineer")),
    (
        "support",
        (
            "software support engineer",
            "system software support engineer",
            "application support engineer",
            "platform support engineer",
        ),
    ),
    ("workflow", ("workflow automation engineer",)),
    ("agentic", ("agentic engineer",)),
    (
        "tooling",
        (
            "developer productivity engineer",
            "internal tools engineer",
        ),
    ),
    ("developer productivity", ("developer productivity engineer",)),
    ("internal tools", ("internal tools engineer",)),
)
JOBSPY_FALLBACK_SEARCH_TERMS = (
    "application security engineer",
    "product security engineer",
    "infrastructure security engineer",
    "security engineer",
    "cloud security engineer",
    "security operations engineer",
    "devsecops engineer",
    "iam engineer",
    "identity engineer",
    "customer identity engineer",
    "ciam engineer",
    "devops engineer",
    "platform reliability engineer",
    "site reliability engineer",
    "cloud sre",
    "platform engineer",
    "cloud platform engineer",
    "azure platform engineer",
    "cloud automation engineer",
    "azure cloud engineer",
    "azure infrastructure engineer",
    "azure devops engineer",
    "cloud operations engineer",
    "aks engineer",
    "cloud engineer",
)
JOBSPY_ADJACENT_FALLBACK_SEARCH_TERMS = (
    "implementation engineer",
    "integration engineer",
    "automation engineer",
    "software support engineer",
    "platform support engineer",
    "workflow automation engineer",
    "developer productivity engineer",
    "internal tools engineer",
    "agentic engineer",
)
JOBSPY_ALLOWED_SINGLE_WORD_TERMS = {"devops", "sre", "iam", "identity", "cybersecurity"}
JOBSPY_EXCLUDED_QUERY_TERMS = {
    "ai engineer",
    "ai infrastructure engineer",
    "automation",
    "cloud ops",
    "cloud operations",
    "cloud platform",
    "cyber",
    "data engineer",
    "data platform",
    "data platform engineer",
    "infrastructure",
    "machine learning infrastructure",
    "machine learning platform",
    "ml platform engineer",
    "network engineer",
    "platform",
    "systems administrator",
    "system administrator",
    "systems analyst",
}
DISCOVERY_QUERY_EXCLUDED_ROLE_PATTERNS = (
    r"\bai engineer\b",
    r"\bai infrastructure engineer\b",
    r"\bdata engineer\b",
    r"\bdata platform\b",
    r"\bmachine learning\b",
    r"\bml platform\b",
    r"\bnetwork engineer\b",
    r"\bsystems analyst\b",
    r"\b(?:systems|system) administrator\b",
    r"\b(?:systems|system) engineer\b",
)
DISCOVERY_HIGH_AFFINITY_TITLE_PATTERNS = (
    (r"\bcloud security engineer\b", 18),
    (r"\bsecurity engineer\b", 16),
    (r"\bsecurity analyst\b", 12),
    (r"\bidentity(?: and access management)?(?: engineer| analyst)?\b", 16),
    (r"\biam(?: engineer| analyst)?\b", 15),
    (r"\bdevops engineer\b", 15),
    (r"\bsite reliability engineer\b", 15),
    (r"\bsre\b", 13),
    (r"\bcloud engineer\b", 12),
    (r"\binfrastructure engineer\b", 10),
    (r"\bplatform engineer\b", 10),
)
DISCOVERY_HIGH_SIGNAL_SKILL_MARKERS = (
    "aks",
    "argo",
    "arm template",
    "arm templates",
    "aws",
    "azure",
    "azure devops yaml",
    "azure landing zone",
    "azure landing zones",
    "bash",
    "bicep",
    "ci/cd",
    "cloudwatch",
    "container insights",
    "cspm",
    "datadog",
    "eks",
    "entra",
    "github actions",
    "gitlab",
    "iam",
    "incident response",
    "incident management",
    "kql",
    "kubernetes",
    "observability",
    "okta",
    "pulumi",
    "python",
    "rbac",
    "siem",
    "sso",
    "terraform",
    "vulnerability",
)
DISCOVERY_NON_TITLE_PREFIXES = (
    "we ",
    "our ",
    "this ",
    "that ",
    "these ",
    "those ",
    "execute ",
    "executing ",
    "deliver ",
    "delivering ",
    "build ",
    "building ",
    "lead ",
    "leading ",
    "manage ",
    "managing ",
    "monitor ",
    "monitoring ",
    "support ",
    "supporting ",
    "experience ",
    "knowledge ",
    "bachelor",
    "master",
    "degree",
    "required",
    "preferred",
    "including",
    "with ",
    "must ",
    "ability ",
    "responsible",
    "responsibilities",
    "about ",
    "join ",
    "work ",
    "working ",
    "you ",
    "your ",
    "team ",
)
DISCOVERY_NON_TITLE_PHRASES = (
    "years of experience",
    "degree in",
    "underrepresented communities",
    "we especially encourage",
    "job description",
    "responsible for",
    "apply now",
)
DISCOVERY_TITLE_ANCHORS = (
    "engineer",
    "analyst",
    "administrator",
    "specialist",
    "architect",
    "officer",
    "technician",
    "manager",
    "lead",
)
DISCOVERY_COMPANY_LABEL_BLOCKLIST = (
    "jobs",
    "job board",
    "careers",
    "career",
    "myworkdayjobs",
    "greenhouse",
    "lever",
    "workable",
    "ashby",
    "icims",
    ".com",
)
DISCOVERY_STRONG_FRESHNESS_MARKERS = (
    "just posted",
    "posted today",
    "today",
    "yesterday",
    "1 day ago",
    "24 hours",
    "new",
)
DISCOVERY_MODERATE_FRESHNESS_MARKERS = (
    "2 days ago",
    "3 days ago",
    "4 days ago",
    "5 days ago",
    "6 days ago",
    "7 days ago",
    "this week",
)


def get_query_terms(config: dict) -> list[str]:
    """Return search phrases used for web-discovery search queries."""
    configured_terms = config.get("startup_discovery", {}).get("google_query_terms", [])
    all_terms = list(DEFAULT_QUERY_TERMS) + [str(term) for term in configured_terms]

    deduped_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in all_terms:
        normalized_term = re.sub(r"\s+", " ", term).strip()
        if not normalized_term:
            continue
        term_key = normalized_term.casefold()
        if term_key in seen_terms:
            continue
        seen_terms.add(term_key)
        deduped_terms.append(normalized_term)

    return deduped_terms


def dedupe_terms(terms: Sequence[str]) -> list[str]:
    """Deduplicate query terms while preserving order."""
    deduped_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in terms:
        normalized_term = re.sub(r"\s+", " ", str(term)).strip()
        if not normalized_term:
            continue
        term_key = normalized_term.casefold()
        if term_key in seen_terms:
            continue
        seen_terms.add(term_key)
        deduped_terms.append(normalized_term)
    return deduped_terms


def select_role_terms(role_terms: Sequence[str], markers: Sequence[str], *, limit: int = 8) -> list[str]:
    """Select role terms matching marker themes for a query profile."""
    selected_terms = [
        term
        for term in role_terms
        if any(marker in term.casefold() for marker in markers)
    ]
    if not selected_terms:
        selected_terms = list(role_terms)
    return dedupe_terms(selected_terms)[:limit]


def prioritize_role_terms(
    role_terms: Sequence[str],
    prioritized_terms: Sequence[str],
    *,
    limit: int = 8,
) -> list[str]:
    """Move high-fit title phrases ahead of broader cluster markers for query building."""
    normalized_role_terms: dict[str, str] = {}
    for term in role_terms:
        normalized_term = re.sub(r"\s+", " ", str(term)).strip()
        if normalized_term:
            normalized_role_terms.setdefault(normalized_term.casefold(), normalized_term)

    ordered_terms = [
        normalized_role_terms.get(str(term).casefold(), str(term).strip())
        for term in prioritized_terms
        if str(term).strip()
    ]
    ordered_terms.extend(normalized_role_terms.values())
    return dedupe_terms(ordered_terms)[:limit]


def filter_high_fit_discovery_role_terms(role_terms: Sequence[str]) -> list[str]:
    """Remove role terms that consistently pull low-fit data, AI, or generic systems results."""
    filtered_terms: list[str] = []
    for role in role_terms:
        normalized_role = re.sub(r"\s+", " ", str(role)).strip()
        if not normalized_role:
            continue
        if any(
            re.search(pattern, normalized_role, re.IGNORECASE)
            for pattern in DISCOVERY_QUERY_EXCLUDED_ROLE_PATTERNS
        ):
            continue
        filtered_terms.append(normalized_role)
    return dedupe_terms(filtered_terms)


def select_adjacent_role_terms(role_terms: Sequence[str], *, limit: int = 12) -> list[str]:
    """Select adjacent-tech titles for the broader discovery lane."""
    selected_terms = [
        term
        for term in role_terms
        if any(marker in term.casefold() for marker in ADJACENT_DISCOVERY_ROLE_MARKERS)
    ]
    prioritized_terms = selected_terms or list(ADJACENT_DISCOVERY_FOCUSED_TITLE_TERMS)
    return prioritize_role_terms(
        prioritized_terms,
        ADJACENT_DISCOVERY_FOCUSED_TITLE_TERMS,
        limit=limit,
    )


def get_company_site_filters(discovery_settings: Mapping[str, Any]) -> list[str]:
    """Return company-career domain filters used by Phase 3 web discovery."""
    configured_filters = discovery_settings.get("company_site_filters", [])
    if not isinstance(configured_filters, list) or not configured_filters:
        configured_filters = list(DEFAULT_COMPANY_SITE_FILTERS)
    return dedupe_terms([str(site_filter) for site_filter in configured_filters])


def build_default_query_profiles(
    role_terms: Sequence[str],
    query_terms: Sequence[str],
    location_terms: Sequence[str],
    site_filters: Sequence[str],
    company_site_filters: Sequence[str],
    *,
    include_adjacent_roles: bool = False,
) -> list[dict[str, Any]]:
    """Build the default Phase 3 query-profile set for web discovery."""
    focused_role_terms = filter_high_fit_discovery_role_terms(role_terms) or list(role_terms)
    security_platform_role_terms = prioritize_role_terms(
        select_role_terms(
            focused_role_terms,
            (
                "application security",
                "product security",
                "infrastructure security",
                "security platform",
                "security operations",
                "secops",
                "devsecops",
                "security",
                "identity",
                "customer identity",
                "ciam",
                "iam",
                "azure iam",
                "devops",
                "site reliability",
                "sre",
                "platform reliability",
                "production",
                "platform engineer",
            ),
            limit=24,
        ),
        DISCOVERY_FOCUSED_TITLE_TERMS,
        limit=24,
    )
    cloud_platform_role_terms = prioritize_role_terms(
        select_role_terms(
            focused_role_terms,
            (
                "cloud security",
                "cloud automation",
                "cloud platform",
                "cloud",
                "azure",
                "azure devops",
                "azure cloud",
                "infrastructure",
                "infrastructure automation",
                "devops",
                "site reliability",
                "security operations",
                "platform reliability",
                "production",
                "cloud operations",
                "security",
                "platform engineer",
            ),
            limit=24,
        ),
        DISCOVERY_FOCUSED_TITLE_TERMS,
        limit=24,
    )
    broad_high_fit_role_terms = prioritize_role_terms(
        dedupe_terms(security_platform_role_terms + cloud_platform_role_terms),
        DISCOVERY_FOCUSED_TITLE_TERMS,
        limit=24,
    )
    adjacent_role_terms = select_adjacent_role_terms(role_terms, limit=12)
    board_site_filters = [
        site_filter
        for site_filter in site_filters
        if any(marker in site_filter for marker in ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co"))
    ]
    board_partner_filters = [
        site_filter
        for site_filter in site_filters
        if any(marker in site_filter for marker in ("linkedin.com/jobs/view", "dice.com/job-detail", "jobs.medixteam.com"))
    ]
    extended_ats_filters = [
        site_filter
        for site_filter in site_filters
        if site_filter not in board_site_filters and site_filter not in board_partner_filters
    ]
    query_profiles = [
        {
            "name": "ats_board_pages",
            "site_filters": board_site_filters,
            "query_terms": list(query_terms),
            "location_terms": list(location_terms),
            "role_terms": list(security_platform_role_terms),
        },
        {
            "name": "ats_pages_extended",
            "site_filters": extended_ats_filters,
            "query_terms": list(query_terms),
            "location_terms": list(location_terms),
            "role_terms": list(cloud_platform_role_terms),
        },
        {
            "name": "board_partner_domains",
            "site_filters": list(board_partner_filters),
            "query_terms": [],
            "location_terms": list(location_terms),
            "role_terms": list(broad_high_fit_role_terms),
        },
        {
            "name": "company_career_domains",
            "site_filters": list(company_site_filters),
            "query_terms": list(query_terms),
            "location_terms": list(location_terms),
            "role_terms": list(broad_high_fit_role_terms),
        },
        {
            "name": "remote_us_roles",
            "site_filters": [],
            "query_terms": ["remote", "remote us", "remote united states", "united states remote", "hybrid united states"],
            "location_terms": list(location_terms),
            "role_terms": list(broad_high_fit_role_terms),
        },
    ]

    if include_adjacent_roles and adjacent_role_terms:
        query_profiles.insert(
            3,
            {
                "name": "adjacent_tech_remote_us",
                "site_filters": [],
                "query_terms": [
                    "remote",
                    "remote us",
                    "remote united states",
                    "technical support",
                    "implementation",
                    "integration",
                    "automation",
                ],
                "location_terms": list(location_terms),
                "role_terms": list(adjacent_role_terms),
            },
        )

    return query_profiles


def build_web_discovery_query_specs(
    config: dict,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> list[dict[str, Any]]:
    """Build structured Phase 3 web-discovery query specs."""
    role_terms = get_role_terms(config)
    query_terms = get_query_terms(config)
    startup_config = dict(config.get("startup_discovery", {}))
    discovery_settings = get_web_discovery_settings()
    location_terms = [
        str(term).strip()
        for term in discovery_settings.get("location_terms", [])
        if str(term).strip()
    ]
    site_filters = [
        str(term).strip()
        for term in discovery_settings.get("site_filters", [])
        if str(term).strip()
    ]
    company_site_filters = get_company_site_filters(discovery_settings)

    configured_profiles = discovery_settings.get("query_profiles", [])
    if isinstance(configured_profiles, list) and configured_profiles:
        query_profiles = [
            profile
            for profile in configured_profiles
            if isinstance(profile, Mapping)
        ]
    else:
        query_profiles = build_default_query_profiles(
            role_terms,
            query_terms,
            location_terms,
            site_filters,
            company_site_filters,
            include_adjacent_roles=bool(
                startup_config.get(
                    "include_adjacent_roles",
                    discovery_settings.get("include_adjacent_roles", False),
                )
            ),
        )

    query_specs: list[dict[str, Any]] = []
    allowed_profile_order = [
        re.sub(r"\s+", "_", str(name)).strip("_").casefold()
        for name in startup_config.get("query_profile_allowlist", [])
        if str(name).strip()
    ]
    allowed_profile_names = set(allowed_profile_order)
    excluded_profile_names = {
        re.sub(r"\s+", "_", str(name)).strip("_").casefold()
        for name in startup_config.get("query_profile_exclude", [])
        if str(name).strip()
    }

    for profile in query_profiles:
        profile_name = re.sub(r"\s+", "_", str(profile.get("name", "web_discovery"))).strip("_") or "web_discovery"
        profile_key = profile_name.casefold()
        if allowed_profile_names and profile_key not in allowed_profile_names:
            continue
        if profile_key in excluded_profile_names:
            continue
        profile_query_terms = dedupe_terms(profile.get("query_terms", query_terms))
        profile_role_terms = dedupe_terms(profile.get("role_terms", role_terms))[:24]
        profile_location_terms = dedupe_terms(profile.get("location_terms", location_terms))
        profile_site_filters = dedupe_terms(profile.get("site_filters", []))

        if not profile_role_terms:
            continue

        site_clause = f"({' OR '.join(profile_site_filters)}) " if profile_site_filters else ""
        quoted_query_terms = " OR ".join(f'"{term}"' for term in profile_query_terms)
        quoted_role_terms = " OR ".join(f'"{role}"' for role in profile_role_terms)
        quoted_location_terms = " OR ".join(f'"{term}"' for term in profile_location_terms)
        query_clause = f"({quoted_query_terms}) " if quoted_query_terms else ""
        role_clause = f"({quoted_role_terms})"
        location_clause = f"({quoted_location_terms})" if quoted_location_terms else '"remote"'
        query_specs.append(
            {
                "name": profile_name,
                "query": f"{site_clause}{query_clause}{role_clause} {location_clause}".strip(),
                "role_terms": profile_role_terms,
                "site_filters": profile_site_filters,
            }
        )

    if allowed_profile_order:
        order_map = {name: index for index, name in enumerate(allowed_profile_order)}
        query_specs.sort(
            key=lambda spec: order_map.get(spec["name"].casefold(), len(order_map))
        )

    return query_specs[:max_queries]


def build_web_discovery_queries(config: dict, max_queries: int = DEFAULT_MAX_QUERIES) -> list[str]:
    """Build the text queries used by Phase 3 web discovery."""
    return [spec["query"] for spec in build_web_discovery_query_specs(config, max_queries=max_queries)]


def build_notion_queries(config: dict, max_queries: int = DEFAULT_MAX_QUERIES) -> list[str]:
    """Backward-compatible alias for generic web discovery queries."""
    return build_web_discovery_queries(config, max_queries=max_queries)


def get_jobspy_discovery_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return the JobSpy discovery configuration if present."""
    settings = config.get("jobspy_discovery", {})
    return dict(settings) if isinstance(settings, dict) else {}


def _get_venv_python_path(venv_path: Path) -> Path:
    """Return the Python executable path for a virtual environment directory."""
    if sys.platform.startswith("win"):
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


@lru_cache(maxsize=1)
def resolve_jobspy_python_executable() -> str | None:
    """Resolve a compatible Python interpreter that can import JobSpy."""
    candidate_paths: list[Path] = []

    configured_python_text = str(os.environ.get(JOBSPY_RUNNER_ENV_VAR, "")).strip()
    if configured_python_text:
        candidate_paths.append(Path(configured_python_text))

    repo_root = Path(__file__).resolve().parent
    configured_venv_name = str(os.environ.get("ATS_SNIPER_VENV_NAME", "")).strip()
    if configured_venv_name:
        candidate_paths.append(_get_venv_python_path(repo_root / configured_venv_name))

    candidate_paths.extend(
        _get_venv_python_path(repo_root / venv_name)
        for venv_name in (".venv-jobspy", ".venv313", ".venv")
    )

    seen_candidates: set[str] = set()
    for candidate_path in candidate_paths:
        candidate_key = str(candidate_path).casefold()
        if candidate_key in seen_candidates or not candidate_path.exists():
            continue
        seen_candidates.add(candidate_key)

        try:
            probe = subprocess.run(
                [str(candidate_path), "-c", "import jobspy"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(Path(__file__).resolve().parent),
            )
        except (OSError, subprocess.SubprocessError):
            continue

        if probe.returncode == 0:
            return str(candidate_path)

    return None


def run_jobspy_queries(query_requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Run one or more JobSpy queries either directly or through a compatible sidecar interpreter."""
    if scrape_jobspy_jobs is not None:
        query_results: list[dict[str, Any]] = []
        for query_request in query_requests:
            search_kwargs = dict(query_request.get("search_kwargs", {}))
            try:
                jobspy_results = scrape_jobspy_jobs(**search_kwargs)
            except Exception as exc:  # noqa: BLE001
                query_results.append({"records": [], "error": str(exc)})
                continue
            query_results.append({"records": coerce_jobspy_records(jobspy_results), "error": ""})
        return query_results

    jobspy_python = resolve_jobspy_python_executable()
    if not jobspy_python or not JOBSPY_RUNNER_SCRIPT.exists():
        return [
            {
                "records": [],
                "error": (
                    "JobSpy package unavailable; no compatible JobSpy Python interpreter was found. "
                    "Checked ATS_SNIPER_JOBSPY_PYTHON plus .venv-jobspy, .venv313, and .venv."
                ),
            }
            for _ in query_requests
        ]

    with tempfile.TemporaryDirectory(prefix="ats_sniper_jobspy_") as temp_dir:
        temp_root = Path(temp_dir)
        input_path = temp_root / "jobspy_input.json"
        output_path = temp_root / "jobspy_output.json"
        input_path.write_text(
            json.dumps({"queries": list(query_requests)}),
            encoding="utf-8",
        )

        try:
            completed = subprocess.run(
                [
                    jobspy_python,
                    str(JOBSPY_RUNNER_SCRIPT),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=JOBSPY_RUNNER_TIMEOUT_SECONDS,
                cwd=str(Path(__file__).resolve().parent),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return [{"records": [], "error": str(exc)} for _ in query_requests]

        if completed.returncode != 0:
            stderr_text = completed.stderr.strip() or completed.stdout.strip() or "JobSpy runner failed"
            return [{"records": [], "error": stderr_text} for _ in query_requests]

        if not output_path.exists():
            return [{"records": [], "error": "JobSpy runner did not produce an output file"} for _ in query_requests]

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        query_results = [
            {
                "records": list(result.get("records", [])) if isinstance(result, Mapping) else [],
                "error": str(result.get("error", "")).strip() if isinstance(result, Mapping) else "",
            }
            for result in raw_results
        ]

    while len(query_results) < len(query_requests):
        query_results.append({"records": [], "error": "JobSpy runner returned incomplete results"})

    return query_results


def build_jobspy_search_terms(config: dict[str, Any]) -> list[str]:
    """Build focused JobSpy search terms that behave better than one broad OR query."""
    jobspy_config = get_jobspy_discovery_config(config)
    include_adjacent_roles = bool(jobspy_config.get("include_adjacent_roles", False))
    max_search_terms = int(jobspy_config.get("max_search_terms", 16) or 16)
    adjacent_max_search_terms = min(
        max(int(jobspy_config.get("adjacent_max_search_terms", 4) or 4), 1),
        max_search_terms,
    )
    role_terms = get_role_terms(config)
    configured_terms = jobspy_config.get("search_terms", [])
    using_explicit_search_terms = False
    if isinstance(configured_terms, list):
        normalized_terms = [
            normalized_term
            for normalized_term in dedupe_terms([str(term) for term in configured_terms])
            if normalized_term and not should_exclude_title(normalized_term)
        ]
        if normalized_terms:
            return normalized_terms[:max_search_terms]
        focused_terms = []
    else:
        focused_terms = []

    explicit_query = re.sub(
        r"\s+",
        " ",
        str(jobspy_config.get("search_term", "")),
    ).strip()
    if explicit_query and not include_adjacent_roles:
        return [explicit_query]
    if explicit_query:
        focused_terms = [explicit_query]

    combined_role_text = " ".join(role.casefold() for role in role_terms)
    seen_terms: set[str] = set()

    for term in focused_terms:
        seen_terms.add(term.casefold())

    def add_term(value: str) -> None:
        normalized_term = re.sub(r"\s+", " ", str(value)).strip()
        term_key = normalized_term.casefold()
        if not normalized_term or term_key in seen_terms:
            return
        seen_terms.add(term_key)
        focused_terms.append(normalized_term)

    if not focused_terms:
        for marker, expansions in JOBSPY_SEARCH_TERM_EXPANSIONS:
            if marker not in combined_role_text:
                continue
            for expansion in expansions:
                add_term(expansion)

        for role in role_terms:
            normalized_role = re.sub(r"\s+", " ", str(role)).strip()
            role_key = normalized_role.casefold()
            if role_key in JOBSPY_EXCLUDED_QUERY_TERMS:
                continue
            if not any(
                re.search(rf"\b{re.escape(anchor)}\b", role_key)
                for anchor in JOBSPY_TITLE_SEARCH_ANCHORS
            ):
                continue
            add_term(normalized_role)

    if not focused_terms:
        for search_term in JOBSPY_FALLBACK_SEARCH_TERMS:
            add_term(search_term)

    if not include_adjacent_roles:
        return focused_terms[:max_search_terms]

    configured_core_priority_terms: list[str] = []
    configured_role_groups = config.get("role_groups", {})
    if isinstance(configured_role_groups, Mapping) and not using_explicit_search_terms:
        for roles in configured_role_groups.values():
            if not isinstance(roles, Sequence) or isinstance(roles, (str, bytes)):
                continue
            for role in roles:
                normalized_role = re.sub(r"\s+", " ", str(role)).strip()
                role_key = normalized_role.casefold()
                if (
                    not normalized_role
                    or should_exclude_title(normalized_role)
                    or role_key in JOBSPY_EXCLUDED_QUERY_TERMS
                    or any(marker in role_key for marker in ADJACENT_DISCOVERY_ROLE_MARKERS)
                    or not any(
                        re.search(rf"\b{re.escape(anchor)}\b", role_key)
                        for anchor in JOBSPY_TITLE_SEARCH_ANCHORS
                    )
                ):
                    continue
                configured_core_priority_terms.append(normalized_role)

    core_priority_terms = [] if using_explicit_search_terms else (
        configured_core_priority_terms or [
            re.sub(r"\s+", " ", str(role)).strip()
            for role in role_terms
            if str(role).strip()
            and str(role).strip().casefold() not in JOBSPY_EXCLUDED_QUERY_TERMS
            and not any(marker in str(role).casefold() for marker in ADJACENT_DISCOVERY_ROLE_MARKERS)
            and any(
                re.search(rf"\b{re.escape(anchor)}\b", str(role).casefold())
                for anchor in JOBSPY_TITLE_SEARCH_ANCHORS
            )
        ]
    )
    if core_priority_terms:
        focused_terms = prioritize_role_terms(
            focused_terms,
            core_priority_terms,
            limit=max(max_search_terms, len(focused_terms) + len(core_priority_terms)),
        )

    adjacent_terms: list[str] = []
    adjacent_seen_terms: set[str] = set()

    def add_adjacent_term(value: str) -> None:
        normalized_term = re.sub(r"\s+", " ", str(value)).strip()
        term_key = normalized_term.casefold()
        if not normalized_term or term_key in adjacent_seen_terms:
            return
        adjacent_seen_terms.add(term_key)
        adjacent_terms.append(normalized_term)

    for marker, expansions in JOBSPY_ADJACENT_SEARCH_TERM_EXPANSIONS:
        if marker not in combined_role_text:
            continue
        for expansion in expansions:
            add_adjacent_term(expansion)

    for role in role_terms:
        normalized_role = re.sub(r"\s+", " ", str(role)).strip()
        if any(marker in normalized_role.casefold() for marker in ADJACENT_DISCOVERY_ROLE_MARKERS):
            add_adjacent_term(normalized_role)

    if not adjacent_terms:
        for search_term in JOBSPY_ADJACENT_FALLBACK_SEARCH_TERMS:
            add_adjacent_term(search_term)

    if not adjacent_terms:
        return focused_terms[:max_search_terms]

    configured_adjacent_priority_terms: list[str] = []
    if isinstance(configured_role_groups, Mapping):
        for roles in configured_role_groups.values():
            if not isinstance(roles, Sequence) or isinstance(roles, (str, bytes)):
                continue
            for role in roles:
                normalized_role = re.sub(r"\s+", " ", str(role)).strip()
                role_key = normalized_role.casefold()
                if (
                    not normalized_role
                    or should_exclude_title(normalized_role)
                    or not any(
                    marker in role_key for marker in ADJACENT_DISCOVERY_ROLE_MARKERS
                    )
                ):
                    continue
                configured_adjacent_priority_terms.append(normalized_role)

    if configured_adjacent_priority_terms:
        adjacent_terms = prioritize_role_terms(
            adjacent_terms,
            configured_adjacent_priority_terms,
            limit=max(max_search_terms, len(adjacent_terms) + len(configured_adjacent_priority_terms)),
        )

    base_limit = max(max_search_terms - adjacent_max_search_terms, 0)
    combined_terms = focused_terms[:base_limit]
    combined_seen = {term.casefold() for term in combined_terms}
    for term in adjacent_terms:
        term_key = term.casefold()
        if term_key in combined_seen:
            continue
        combined_terms.append(term)
        combined_seen.add(term_key)
        if len(combined_terms) >= max_search_terms:
            break

    return dedupe_terms(combined_terms)[:max_search_terms]


def _format_jobspy_query_term(search_term: str) -> str:
    """Format one JobSpy search term for logging or legacy OR-query use."""
    normalized_term = re.sub(r"\s+", " ", str(search_term)).strip()
    if not normalized_term:
        return ""
    if any(token in normalized_term for token in ('"', " OR ", " AND ", "(", ")")):
        return normalized_term
    return f'"{normalized_term}"' if " " in normalized_term else normalized_term


def build_jobspy_search_term(config: dict[str, Any]) -> str:
    """Build a legacy combined JobSpy expression from the focused search terms."""
    search_terms = build_jobspy_search_terms(config)
    if len(search_terms) == 1:
        return search_terms[0]

    return " OR ".join(
        formatted_term
        for formatted_term in (_format_jobspy_query_term(search_term) for search_term in search_terms)
        if formatted_term
    )


def _append_role_term(role_terms: list[str], seen_terms: set[str], role: str) -> None:
    """Append a normalized in-scope role term if it passes shared discovery rules."""
    normalized_role = re.sub(r"\s+", " ", str(role)).strip()
    role_key = normalized_role.casefold()
    if not normalized_role or role_key in seen_terms or should_exclude_title(normalized_role):
        return
    if any(
        re.search(rf"\b{re.escape(marker)}\b", normalized_role, re.IGNORECASE)
        for marker in STARTUP_DISCOVERY_EXCLUDED_ROLE_MARKERS
    ):
        return
    seen_terms.add(role_key)
    role_terms.append(normalized_role)


def get_role_terms(config: dict) -> list[str]:
    """Return a deduplicated list of target roles for web and JobSpy discovery."""
    role_groups = config.get("role_groups", {})
    configured_clusters = get_target_role_clusters()
    role_terms: list[str] = []
    seen_terms: set[str] = set()

    for cluster_terms in configured_clusters.values():
        for role in cluster_terms:
            _append_role_term(role_terms, seen_terms, role)

    for roles in role_groups.values():
        if not isinstance(roles, list):
            continue
        for role in roles:
            _append_role_term(role_terms, seen_terms, str(role))

    return role_terms


def is_allowed_discovery_result(link: str) -> bool:
    """Return True when a Google result host matches the configured ATS allowlist."""
    discovery_settings = get_web_discovery_settings()
    hostname = urlparse(link).netloc.casefold()
    allowlist_markers = [
        str(marker).casefold().strip()
        for marker in discovery_settings.get("host_allowlist_markers", [])
        if str(marker).strip()
    ]
    if not allowlist_markers:
        return True
    return any(marker in hostname for marker in allowlist_markers)


def _clean_candidate_label(text: str) -> str:
    """Normalize a freeform title or company label candidate."""
    return re.sub(r"\s+", " ", str(text)).strip(" \t-|:;,.\u2013\u2014")


def _is_probable_job_title(text: str) -> bool:
    """Return True when a short text fragment looks like a job title."""
    cleaned_text = _clean_candidate_label(text)
    normalized_text = cleaned_text.casefold()
    if not cleaned_text:
        return False

    word_count = len(cleaned_text.split())
    if word_count < 2 or word_count > 12:
        return False
    if cleaned_text[0].isdigit():
        return False
    if any(normalized_text.startswith(prefix) for prefix in DISCOVERY_NON_TITLE_PREFIXES):
        return False
    if any(phrase in normalized_text for phrase in DISCOVERY_NON_TITLE_PHRASES):
        return False
    if re.search(r"[.!?]", cleaned_text[:-1]):
        return False

    return any(
        re.search(rf"\b{re.escape(anchor)}\b", normalized_text)
        for anchor in DISCOVERY_TITLE_ANCHORS
    )


def _is_probable_company_name(text: str) -> bool:
    """Return True when a short label is more likely a company than a role title."""
    cleaned_text = _clean_candidate_label(text)
    normalized_text = cleaned_text.casefold()
    if not cleaned_text or len(cleaned_text.split()) > 8:
        return False
    if any(marker in normalized_text for marker in DISCOVERY_COMPANY_LABEL_BLOCKLIST):
        return False
    return not _is_probable_job_title(cleaned_text)


def _split_candidate_title_parts(text: str) -> list[str]:
    """Split a search-result title into title-sized chunks."""
    cleaned_text = _clean_candidate_label(text)
    if not cleaned_text:
        return []

    parts: list[str] = []
    for part in re.split(r"\s+\|\s+|\s+[:\u2013\u2014-]\s+", cleaned_text):
        cleaned_part = _clean_candidate_label(part)
        if cleaned_part and cleaned_part not in parts:
            parts.append(cleaned_part)
    if cleaned_text not in parts:
        parts.append(cleaned_text)
    return parts


def _extract_direct_job_title(
    page_title: str,
    result_title: str,
    role_terms: Sequence[str],
) -> str:
    """Prefer a single direct-job title before scanning page text for role lines."""
    normalized_role_terms = tuple(role.casefold() for role in role_terms)
    for candidate_source in (page_title, result_title):
        for candidate in _split_candidate_title_parts(candidate_source):
            normalized_candidate = candidate.casefold()
            if not any(role in normalized_candidate for role in normalized_role_terms):
                continue
            if _is_probable_job_title(candidate):
                return candidate
    return ""


def _normalize_discovery_url(url: str) -> str:
    """Strip discovery-only fragments and Workday apply suffixes from URLs."""
    split_url = urlsplit(str(url).strip())
    path = re.sub(r"/apply(?:/.*)?$", "", split_url.path)
    return urlunsplit((split_url.scheme, split_url.netloc, path, "", ""))


def infer_source_family(link: str) -> str:
    """Classify discovery hits into broad ATS or company-site families."""
    hostname = urlparse(link).netloc.casefold()
    if "linkedin.com" in hostname or "dice.com" in hostname or "indeed.com" in hostname or "ziprecruiter.com" in hostname:
        return "job_board"
    if "medixteam.com" in hostname:
        return "recruiter_platform"
    if "greenhouse" in hostname:
        return "greenhouse_board"
    if "lever.co" in hostname:
        return "lever_board"
    if "workable.com" in hostname:
        return "workable"
    if "smartrecruiters.com" in hostname:
        return "smartrecruiters"
    if "jobvite.com" in hostname:
        return "jobvite"
    if "builtin.com" in hostname:
        return "builtin"
    if "ashbyhq.com" in hostname:
        return "ashby"
    if "icims.com" in hostname:
        return "icims"
    if "myworkdayjobs.com" in hostname:
        return "workday"
    return "company_career_site"


def infer_source_board(link: str) -> str:
    """Classify the underlying board or ATS host for reporting."""
    hostname = urlparse(link).netloc.casefold()
    if "linkedin.com" in hostname:
        return "linkedin_jobs"
    if "dice.com" in hostname:
        return "dice"
    if "indeed.com" in hostname:
        return "indeed"
    if "ziprecruiter.com" in hostname:
        return "zip_recruiter"
    if "medixteam.com" in hostname:
        return "medix"
    if "greenhouse" in hostname:
        return "greenhouse"
    if "lever.co" in hostname:
        return "lever"
    if "workable.com" in hostname:
        return "workable"
    if "smartrecruiters.com" in hostname:
        return "smartrecruiters"
    if "jobvite.com" in hostname:
        return "jobvite"
    if "builtin.com" in hostname:
        return "builtin"
    if "ashbyhq.com" in hostname:
        return "ashby"
    if "icims.com" in hostname:
        return "icims"
    if "myworkdayjobs.com" in hostname:
        return "workday"
    if hostname:
        return hostname
    return "unknown"


def freshness_hint_score(text: str) -> int:
    """Score snippet freshness hints from search-result text."""
    normalized_text = str(text).casefold()
    if any(marker in normalized_text for marker in DISCOVERY_STRONG_FRESHNESS_MARKERS):
        return 10
    if any(marker in normalized_text for marker in DISCOVERY_MODERATE_FRESHNESS_MARKERS):
        return 5
    return 0


def exclusion_risk_score(*, title: str, result_title: str, snippet_text: str) -> int:
    """Estimate a negative confidence adjustment for borderline exclusion cues."""
    risk_text = " ".join(part.casefold() for part in (title, result_title, snippet_text) if part)
    if re.search(r"\b(?:staff|principal|director|vice president|vp|head of)\b", risk_text):
        return -25
    if re.search(r"\b(?:lead|architect|manager)\b", risk_text):
        return -10
    if re.search(r"\b(?:senior|sr\.?)\b", risk_text):
        return -5
    return 0


def title_affinity_score(*, title: str, result_title: str) -> int:
    """Reward discovery results whose titles align with the strongest target-role lanes."""
    normalized_title = " ".join(part.casefold() for part in (title, result_title) if part)
    for pattern, score in DISCOVERY_HIGH_AFFINITY_TITLE_PATTERNS:
        if re.search(pattern, normalized_title, re.IGNORECASE):
            return score
    return 0


def skill_signal_score(*, description_text: str, snippet_text: str) -> int:
    """Reward discovery hits whose copy mentions high-signal platform and security skills."""
    normalized_text = " ".join(part.casefold() for part in (description_text, snippet_text) if part)
    if not normalized_text:
        return 0

    matched_markers = sum(1 for marker in DISCOVERY_HIGH_SIGNAL_SKILL_MARKERS if marker in normalized_text)
    if matched_markers >= 4:
        return 8
    if matched_markers >= 2:
        return 4
    return 0


def score_discovery_hit(
    link: str,
    *,
    title: str,
    result_title: str,
    location: str,
    snippet_text: str,
    description_text: str,
) -> tuple[int, dict[str, int]]:
    """Compute a Phase 3 confidence score for a web-discovery hit."""
    normalized_path = urlparse(link).path.casefold()
    source_family = infer_source_family(link)
    direct_ats_score = 40 if re.search(r"/job[s]?/", normalized_path) else 25
    title_quality = 25 if _is_probable_job_title(title) else 10
    if title and result_title and _clean_candidate_label(title).casefold() != _clean_candidate_label(result_title).casefold():
        title_quality = min(30, title_quality + 5)

    location_quality = 0
    if matches_preferred_location(location, description=description_text or snippet_text):
        location_quality = 20 if location else 12

    freshness_score = freshness_hint_score(snippet_text)
    risk_adjustment = exclusion_risk_score(title=title, result_title=result_title, snippet_text=snippet_text)
    role_affinity = title_affinity_score(title=title, result_title=result_title)
    skill_signal = skill_signal_score(description_text=description_text, snippet_text=snippet_text)

    score_breakdown = {
        "direct_ats_page": direct_ats_score,
        "title_quality": title_quality,
        "location_quality": location_quality,
        "freshness_hint": freshness_score,
        "role_affinity": role_affinity,
        "skill_signal": skill_signal,
        "exclusion_risk": risk_adjustment,
    }
    total_score = max(0, min(100, sum(score_breakdown.values())))
    return total_score, score_breakdown


def build_web_discovery_job(
    *,
    title: str,
    company: str,
    link: str,
    location: str,
    page_details: Mapping[str, Any],
    result_title: str,
    snippet_text: str,
    page_text: str,
    query_profile: str,
) -> dict[str, Any]:
    """Build a normalized web-discovery job with Phase 3 confidence metadata."""
    scraped_at = datetime.now().isoformat()
    discovery_confidence, confidence_breakdown = score_discovery_hit(
        link,
        title=title,
        result_title=result_title,
        location=location,
        snippet_text=snippet_text,
        description_text=page_text,
    )
    return {
        "title": title,
        "company": company,
        "url": link,
        "source_url": link,
        "location": location,
        "posted_date": str(page_details.get("posted_date") or page_details.get("date_posted") or "").strip(),
        "source": "web_google",
        "source_family": infer_source_family(link),
        "source_board": infer_source_board(link),
        "query_profile": query_profile or "web_discovery",
        "tier": "web_discovery",
        "priority": "HIGH" if discovery_confidence >= 70 else "MEDIUM",
        "scraped_at": scraped_at,
        "source_detected_at": scraped_at,
        "first_seen_at": scraped_at,
        "discovery_confidence": discovery_confidence,
        "discovery_confidence_breakdown": confidence_breakdown,
        "contact_email": page_details.get("contact_email", ""),
        "contact_emails": page_details.get("contact_emails", []),
        "job_description": page_text,
    }


def extract_company_name(title_text: str, url: str, *, job_title_hint: str = "") -> str:
    """Extract a company label from a discovery result title or URL."""
    cleaned_title = re.sub(
        r"\b(we'?re hiring|open roles?|join our team|careers?|jobs?)\b",
        "",
        title_text,
        flags=re.IGNORECASE,
    )
    title_parts = [
        part.strip()
        for part in re.split(r"\|| - |: ", cleaned_title)
        if part.strip()
    ]
    normalized_job_title_hint = _clean_candidate_label(job_title_hint).casefold()
    if len(title_parts) >= 2:
        first_part = _clean_candidate_label(title_parts[0])
        second_part = _clean_candidate_label(title_parts[1])
        if normalized_job_title_hint and first_part.casefold() == normalized_job_title_hint:
            if _is_probable_company_name(second_part):
                return second_part
        if _is_probable_job_title(first_part) and _is_probable_company_name(second_part):
            return second_part

    for part in title_parts:
        cleaned_part = re.sub(r"^at\s+", "", _clean_candidate_label(part), flags=re.IGNORECASE).strip()
        if cleaned_part and _is_probable_company_name(cleaned_part):
            return cleaned_part

    hostname = urlparse(url).netloc.split(".")[0]
    return hostname.replace("-", " ").title() or "Unknown"


def extract_role_lines(page_text: str, role_terms: Sequence[str]) -> list[str]:
    """Extract role-like lines from a public hiring page."""
    matches: list[str] = []
    seen_lines: set[str] = set()
    raw_chunks: list[str] = []
    for raw_line in page_text.splitlines():
        cleaned_line = re.sub(r"\s+", " ", raw_line).strip()
        if not cleaned_line:
            continue
        raw_chunks.extend(
            chunk.strip()
            for chunk in re.split(r"\s*[•·|;]\s*|\s+�\s+", cleaned_line)
            if chunk.strip()
        )

    candidate_lines: list[str] = []
    for chunk in raw_chunks:
        candidate_lines.extend(
            part.strip(" -*•\t")
            for part in re.split(r"(?<=\.)\s+(?=[A-Z])|(?<=\))\s+(?=[A-Z])", chunk)
            if part.strip(" -*•\t")
        )

    for line in candidate_lines:
        line = line.rstrip(".")
        if len(line.split()) > 14:
            continue
        if not _is_probable_job_title(line):
            continue
        if not any(role.casefold() in line.casefold() for role in role_terms):
            continue
        line_key = line.casefold()
        if line_key in seen_lines:
            continue
        seen_lines.add(line_key)
        matches.append(line)
    return matches[:5]


def slugify_role(role: str) -> str:
    """Convert a role title into a stable fragment-friendly slug."""
    return re.sub(r"[^a-z0-9]+", "-", role.casefold()).strip("-")


def coerce_jobspy_records(jobspy_results: Any) -> list[dict[str, Any]]:
    """Normalize JobSpy results into a plain list of record dictionaries."""
    if jobspy_results is None:
        return []

    if hasattr(jobspy_results, "to_dict"):
        try:
            records = jobspy_results.to_dict("records")
        except TypeError:
            records = jobspy_results.to_dict()
        if isinstance(records, list):
            return [dict(record) for record in records if isinstance(record, Mapping)]
        return []

    if isinstance(jobspy_results, list):
        return [dict(record) for record in jobspy_results if isinstance(record, Mapping)]

    return []


def format_jobspy_location(record: Mapping[str, Any]) -> str:
    """Build a readable location string from JobSpy record fields."""
    nested_location = record.get("location")
    location_parts: list[str] = []
    if isinstance(nested_location, Mapping):
        for key in ("city", "state", "country"):
            value = str(nested_location.get(key, "")).strip()
            if value and value not in location_parts:
                location_parts.append(value)

    if not location_parts:
        for key in ("city", "state", "country"):
            value = str(record.get(key, "")).strip()
            if value and value not in location_parts:
                location_parts.append(value)

    raw_location = ""
    if isinstance(nested_location, str):
        raw_location = nested_location.strip()

    location_text = raw_location or ", ".join(location_parts)
    if record.get("is_remote"):
        if location_text:
            return location_text if location_text.casefold().startswith("remote") else f"Remote - {location_text}"
        return "Remote"

    return location_text


def serialize_jobspy_timestamp(value: Any) -> str:
    """Serialize a JobSpy timestamp-like value to a string."""
    if isinstance(value, datetime):
        return value.isoformat()

    normalized_value = str(value).strip()
    return normalized_value if normalized_value else datetime.now().isoformat()


def format_jobspy_salary(record: Mapping[str, Any]) -> str:
    """Build a salary string from JobSpy pay fields when available."""
    min_amount = record.get("min_amount")
    max_amount = record.get("max_amount")
    interval = str(record.get("interval", "")).strip()
    currency = str(record.get("currency", "USD")).strip()

    def _format_amount(amount: Any) -> str:
        if not isinstance(amount, int | float):
            return ""
        prefix = "$" if currency.upper() == "USD" else f"{currency} "
        return f"{prefix}{amount:,.0f}"

    min_text = _format_amount(min_amount)
    max_text = _format_amount(max_amount)
    if min_text and max_text:
        salary_text = f"{min_text}-{max_text}"
    elif min_text:
        salary_text = min_text
    elif max_text:
        salary_text = max_text
    else:
        return ""

    return f"{salary_text} {interval}".strip()


def build_job_from_jobspy_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Convert a JobSpy record into ATS Sniper's shared job shape."""
    title = re.sub(r"\s+", " ", str(record.get("title", ""))).strip()
    job_url = str(record.get("job_url") or record.get("url") or "").strip()
    if not title or not job_url:
        return None

    location = format_jobspy_location(record)
    description = str(record.get("description") or "").strip()
    workplace_type = "Remote" if bool(record.get("is_remote")) else ""
    filter_explanation = explain_job_targeting(
        title,
        location=location,
        workplace_type=workplace_type,
        description=description,
    )
    if not filter_explanation["keep"]:
        return None

    raw_emails = record.get("emails", [])
    emails = [
        str(email).strip()
        for email in raw_emails
        if str(email).strip()
    ] if isinstance(raw_emails, list) else []
    site_name = re.sub(r"[^a-z0-9_]+", "_", str(record.get("site") or record.get("site_name") or "jobspy").casefold())
    salary = format_jobspy_salary(record)
    scraped_at = datetime.now().isoformat()
    job: dict[str, Any] = {
        "title": title,
        "company": re.sub(r"\s+", " ", str(record.get("company") or "Unknown")).strip() or "Unknown",
        "url": job_url,
        "source_url": job_url,
        "location": location,
        "source": f"jobspy_{site_name or 'board'}",
        "source_family": "job_board",
        "source_board": site_name or "jobspy",
        "query_profile": "jobspy_pilot",
        "tier": "job_board",
        "priority": "MEDIUM",
        "scraped_at": scraped_at,
        "source_detected_at": scraped_at,
        "first_seen_at": scraped_at,
        "date_posted": serialize_jobspy_timestamp(record.get("date_posted")),
        "posted_date": serialize_jobspy_timestamp(record.get("date_posted")),
        "contact_email": emails[0] if emails else "",
        "contact_emails": emails,
        "job_description": description,
    }

    if salary:
        job["salary"] = salary

    job_type = str(record.get("job_type", "")).strip()
    if job_type:
        job["job_type"] = job_type

    return job


def record_discovered_job(
    state: dict[str, Any],
    job: dict[str, Any],
    new_jobs: list[dict[str, Any]],
) -> None:
    """Persist a newly discovered job to state and append it to the current batch."""
    ensure_job_identity_index(state)
    job_url = job.get("url", "")
    if not job_url:
        return
    existing_url = find_existing_job_url(state, job)
    if existing_url:
        store_job_identity_record(state, job, stored_url=existing_url)
        return
    if job_url in state.get("seen_jobs", {}):
        return
    if should_exclude_title(job.get("title", "")):
        return

    state.setdefault("seen_jobs", {})[job_url] = job["scraped_at"]
    store_job_identity_record(state, {
        "title": job.get("title", "Unknown"),
        "company": job.get("company", "Unknown"),
        "url": job_url,
        "location": job.get("location", ""),
        "source": job.get("source", "web_google"),
        "source_family": job.get("source_family", "web_discovery"),
        "source_board": job.get("source_board", ""),
        "query_profile": job.get("query_profile", ""),
        "tier": job.get("tier", "web_discovery"),
        "priority": job.get("priority", "HIGH"),
        "scraped_at": job.get("scraped_at", datetime.now().isoformat()),
        "source_detected_at": job.get("source_detected_at", job.get("scraped_at", datetime.now().isoformat())),
        "first_seen_at": job.get("first_seen_at", job.get("scraped_at", datetime.now().isoformat())),
        "discovery_confidence": job.get("discovery_confidence", 0),
        "discovery_confidence_breakdown": job.get("discovery_confidence_breakdown", {}),
        "contact_email": job.get("contact_email", ""),
        "contact_emails": job.get("contact_emails", []),
        "source_url": job.get("source_url", job_url),
        "date_posted": job.get("date_posted", ""),
    })
    new_jobs.append(job)


def search_google(query: str, serpapi_key: str, max_results: int) -> list[dict[str, Any]]:
    """Search Google via SerpApi and return organic results."""
    response = requests.get(
        "https://serpapi.com/search",
        params={
            "engine": "google",
            "q": query,
            "api_key": serpapi_key,
            "num": max_results,
            "tbs": "qdr:m",
            "hl": "en",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("organic_results", [])


def build_jobs_from_result(
    result: dict[str, Any],
    role_terms: Sequence[str],
    *,
    query_profile: str = "",
    telemetry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Expand a Google result into one or more role-specific jobs."""
    link = _normalize_discovery_url(result.get("link", ""))
    if not link:
        return []
    if not is_allowed_discovery_result(link):
        return []

    page_details = fetch_job_description(link, log_warnings=False) or {}
    result_title = _clean_candidate_label(result.get("title", ""))
    page_title = _clean_candidate_label(page_details.get("title", ""))
    if not page_title or page_title.casefold() == "notion":
        page_title = result_title
    description_text = str(page_details.get("description") or "").strip()
    snippet_text = _clean_candidate_label(result.get("snippet", ""))
    page_text = description_text or snippet_text
    direct_job_title = _extract_direct_job_title(page_title, result_title, role_terms)
    company = extract_company_name(result_title or page_title, link, job_title_hint=direct_job_title)
    location = str(page_details.get("location", "")).strip()

    if direct_job_title:
        explanation = explain_job_targeting(
            direct_job_title,
            location=location,
            description=page_text,
        )
        if explanation["keep"]:
            _bump_nested_counter(telemetry, "query_profile_yield", query_profile or "web_discovery")
            _bump_nested_counter(telemetry, "kept_reasons", explanation["decision_reason"])
            return [
                build_web_discovery_job(
                    title=direct_job_title,
                    company=company,
                    link=link,
                    location=location,
                    page_details=page_details,
                    result_title=result_title,
                    snippet_text=snippet_text,
                    page_text=page_text,
                    query_profile=query_profile,
                )
            ]
        for reason in explanation["rejection_reasons"]:
            _bump_nested_counter(telemetry, "rejected_reasons", reason)

    role_lines = extract_role_lines(description_text, role_terms) if description_text else []

    if not role_lines:
        fallback_title = _extract_direct_job_title("", result_title, role_terms)
        if fallback_title:
            explanation = explain_job_targeting(
                fallback_title,
                location=location,
                description=page_text,
            )
            if explanation["keep"]:
                role_lines = [fallback_title]
            else:
                for reason in explanation["rejection_reasons"]:
                    _bump_nested_counter(telemetry, "rejected_reasons", reason)
        else:
            _bump_nested_counter(telemetry, "rejected_reasons", "no_target_role_lines")

    jobs: list[dict[str, Any]] = []
    for role_line in role_lines:
        explanation = explain_job_targeting(
            role_line,
            location=location,
            description=page_text,
        )
        if not explanation["keep"]:
            for reason in explanation["rejection_reasons"]:
                _bump_nested_counter(telemetry, "rejected_reasons", reason)
            continue
        role_slug = slugify_role(role_line)
        _bump_nested_counter(telemetry, "query_profile_yield", query_profile or "web_discovery")
        _bump_nested_counter(telemetry, "kept_reasons", explanation["decision_reason"])
        jobs.append(
            build_web_discovery_job(
                title=role_line,
                company=company,
                link=f"{link}#role-{role_slug}" if role_slug and len(role_lines) > 1 else link,
                location=location,
                page_details=page_details,
                result_title=result_title,
                snippet_text=snippet_text,
                page_text=page_text,
                query_profile=query_profile,
            )
        )

    return jobs


def collect_jobspy_discovery_jobs(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    run_type: str,
    telemetry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect optional JobSpy job-board leads and normalize them into shared jobs."""
    jobspy_config = get_jobspy_discovery_config(config)
    if not jobspy_config.get("enabled", False):
        print("   JobSpy discovery disabled in config")
        return []

    allowed_run_types = {
        str(value).casefold().strip()
        for value in jobspy_config.get("run_types", ["afternoon", "full"])
        if str(value).strip()
    }
    if allowed_run_types and run_type.casefold() not in allowed_run_types:
        print(f"   Skipping JobSpy discovery during the {run_type} run")
        return []

    site_names = [
        str(site).strip()
        for site in jobspy_config.get("site_name", list(DEFAULT_JOBSPY_SITES))
        if str(site).strip()
    ]
    if not site_names:
        site_names = list(DEFAULT_JOBSPY_SITES)
    search_terms = build_jobspy_search_terms(config)
    if not search_terms:
        print("   JobSpy search terms unavailable; skipping JobSpy discovery")
        return []

    requested_results = int(jobspy_config.get("results_wanted", 30) or 30)
    results_per_search_term = int(
        jobspy_config.get(
            "results_per_search_term",
            max(5, min(requested_results, 10)),
        )
        or max(5, min(requested_results, 10))
    )
    base_search_kwargs: dict[str, Any] = {
        "location": str(jobspy_config.get("location", "United States")).strip(),
        "hours_old": int(jobspy_config.get("hours_old", 24)),
        "country_indeed": str(jobspy_config.get("country_indeed", "USA")).strip() or "USA",
        "is_remote": bool(jobspy_config.get("is_remote", True)),
        "verbose": int(jobspy_config.get("verbose", 0)),
    }
    google_search_term = str(jobspy_config.get("google_search_term", "")).strip()

    print(f"   JobSpy sites: {', '.join(site_names)}")
    print(f"   JobSpy search terms: {', '.join(search_terms)}")

    new_jobs: list[dict[str, Any]] = []
    seen_record_urls: set[str] = set()
    raw_record_count = 0

    query_requests: list[dict[str, Any]] = []
    for site_name in site_names:
        for search_term in search_terms:
            search_kwargs = {
                **base_search_kwargs,
                "site_name": [site_name],
                "search_term": search_term,
                "results_wanted": results_per_search_term,
            }
            if google_search_term and site_name.casefold() == "google":
                search_kwargs["google_search_term"] = google_search_term
            query_requests.append(
                {
                    "site_name": site_name,
                    "search_term": search_term,
                    "search_kwargs": search_kwargs,
                }
            )

    query_results = run_jobspy_queries(query_requests)
    if query_results and all(result.get("error") for result in query_results):
        print(f"   {query_results[0]['error']}")
        return []

    for query_request, query_result in zip(query_requests, query_results):
        site_name = str(query_request.get("site_name", "")).strip() or "unknown"
        search_term = str(query_request.get("search_term", "")).strip() or "unknown"
        error_text = str(query_result.get("error", "")).strip()
        if error_text:
            print(f"   ⚠️ JobSpy discovery error [{site_name} | {search_term}]: {error_text}")
            continue

        records = [
            dict(record)
            for record in query_result.get("records", [])
            if isinstance(record, Mapping)
        ]
        raw_record_count += len(records)
        if records:
            print(f"   JobSpy query [{site_name}] {search_term}: {len(records)} raw")

        for record in records:
            record_url = str(record.get("job_url") or record.get("url") or "").strip()
            if record_url and record_url in seen_record_urls:
                continue
            if record_url:
                seen_record_urls.add(record_url)

            job = build_job_from_jobspy_record(record)
            if job is None:
                title = str(record.get("title", "")).strip()
                location = format_jobspy_location(record)
                description = str(record.get("description") or "").strip()
                explanation = explain_job_targeting(
                    title,
                    location=location,
                    workplace_type="Remote" if bool(record.get("is_remote")) else "",
                    description=description,
                )
                for reason in explanation["rejection_reasons"]:
                    _bump_nested_counter(telemetry, "rejected_reasons", reason)
                continue

            _bump_nested_counter(telemetry, "query_profile_yield", "jobspy_pilot")
            record_discovered_job(state, job, new_jobs)

    print(f"   JobSpy raw records: {raw_record_count}")
    print(f"   New JobSpy leads: {len(new_jobs)}")
    return new_jobs


def run_startup_discovery_scrape(
    dry_run: bool = False,
    run_type: str = "full",
    telemetry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run web discovery sources and persist newly found pages to state."""
    print("🚀 WEB DISCOVERY - ATS Pages + Board Leads")

    config = load_config()
    discovery_settings = get_web_discovery_settings()
    allowed_run_types = {
        str(value).casefold().strip()
        for value in discovery_settings.get("allowed_run_types", [])
        if str(value).strip()
    }
    if allowed_run_types and run_type.casefold() not in allowed_run_types:
        print(f"   Skipping web discovery during the {run_type} run")
        return []

    startup_config = config.get("startup_discovery", {})
    jobspy_config = get_jobspy_discovery_config(config)
    if not startup_config.get("enabled", True) and not jobspy_config.get("enabled", False):
        print("   Web discovery disabled in config")
        return []

    serpapi_key = config.get("serpapi_key", "")
    serpapi_enabled = startup_config.get("enabled", True) and bool(serpapi_key) and not serpapi_key.startswith("YOUR_")

    max_queries = int(discovery_settings.get("max_queries", startup_config.get("max_queries", DEFAULT_MAX_QUERIES)))
    max_results = int(
        discovery_settings.get(
            "max_results_per_query",
            startup_config.get("max_results_per_query", DEFAULT_MAX_RESULTS),
        )
    )
    role_terms = get_role_terms(config)
    state = load_state()
    new_jobs: list[dict[str, Any]] = []

    if serpapi_enabled:
        query_specs = build_web_discovery_query_specs(config, max_queries=max_queries)
        for index, query_spec in enumerate(query_specs, start=1):
            print(f"   Query {index}/{len(query_specs)} [{query_spec['name']}]: {query_spec['query']}")
            try:
                results = search_google(query_spec["query"], serpapi_key, max_results)
            except requests.RequestException as exc:
                print(f"   ⚠️ Web discovery search error: {exc}")
                continue

            for result in results:
                for job in build_jobs_from_result(
                    result,
                    role_terms,
                    query_profile=query_spec["name"],
                    telemetry=telemetry,
                ):
                    record_discovered_job(state, job, new_jobs)

            if index < len(query_specs):
                time.sleep(2)
    else:
        print("   SerpApi key not configured; skipping ATS-page search")

    new_jobs.extend(collect_jobspy_discovery_jobs(config, state, run_type=run_type, telemetry=telemetry))

    if not dry_run and new_jobs:
        save_state(state)

    print(f"   New web discovery leads: {len(new_jobs)}")
    return new_jobs


def run_web_discovery_scrape(
    dry_run: bool = False,
    run_type: str = "full",
    telemetry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Preferred alias for the renamed web-discovery stage."""
    return run_startup_discovery_scrape(dry_run=dry_run, run_type=run_type, telemetry=telemetry)


if __name__ == "__main__":
    run_startup_discovery_scrape()