"""Shared title, location, and discovery-profile filtering for ATS Sniper scrapers."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from utils.state import load_config

_ROLE_PROFILES_PATH = Path(__file__).resolve().parents[1] / "role_profiles.json"
_HARD_NON_IT_TITLE_MARKERS = (
    "mechanical",
    "manufacturing",
    "process engineer",
    "quality engineer",
    "civil",
    "electrical",
    "chemical",
    "field service",
    "controls engineer",
    "industrial",
    "plant",
    "facilities",
    "construction",
    "warehouse",
    "supply chain",
)
_DESCRIPTION_RESCUEABLE_REJECTION_REASONS = frozenset(
    {
        "excluded_title_pattern_software_engineer",
        "excluded_title_pattern_backend_engineer",
        "excluded_title_pattern_data_engineer",
        "excluded_title_pattern_data_platform",
        "excluded_title_pattern_product_engineer",
    }
)
_DESCRIPTION_SCOPE_STRONG_MARKERS = (
    "site reliability",
    "sre",
    "terraform",
    "terragrunt",
    "kubernetes",
    "eks",
    "aks",
    "azure kubernetes service",
    "ecs",
    "ci/cd",
    "azure devops yaml",
    "yaml pipeline",
    "infrastructure as code",
    "bicep",
    "arm template",
    "arm templates",
    "observability",
    "prometheus",
    "grafana",
    "datadog",
    "cloudwatch",
    "cloudtrail",
    "container insights",
    "kql",
    "azure landing zone",
    "azure landing zones",
    "incident response",
    "on-call",
    "identity and access",
    "iam",
    "okta",
    "entra id",
    "sailpoint",
    "sso",
    "rbac",
    "pam",
    "security monitoring",
    "siem",
    "security hub",
    "guardduty",
    "vulnerability management",
    "active directory",
    "transit gateway",
    "direct connect",
    "secrets manager",
    "disaster recovery",
    "root cause analysis",
)
_DESCRIPTION_SCOPE_SUPPORT_MARKERS = (
    "aws",
    "azure",
    "gcp",
    "cloud",
    "platform",
    "infrastructure",
    "linux",
    "network",
    "security",
    "automation",
    "monitoring",
    "compliance",
    "reliability",
    "incident",
    "vpn",
    "dns",
)

_DEFAULT_ROLE_FILTER_SETTINGS: dict[str, Any] = {
    "target_role_clusters": {
        "security": [
            "security engineer",
            "security analyst",
            "information security",
            "application security",
            "cloud security",
            "identity",
            "identity and access management",
            "iam",
            "access management",
            "cybersecurity",
            "cyber",
        ],
        "platform": [
            "devops",
            "site reliability",
            "sre",
            "platform engineer",
            "platform",
            "automation",
            "incident management",
            "systems analyst",
            "cloud operations",
            "cloud ops",
        ],
        "cloud": [
            "cloud engineer",
            "cloud platform",
            "infrastructure",
            "infrastructure engineer",
            "systems engineer",
            "system engineer",
            "network engineer",
            "systems administrator",
            "system administrator",
            "data platform",
        ],
        "adjacent_tech": [
            "implementation engineer",
            "technical implementation engineer",
            "integration engineer",
            "systems integration engineer",
            "software support engineer",
            "systems software support engineer",
            "application support engineer",
            "platform support engineer",
            "workflow automation engineer",
            "developer productivity engineer",
            "internal tools engineer",
        ],
    },
    "exclude_patterns": [
        "data engineer",
        "data platform",
        "frontend",
        "front-end",
        "full ?stack",
        "mobile engineer",
        "android",
        "ai engineer",
        "ai infrastructure engineer",
        "ios",
        "machine learning",
        "ml platform",
        "web engineer",
        "software engineer",
        "backend engineer",
        "product engineer",
        "consultant",
        "recruiter",
        "marketing",
        "sales",
        "legal",
        "finance",
        "hr",
        "people operations",
        "customer success",
        "business development",
        "mechanical",
        "manufacturing",
        "process engineer",
        "quality engineer",
        "civil",
        "electrical",
        "chemical",
        "field service",
        "controls engineer",
        "industrial",
        "plant",
        "facilities",
        "construction",
        "warehouse",
        "supply chain",
        "intern",
        "internship",
        "co-op",
    ],
    "exclude_prefixes": ["lead ", "lead/"],
    "seniority_patterns": {
        "senior": r"\b(?:senior|sr\.?)\b",
        "lead": r"\blead\b",
        "staff": r"\bstaff\b",
        "principal": r"\bprincipal\b",
        "director": r"\bdirector\b",
        "manager": r"\bmanager\b",
        "architect": r"\barchitect\b",
        "head": r"\bhead of\b",
        "chief": r"\bchief\b",
        "vp": r"\b(?:vp|vice president)\b",
    },
    "seniority_allowlist": [],
    "seniority_title_allowlist_patterns": {
        "senior": [
            r"systems?\s+administrator",
            r"sys\s*admin",
            r"it\s+security\s+analyst",
            r"information\s+security\s+analyst",
            r"security\s+analyst",
            r"identity\s+(?:and\s+)?access\s+management\s+analyst",
        ],
    },
    "allowed_location_markers": [
        "united states",
        "u.s.",
        "us only",
        "us-remote",
        "cincinnati",
        "blue ash",
        "mason",
        "west chester",
        "evendale",
        "covington",
        "union township",
        "springdale",
        "fairfield",
        "kenwood",
        "norwood",
        "sharonville",
        "montgomery",
        "florence",
        "fort mitchell",
        "hamilton",
        "highland heights",
        "milford",
        "loveland",
        "newport",
        "erlanger",
        "crestview hills",
        "northern kentucky",
    ],
    "blocked_location_markers": [
        "canada",
        "toronto",
        "vancouver",
        "montreal",
        "london",
        "united kingdom",
        "uk",
        "england",
        "manchester",
        "stockholm",
        "poland",
        "india",
        "singapore",
        "emea",
        "apac",
        "germany",
        "australia",
    ],
    "remote_markers": [
        " remote ",
        "remote-",
        "-remote",
        "work from home",
        "wfh",
        "telecommute",
        "telework",
        "home based",
        "virtual",
        "anywhere in the u.s",
        "us remote",
        "remote us",
        "remote, us",
        "remote first",
        "remote-first",
        "work from anywhere in the us",
        "remote, united states",
    ],
    "remote_negation_markers": [
        "not remote",
        "not a remote",
        "not eligible for remote",
        "no remote",
        "must relocate",
        "must be onsite",
        "on-site only",
    ],
    "search_terms": {
        "workday": [
            "Security",
            "Identity",
            "IAM",
            "DevOps",
            "Site Reliability",
            "Cloud SRE",
            "Platform Engineer",
            "Azure Platform Engineer",
            "Cloud Engineer",
            "Cloud Platform Engineer",
            "Azure Infrastructure Engineer",
            "Infrastructure Engineer",
            "Systems Engineer",
            "AKS",
            "Bicep",
            "Azure Landing Zones",
            "Incident Management",
            "Cloud Operations",
            "Automation",
        ],
        "board_keywords": [
            "security",
            "identity",
            "iam",
            "access management",
            "devops",
            "site reliability",
            "sre",
            "platform",
            "cloud",
            "cloud platform engineer",
            "cloud sre",
            "infrastructure",
            "azure platform engineer",
            "azure infrastructure engineer",
            "aks",
            "bicep",
            "arm templates",
            "container insights",
            "kql",
            "azure landing zones",
            "systems engineer",
            "system engineer",
            "systems analyst",
            "implementation engineer",
            "technical implementation engineer",
            "integration engineer",
            "systems integration engineer",
            "software support engineer",
            "systems software support engineer",
            "application support engineer",
            "platform support engineer",
            "workflow automation engineer",
            "developer productivity engineer",
            "internal tools engineer",
            "incident",
            "automation",
            "operations",
            "network",
        ],
    },
    "web_discovery": {
        "query_terms": [
            "careers at",
            "careers",
            "open roles",
            "open role",
            "open positions",
            "We're hiring",
        ],
        "location_terms": ["remote", "hybrid", "United States", "Cincinnati", "Ohio", "US Remote"],
        "site_filters": [
            "site:boards.greenhouse.io",
            "site:job-boards.greenhouse.io",
            "site:jobs.lever.co",
            "site:apply.workable.com",
            "site:jobs.ashbyhq.com",
            "site:myworkdayjobs.com",
            "site:wd1.myworkdayjobs.com",
            "site:wd5.myworkdayjobs.com",
            "site:icims.com",
            "site:www.linkedin.com/jobs/view",
            "site:www.dice.com/job-detail",
            "site:jobs.medixteam.com",
        ],
        "host_allowlist_markers": [
            "greenhouse.io",
            "lever.co",
            "workable.com",
            "ashbyhq.com",
            "myworkdayjobs.com",
            "wd1.myworkdayjobs.com",
            "wd5.myworkdayjobs.com",
            "icims.com",
            "linkedin.com",
            "dice.com",
            "medixteam.com",
            "jobs.cardinalhealth.com",
            "careers.atsginc.com",
            "careers.leidos.com",
            "careers.peraton.com",
            "devitjobs.com",
        ],
        "allowed_run_types": ["morning", "afternoon", "full", "lightweight", "fresh_watch"],
        "max_queries": 5,
        "max_results_per_query": 8,
    },
}


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge nested dictionaries with override values taking precedence."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def load_role_filter_settings() -> dict[str, Any]:
    """Load role, filter, and discovery settings from shared JSON plus optional config overrides."""
    settings = deepcopy(_DEFAULT_ROLE_FILTER_SETTINGS)
    if _ROLE_PROFILES_PATH.exists():
        with open(_ROLE_PROFILES_PATH, "r", encoding="utf-8") as file_handle:
            settings = _deep_merge_dicts(settings, json.load(file_handle))

    try:
        config = load_config()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        config = {}

    override_keys = (
        "target_role_clusters",
        "seniority_title_allowlist_patterns",
        "exclude_patterns",
        "exclude_prefixes",
        "seniority_patterns",
        "seniority_allowlist",
        "allowed_location_markers",
        "blocked_location_markers",
        "remote_markers",
        "remote_negation_markers",
        "search_terms",
        "web_discovery",
    )
    config_overrides = {
        key: config[key]
        for key in override_keys
        if key in config
    }
    if config_overrides:
        settings = _deep_merge_dicts(settings, config_overrides)

    startup_discovery_overrides = config.get("startup_discovery", {})
    if isinstance(startup_discovery_overrides, dict) and startup_discovery_overrides:
        web_discovery_updates: dict[str, Any] = {}
        if "google_query_terms" in startup_discovery_overrides:
            web_discovery_updates["query_terms"] = startup_discovery_overrides["google_query_terms"]
        if "max_queries" in startup_discovery_overrides:
            web_discovery_updates["max_queries"] = startup_discovery_overrides["max_queries"]
        if "max_results_per_query" in startup_discovery_overrides:
            web_discovery_updates["max_results_per_query"] = startup_discovery_overrides["max_results_per_query"]
        if "allowed_run_types" in startup_discovery_overrides:
            web_discovery_updates["allowed_run_types"] = startup_discovery_overrides["allowed_run_types"]
        if web_discovery_updates:
            settings = _deep_merge_dicts(settings, {"web_discovery": web_discovery_updates})

    return settings


def clear_role_filter_cache() -> None:
    """Clear cached role filter settings for tests or local reloads."""
    load_role_filter_settings.cache_clear()


def get_target_role_clusters() -> dict[str, list[str]]:
    """Return configured target role clusters."""
    clusters = load_role_filter_settings().get("target_role_clusters", {})
    return {
        str(cluster_name): [str(term) for term in terms]
        for cluster_name, terms in clusters.items()
        if isinstance(terms, list)
    }


def get_target_title_markers() -> tuple[str, ...]:
    """Flatten all configured target role terms into a tuple of title markers."""
    seen_terms: set[str] = set()
    markers: list[str] = []
    for terms in get_target_role_clusters().values():
        for term in terms:
            normalized_term = term.casefold().strip()
            if not normalized_term or normalized_term in seen_terms:
                continue
            seen_terms.add(normalized_term)
            markers.append(normalized_term)
    return tuple(markers)


def get_board_keyword_markers() -> tuple[str, ...]:
    """Return configured short keywords used for board-level prefiltering."""
    search_terms = load_role_filter_settings().get("search_terms", {})
    configured_keywords = search_terms.get("board_keywords", [])
    if configured_keywords:
        return tuple(str(keyword).casefold().strip() for keyword in configured_keywords if str(keyword).strip())
    return get_target_title_markers()


def get_workday_search_terms() -> list[str]:
    """Return configured Workday search terms."""
    search_terms = load_role_filter_settings().get("search_terms", {})
    configured_terms = search_terms.get("workday", [])
    return [str(term).strip() for term in configured_terms if str(term).strip()]


def get_web_discovery_settings() -> dict[str, Any]:
    """Return configured settings for Google or SerpApi-based web discovery."""
    settings = load_role_filter_settings().get("web_discovery", {})
    return dict(settings) if isinstance(settings, dict) else {}


def normalize_filter_text(*parts: str) -> str:
    """Normalize freeform text used in shared job filters."""
    return " ".join(
        re.sub(r"\s+", " ", str(part)).strip().casefold()
        for part in parts
        if str(part).strip()
    )


def _slugify_reason(value: str) -> str:
    """Convert a rejection or role label into a stable slug."""
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _contains_marker(normalized_text: str, marker: str) -> bool:
    """Match short phrase markers safely without accidental substring collisions."""
    normalized_marker = str(marker).casefold().strip()
    if not normalized_marker:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", normalized_marker):
        return re.search(rf"\b{re.escape(normalized_marker)}\b", normalized_text) is not None
    return normalized_marker in normalized_text


def should_exclude_title(title: str) -> bool:
    """Check if a job title should be excluded based on seniority/irrelevance."""
    return get_title_rejection_reason(title) is not None


def get_title_rejection_reason(title: str) -> str | None:
    """Return a stable reason when a title is excluded by shared filters."""
    title_lower = normalize_filter_text(title)
    if not title_lower:
        return None

    settings = load_role_filter_settings()
    allowlist = {
        str(item).casefold().strip()
        for item in settings.get("seniority_allowlist", [])
        if str(item).strip()
    }
    title_allowlist_patterns = settings.get("seniority_title_allowlist_patterns", {}) or {}
    seniority_patterns = settings.get("seniority_patterns", {})
    for seniority_label, pattern in seniority_patterns.items():
        label_key = str(seniority_label).casefold()
        if label_key in allowlist:
            continue
        if re.search(str(pattern), title_lower, re.IGNORECASE):
            allowed_patterns = title_allowlist_patterns.get(seniority_label) or title_allowlist_patterns.get(label_key) or []
            if any(
                re.search(str(allowed), title_lower, re.IGNORECASE)
                for allowed in allowed_patterns
                if str(allowed).strip()
            ):
                continue
            return f"excluded_seniority_{_slugify_reason(str(seniority_label))}"

    for pattern in settings.get("exclude_patterns", []):
        if re.search(str(pattern), title_lower, re.IGNORECASE):
            return f"excluded_title_pattern_{_slugify_reason(str(pattern))}"

    for prefix in settings.get("exclude_prefixes", []):
        if title_lower.startswith(str(prefix).casefold()):
            return f"excluded_title_prefix_{_slugify_reason(str(prefix))}"

    return None


def is_target_tech_title(title: str) -> bool:
    """Return True for titles that fit the user's target technical scope."""
    title_lower = normalize_filter_text(title)
    if not title_lower:
        return False
    settings = load_role_filter_settings()
    non_it_markers = settings.get("exclude_patterns", [])
    if any(marker in title_lower for marker in _HARD_NON_IT_TITLE_MARKERS):
        return False
    if any(
        re.search(str(pattern), title_lower, re.IGNORECASE)
        for pattern in non_it_markers
        if str(pattern).strip()
    ):
        return False
    return any(marker in title_lower for marker in get_target_title_markers())


def description_has_target_scope(description: str) -> bool:
    """Return True when the job description shows strong infra/security/IAM signals."""
    normalized_description = f" {normalize_filter_text(description)} "
    if not normalized_description.strip():
        return False

    strong_hits = {
        marker
        for marker in _DESCRIPTION_SCOPE_STRONG_MARKERS
        if _contains_marker(normalized_description, marker)
    }
    support_hits = {
        marker
        for marker in _DESCRIPTION_SCOPE_SUPPORT_MARKERS
        if _contains_marker(normalized_description, marker)
    }

    if len(strong_hits) >= 2:
        return True
    return bool(strong_hits) and len(support_hits) >= 2


def _matches_preferred_location_markers(
    normalized_text: str,
    *,
    normalized_primary: str,
    settings: dict[str, Any],
) -> bool:
    """Evaluate preferred-location markers without invoking blocked-location logic."""
    wrapped_text = f" {normalized_text} "
    if not wrapped_text.strip():
        return False

    remote_negation_markers = settings.get("remote_negation_markers", [])
    remote_markers = settings.get("remote_markers", [])
    allowed_location_markers = settings.get("allowed_location_markers", [])
    has_allowed_location_marker = any(
        _contains_marker(wrapped_text, str(marker)) for marker in allowed_location_markers
    )
    has_remote_marker = any(
        _contains_marker(wrapped_text, str(marker)) for marker in remote_markers
    )
    has_us_remote_shorthand = bool(
        re.search(r"\bremote\b", normalized_primary)
        and re.search(r"\b(?:us|u\.s\.?|usa)\b", normalized_primary)
    )
    has_remote_negation = any(
        _contains_marker(wrapped_text, str(marker)) for marker in remote_negation_markers
    )
    if not has_remote_negation and has_remote_marker:
        if has_allowed_location_marker or has_us_remote_shorthand:
            return True

        normalized_location_words = re.sub(r"[^a-z ]+", " ", normalized_primary).strip()
        if not normalized_location_words:
            return True
        return normalized_location_words in {"remote", "hybrid", "remote hybrid", "hybrid remote"}

    return has_allowed_location_marker


def matches_blocked_location(
    location: str = "",
    *,
    workplace_type: str = "",
    description: str = "",
) -> bool:
    """Return True when location data indicates the job is outside preferred geography."""
    normalized_primary = normalize_filter_text(location, workplace_type)
    normalized_description = normalize_filter_text(description)
    if not normalized_primary and not normalized_description:
        return False

    primary = f" {normalized_primary} "
    description_text = f" {normalized_description} "
    settings = load_role_filter_settings()
    blocked_markers = settings.get("blocked_location_markers", [])

    if normalized_primary:
        has_primary_blocked_marker = any(
            _contains_marker(primary, str(marker)) for marker in blocked_markers
        )
        if has_primary_blocked_marker:
            return True

        if _matches_preferred_location_markers(
            normalized_primary,
            normalized_primary=normalized_primary,
            settings=settings,
        ):
            remote_negation_markers = settings.get("remote_negation_markers", [])
            return any(
                _contains_marker(description_text, str(marker))
                for marker in remote_negation_markers
            )

    if not normalized_description:
        return False

    return any(_contains_marker(description_text, str(marker)) for marker in blocked_markers)


def matches_preferred_location(
    location: str = "",
    *,
    workplace_type: str = "",
    description: str = "",
) -> bool:
    """Return True for remote roles or Cincinnati-metro onsite/hybrid roles."""
    normalized_location = normalize_filter_text(location, workplace_type)
    normalized_description = normalize_filter_text(description)
    if not normalized_location and not normalized_description:
        return False
    settings = load_role_filter_settings()
    if matches_blocked_location(location, workplace_type=workplace_type, description=description):
        return False

    if normalized_location:
        return _matches_preferred_location_markers(
            normalized_location,
            normalized_primary=normalized_location,
            settings=settings,
        )

    return _matches_preferred_location_markers(
        normalized_description,
        normalized_primary=normalized_description,
        settings=settings,
    )


def infer_reporting_role_clusters(title: str, *, description: str = "") -> list[str]:
    """Map a job into telemetry-friendly role clusters."""
    normalized = f" {normalize_filter_text(title, description)} "
    if not normalized.strip():
        return []

    cluster_markers = {
        "security": ("security", "cyber", "application security", "information security"),
        "iam": ("identity", "iam", "access management", "identity and access"),
        "devops": ("devops", "automation", "platform engineering"),
        "sre": ("site reliability", "sre"),
        "cloud": ("cloud", "azure", "infrastructure", "platform", "systems engineer"),
    }

    matched_clusters: list[str] = []
    for cluster_name, markers in cluster_markers.items():
        if any(_contains_marker(normalized, marker) for marker in markers):
            matched_clusters.append(cluster_name)
    return matched_clusters


def explain_job_targeting(
    title: str,
    *,
    location: str = "",
    workplace_type: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Explain whether a job is in-scope and why it was kept or rejected."""
    title_rejection_reason = get_title_rejection_reason(title)
    target_title = is_target_tech_title(title)
    description_scope_match = description_has_target_scope(description)
    description_rescue = False

    if title_rejection_reason in _DESCRIPTION_RESCUEABLE_REJECTION_REASONS and description_scope_match:
        title_rejection_reason = None
        target_title = True
        description_rescue = True
    elif not title_rejection_reason and not target_title and description_scope_match:
        target_title = True
        description_rescue = True

    blocked_location = matches_blocked_location(
        location,
        workplace_type=workplace_type,
        description=description,
    )
    preferred_location = matches_preferred_location(
        location,
        workplace_type=workplace_type,
        description=description,
    )
    role_clusters = infer_reporting_role_clusters(title, description=description)

    rejection_reasons: list[str] = []
    if title_rejection_reason:
        rejection_reasons.append(title_rejection_reason)
    elif not target_title:
        rejection_reasons.append("non_target_title")

    if blocked_location:
        rejection_reasons.append("blocked_location")
    elif not preferred_location:
        rejection_reasons.append("unpreferred_location")

    keep = not rejection_reasons
    return {
        "keep": keep,
        "decision": "kept" if keep else "rejected",
        "decision_reason": "kept_target_role" if keep else rejection_reasons[0],
        "rejection_reasons": rejection_reasons,
        "role_clusters": role_clusters,
        "preferred_location": preferred_location,
        "blocked_location": blocked_location,
        "target_title": target_title,
        "description_scope_match": description_scope_match,
        "description_rescue": description_rescue,
    }


def should_keep_job(
    title: str,
    *,
    location: str = "",
    workplace_type: str = "",
    description: str = "",
    telemetry: dict[str, Any] | None = None,
    telemetry_source: str = "",
) -> bool:
    """Return True when a job matches title scope and preferred location."""
    explanation = explain_job_targeting(
        title,
        location=location,
        workplace_type=workplace_type,
        description=description,
    )

    if telemetry is not None and telemetry_source:
        from utils.pipeline_telemetry import record_source_filter_decision

        record_source_filter_decision(telemetry, telemetry_source, explanation)

    return bool(explanation["keep"])
