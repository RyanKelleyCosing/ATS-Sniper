#!/usr/bin/env python3
"""
AI Resume Tailoring Engine - Surgical Editor for Hot Jobs

Strategy: Use LLM as a surgical editor, NOT to write from scratch.
This avoids "AI-sounding" resumes while ensuring perfect job alignment.

Process:
1. Context Injection: Feed LLM the JD + Master Resume + Accomplishments Bank
2. Pivot Logic: LLM selects top 5 most relevant accomplishments
3. Summary Rewrite: LLM rewrites Professional Summary for company's tech stack
4. Render: Inject selections into Jinja2 template
5. Export: Generate PDF/DOCX via existing generate_resumes.py

Only runs for HOT jobs (match score >= 80%)
"""

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from openai import OpenAI

from params.experience_dates import (
    RESURGENT_PROMOTION_BULLET_SOURCE,
    RESURGENT_PROMOTION_BULLET_TEMPLATES,
    build_experience_header_line,
)
from utils.openai_chat import create_chat_completion
from utils.runtime_paths import outputs_dir
from utils.state import load_config

# Paths
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
SKILLS_PATH = SCRIPT_DIR / "master_skills.json"
ACCOMPLISHMENTS_PATH = ROOT_DIR / "accomplishments.md"
OUTPUTS_DIR = outputs_dir()

MAX_SKILL_CATEGORIES = 4
MAX_SKILLS_PER_CATEGORY = 5
MAX_TECHNOLOGIES_PER_BULLET = 3
MIN_RELEVANT_TECH_MENTIONS = 2
MAX_SELECTED_ACHIEVEMENTS = 6
PRIMARY_ROLE_BULLET_CAP = 9
TARGET_STRUCTURES_PER_SECTION = 4
MAX_TECHNICAL_ENVIRONMENT_ITEMS = 10
MIN_UNIQUE_BULLET_RATIO = 0.6
REQUIRED_COMPANY_KEYWORD_MENTIONS = 2
MAX_PROMPT_ACCOMPLISHMENTS = 24
MIN_BULLET_KEYWORD_COVERAGE_RATIO = 0.65
MIN_IMPACT_FIRST_BULLET_RATIO = 0.55
MIN_ACTION_CONTEXT_RESULT_RATIO = 0.6
MAX_REQUIRED_JD_PHRASE_REUSE = 4

LEGACY_EXPERIENCE_SECTION_HEADING = "## Experience"
STANDARD_EXPERIENCE_SECTION_HEADING = "## Work Experience"
EXPERIENCE_SECTION_HEADINGS = frozenset(
    {LEGACY_EXPERIENCE_SECTION_HEADING, STANDARD_EXPERIENCE_SECTION_HEADING}
)

IMPACT_METRIC_PATTERN = re.compile(
    r"\b(?:\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?(?:[KMB])?|\d+(?:\.\d+)?x|\d[\d,]*\+?\s*(?:actions?|applications?|alerts?|changes?|dashboards?|days?|deployments?|endpoints?|findings?|hours?|incidents?|minutes?|pipelines?|records?|releases?|services?|tickets?|users?|vulnerabilities?))\b",
    re.IGNORECASE,
)
IMPACT_ACTION_VERBS = frozenset(
    {
        "accelerated",
        "architected",
        "automated",
        "built",
        "containerized",
        "cut",
        "delivered",
        "eliminated",
        "established",
        "executed",
        "hardened",
        "implemented",
        "improved",
        "increased",
        "led",
        "migrated",
        "operated",
        "promoted",
        "optimized",
        "reduced",
        "remediated",
        "restored",
        "secured",
        "stabilized",
        "streamlined",
    }
)
WEAK_BULLET_OPENERS = frozenset(
    {
        "assisted",
        "created",
        "helped",
        "responsible",
        "supported",
        "worked",
    }
)
IMPACT_OUTCOME_MARKERS = (
    "accelerated",
    "cut",
    "eliminated",
    "improved",
    "increased",
    "maintained",
    "promoted",
    "reduced",
    "restored",
    "saved",
    "stabilized",
)
ACTION_CONTEXT_MARKERS = (
    "across",
    "by",
    "during",
    "for",
    "from",
    "in",
    "into",
    "serving",
    "supporting",
    "through",
    "using",
    "with",
    "within",
)
ACTION_RESULT_MARKERS = IMPACT_OUTCOME_MARKERS + (
    "enabled",
    "expanding",
    "maintaining",
    "prevented",
    "preserved",
)

TEMPLATE_HINT_FILES = {
    "adjacent": "resume_adjacent.md",
    "cloud": "resume_cloud.md",
    "database": "resume_system_analyst.md",
    "devops": "resume_devops.md",
    "infrastructure": "resume_infrastructure.md",
    "security": "resume_infrastructure.md",
    "sre": "resume_sre.md",
    "system_analyst": "resume_system_analyst.md",
    "system analyst": "resume_system_analyst.md",
}

PRIMARY_BULLET_THEME_ORDER = (
    "ci_cd",
    "container_iac",
    "security",
    "monitoring",
    "cost",
    "incident",
    "ai_scale",
)

PRIMARY_BULLET_THEME_KEYWORDS = {
    "ci_cd": (
        "argo",
        "azure devops",
        "build",
        "ci/cd",
        "deployment",
        "deployments",
        "gitlab ci",
        "github actions",
        "jenkins",
        "pipeline",
        "pipelines",
        "release",
        "releases",
        "yaml",
    ),
    "container_iac": (
        "aks",
        "bicep",
        "container",
        "containerized",
        "containers",
        "docker",
        "helm",
        "iac",
        "infrastructure as code",
        "kubernetes",
        "provision",
        "provisioning",
        "terraform",
    ),
    "security": (
        "audit",
        "compliance",
        "fedramp",
        "iam",
        "key vault",
        "policy",
        "policies",
        "rbac",
        "risk",
        "scan",
        "scanning",
        "security",
        "soc2",
        "vulnerability",
        "zero-trust",
        "zero trust",
    ),
    "monitoring": (
        "alert",
        "alerts",
        "application insights",
        "availability",
        "dashboard",
        "dashboards",
        "detection",
        "grafana",
        "kql",
        "log analytics",
        "monitor",
        "monitoring",
        "observability",
        "prometheus",
        "uptime",
        "visibility",
    ),
    "cost": (
        "advisor",
        "budget",
        "cost",
        "costs",
        "expense",
        "expenses",
        "rightsizing",
        "savings",
        "spend",
    ),
    "incident": (
        "disaster recovery",
        "dr strategy",
        "incident",
        "incidents",
        "mttr",
        "outage",
        "outages",
        "recovery",
        "response",
        "rpo",
        "rto",
        "runbook",
        "runbooks",
        "self-healing",
        "self healing",
    ),
    "ai_scale": (
        "ai",
        "autoscaling",
        "document intelligence",
        "high-traffic",
        "high traffic",
        "ocr",
        "processing throughput",
        "scale",
        "scaling",
        "throughput",
    ),
}

PROMOTION_BULLET_SOURCE = RESURGENT_PROMOTION_BULLET_SOURCE

COMPANY_KEYWORD_STOPWORDS = {
    "and",
    "capital",
    "company",
    "corp",
    "corporation",
    "financial",
    "group",
    "holdings",
    "inc",
    "llc",
    "services",
    "solutions",
    "systems",
    "technologies",
}

GENERIC_SKILL_SUFFIXES = {
    "administration",
    "alerts",
    "automation",
    "backup",
    "compliance",
    "controls",
    "deployment",
    "deployments",
    "guardrails",
    "management",
    "monitoring",
    "operations",
    "ops",
    "performance",
    "platform",
    "platforms",
    "policies",
    "policy",
    "processes",
    "queries",
    "query",
    "reporting",
    "scanning",
    "security",
    "services",
    "support",
    "tooling",
    "tools",
}

SKILL_FAMILY_DISPLAY_NAMES = {
    "application insights": "Application Insights",
    "arm templates": "ARM Templates",
    "azure container registry": "Azure Container Registry",
    "azure cost management": "Azure Cost Management",
    "azure data factory": "Azure Data Factory",
    "azure devops": "Azure DevOps",
    "azure monitor": "Azure Monitor",
    "azure policy": "Azure Policy",
    "azure site recovery": "Azure Site Recovery",
    "azure sql database": "Azure SQL Database",
    "azure security center": "Azure Security Center",
    "gitlab": "GitLab",
    "gitlab ci": "GitLab CI",
    "gitlab runners": "GitLab Runners",
    "kql": "KQL",
    "powershell": "PowerShell",
    "security scanning": "Security Scanning",
    "ssis": "SSIS",
    "sql server": "SQL Server",
    "t-sql": "T-SQL",
    "yaml pipelines": "YAML Pipelines",
}

ROLE_PROMOTION_FOCUS = {
    "adjacent": "workflow automation",
    "security": "security",
    "sre": "reliability",
    "devops": "automation",
    "cloud": "cost-optimization",
    "infrastructure": "platform engineering",
    "database": "data reliability",
    "mainframe": "modernization",
    "general": "platform delivery",
}

ROLE_PROMOTION_VARIANTS = {
    "adjacent": (
        "software platform support, workflow automation, and implementation delivery",
        "technical troubleshooting, integration work, and dependable release coordination",
        "customer-impacting issue resolution, scripting-led automation, and stable platform support",
        "application support engineering, deployment readiness, and API-driven workflow improvements",
        "integration delivery, technical tooling, and cross-team production support",
    ),
    "security": (
        "enterprise cloud security, zero-trust architecture, and automated compliance tooling",
        "security-first cloud operations, policy-driven delivery, and vulnerability reduction",
        "container hardening, identity controls, and audit-ready release guardrails",
        "secure CI/CD controls, cloud governance, and threat-informed platform delivery",
        "platform security engineering, compliance automation, and production risk reduction",
    ),
    "sre": (
        "full-site reliability engineering, observability platforms, and production incident command",
        "service reliability, alert tuning, and automation-driven recovery engineering",
        "uptime-focused platform operations, incident response leadership, and observability automation",
        "production reliability, post-incident improvement, and resilient recovery planning",
        "monitoring-first platform engineering, service health automation, and incident coordination",
    ),
    "devops": (
        "CI/CD automation, release engineering, and self-service platform delivery",
        "pipeline standardization, environment automation, and faster release orchestration",
        "cloud delivery automation, infrastructure scripting, and repeatable platform changes",
        "build-and-release engineering, operational automation, and deployment reliability",
        "developer enablement, pipeline automation, and hybrid-platform delivery",
    ),
    "cloud": (
        "Azure platform engineering, cost-aware architecture, and infrastructure-as-code delivery",
        "cloud platform design, spend optimization, and repeatable environment provisioning",
        "Azure architecture, landing-zone automation, and cost-conscious service delivery",
        "hybrid-cloud engineering, platform standardization, and predictable infrastructure rollouts",
        "cloud operations, infrastructure automation, and performance-aware platform scaling",
    ),
    "infrastructure": (
        "platform operations, infrastructure automation, and reliability-centered delivery",
        "hybrid-platform engineering, service stability, and repeatable environment changes",
        "platform standardization, operational resilience, and automation-led delivery",
        "infrastructure engineering, monitoring maturity, and dependable release execution",
        "service stability, automation-first operations, and scalable platform support",
    ),
    "database": (
        "SQL performance engineering, ETL automation, and reporting reliability",
        "data platform support, query optimization, and repeatable release coordination",
        "database modernization, stored procedure tuning, and operational reporting delivery",
        "data reliability engineering, migration support, and performance-focused automation",
        "SQL platform operations, reporting improvements, and dependable release execution",
    ),
    "mainframe": (
        "legacy-platform modernization, production support, and automation-led delivery",
        "mainframe support, modernization readiness, and dependable release coordination",
        "enterprise platform support, legacy-to-modern transitions, and operational automation",
        "production application support, modernization planning, and stable delivery execution",
        "legacy-platform reliability, automation improvements, and cross-team release support",
    ),
    "general": (
        "platform automation, cloud architecture, and AI-enabled enterprise delivery",
        "production support, automation-first operations, and scalable platform delivery",
        "service reliability, infrastructure improvements, and platform modernization",
        "automation-led delivery, operational consistency, and enterprise platform support",
        "hybrid-platform operations, architecture improvements, and dependable release execution",
    ),
}

PROMOTION_BULLET_TEMPLATES = RESURGENT_PROMOTION_BULLET_TEMPLATES

ROLE_PLATFORM_LABELS = {
    "adjacent": "Implementation Platforms",
    "security": "Secure Cloud",
    "sre": "Platform Operations",
    "devops": "Cloud Platform",
    "cloud": "Cloud Architecture",
    "infrastructure": "Technical Ecosystem",
    "database": "Data Platform",
    "mainframe": "Legacy Platforms",
    "general": "Cloud Platform",
}

ROLE_OPERATIONS_LABELS = {
    "adjacent": "Support Operations",
    "security": "Security Ops",
    "sre": "Reliability Ops",
    "devops": "Operations",
    "cloud": "Operations",
    "infrastructure": "Operations",
    "database": "Data Operations",
    "mainframe": "Platform Ops",
    "general": "Operations",
}

ROLE_DELIVERY_LABELS = {
    "adjacent": "Implementation Delivery",
    "security": "CI/CD Automation",
    "sre": "CI/CD Automation",
    "devops": "CI/CD Automation",
    "cloud": "CI/CD Automation",
    "infrastructure": "CI/CD Automation",
    "database": "CI/CD Automation",
    "mainframe": "CI/CD Automation",
    "general": "CI/CD Automation",
}

ROLE_AUTOMATION_LABELS = {
    "adjacent": "Automation & Integrations",
    "security": "Scripting & Data",
    "sre": "Scripting & Data",
    "devops": "Scripting & Data",
    "cloud": "Scripting & Data",
    "infrastructure": "Scripting & Data",
    "database": "Data Automation",
    "mainframe": "Scripting & Data",
    "general": "Scripting & Data",
}

ROLE_SKILL_ORDER = {
    "adjacent": ("automation", "operations", "platform", "delivery"),
    "security": ("operations", "platform", "delivery", "automation"),
    "sre": ("operations", "platform", "delivery", "automation"),
    "devops": ("delivery", "platform", "operations", "automation"),
    "cloud": ("platform", "delivery", "operations", "automation"),
    "infrastructure": ("platform", "operations", "delivery", "automation"),
    "database": ("automation", "operations", "platform", "delivery"),
    "mainframe": ("automation", "delivery", "operations", "platform"),
    "general": ("delivery", "platform", "operations", "automation"),
}

ROLE_SUMMARY_OPENINGS = {
    "adjacent": (
        "Technical support and implementation engineer with 5+ years improving workflow automation, "
        "troubleshooting production issues, and keeping business-critical software platforms stable."
    ),
    "security": (
        "Results-driven cloud security engineer with 5+ years securing delivery pipelines, "
        "container platforms, and production operations."
    ),
    "sre": (
        "Site Reliability Engineer with 5+ years improving uptime, observability, and "
        "incident response across production workloads."
    ),
    "devops": (
        "DevOps engineer with 5+ years automating delivery pipelines, Linux operations, and "
        "cloud platform changes."
    ),
    "cloud": (
        "Cloud engineer with 5+ years building Azure infrastructure, infrastructure-as-code "
        "foundations, and cost-aware platform services."
    ),
    "infrastructure": (
        "Infrastructure engineer with 5+ years modernizing hybrid cloud platforms, automation, "
        "and service reliability."
    ),
    "database": (
        "Data and database engineer with 5+ years optimizing SQL workloads, ETL pipelines, and "
        "reporting reliability."
    ),
    "mainframe": (
        "Application engineer with 5+ years supporting production platforms, automation, and "
        "legacy-to-modern delivery transitions."
    ),
    "general": (
        "Systems engineer with 5+ years improving automation, reliability, and delivery across "
        "business-critical platforms."
    ),
}

ROLE_SUMMARY_OUTCOMES = {
    "adjacent": "Uses {tech_phrase} to streamline implementations, reduce support friction, and keep internal or customer-facing platforms running predictably.",
    "security": "Uses {tech_phrase} to harden releases, automate controls, and reduce exposure in regulated environments.",
    "sre": "Applies {tech_phrase} to reduce alert noise, tighten recovery paths, and keep services stable under load.",
    "devops": "Brings hands-on depth with {tech_phrase} to speed releases, standardize environments, and reduce manual work.",
    "cloud": "Uses {tech_phrase} to scale Azure services, tighten spend, and keep infrastructure changes predictable.",
    "infrastructure": "Applies {tech_phrase} to modernize platform operations, standardize builds, and improve service resilience.",
    "database": "Uses {tech_phrase} to improve data throughput, reporting performance, and operational reliability.",
    "mainframe": "Applies {tech_phrase} to improve production support, release coordination, and modernization readiness.",
    "general": "Brings practical experience with {tech_phrase} to improve delivery speed, operational consistency, and platform health.",
}

ROLE_SUMMARY_COMPANY_ALIGNMENT = {
    "adjacent": "supporting the kind of implementation, technical support, integration, and workflow-automation work {company_label} is hiring for.",
    "security": "supporting the kind of security, compliance, and platform-hardening work {company_label} is hiring for.",
    "sre": "supporting the kind of uptime, observability, and incident-response work {company_label} is hiring for.",
    "devops": "supporting the kind of release automation, platform consistency, and operational scale {company_label} is hiring for.",
    "cloud": "supporting the kind of cloud platform, infrastructure-as-code, and cost-aware engineering work {company_label} is hiring for.",
    "infrastructure": "supporting the kind of platform reliability, standards, and operational resilience work {company_label} is hiring for.",
    "database": "supporting the kind of data platform, reporting, and reliability work {company_label} is hiring for.",
    "mainframe": "supporting the kind of modernization, production support, and release coordination work {company_label} is hiring for.",
    "general": "supporting the kind of delivery, automation, and platform support work {company_label} is hiring for.",
}

ROLE_SUMMARY_COMPANY_OUTCOMES = {
    "adjacent": "Uses {tech_phrase} to help {company_label} streamline implementations, reduce support friction, and keep software platforms running predictably.",
    "security": "Brings hands-on depth in {tech_phrase} to help {company_label} harden releases, automate controls, and reduce exposure in regulated environments.",
    "sre": "Applies {tech_phrase} to help {company_label} reduce alert noise, tighten recovery paths, and keep services stable under load.",
    "devops": "Brings hands-on depth in {tech_phrase} to help {company_label} speed releases, standardize environments, and reduce manual work.",
    "cloud": "Uses {tech_phrase} to help {company_label} scale cloud services, tighten spend, and keep infrastructure changes predictable.",
    "infrastructure": "Applies {tech_phrase} to help {company_label} modernize platform operations, standardize builds, and improve service resilience.",
    "database": "Uses {tech_phrase} to help {company_label} improve data throughput, reporting performance, and operational reliability.",
    "mainframe": "Applies {tech_phrase} to help {company_label} improve production support, release coordination, and modernization readiness.",
    "general": "Brings practical experience with {tech_phrase} to help {company_label} improve delivery speed, operational consistency, and platform health.",
}

ROLE_ACCOMPLISHMENT_TAGS = {
    "adjacent": {"adjacent", "system_analyst", "devops", "infrastructure", "sre", "cloud"},
    "security": {"security", "devops", "sre", "infrastructure", "cloud"},
    "sre": {"sre", "devops", "infrastructure", "cloud"},
    "devops": {"devops", "sre", "infrastructure", "cloud"},
    "cloud": {"cloud", "devops", "infrastructure", "sre"},
    "infrastructure": {"infrastructure", "cloud", "devops", "sre"},
    "database": {"database", "system_analyst", "infrastructure", "sre", "cloud"},
    "mainframe": {"mainframe", "system_analyst", "infrastructure", "devops"},
    "general": {"devops", "sre", "cloud", "infrastructure", "system_analyst"},
}

ROLE_ACCOMPLISHMENT_TEXT_MARKERS = {
    "adjacent": (
        "implementation",
        "integration",
        "technical support",
        "software support",
        "troubleshooting",
        "issue resolution",
        "workflow",
        "api",
        "deployment",
        "ticket",
        "knowledge base",
        "documentation",
        "release coordination",
    ),
    "database": (
        "sql server",
        "database",
        "stored procedure",
        "query",
        "execution plan",
        "index",
        "etl",
        "ssis",
        "reporting",
        "data warehouse",
        "ssrs",
        "tableau",
        "power bi",
        "data migration",
    ),
    "system_analyst": (
        "requirements",
        "stakeholder",
        "user story",
        "analysis",
        "assessment",
        "modernization",
        "dependency",
        "cutover",
        "documentation",
        "process",
    ),
}

ROLE_RELEVANCE_ANCHORS = {
    "adjacent": (
        "implementation",
        "integration",
        "support",
        "troubleshooting",
        "workflow",
        "automation",
        "api",
        "sql",
        "powershell",
        "configuration",
        "deployment",
        "testing",
    ),
    "security": ("security", "vulnerability", "compliance", "rbac", "zero-trust", "fedramp", "soc2", "owasp", "scanning"),
    "sre": ("reliability", "incident", "observability", "mttr", "alert", "prometheus", "grafana", "datadog", "kubernetes"),
    "devops": ("ci/cd", "pipeline", "deployment", "jenkins", "ansible", "gitops", "linux", "automation"),
    "cloud": ("cloud", "azure", "aws", "gcp", "terraform", "bicep", "kubernetes", "docker", "aks"),
    "infrastructure": ("infrastructure", "platform", "bicep", "terraform", "monitoring", "availability", "networking"),
    "database": ("sql", "database", "etl", "ssis", "stored procedure", "query", "data migration"),
    "mainframe": ("mainframe", "cobol", "z/os", "zos", "db2", "cics", "jcl", "legacy"),
    "general": (),
}

SKILL_GROUPS = {
    "delivery": {"ci_cd"},
    "platform": {"cloud_platforms", "containers", "iac", "networking"},
    "operations": {"monitoring", "security", "methodologies"},
    "automation": {"scripting", "databases"},
}

SPECIAL_TECH_TERMS = (
    "mainframe",
    "cobol",
    "z/os",
    "zos",
    "db2",
    "cics",
    "jcl",
    "linux",
    "ansible",
    "security scanning",
    "cost optimization",
    "kubernetes",
    "docker",
    "terraform",
    "bicep",
    "azure devops",
    "azure",
    "aws",
    "grafana",
    "prometheus",
    "datadog",
    "key vault",
    "rbac",
    "nsgs",
    "sonarqube",
    "owasp",
)

ATS_MULTIWORD_PHRASE_CANDIDATES = (
    "application security",
    "product security",
    "infrastructure security",
    "cloud security",
    "security operations",
    "incident response",
    "vulnerability management",
    "identity and access management",
    "access management",
    "access reviews",
    "authentication",
    "authorization",
    "infrastructure as code",
    "ci/cd pipelines",
    "ci/cd pipeline",
    "platform reliability",
    "site reliability",
    "cloud platform",
    "platform engineering",
    "production operations",
    "release automation",
    "compliance automation",
    "container security",
    "policy enforcement",
    "threat detection",
    "zero trust",
    "federation",
    "provisioning automation",
    "observability",
)

RESULT_LEAD_MAP = {
    "achieving": "Achieved",
    "reducing": "Reduced",
    "cutting": "Cut",
    "lowering": "Lowered",
    "decreasing": "Reduced",
    "improving": "Improved",
    "increasing": "Increased",
    "boosting": "Boosted",
    "eliminating": "Eliminated",
    "preventing": "Prevented",
    "enabling": "Enabled",
}

GERUND_LEAD_MAP = {
    "architected": "architecting",
    "automated": "automating",
    "built": "building",
    "containerized": "containerizing",
    "created": "creating",
    "defined": "defining",
    "designed": "designing",
    "developed": "developing",
    "eliminated": "eliminating",
    "enabled": "enabling",
    "established": "establishing",
    "hardened": "hardening",
    "implemented": "implementing",
    "improved": "improving",
    "managed": "managing",
    "migrated": "migrating",
    "optimized": "optimizing",
    "orchestrated": "orchestrating",
    "promoted": "expanding",
    "reduced": "reducing",
    "remediated": "remediating",
    "secured": "securing",
    "streamlined": "streamlining",
}

RESULT_OPENERS = {
    "boosted",
    "cut",
    "eliminated",
    "enabled",
    "improved",
    "increased",
    "lowered",
    "prevented",
    "reduced",
}

SCOPE_OPENERS = {"across", "during", "for", "within"}
SCOPE_ALLOWED_TERMS = {
    "alerting",
    "application",
    "applications",
    "audit",
    "audits",
    "checks",
    "ci",
    "cloud",
    "cluster",
    "clusters",
    "compliance",
    "container",
    "containers",
    "deployment",
    "deployments",
    "environment",
    "environments",
    "health",
    "incident",
    "incidents",
    "infrastructure",
    "management",
    "monitoring",
    "non",
    "pipeline",
    "pipelines",
    "platform",
    "platforms",
    "production",
    "resource",
    "resources",
    "response",
    "security",
    "service",
    "services",
    "system",
    "systems",
    "team",
    "teams",
}
SCOPE_BANNED_TERMS = {
    "accuracy",
    "automated",
    "availability",
    "cost",
    "costs",
    "decrease",
    "downtime",
    "increase",
    "improvement",
    "integrity",
    "reduction",
    "time",
    "uptime",
}

BULLET_DUPLICATE_STOPWORDS = {
    "a",
    "an",
    "and",
    "across",
    "application",
    "applications",
    "automation",
    "by",
    "cloud",
    "environment",
    "environments",
    "for",
    "from",
    "hybrid",
    "in",
    "into",
    "of",
    "on",
    "operations",
    "platform",
    "platforms",
    "production",
    "services",
    "support",
    "system",
    "systems",
    "team",
    "teams",
    "the",
    "through",
    "to",
    "using",
    "via",
    "with",
}

ROLE_SENIOR_CONTEXT = {
    "adjacent": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "customer-impacting issue resolution, and stable software delivery."
    ),
    "security": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "pipeline controls, and compliance-focused production support."
    ),
    "sre": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "incident response, and production reliability."
    ),
    "devops": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "release automation, and hybrid-platform support."
    ),
    "cloud": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "cloud changes, and operational support across hybrid environments."
    ),
    "infrastructure": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "platform standards, and service stability."
    ),
    "database": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "data reliability, and release support for reporting platforms."
    ),
    "mainframe": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "application support, and modernization readiness."
    ),
    "general": (
        "Key contributions as Senior Software Support Analyst II centered on {focus}, "
        "automation, and production support."
    ),
}

ROLE_PROMOTION_SCOPE = {
    "adjacent": "implementation delivery, workflow automation, and production platform support",
    "security": "security controls, cloud automation, and AI-enabled platform delivery",
    "sre": "observability, disaster recovery, platform automation, and AI-enabled delivery",
    "devops": "CI/CD automation, cloud operations, and AI-enabled platform delivery",
    "cloud": "cloud architecture, API design, and AI-enabled platform delivery",
    "infrastructure": "platform operations, architecture, and AI-enabled enterprise delivery",
    "database": "platform automation and enterprise application delivery",
    "mainframe": "platform modernization and enterprise application delivery",
    "general": "automation, architecture, and AI-enabled enterprise delivery",
}

# Import from parent directory
sys.path.insert(0, str(ROOT_DIR))


def load_skills_taxonomy() -> dict:
    with open(SKILLS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def is_experience_section_heading(line: str) -> bool:
    """Return True when a markdown line is an accepted experience section heading."""
    return str(line).strip() in EXPERIENCE_SECTION_HEADINGS


def load_accomplishments() -> str:
    """Load raw accomplishments markdown."""
    with open(ACCOMPLISHMENTS_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def resolve_resume_template_file(role: str, template_hint: Optional[str] = None) -> str:
    """Resolve the resume template filename for the target role or an explicit hint."""
    if template_hint:
        hint_key = template_hint.casefold().strip().replace('-', '_')
        if hint_key in TEMPLATE_HINT_FILES:
            return TEMPLATE_HINT_FILES[hint_key]
        if hint_key.endswith('.md'):
            return template_hint.strip()

    skills = load_skills_taxonomy()
    role_lower = role.lower().replace(" ", "_").replace("-", "_")

    for role_key, role_config in skills.get("role_mappings", {}).items():
        if role_key in role_lower or role_lower in role_key:
            return str(role_config.get("template", "resume_devops.md"))

    profile_key = detect_role_profile(role).casefold().strip().replace("-", "_")
    if profile_key in TEMPLATE_HINT_FILES:
        return TEMPLATE_HINT_FILES[profile_key]

    return "resume_devops.md"


def load_resume_template(role: str, template_hint: Optional[str] = None) -> str:
    """Load the appropriate resume template based on role."""
    template_file = resolve_resume_template_file(role, template_hint)
    template_path = ROOT_DIR / template_file
    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    return ""


def detect_role_profile(role: str, job_description: str = "") -> str:
    """Return the tailoring profile that best matches the target role."""
    role_text = role.lower()
    text = f"{role} {job_description}".lower()
    adjacent_role_markers = (
        "implementation engineer",
        "technical implementation engineer",
        "integration engineer",
        "software support",
        "support engineer",
        "application support",
        "platform support",
        "technical support",
        "workflow automation",
        "developer productivity",
        "internal tools",
        "agentic engineer",
    )
    adjacent_text_markers = (
        "implementation",
        "integration",
        "troubleshooting",
        "technical support",
        "application support",
        "platform support",
        "workflow automation",
        "developer productivity",
        "internal tools",
        "api integration",
        "system configuration",
        "customer environment",
        "issue resolution",
        "llm tooling",
        "agentic",
    )
    if any(term in role_text for term in ("mainframe", "cobol", "z/os", "zos", "db2", "cics", "jcl")):
        return "mainframe"
    if any(
        term in role_text
        for term in (
            "security",
            "devsecops",
            "security engineer",
            "application security",
            "identity",
            "iam",
            "access management",
        )
    ):
        return "security"
    if any(term in role_text for term in adjacent_role_markers):
        return "adjacent"
    if any(term in role_text for term in ("site reliability", "sre", "reliability")):
        return "sre"
    if any(term in role_text for term in ("devops", "platform engineer", "release engineer", "build engineer")):
        return "devops"
    if any(term in role_text for term in ("cloud", "cloud engineer", "cloud infrastructure")):
        return "cloud"
    if any(term in role_text for term in ("database", "data engineer", "data platform", "sql")):
        return "database"
    if any(term in role_text for term in ("infrastructure", "systems")):
        return "infrastructure"
    if any(term in text for term in ("mainframe", "cobol", "z/os", "zos", "db2", "cics", "jcl")):
        return "mainframe"
    if any(
        term in text
        for term in (
            "security",
            "devsecops",
            "fedramp",
            "soc2",
            "zero-trust",
            "identity and access management",
            "active directory",
            "entra id",
            "least privilege",
            "rbac",
            "vulnerability management",
        )
    ):
        return "security"
    if any(term in text for term in adjacent_text_markers):
        return "adjacent"
    if any(term in text for term in ("site reliability", "sre", "reliability", "observability", "incident")):
        return "sre"
    if any(term in text for term in ("cloud", "azure", "aws", "gcp")):
        return "cloud"
    if any(term in text for term in ("devops", "platform", "release", "automation")):
        return "devops"
    if any(term in text for term in ("database", "data engineer", "data platform", "sql")):
        return "database"
    if any(term in text for term in ("infrastructure", "systems")):
        return "infrastructure"
    return "general"


def is_senior_role(role: str) -> bool:
    """Return True when the target title implies elevated seniority."""
    return bool(
        re.search(r"\b(senior|sr\.?|lead|principal|staff|ii|iii|iv)\b", role, re.IGNORECASE)
    )


def normalize_company_name(name: str) -> str:
    """Normalize a company name for fuzzy matching across outputs and prompts."""
    return name.lower().replace(" ", "").replace(".", "").replace("-", "")


def extract_required_company_keywords(company: str) -> List[str]:
    """Return distinctive company keywords that must appear in the tailored resume."""
    tokens = re.findall(r"[A-Za-z0-9]+", company)
    distinctive_tokens: List[str] = []
    for token in tokens:
        token_key = token.casefold()
        if token_key in COMPANY_KEYWORD_STOPWORDS or len(token) < 4:
            continue
        if token.isupper() or re.search(r"[A-Z][a-z]+[A-Z][A-Za-z]+", token):
            distinctive_tokens.append(token)

    if not distinctive_tokens and len(tokens) == 1 and len(tokens[0]) >= 4:
        distinctive_tokens.append(tokens[0])

    seen_tokens: set[str] = set()
    ordered_tokens: List[str] = []
    for token in distinctive_tokens:
        token_key = token.casefold()
        if token_key in seen_tokens:
            continue
        seen_tokens.add(token_key)
        ordered_tokens.append(token)

    return ordered_tokens[:2]


def build_default_promotion_bullet_options(role: str, job_description: str) -> List[str]:
    """Return role-aligned promotion bullet variants for the Resurgent experience."""
    profile = detect_role_profile(role, job_description)
    focus_phrases = ROLE_PROMOTION_VARIANTS[profile]
    return [
        PROMOTION_BULLET_TEMPLATES[index].format(focus=focus_phrases[index])
        for index in range(len(PROMOTION_BULLET_TEMPLATES))
    ]


def sanitize_analysis_promotion_options(options: object) -> List[str]:
    """Normalize any promotion bullet candidates returned by the LLM."""
    if not isinstance(options, list):
        return []

    cleaned_options: List[str] = []
    seen_options: set[str] = set()
    for option in options:
        cleaned_option = normalize_bullet_text(str(option))
        if not cleaned_option.casefold().startswith("promoted twice"):
            continue
        if re.search(r"\bfrom\b.+\bto\b.+\bwhile\b", cleaned_option, re.IGNORECASE):
            continue
        option_key = cleaned_option.casefold()
        if option_key in seen_options:
            continue
        seen_options.add(option_key)
        cleaned_options.append(cleaned_option)

    return cleaned_options


def sanitize_skill_name(skill: str) -> str:
    """Remove inference markers and normalize skill spacing."""
    cleaned_skill = re.sub(r'\*+', '', skill).strip()
    cleaned_skill = re.sub(r"\s+", " ", cleaned_skill)
    return cleaned_skill.strip(" .;:,")


def find_exact_phrase(text: str, phrase: str) -> str:
    """Return the first case-preserving whole-phrase match from text."""
    match = re.search(
        rf"(?<![A-Za-z0-9]){re.escape(str(phrase).strip())}(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    )
    return sanitize_skill_name(match.group(0)) if match else ""


def build_role_phrase_targets(role: str) -> List[str]:
    """Return normalized role phrases that can be reused naturally in a resume."""
    normalized_role = sanitize_skill_name(role)
    if not normalized_role:
        return []

    candidates: List[str] = []
    for candidate in (
        normalized_role,
        re.sub(r"\s*\([^)]*\)", "", normalized_role),
    ):
        cleaned_candidate = re.sub(
            r"\b(?:onsite|on-site|remote|hybrid)\b",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        cleaned_candidate = re.sub(
            r"\b(?:I|II|III|IV|V|VI|1|2|3|4|5|6)\b",
            "",
            cleaned_candidate,
            flags=re.IGNORECASE,
        )
        cleaned_candidate = re.sub(r"\(\s*\)", "", cleaned_candidate)
        cleaned_candidate = sanitize_skill_name(re.sub(r"\s+", " ", cleaned_candidate))
        if len(cleaned_candidate.split()) < 2:
            continue
        candidates.append(cleaned_candidate)

    deduped_candidates: List[str] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        candidate_key = candidate.casefold()
        if candidate_key in seen_candidates:
            continue
        seen_candidates.add(candidate_key)
        deduped_candidates.append(candidate)

    return deduped_candidates


def tokenize_phrase_support(text: str) -> set[str]:
    """Return lowercase tokens used to validate phrase support from source material."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if len(token) >= 2
    }


def phrase_is_supported_by_source(
    phrase: str,
    support_text: str,
    support_tokens: Optional[set[str]] = None,
) -> bool:
    """Return True when the source material can plausibly support reusing a JD phrase."""
    normalized_support_text = re.sub(r"\s+", " ", support_text).strip()
    if not normalized_support_text:
        return True
    if find_exact_phrase(normalized_support_text, phrase):
        return True

    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", phrase.casefold())
        if len(token) >= 3 and token not in {"and", "for", "the", "with"}
    ]
    if not tokens:
        return False

    normalized_support_tokens = support_tokens or tokenize_phrase_support(normalized_support_text)
    return all(token in normalized_support_tokens for token in tokens)


def normalize_skill_category_label(category: str, role: str = "") -> str:
    """Map skill category labels to whole-word, layout-safe titles."""
    normalized = re.sub(r"\s+", " ", str(category).replace("_", " ")).strip()
    lowered = normalized.lower()
    role_profile = detect_role_profile(role)

    if "cloud" in lowered or "infra" in lowered or "platform" in lowered:
        return ROLE_PLATFORM_LABELS[role_profile]
    if "monitor" in lowered or "observ" in lowered:
        return ROLE_OPERATIONS_LABELS[role_profile]
    if "security" in lowered:
        return "Security Ops"
    if "script" in lowered or "database" in lowered or "data" in lowered:
        return ROLE_AUTOMATION_LABELS[role_profile]
    if "ci/cd" in lowered or "automation" in lowered or "delivery" in lowered:
        return ROLE_DELIVERY_LABELS[role_profile]
    return normalized.title()


def normalize_tailored_skills(raw_skills: object, role: str = "") -> Dict[str, List[str]]:
    """Limit tailored skills to compact, display-safe categories and items."""
    if not isinstance(raw_skills, dict):
        return {}

    normalized: Dict[str, List[str]] = {}
    for category, values in raw_skills.items():
        category_name = normalize_skill_category_label(str(category), role)
        if not category_name:
            continue

        skill_values = values if isinstance(values, list) else [values]
        cleaned_skills: List[str] = []
        seen_skills: set[str] = set()

        for value in skill_values:
            skill = sanitize_skill_name(str(value))
            if not skill:
                continue

            skill_key = skill.casefold()
            if skill_key in seen_skills:
                continue

            seen_skills.add(skill_key)
            cleaned_skills.append(skill)
            if len(cleaned_skills) == MAX_SKILLS_PER_CATEGORY:
                break

        if cleaned_skills:
            normalized[category_name] = cleaned_skills

        if len(normalized) == MAX_SKILL_CATEGORIES:
            break

    return normalized


def split_technologies(technology_text: str) -> List[str]:
    """Split a technology suffix into a normalized, unique list."""
    cleaned_items: List[str] = []
    seen_items: set[str] = set()

    for item in technology_text.split(','):
        skill = sanitize_skill_name(item)
        if not skill:
            continue
        skill_key = skill.casefold()
        if skill_key in seen_items:
            continue
        seen_items.add(skill_key)
        cleaned_items.append(skill)

    return cleaned_items


def extract_priority_terms(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> List[str]:
    """Collect high-signal terms from the title, JD, and detected stack."""
    taxonomy = load_skills_taxonomy().get("skills_taxonomy", {})
    search_text = f"{role} {job_description}".lower()
    candidate_terms: List[str] = []

    for tech in detected_tech_stack:
        normalized = sanitize_skill_name(str(tech))
        if normalized:
            candidate_terms.append(normalized)

    for category in taxonomy.values():
        for keyword in category.get("keywords", []):
            if keyword in search_text:
                candidate_terms.append(keyword)

    for term in SPECIAL_TECH_TERMS:
        if term in search_text:
            candidate_terms.append(term)

    deduped: List[str] = []
    seen_terms: set[str] = set()
    for term in candidate_terms:
        normalized = sanitize_skill_name(term)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen_terms:
            continue
        seen_terms.add(key)
        deduped.append(normalized)

    return deduped


def extract_exact_jd_phrase_targets(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    *,
    max_terms: int = 10,
    support_text: str = "",
) -> List[str]:
    """Extract high-signal exact JD phrases to preserve in the tailored output."""
    search_text = re.sub(r"\s+", " ", f"{role}\n{job_description}").strip()
    if not search_text:
        return []

    candidates: List[str] = []
    role_phrase_targets = build_role_phrase_targets(role)
    candidates.extend(role_phrase_targets)

    for phrase in ATS_MULTIWORD_PHRASE_CANDIDATES:
        matched_phrase = find_exact_phrase(search_text, phrase)
        if matched_phrase:
            candidates.append(matched_phrase)

    for technology in detected_tech_stack:
        cleaned_technology = sanitize_skill_name(str(technology))
        if not cleaned_technology:
            continue
        if len(cleaned_technology.split()) < 2 and "/" not in cleaned_technology:
            continue
        matched_technology = find_exact_phrase(search_text, cleaned_technology)
        if matched_technology:
            candidates.append(matched_technology)

    for term in extract_priority_terms(role, job_description, detected_tech_stack):
        cleaned_term = sanitize_skill_name(term)
        if not cleaned_term:
            continue
        if len(cleaned_term.split()) < 2 and "/" not in cleaned_term and not cleaned_term.isupper():
            continue
        matched_term = find_exact_phrase(search_text, cleaned_term)
        if matched_term:
            candidates.append(matched_term)

    deduped_targets: List[str] = []
    seen_targets: set[str] = set()
    role_target_keys = {candidate.casefold() for candidate in role_phrase_targets}
    normalized_support_text = re.sub(r"\s+", " ", support_text).strip()
    support_tokens = tokenize_phrase_support(normalized_support_text)
    for candidate in candidates:
        candidate_key = candidate.casefold()
        if candidate_key in seen_targets:
            continue
        if (
            normalized_support_text
            and candidate_key not in role_target_keys
            and not phrase_is_supported_by_source(candidate, normalized_support_text, support_tokens)
        ):
            continue
        seen_targets.add(candidate_key)
        deduped_targets.append(candidate)
        if len(deduped_targets) == max_terms:
            break

    return deduped_targets


def categorize_technology(technology: str) -> Optional[str]:
    """Map a technology string to the closest taxonomy category."""
    skill_taxonomy = load_skills_taxonomy().get("skills_taxonomy", {})
    lowered = sanitize_skill_name(technology).casefold()
    technology_tokens = set(re.findall(r"[a-z0-9]+", lowered))

    if not lowered or not technology_tokens:
        return None

    for category_key, category in skill_taxonomy.items():
        for keyword in category.get("keywords", []):
            keyword_lower = sanitize_skill_name(str(keyword)).casefold()
            if keyword_lower == lowered:
                return category_key

    for category_key, category in skill_taxonomy.items():
        for keyword in category.get("keywords", []):
            keyword_lower = sanitize_skill_name(str(keyword)).casefold()
            keyword_tokens = set(re.findall(r"[a-z0-9]+", keyword_lower))
            if len(keyword_tokens) >= 2 and keyword_tokens.issubset(technology_tokens):
                return category_key

    if "security" in technology_tokens or "compliance" in technology_tokens:
        return "security"
    if technology_tokens & {"monitor", "monitoring", "observability", "alerts", "alert", "kql"}:
        return "monitoring"
    if technology_tokens & {"python", "powershell", "bash", "shell", "go", "golang"}:
        return "scripting"
    if technology_tokens & {"sql", "ssql", "postgresql", "mysql", "mongodb", "redis", "database"}:
        return "databases"

    return None


def map_category_to_skill_group(category_key: Optional[str]) -> Optional[str]:
    """Translate detailed taxonomy categories into the rendered skill buckets."""
    if not category_key:
        return None
    for group_key, category_keys in SKILL_GROUPS.items():
        if category_key in category_keys:
            return group_key
    return None


def score_technology(
    technology: str,
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> int:
    """Score a technology by job relevance for ordering and trimming."""
    technology_lower = technology.casefold()
    score = 0

    for term in extract_priority_terms(role, job_description, detected_tech_stack):
        term_lower = term.casefold()
        if term_lower == technology_lower:
            score += 6
        elif term_lower in technology_lower or technology_lower in term_lower:
            score += 4

    profile = detect_role_profile(role, job_description)
    category_key = categorize_technology(technology)
    if category_key in SKILL_GROUPS.get(ROLE_SKILL_ORDER[profile][0], set()):
        score += 2

    return score


def order_technologies(
    technologies: Sequence[str],
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> List[str]:
    """Reorder technologies so the most relevant tools appear first."""
    indexed_items = list(enumerate(technologies))
    ranked_items = sorted(
        indexed_items,
        key=lambda item: (
            -score_technology(item[1], role, job_description, detected_tech_stack),
            item[0],
        ),
    )
    return [technology for _, technology in ranked_items]


def build_focus_phrase(technologies: Sequence[str]) -> str:
    """Build a readable lead-in from the top technologies for a bullet."""
    if not technologies:
        return "automation"
    if len(technologies) == 1:
        return technologies[0]
    if len(technologies) == 2:
        return f"{technologies[0]} and {technologies[1]}"
    return f"{technologies[0]}, {technologies[1]}, and {technologies[2]}"


def build_summary_opening(role: str, profile: str) -> str:
    """Choose a more natural summary opening for role-specific analyst and DBA titles."""
    role_text = role.casefold()
    if profile == "security":
        if any(term in role_text for term in ("identity", "access management", "iam")):
            return (
                "Security-focused IAM analyst with 5+ years supporting access controls, "
                "vulnerability remediation, and production operations"
            )
        if "analyst" in role_text:
            return (
                "Security-focused analyst with 5+ years supporting access controls, "
                "vulnerability remediation, and production operations"
            )
    if profile == "database":
        if "database administrator" in role_text or "sql dba" in role_text or re.search(
            r"\bdba\b",
            role_text,
        ):
            return (
                "SQL DBA with 5+ years optimizing SQL Server workloads, supporting Azure SQL migrations, "
                "and improving reporting and operational data reliability"
            )

    return ROLE_SUMMARY_OPENINGS[profile].rstrip(".")


def build_summary_outcome(role: str, profile: str, tech_phrase: str) -> str:
    """Keep summary outcomes aligned with analyst-oriented security roles."""
    role_text = role.casefold()
    if profile == "security":
        if any(term in role_text for term in ("identity", "access management", "iam")):
            return (
                f"Uses {tech_phrase} to strengthen access controls, automate security workflows, "
                "and reduce exposure in regulated environments."
            )
        if "analyst" in role_text:
            return (
                f"Uses {tech_phrase} to improve incident visibility, tighten controls, "
                "and reduce exposure in regulated environments."
            )

    return ROLE_SUMMARY_OUTCOMES[profile].format(tech_phrase=tech_phrase)


def prioritize_summary_technologies(
    ordered_technologies: Sequence[str],
    role: str,
    profile: str,
) -> List[str]:
    """Promote the most role-specific terms into the summary tech phrase."""
    if profile != "security":
        return list(ordered_technologies)

    role_text = role.casefold()
    if any(term in role_text for term in ("identity", "access management", "iam")):
        preferred_terms = (
            "active directory",
            "microsoft entra id",
            "entra id",
            "rbac",
            "powershell",
            "incident response",
            "microsoft defender",
        )
    elif "analyst" in role_text:
        preferred_terms = (
            "incident response",
            "microsoft defender",
            "vulnerability management",
            "fortify",
            "active directory",
            "rbac",
        )
    else:
        return list(ordered_technologies)

    prioritized: List[str] = []
    seen_keys: set[str] = set()
    for preferred_term in preferred_terms:
        for technology in ordered_technologies:
            technology_key = technology.casefold()
            if technology_key in seen_keys:
                continue
            if preferred_term in technology_key:
                prioritized.append(technology)
                seen_keys.add(technology_key)
                break

    for technology in ordered_technologies:
        technology_key = technology.casefold()
        if technology_key in seen_keys:
            continue
        prioritized.append(technology)
        seen_keys.add(technology_key)

    return prioritized


def lowercase_sentence_lead(text: str) -> str:
    """Lowercase the first character after a clause lead-in when safe."""
    if not text:
        return ""
    if len(text) > 1 and text[:2].isupper():
        return text
    return text[0].lower() + text[1:]


def strip_legacy_using_prefix(text: str) -> str:
    """Remove the old templated bullet prefix when it appears."""
    return re.sub(r"^Using [^,]+,\s*", "", text, flags=re.IGNORECASE).strip()


def polish_generated_bullet_text(text: str) -> str:
    """Apply narrow wording cleanups to generated bullets without changing meaning."""
    polished = text
    replacements = (
        (
            r"^For ([^,]+), built ([^,]+)(.*)$",
            r"Built \2 for \1\3",
        ),
        (
            r"^Built automated security scanning for ([^,]+),",
            r"Implemented automated security scanning in \1,",
        ),
        (
            r"^Built automated security scanning processes for ([^,]+),",
            r"Implemented automated security scanning for \1,",
        ),
        (
            r"^Built a zero-trust architecture for ([^,]+),",
            r"Implemented zero-trust controls across \1,",
        ),
        (
            r"^Built security controls in ([^,]+),",
            r"Strengthened security controls across \1,",
        ),
        (
            r"^Built automated scripts for ([^,]+), reducing manual tasks by",
            r"Automated \1, reducing manual work by",
        ),
        (
            r"^Built automated runbooks for ([^,]+),",
            r"Implemented automated runbooks for \1,",
        ),
        (
            r",\s*with compliance with SLAs",
            r", helping teams stay within SLA targets",
        ),
        (
            r"\bwith compliance with\b",
            "supporting",
        ),
        (
            r", supporting ([^,]+), reducing",
            r", supporting \1 and reducing",
        ),
        (
            r",\s*with data integrity and reducing",
            ", preserving data integrity and reducing",
        ),
        (
            r",\s*with high-quality datasets for analytics",
            ", producing higher-quality datasets for analytics",
        ),
        (
            r"enhancing visibility for teams and reducing mean time to detection",
            "improving team visibility and cutting mean time to detection",
        ),
        (
            r"enhancing real-time visibility and reducing mean time to detection",
            "improving real-time visibility and cutting mean time to detection",
        ),
        (
            r"improving real-time visibility and cutting mean time to detection",
            "improving real-time visibility and reducing mean time to detection",
        ),
        (
            r"reducing setup time by ([0-9]+%) and enabling self-service deployment",
            r"cutting setup time by \1 while enabling self-service deployment",
        ),
        (
            r"while with ([^,.;]+)",
            r"while maintaining \1",
        ),
        (
            r"with zero downtime for users",
            "without user-facing downtime",
        ),
        (
            r",\s*with zero downtime and maintaining data integrity",
            ", with zero downtime while preserving data integrity",
        ),
    )

    for pattern, replacement in replacements:
        polished = re.sub(pattern, replacement, polished, flags=re.IGNORECASE)

    polished = re.sub(r"\s+", " ", polished).strip()
    polished = re.sub(r"\s+([,.;:])", r"\1", polished)
    return polished


def normalize_bullet_text(text: str) -> str:
    """Normalize bullet text without forcing a templated lead-in."""
    cleaned = re.sub(r"\s+", " ", text.replace("**", "").replace("*", " ")).strip()
    cleaned = strip_legacy_using_prefix(cleaned)
    cleaned = re.sub(r",\s*for a\s+", ", achieving a ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return polish_generated_bullet_text(cleaned)


def to_gerund_phrase(text: str) -> str:
    """Convert a leading action verb into a gerund phrase when possible."""
    cleaned = normalize_bullet_text(text)
    if not cleaned:
        return ""

    parts = cleaned.split(maxsplit=1)
    lead_word = parts[0].casefold()
    remainder = parts[1] if len(parts) > 1 else ""
    if lead_word in GERUND_LEAD_MAP:
        return f"{GERUND_LEAD_MAP[lead_word]} {remainder}".strip()
    return lowercase_sentence_lead(cleaned)


def prefer_scope_led_variant(text: str) -> Optional[str]:
    """Move a strong scope phrase to the front of the sentence when helpful."""
    match = re.search(r"\b(across|during|within)\s+([^,]+)", text, re.IGNORECASE)
    if not match:
        return None

    if match.start() > 45:
        return None

    scope_tokens = set(re.findall(r"[a-z0-9]+", match.group(2).casefold()))
    if scope_tokens & SCOPE_BANNED_TERMS:
        return None
    if not scope_tokens & SCOPE_ALLOWED_TERMS:
        return None

    scope_phrase = f"{match.group(1).title()} {match.group(2).strip()}"
    remainder = (text[:match.start()] + text[match.end():]).strip(" ,.")
    remainder = re.sub(r"\s+([,.;:])", r"\1", remainder)
    remainder = re.sub(r",\s*,+", ", ", remainder)
    if not remainder:
        return None

    return f"{scope_phrase}, {lowercase_sentence_lead(remainder)}"


def prefer_audience_led_variant(text: str) -> Optional[str]:
    """Move a meaningful audience clause to the front to vary repeated action leads."""
    match = re.search(r"\bfor\s+([^,]+)", text, re.IGNORECASE)
    if not match or match.start() > 55:
        return None

    audience_phrase = match.group(1).strip()
    audience_tokens = set(re.findall(r"[a-z0-9]+", audience_phrase.casefold()))
    if not audience_tokens & {"application", "applications", "team", "teams", "service", "services"}:
        return None

    remainder = (text[:match.start()] + text[match.end():]).strip(" ,.")
    remainder = re.sub(r"\s+([,.;:])", r"\1", remainder)
    remainder = re.sub(r",\s*,+", ", ", remainder)
    if not remainder:
        return None

    return f"For {audience_phrase}, {lowercase_sentence_lead(remainder)}"


def prefer_outcome_led_variant(text: str) -> Optional[str]:
    """Lead with the measurable outcome when the bullet already includes one."""
    match = re.search(
        r"^(.*?),\s*(achieving|reducing|cutting|lowering|decreasing|improving|increasing|boosting|eliminating|preventing|enabling)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    action_text = match.group(1).strip().rstrip(".")
    outcome_verb = RESULT_LEAD_MAP.get(match.group(2).casefold())
    outcome_text = match.group(3).strip().rstrip(".")
    if re.search(
        r"\band\s+(achieving|reducing|cutting|lowering|decreasing|improving|increasing|boosting|eliminating|preventing|enabling)\b",
        outcome_text,
        re.IGNORECASE,
    ):
        return None
    gerund_phrase = to_gerund_phrase(action_text)
    if not outcome_verb or not gerund_phrase:
        return None

    return f"{outcome_verb} {outcome_text} by {gerund_phrase}"


def build_bullet_variants(text: str) -> List[str]:
    """Return human-sounding structural variants for a bullet."""
    cleaned = normalize_bullet_text(text)
    candidates = [
        prefer_outcome_led_variant(cleaned),
        prefer_scope_led_variant(cleaned),
        prefer_audience_led_variant(cleaned),
        cleaned,
    ]

    variants: List[str] = []
    seen_variants: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen_variants:
            continue
        seen_variants.add(key)
        variants.append(candidate)

    return variants or [cleaned]


def choose_best_bullet_variant(
    text: str,
    historical_signatures: Sequence[str],
) -> str:
    """Prefer a variant that has not already appeared in prior tailored resumes."""
    seen_history = {signature.casefold() for signature in historical_signatures}
    variants = build_bullet_variants(text)
    for variant in variants:
        if make_bullet_signature(variant) not in seen_history:
            return variant
    return variants[0]


def extract_opening_pattern(text: str) -> str:
    """Capture the opening phrase used to detect repeated bullet starts."""
    tokens = re.findall(r"[A-Za-z0-9/+.-]+", text.casefold())
    if not tokens:
        return ""
    if tokens[0] in SCOPE_OPENERS and len(tokens) > 1:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0]


def extract_sentence_structure(text: str) -> str:
    """Classify a bullet's opening structure for variety checks."""
    opening = extract_opening_pattern(text)
    if not opening:
        return ""

    first_word = opening.split()[0]
    if first_word in SCOPE_OPENERS:
        return f"scope:{first_word}"
    if first_word in RESULT_OPENERS:
        return "outcome"
    if first_word.endswith("ing"):
        return "gerund"
    return f"action:{first_word}"


def ensure_section_sentence_variety(
    entries: Sequence[Tuple[str, Sequence[str]]],
) -> List[Tuple[str, List[str]]]:
    """Reduce repeated openings and encourage varied sentence shapes per section."""
    if not entries:
        return []

    varied_entries: List[Tuple[str, List[str]]] = []
    opening_counts: Dict[str, int] = {}
    structure_counts: Dict[str, int] = {}
    required_structures = min(TARGET_STRUCTURES_PER_SECTION, len(entries))

    for text, technologies in entries:
        variants = build_bullet_variants(text)
        chosen_text = variants[0]
        for variant in variants:
            opening = extract_opening_pattern(variant)
            if opening_counts.get(opening, 0) == 0:
                chosen_text = variant
                break

        chosen_opening = extract_opening_pattern(chosen_text)
        chosen_structure = extract_sentence_structure(chosen_text)
        opening_counts[chosen_opening] = opening_counts.get(chosen_opening, 0) + 1
        structure_counts[chosen_structure] = structure_counts.get(chosen_structure, 0) + 1
        varied_entries.append((chosen_text, [sanitize_skill_name(str(item)) for item in technologies]))

    if len(structure_counts) >= required_structures:
        return varied_entries

    for index, (text, technologies) in enumerate(varied_entries):
        current_structure = extract_sentence_structure(text)
        if structure_counts.get(current_structure, 0) <= 1:
            continue

        for variant in build_bullet_variants(text)[1:]:
            variant_structure = extract_sentence_structure(variant)
            if variant_structure in structure_counts:
                continue

            structure_counts[current_structure] -= 1
            structure_counts[variant_structure] = 1
            varied_entries[index] = (variant, technologies)
            break

        if len(structure_counts) >= required_structures:
            break

    return varied_entries


def classify_primary_bullet_theme(
    text: str,
    technologies: Sequence[str],
) -> str:
    """Assign a primary theme so the main experience section can avoid repeats."""
    haystack = f"{text} {' '.join(technologies)}".casefold()
    theme_scores = {theme: 0 for theme in PRIMARY_BULLET_THEME_ORDER}

    for theme, keywords in PRIMARY_BULLET_THEME_KEYWORDS.items():
        for keyword in keywords:
            if keyword in haystack:
                theme_scores[theme] += 1

    if any(term in haystack for term in ("runbook", "runbooks", "incident", "mttr", "rto", "rpo", "self-healing", "disaster recovery")):
        theme_scores["incident"] += 3
    if any(term in haystack for term in ("uptime", "dashboard", "dashboards", "visibility", "detection", "observability")):
        theme_scores["monitoring"] += 2
    if any(term in haystack for term in ("bicep", "terraform", "docker", "kubernetes", "aks", "provisioning", "containerized")):
        theme_scores["container_iac"] += 2
    if any(term in haystack for term in ("security", "compliance", "soc2", "fedramp", "zero-trust", "zero trust", "rbac", "policy", "policies")):
        theme_scores["security"] += 2
    if any(term in haystack for term in ("cost", "spend", "rightsizing", "savings", "advisor")):
        theme_scores["cost"] += 3
    if any(term in haystack for term in ("pipeline", "pipelines", "ci/cd", "azure devops", "jenkins", "release")):
        theme_scores["ci_cd"] += 2
    if any(term in haystack for term in ("ai", "document intelligence", "ocr", "throughput", "autoscaling", "scaling")):
        theme_scores["ai_scale"] += 2

    best_theme = max(PRIMARY_BULLET_THEME_ORDER, key=lambda theme: theme_scores[theme])
    if theme_scores[best_theme] <= 0:
        return "other"
    return best_theme


def score_primary_bullet_entry(
    text: str,
    technologies: Sequence[str],
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> int:
    """Score competing bullets within a theme so the strongest one survives deduping."""
    haystack = f"{text} {' '.join(technologies)}".casefold()
    score = 0
    score += len(re.findall(r"\b\d[\d,]*(?:\.\d+)?(?:%|x|\+)?\b", text)) * 3
    score += len({sanitize_skill_name(str(item)).casefold() for item in technologies})
    for term in extract_priority_terms(role, job_description, detected_tech_stack):
        if term.casefold() in haystack:
            score += 1
    if any(keyword in haystack for keyword in ("achieved", "cut", "improved", "optimized", "reduced", "slashed")):
        score += 1
    return score


def build_primary_theme_order(
    role: str,
    job_description: str,
    available_themes: Sequence[str],
    seed_text: str,
) -> List[str]:
    """Keep a consistent theme spread while introducing small resume-to-resume variation."""
    ordered_themes = list(PRIMARY_BULLET_THEME_ORDER)
    profile = detect_role_profile(role, job_description)
    available = set(available_themes)

    if profile == "security" and "security" in available and "container_iac" in available:
        security_index = ordered_themes.index("security")
        container_index = ordered_themes.index("container_iac")
        ordered_themes[security_index], ordered_themes[container_index] = (
            ordered_themes[container_index],
            ordered_themes[security_index],
        )
    if profile == "sre" and "incident" in available and "cost" in available:
        incident_index = ordered_themes.index("incident")
        cost_index = ordered_themes.index("cost")
        ordered_themes[incident_index], ordered_themes[cost_index] = (
            ordered_themes[cost_index],
            ordered_themes[incident_index],
        )

    swap_candidates = [
        ("monitoring", "cost"),
        ("cost", "incident"),
    ]
    valid_swaps = [pair for pair in swap_candidates if pair[0] in available and pair[1] in available]
    if valid_swaps:
        rng = random.Random(seed_text)
        theme_a, theme_b = valid_swaps[rng.randrange(len(valid_swaps))]
        index_a = ordered_themes.index(theme_a)
        index_b = ordered_themes.index(theme_b)
        ordered_themes[index_a], ordered_themes[index_b] = ordered_themes[index_b], ordered_themes[index_a]

    return ordered_themes


def organize_primary_section_entries(
    entries: Sequence[Tuple[str, List[str]]],
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    seed_text: str,
    priority_by_signature: Optional[Dict[str, int]] = None,
) -> List[Tuple[str, List[str]]]:
    """Spread primary-section bullets across distinct themes before final rendering."""
    if not entries:
        return []

    effective_priorities = priority_by_signature or {}

    promotion_entries: List[Tuple[str, List[str]]] = []
    remaining_entries: List[Tuple[str, List[str]]] = []
    for text, technologies in entries:
        if not promotion_entries and text.casefold().startswith("promoted twice"):
            promotion_entries.append((text, technologies))
            continue
        remaining_entries.append((text, technologies))

    if len(remaining_entries) <= 2:
        return promotion_entries + remaining_entries

    scored_entries: List[Dict[str, object]] = []
    for index, (text, technologies) in enumerate(remaining_entries):
        theme = classify_primary_bullet_theme(text, technologies)
        score = score_primary_bullet_entry(
            text,
            technologies,
            role,
            job_description,
            detected_tech_stack,
        )
        scored_entries.append(
            {
                "index": index,
                "text": text,
                "technologies": technologies,
                "theme": theme,
                "priority": effective_priorities.get(make_bullet_signature(text), 0),
                "score": score,
            }
        )

    available_themes = [str(item["theme"]) for item in scored_entries]
    theme_order = build_primary_theme_order(role, job_description, available_themes, seed_text)
    desired_count = min(len(remaining_entries), PRIMARY_ROLE_BULLET_CAP - len(promotion_entries))

    ordered_entries: List[Tuple[str, List[str]]] = []
    used_indexes: set[int] = set()
    used_themes: set[str] = set()

    for theme in theme_order:
        theme_candidates = sorted(
            [item for item in scored_entries if item["theme"] == theme and int(item["index"]) not in used_indexes],
            key=lambda item: (-int(item["priority"]), -int(item["score"]), int(item["index"])),
        )
        if not theme_candidates:
            continue

        chosen_entry = theme_candidates[0]
        used_indexes.add(int(chosen_entry["index"]))
        used_themes.add(str(chosen_entry["theme"]))
        ordered_entries.append(
            (str(chosen_entry["text"]), list(chosen_entry["technologies"]))
        )

    extra_candidates = sorted(
        [item for item in scored_entries if int(item["index"]) not in used_indexes],
        key=lambda item: (-int(item["priority"]), -int(item["score"]), int(item["index"])),
    )
    for item in extra_candidates:
        theme = str(item["theme"])
        if theme in used_themes and len(ordered_entries) >= desired_count:
            continue
        if theme in used_themes:
            continue
        used_indexes.add(int(item["index"]))
        used_themes.add(theme)
        ordered_entries.append((str(item["text"]), list(item["technologies"])))
        if len(ordered_entries) >= desired_count:
            break

    minimum_unique_floor = min(desired_count, 6)
    if len(ordered_entries) < minimum_unique_floor:
        fallback_candidates = sorted(
            [item for item in scored_entries if int(item["index"]) not in used_indexes],
            key=lambda item: (-int(item["priority"]), -int(item["score"]), int(item["index"])),
        )
        for item in fallback_candidates:
            ordered_entries.append((str(item["text"]), list(item["technologies"])))
            if len(ordered_entries) >= minimum_unique_floor:
                break

    return promotion_entries + ordered_entries


def tokenize_bullet_text(text: str) -> set[str]:
    """Tokenize bullet text for fuzzy duplicate detection."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if len(token) > 2 and token not in BULLET_DUPLICATE_STOPWORDS
    }


def build_bullet_record(text: str, technologies: Sequence[str]) -> Dict[str, object]:
    """Capture the normalized text and technology footprint of a bullet."""
    return {
        "text": text,
        "tokens": tokenize_bullet_text(text),
        "techs": {
            sanitize_skill_name(str(technology)).casefold()
            for technology in technologies
            if sanitize_skill_name(str(technology))
        },
    }


def is_duplicate_bullet(
    text: str,
    technologies: Sequence[str],
    seen_records: Sequence[Dict[str, object]],
) -> bool:
    """Treat near-identical bullets as duplicates even if the wording differs slightly."""
    candidate_tokens = tokenize_bullet_text(text)
    candidate_techs = {
        sanitize_skill_name(str(technology)).casefold()
        for technology in technologies
        if sanitize_skill_name(str(technology))
    }

    for record in seen_records:
        record_tokens = set(record.get("tokens", set()))
        if not candidate_tokens or not record_tokens:
            continue

        overlap = candidate_tokens & record_tokens
        token_similarity = len(overlap) / len(candidate_tokens | record_tokens)
        record_techs = set(record.get("techs", set()))
        tech_overlap = len(candidate_techs & record_techs)
        if token_similarity >= 0.82:
            return True
        if token_similarity >= 0.68 and tech_overlap >= 1:
            return True

    return False


def skill_supported_by_bullets(skill: str, bullet_pairs: Sequence[Tuple[str, str]]) -> bool:
    """Return True when a rendered skill appears in at least one final bullet."""
    normalized_skill = re.sub(r"[^a-z0-9]+", "", sanitize_skill_name(skill).casefold())
    if not normalized_skill:
        return False

    for bullet_text, technology_text in bullet_pairs:
        haystack = re.sub(r"[^a-z0-9]+", "", f"{bullet_text} {technology_text}".casefold())
        if normalized_skill in haystack:
            return True

    return False


def normalize_skill_for_sidebar(skill: str) -> str:
    """Collapse generic suffix-heavy skill labels back to their core technology family."""
    cleaned_skill = sanitize_skill_name(skill)
    normalized_skill = cleaned_skill.casefold()

    for family_key, display_name in SKILL_FAMILY_DISPLAY_NAMES.items():
        if normalized_skill == family_key:
            return display_name
        if not normalized_skill.startswith(f"{family_key} "):
            continue

        suffix_tokens = re.findall(r"[a-z0-9]+", normalized_skill[len(family_key):])
        if suffix_tokens and all(token in GENERIC_SKILL_SUFFIXES for token in suffix_tokens):
            return display_name

    return cleaned_skill


def skill_family_key(skill: str) -> str:
    """Group semantically similar skill labels into a single sidebar family."""
    normalized_skill = normalize_skill_for_sidebar(skill).casefold()
    for family_key in SKILL_FAMILY_DISPLAY_NAMES:
        if normalized_skill == family_key or normalized_skill.startswith(f"{family_key} "):
            return family_key

    if normalized_skill in {"sql", "t-sql"}:
        return "sql-family"
    if normalized_skill.startswith("security "):
        return "security-family"

    tokens = re.findall(r"[a-z0-9]+", normalized_skill)
    if not tokens:
        return normalized_skill
    if len(tokens) > 1:
        return " ".join(tokens[:2])
    return tokens[0]


def is_generic_skill_label(skill: str) -> bool:
    """Return True when a sidebar skill is mostly a root term plus filler suffixes."""
    normalized_skill = sanitize_skill_name(skill).casefold()
    for family_key in SKILL_FAMILY_DISPLAY_NAMES:
        if normalized_skill == family_key:
            return False
        if not normalized_skill.startswith(f"{family_key} "):
            continue
        suffix_tokens = re.findall(r"[a-z0-9]+", normalized_skill[len(family_key):])
        return bool(suffix_tokens) and all(token in GENERIC_SKILL_SUFFIXES for token in suffix_tokens)

    return any(token in GENERIC_SKILL_SUFFIXES for token in re.findall(r"[a-z0-9]+", normalized_skill))


def select_preferred_skill(existing_skill: str, candidate_skill: str) -> str:
    """Choose the cleaner of two same-family skill labels for the sidebar."""
    existing_generic = is_generic_skill_label(existing_skill)
    candidate_generic = is_generic_skill_label(candidate_skill)
    if existing_generic != candidate_generic:
        return candidate_skill if not candidate_generic else existing_skill
    if len(candidate_skill) < len(existing_skill):
        return candidate_skill
    return existing_skill


def should_drop_generic_skill(skill: str) -> bool:
    """Remove generic filler labels that do not identify a concrete technology."""
    normalized_skill = sanitize_skill_name(skill).casefold()
    if normalized_skill in SKILL_FAMILY_DISPLAY_NAMES:
        return False
    generic_tokens = re.findall(r"[a-z0-9]+", normalized_skill)
    if not generic_tokens:
        return True
    return len(generic_tokens) <= 2 and any(
        token in GENERIC_SKILL_SUFFIXES for token in generic_tokens
    )


def filter_supported_skills(
    skills: Dict[str, List[str]],
    bullet_pairs: Sequence[Tuple[str, str]],
) -> Dict[str, List[str]]:
    """Remove any rendered skill that is not backed by a final bullet."""
    filtered_skills: Dict[str, List[str]] = {}
    for category, values in skills.items():
        supported_values: List[str] = []
        seen_values: set[str] = set()
        family_indexes: Dict[str, int] = {}
        for value in values:
            skill = normalize_skill_for_sidebar(str(value))
            skill_key = skill.casefold()
            if not skill or skill_key in seen_values:
                continue
            if not skill_supported_by_bullets(skill, bullet_pairs):
                continue
            if should_drop_generic_skill(skill):
                continue

            if skill_key == "sql" and any("sql" in existing.casefold() for existing in supported_values):
                continue

            family_key = skill_family_key(skill)
            existing_index = family_indexes.get(family_key)
            if existing_index is not None:
                preferred_skill = select_preferred_skill(supported_values[existing_index], skill)
                supported_values[existing_index] = preferred_skill
                seen_values.add(preferred_skill.casefold())
                continue

            seen_values.add(skill_key)
            supported_values.append(skill)
            family_indexes[family_key] = len(supported_values) - 1
            if len(supported_values) == MAX_SKILLS_PER_CATEGORY:
                break

        if supported_values:
            filtered_skills[category] = supported_values

    return filtered_skills


def build_senior_context_bullet(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> str:
    """Add a concise context line when the primary title is promoted for a senior target."""
    profile = detect_role_profile(role, job_description)
    ordered_techs = order_technologies(
        list(detected_tech_stack),
        role,
        job_description,
        detected_tech_stack,
    )[:2]
    focus = build_focus_phrase(ordered_techs or ["automation", "production support"])
    return ROLE_SENIOR_CONTEXT[profile].format(focus=focus)


def build_resurgent_promotion_bullet(role: str, job_description: str) -> str:
    """Describe the known promotion path at Resurgent to strengthen title relevance."""
    return build_resurgent_promotion_bullet_with_options(
        role,
        job_description,
        existing_bullets=[],
        candidate_options=[],
    )


def build_resurgent_promotion_bullet_with_options(
    role: str,
    job_description: str,
    existing_bullets: Sequence[str],
    candidate_options: object,
) -> str:
    """Pick a unique, role-specific promotion bullet for the Resurgent experience."""
    promotion_options = build_default_promotion_bullet_options(role, job_description)
    used_bullets = {normalize_bullet_text(str(bullet)).casefold() for bullet in existing_bullets}

    for option in promotion_options:
        if normalize_bullet_text(option).casefold() not in used_bullets:
            return option

    return promotion_options[0]


def build_technical_environment(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    bullet_pairs: Sequence[Tuple[str, str]],
    rendered_skills: Dict[str, List[str]],
) -> str:
    """Build a compact technology line the renderer can use to fill leftover space."""
    candidate_technologies: List[str] = []
    for _, technology_text in bullet_pairs:
        candidate_technologies.extend(split_technologies(technology_text))
    for values in rendered_skills.values():
        candidate_technologies.extend(values)

    ordered_technologies = order_technologies(
        candidate_technologies,
        role,
        job_description,
        detected_tech_stack,
    )
    unique_technologies: List[str] = []
    seen_technologies: set[str] = set()
    for technology in ordered_technologies:
        normalized_technology = normalize_skill_for_sidebar(technology)
        if should_drop_generic_skill(normalized_technology):
            continue
        technology_key = normalized_technology.casefold()
        if technology_key in seen_technologies:
            continue
        seen_technologies.add(technology_key)
        unique_technologies.append(normalized_technology)
        if len(unique_technologies) == MAX_TECHNICAL_ENVIRONMENT_ITEMS:
            break

    return ", ".join(unique_technologies)


def rewrite_selected_bullet(
    bullet_text: str,
    technology_text: str,
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> Tuple[str, List[str]]:
    """Normalize a bank accomplishment without forcing an artificial lead-in."""
    technologies = order_technologies(
        split_technologies(technology_text),
        role,
        job_description,
        detected_tech_stack,
    )[:MAX_TECHNOLOGIES_PER_BULLET]
    cleaned_bullet = normalize_bullet_text(bullet_text)
    if not cleaned_bullet:
        return cleaned_bullet, technologies

    return build_bullet_variants(cleaned_bullet)[0], technologies


def parse_inline_bullet(bullet_text: str) -> Tuple[str, List[str]]:
    """Split a direct bullet into body text and technologies."""
    cleaned = compact_inferred_bullet(bullet_text)
    if ". Technologies:" in cleaned:
        text_part, technology_part = cleaned.rsplit(". Technologies:", 1)
        return text_part.strip().rstrip("."), split_technologies(technology_part)
    if "Technologies:" in cleaned:
        text_part, technology_part = cleaned.rsplit("Technologies:", 1)
        return text_part.strip().rstrip("."), split_technologies(technology_part)
    return cleaned.strip().rstrip("."), []


def format_direct_bullet(text: str, technologies: Sequence[str]) -> str:
    """Render a final bullet line for the markdown source file."""
    cleaned_text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    if not technologies:
        return cleaned_text
    return f"{cleaned_text}. Technologies: {', '.join(technologies)}"


def make_bullet_signature(text: str) -> str:
    """Normalize bullet text for duplicate detection across the final resume."""
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def extract_experience_bullets_from_resume(resume_path: Path) -> List[str]:
    """Read a generated resume source file and return only experience bullet text."""
    try:
        content = resume_path.read_text(encoding="utf-8")
    except OSError:
        return []

    bullets: List[str] = []
    in_experience = False
    for line in content.splitlines():
        stripped_line = line.strip()
        if is_experience_section_heading(stripped_line):
            in_experience = True
            continue
        if in_experience and stripped_line.startswith("## "):
            break
        if not in_experience or not stripped_line.startswith("- "):
            continue

        bullet_text, _ = parse_inline_bullet(stripped_line[2:])
        if bullet_text:
            bullets.append(bullet_text)

    return bullets


def load_existing_resume_bullet_signatures(exclude_output_dir: Optional[Path] = None) -> set[str]:
    """Load bullet signatures from prior generated resumes for uniqueness checks."""
    signatures: set[str] = set()
    if not OUTPUTS_DIR.exists():
        return signatures

    for resume_path in OUTPUTS_DIR.glob("**/resume_*.md"):
        if exclude_output_dir and resume_path.parent == exclude_output_dir:
            continue
        for bullet_text in extract_experience_bullets_from_resume(resume_path):
            signatures.add(make_bullet_signature(bullet_text))

    return signatures


def load_existing_promotion_bullets(exclude_output_dir: Optional[Path] = None) -> List[str]:
    """Load promotion bullets from prior resume outputs so new versions can avoid reuse."""
    promotion_bullets: List[str] = []
    if not OUTPUTS_DIR.exists():
        return promotion_bullets

    for resume_path in OUTPUTS_DIR.glob("**/resume_*.md"):
        if exclude_output_dir and resume_path.parent == exclude_output_dir:
            continue
        for bullet_text in extract_experience_bullets_from_resume(resume_path):
            if bullet_text.casefold().startswith("promoted twice"):
                promotion_bullets.append(bullet_text)

    return promotion_bullets


def shuffle_section_entries(
    entries: Sequence[Tuple[str, List[str]]],
    seed_text: str,
) -> List[Tuple[str, List[str]]]:
    """Vary bullet order per resume while keeping the promotion bullet anchored first."""
    shuffled_entries = list(entries)
    if len(shuffled_entries) < 3:
        return shuffled_entries

    fixed_entries: List[Tuple[str, List[str]]] = []
    if shuffled_entries and shuffled_entries[0][0].casefold().startswith("promoted twice"):
        fixed_entries.append(shuffled_entries.pop(0))

    random.Random(seed_text).shuffle(shuffled_entries)
    return fixed_entries + shuffled_entries


def evaluate_bullet_uniqueness(
    bullet_pairs: Sequence[Tuple[str, str]],
    historical_signatures: Sequence[str],
) -> Dict[str, object]:
    """Measure how many final bullets are unique against prior generated resumes."""
    if not bullet_pairs:
        return {
            "status": "pass",
            "unique_ratio": 1.0,
            "unique_count": 0,
            "reused_count": 0,
            "message": "No experience bullets were rendered for uniqueness checks.",
        }

    history = set(historical_signatures)
    unique_count = sum(
        1 for bullet_text, _ in bullet_pairs if make_bullet_signature(bullet_text) not in history
    )
    reused_count = len(bullet_pairs) - unique_count
    unique_ratio = unique_count / len(bullet_pairs)
    status = "pass" if unique_ratio >= MIN_UNIQUE_BULLET_RATIO else "warn"
    return {
        "status": status,
        "unique_ratio": round(unique_ratio, 2),
        "unique_count": unique_count,
        "reused_count": reused_count,
        "message": (
            "At least 60% of experience bullets are unique to this tailored resume."
            if status == "pass"
            else "Fewer than 60% of bullets are unique versus prior tailored resume outputs."
        ),
    }


def count_keyword_mentions(text: str, keyword: str) -> int:
    """Count case-insensitive whole-word keyword mentions in rendered resume text."""
    return len(re.findall(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", text, re.IGNORECASE))


def evaluate_standard_section_coverage(
    resume_data: Mapping[str, object],
    resume_source_text: str,
) -> Dict[str, object]:
    """Check that ATS-standard resume sections are materially present in the generated source."""
    present_sections: list[str] = []
    missing_sections: list[str] = []

    experience_entries = resume_data.get("experience", [])
    if isinstance(experience_entries, list) and experience_entries:
        present_sections.append("Work Experience")
    else:
        missing_sections.append("Work Experience")

    education_value = str(resume_data.get("education", "") or "").strip()
    if education_value:
        present_sections.append("Education")
    else:
        missing_sections.append("Education")

    skills_value = resume_data.get("skills", {})
    if isinstance(skills_value, dict) and any(skills_value.values()):
        present_sections.append("Skills")
    else:
        missing_sections.append("Skills")

    certifications_value = resume_data.get("certifications", [])
    recommended_sections: list[str] = []
    if isinstance(certifications_value, list) and certifications_value:
        present_sections.append("Certifications")
    else:
        recommended_sections.append("Certifications")

    experience_heading = "missing"
    if re.search(r"(?m)^## Work Experience\s*$", resume_source_text):
        experience_heading = "Work Experience"
    elif re.search(r"(?m)^## Experience\s*$", resume_source_text):
        experience_heading = "Experience"

    status = "pass" if not missing_sections else "warn"
    if missing_sections:
        message = (
            "Generated resume source is missing required ATS sections: "
            + ", ".join(missing_sections)
        )
    elif experience_heading == "Experience":
        message = (
            "Required ATS sections are present in the generated source. "
            "The intermediate markdown still uses 'Experience'; the final renderer should normalize this to 'Work Experience'."
        )
    else:
        message = "Required ATS sections are present in the generated source."

    return {
        "status": status,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "recommended_missing_sections": recommended_sections,
        "experience_heading": experience_heading,
        "message": message,
    }


def evaluate_page_length(
    rendered_resume_data: Mapping[str, object],
    condensed_resume_data: Mapping[str, object],
    estimated_pages: int,
) -> Dict[str, object]:
    """Check whether the export path can still keep the resume to one page."""

    def count_bullets(resume_data: Mapping[str, object]) -> int:
        return sum(
            len(experience.get("bullets", []))
            for experience in resume_data.get("experience", [])
            if isinstance(experience, dict)
        )

    compacted_to_fit = dict(rendered_resume_data) != dict(condensed_resume_data)
    rendered_bullet_count = count_bullets(rendered_resume_data)
    condensed_bullet_count = count_bullets(condensed_resume_data)
    trimmed_bullets = max(0, rendered_bullet_count - condensed_bullet_count)
    summary_shortened = str(rendered_resume_data.get("summary", "") or "").strip() != str(
        condensed_resume_data.get("summary", "") or ""
    ).strip()
    selected_achievements_removed = bool(
        rendered_resume_data.get("selected_achievements")
    ) and not bool(condensed_resume_data.get("selected_achievements"))
    technical_environment_removed = bool(
        rendered_resume_data.get("technical_environment")
    ) and not bool(condensed_resume_data.get("technical_environment"))

    status = "pass" if estimated_pages <= 1 else "reject"
    if status == "reject":
        message = (
            "Resume rejected because the export path still estimates more than one page after compaction."
        )
    elif compacted_to_fit:
        message = "Resume fits the one-page target after export-time compaction."
    else:
        message = "Resume fits the one-page target without export-time compaction."

    return {
        "status": status,
        "estimated_pages": estimated_pages,
        "page_limit": 1,
        "compacted_to_fit": compacted_to_fit,
        "trimmed_bullets": trimmed_bullets,
        "summary_shortened": summary_shortened,
        "selected_achievements_removed": selected_achievements_removed,
        "technical_environment_removed": technical_environment_removed,
        "message": message,
    }


def evaluate_must_have_keyword_coverage(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    resume_data: Mapping[str, object],
    bullet_pairs: Sequence[Tuple[str, str]],
    target_phrases: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    """Check whether the final resume reused enough exact JD phrases to stay ATS-aligned."""
    target_phrases = list(target_phrases or ()) or extract_exact_jd_phrase_targets(
        role,
        job_description,
        detected_tech_stack,
    )
    if not target_phrases:
        return {
            "status": "pass",
            "target_phrases": [],
            "matched_phrases": [],
            "missing_phrases": [],
            "matched_count": 0,
            "required_matches": 0,
            "message": "No exact JD phrases were identified for must-have keyword checks.",
        }

    rendered_segments: list[str] = [str(resume_data.get("summary", "") or "")]
    skills_value = resume_data.get("skills", {})
    if isinstance(skills_value, dict):
        for category, values in skills_value.items():
            rendered_segments.append(str(category))
            if isinstance(values, list):
                rendered_segments.extend(str(value) for value in values)
            else:
                rendered_segments.append(str(values))

    rendered_segments.extend(
        f"{bullet_text} {technology_text}" for bullet_text, technology_text in bullet_pairs
    )
    rendered_text = " ".join(segment for segment in rendered_segments if segment)

    matched_phrases = [
        phrase for phrase in target_phrases if count_keyword_mentions(rendered_text, phrase) > 0
    ]
    missing_phrases = [
        phrase for phrase in target_phrases if phrase not in matched_phrases
    ]
    required_matches = min(MAX_REQUIRED_JD_PHRASE_REUSE, len(target_phrases))
    minimum_warn_matches = max(1, (required_matches + 1) // 2)
    matched_count = len(matched_phrases)

    if matched_count >= required_matches:
        status = "pass"
        message = "Exact JD phrase reuse meets the ATS must-have keyword threshold."
    elif matched_count >= minimum_warn_matches:
        status = "warn"
        message = (
            "Some exact JD phrases landed in the final resume, but must-have keyword coverage is still thin. "
            "Push more multi-word JD phrases into the summary, skills, or experience bullets."
        )
    else:
        status = "reject"
        message = (
            "Resume rejected because too few exact JD phrases survived into the final summary, skills, and bullets."
        )

    return {
        "status": status,
        "target_phrases": target_phrases,
        "matched_phrases": matched_phrases,
        "missing_phrases": missing_phrases,
        "matched_count": matched_count,
        "required_matches": required_matches,
        "message": message,
    }


def evaluate_company_keyword_mentions(
    company: str,
    summary: str,
    bullet_pairs: Sequence[Tuple[str, str]],
) -> Dict[str, object]:
    """Report whether distinctive company keywords appear naturally in the resume."""
    required_keywords = extract_required_company_keywords(company)
    if not required_keywords:
        return {
            "status": "pass",
            "required_keywords": [],
            "mentions": {},
            "message": "No company-specific keyword gate was required for this resume.",
        }

    rendered_text = " ".join(
        [summary] + [f"{bullet_text} {technology_text}" for bullet_text, technology_text in bullet_pairs]
    )
    mentions = {
        keyword: count_keyword_mentions(rendered_text, keyword)
        for keyword in required_keywords
    }
    missing_keywords = [
        keyword
        for keyword, mention_count in mentions.items()
        if mention_count < REQUIRED_COMPANY_KEYWORD_MENTIONS
    ]
    status = "pass" if not missing_keywords else "warn"

    return {
        "status": status,
        "required_keywords": required_keywords,
        "mentions": mentions,
        "message": (
            "Distinctive company keywords appear often enough in the tailored resume."
            if status == "pass"
            else (
                "Distinctive company keywords were not repeated often, but this is advisory only; "
                "ATS resumes should prioritize truthful role and technology wording over forced employer-name repetition."
            )
        ),
    }


def evaluate_keyword_bullet_density(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    bullet_pairs: Sequence[Tuple[str, str]],
) -> Dict[str, object]:
    """Measure how consistently high-signal JD terms appear inside experience bullets."""
    priority_terms = extract_priority_terms(role, job_description, detected_tech_stack)[:8]
    if not bullet_pairs:
        return {
            "status": "pass",
            "priority_terms": priority_terms,
            "mentioned_terms": [],
            "missing_terms": priority_terms,
            "bullets_with_priority_terms": 0,
            "total_bullets": 0,
            "density": 1.0,
            "message": "No experience bullets were available for ATS keyword-density checks.",
        }
    if not priority_terms:
        return {
            "status": "pass",
            "priority_terms": [],
            "mentioned_terms": [],
            "missing_terms": [],
            "bullets_with_priority_terms": len(bullet_pairs),
            "total_bullets": len(bullet_pairs),
            "density": 1.0,
            "message": "No high-signal JD terms were detected for keyword-density checks.",
        }

    bullets_with_priority_terms = 0
    mentioned_terms: List[str] = []
    seen_terms: set[str] = set()
    for bullet_text, technology_text in bullet_pairs:
        rendered_bullet = f"{bullet_text} {technology_text}".casefold()
        matched_this_bullet = False
        for term in priority_terms:
            term_key = term.casefold()
            if term_key not in rendered_bullet:
                continue
            matched_this_bullet = True
            if term_key not in seen_terms:
                seen_terms.add(term_key)
                mentioned_terms.append(term)
        if matched_this_bullet:
            bullets_with_priority_terms += 1

    density = bullets_with_priority_terms / len(bullet_pairs)
    missing_terms = [term for term in priority_terms if term.casefold() not in seen_terms]
    status = "pass" if density >= MIN_BULLET_KEYWORD_COVERAGE_RATIO else "warn"
    return {
        "status": status,
        "priority_terms": priority_terms,
        "mentioned_terms": mentioned_terms[:6],
        "missing_terms": missing_terms[:6],
        "bullets_with_priority_terms": bullets_with_priority_terms,
        "total_bullets": len(bullet_pairs),
        "density": round(density, 2),
        "message": (
            "Priority JD terms appear across enough bullets to support ATS matching."
            if status == "pass"
            else "Too few experience bullets carry high-signal JD terms; move more priority keywords into bullet text or Technologies suffixes."
        ),
    }


def evaluate_keyword_role_spread(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    resume_data: Dict[str, object],
) -> Dict[str, object]:
    """Check whether top JD terms appear outside a single experience section."""
    priority_terms = extract_priority_terms(role, job_description, detected_tech_stack)[:6]
    experience_sections = [
        experience
        for experience in resume_data.get("experience", [])
        if experience.get("bullets")
    ]
    if len(experience_sections) < 2:
        return {
            "status": "pass",
            "priority_terms": priority_terms,
            "multi_role_terms": priority_terms[:3],
            "single_role_terms": [],
            "sections_with_bullets": len(experience_sections),
            "message": "Resume has fewer than two populated role sections, so role-spread checks are not needed.",
        }
    if not priority_terms:
        return {
            "status": "pass",
            "priority_terms": [],
            "multi_role_terms": [],
            "single_role_terms": [],
            "sections_with_bullets": len(experience_sections),
            "message": "No priority JD terms were detected for keyword-placement checks.",
        }

    section_texts = [
        " ".join(
            f"{bullet_text} {technology_text}"
            for bullet_text, technology_text in experience.get("bullets", [])
        )
        for experience in experience_sections
    ]
    section_mentions = {
        term: sum(
            1
            for section_text in section_texts
            if count_keyword_mentions(section_text, term) > 0
        )
        for term in priority_terms
    }
    multi_role_terms = [term for term, count in section_mentions.items() if count >= 2]
    single_role_terms = [term for term, count in section_mentions.items() if count == 1]
    status = "pass" if multi_role_terms else "warn"
    return {
        "status": status,
        "priority_terms": priority_terms,
        "multi_role_terms": multi_role_terms[:6],
        "single_role_terms": single_role_terms[:6],
        "sections_with_bullets": len(experience_sections),
        "message": (
            "At least one high-signal JD term appears across multiple role sections, which improves ATS keyword placement."
            if status == "pass"
            else "Priority JD terms are concentrated in one role section; spread portable keywords across multiple roles when the source material supports it."
        ),
    }


def evaluate_impact_first_bullet_structure(
    bullet_pairs: Sequence[Tuple[str, str]],
) -> Dict[str, object]:
    """Check whether bullets open in an impact-first, metrics-aware style."""
    if not bullet_pairs:
        return {
            "status": "pass",
            "impact_led_bullets": 0,
            "metric_opening_bullets": 0,
            "total_bullets": 0,
            "impact_ratio": 1.0,
            "weak_openers": [],
            "message": "No experience bullets were available for impact-first checks.",
        }

    impact_led_bullets = 0
    metric_opening_bullets = 0
    weak_openers: List[str] = []
    seen_weak_openers: set[str] = set()

    for bullet_text, _technology_text in bullet_pairs:
        cleaned_bullet = normalize_bullet_text(bullet_text)
        if not cleaned_bullet:
            continue
        opening_clause = re.split(r"[,;:—-]", cleaned_bullet, maxsplit=1)[0].strip()
        first_word_match = re.match(r"[A-Za-z]+", cleaned_bullet)
        first_word = first_word_match.group(0).casefold() if first_word_match else ""
        has_metric_anywhere = bool(IMPACT_METRIC_PATTERN.search(cleaned_bullet))
        has_metric_in_opening = bool(IMPACT_METRIC_PATTERN.search(opening_clause))
        if has_metric_in_opening:
            metric_opening_bullets += 1
        if first_word in WEAK_BULLET_OPENERS and first_word not in seen_weak_openers:
            seen_weak_openers.add(first_word)
            weak_openers.append(first_word)

        opening_text = opening_clause.casefold()
        opening_signals_impact = (
            has_metric_in_opening
            or any(marker in opening_text for marker in IMPACT_OUTCOME_MARKERS)
        )
        starts_strong = first_word in IMPACT_ACTION_VERBS
        impact_led = opening_signals_impact or (starts_strong and has_metric_anywhere)
        if impact_led:
            impact_led_bullets += 1

    impact_ratio = impact_led_bullets / len(bullet_pairs)
    status = (
        "pass"
        if impact_ratio >= MIN_IMPACT_FIRST_BULLET_RATIO and len(weak_openers) <= max(1, len(bullet_pairs) // 3)
        else "warn"
    )
    return {
        "status": status,
        "impact_led_bullets": impact_led_bullets,
        "metric_opening_bullets": metric_opening_bullets,
        "total_bullets": len(bullet_pairs),
        "impact_ratio": round(impact_ratio, 2),
        "weak_openers": weak_openers[:6],
        "message": (
            "The bullet set reads as impact-first often enough for a quick recruiter scan."
            if status == "pass"
            else "Too many bullets still read as task-led; front-load stronger outcomes, metrics, and business impact in the opening clause."
        ),
    }


def evaluate_action_context_result_bullets(
    bullet_pairs: Sequence[Tuple[str, str]],
) -> Dict[str, object]:
    """Check whether bullets follow an action-context-result arc often enough."""
    if not bullet_pairs:
        return {
            "status": "pass",
            "action_context_result_bullets": 0,
            "total_bullets": 0,
            "acr_ratio": 1.0,
            "missing_context_examples": [],
            "message": "No experience bullets were available for action-context-result checks.",
        }

    qualifying_bullets = 0
    missing_context_examples: List[str] = []
    for bullet_text, _technology_text in bullet_pairs:
        cleaned_bullet = normalize_bullet_text(bullet_text)
        if not cleaned_bullet:
            continue

        opening_clause = re.split(r"[,;:—-]", cleaned_bullet, maxsplit=1)[0].strip()
        first_word_match = re.match(r"[A-Za-z]+", cleaned_bullet)
        first_word = first_word_match.group(0).casefold() if first_word_match else ""
        action_signal = (
            first_word in IMPACT_ACTION_VERBS
            or (first_word.endswith("ed") and first_word not in WEAK_BULLET_OPENERS)
            or bool(IMPACT_METRIC_PATTERN.search(opening_clause))
        )
        context_signal = bool(
            re.search(
                rf"\b(?:{'|'.join(ACTION_CONTEXT_MARKERS)})\b",
                cleaned_bullet,
                re.IGNORECASE,
            )
        )
        result_signal = bool(IMPACT_METRIC_PATTERN.search(cleaned_bullet)) or any(
            marker in cleaned_bullet.casefold() for marker in ACTION_RESULT_MARKERS
        )

        if action_signal and context_signal and result_signal:
            qualifying_bullets += 1
            continue

        if not context_signal and len(missing_context_examples) < 3:
            missing_context_examples.append(cleaned_bullet)

    acr_ratio = qualifying_bullets / len(bullet_pairs)
    status = "pass" if acr_ratio >= MIN_ACTION_CONTEXT_RESULT_RATIO else "warn"
    return {
        "status": status,
        "action_context_result_bullets": qualifying_bullets,
        "total_bullets": len(bullet_pairs),
        "acr_ratio": round(acr_ratio, 2),
        "missing_context_examples": missing_context_examples,
        "message": (
            "Most bullets follow an action-context-result arc, which reads as problem-solving instead of task logging."
            if status == "pass"
            else "Too many bullets still miss clear action, context, or result signals; rewrite them to read as action-context-result statements."
        ),
    }


def build_role_aware_summary(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    bullet_pairs: Sequence[Tuple[str, str]],
    company: str = "",
) -> str:
    """Create a role-aware summary that never falls back to generic wording."""
    profile = detect_role_profile(role, job_description)
    prioritized_techs = list(detected_tech_stack)

    for _, technology_text in bullet_pairs:
        prioritized_techs.extend(split_technologies(technology_text))

    ordered_techs = order_technologies(
        prioritized_techs,
        role,
        job_description,
        detected_tech_stack,
    )
    ordered_techs = prioritize_summary_technologies(ordered_techs, role, profile)
    unique_techs: List[str] = []
    seen_techs: set[str] = set()
    for technology in ordered_techs:
        key = technology.casefold()
        if key in seen_techs:
            continue
        seen_techs.add(key)
        unique_techs.append(technology)
        if len(unique_techs) == 3:
            break

    tech_phrase = build_focus_phrase(unique_techs or ["Azure", "automation", "Python"])
    summary_opening = build_summary_opening(role, profile)
    summary_outcome = build_summary_outcome(role, profile, tech_phrase)
    company_keywords = extract_required_company_keywords(company)
    if not company_keywords:
        return f"{summary_opening}. {summary_outcome}"

    company_label = re.sub(r"\s+", " ", company).strip() or company_keywords[0]
    company_alignment = ROLE_SUMMARY_COMPANY_ALIGNMENT[profile].format(company_label=company_label)
    company_outcome = ROLE_SUMMARY_COMPANY_OUTCOMES[profile].format(
        tech_phrase=tech_phrase,
        company_label=company_label,
    )
    return f"{summary_opening}, {company_alignment} {company_outcome}"


def normalize_generated_summary(summary: str) -> str:
    """Normalize a model-written summary while preserving ATS-relevant phrasing."""
    cleaned = re.sub(
        r"\s+",
        " ",
        str(summary).replace("**", "").replace("*", " "),
    ).strip()
    cleaned = cleaned.strip('"')
    if not cleaned:
        return ""

    sentences = [
        re.sub(r"\s+", " ", sentence).strip(" .")
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned)
        if sentence.strip()
    ]
    normalized_sentences: List[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        normalized_sentences.append(
            sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."
        )
        if len(normalized_sentences) == 2:
            break

    return " ".join(normalized_sentences).strip()


def build_role_aware_skills(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    bullet_pairs: Sequence[Tuple[str, str]],
    fallback_skills: object,
    template_skills: object = None,
    must_have_keyword_targets: Sequence[str] = (),
) -> Dict[str, List[str]]:
    """Build rendered skills from technologies that actually appear in final bullets."""
    grouped_skills: Dict[str, List[str]] = {group: [] for group in SKILL_GROUPS}
    grouped_seen: Dict[str, set[str]] = {group: set() for group in SKILL_GROUPS}
    grouped_families: Dict[str, set[str]] = {group: set() for group in SKILL_GROUPS}

    for _, technology_text in bullet_pairs:
        for technology in split_technologies(technology_text):
            normalized_technology = normalize_skill_for_sidebar(technology)
            category_key = categorize_technology(normalized_technology)
            group_key = map_category_to_skill_group(category_key)
            if not group_key:
                continue
            technology_key = normalized_technology.casefold()
            if technology_key in grouped_seen[group_key]:
                continue
            family_key = skill_family_key(normalized_technology)
            if family_key in grouped_families[group_key]:
                continue
            grouped_seen[group_key].add(technology_key)
            grouped_families[group_key].add(family_key)
            grouped_skills[group_key].append(normalized_technology)

    profile = detect_role_profile(role, job_description)
    rendered_skills: Dict[str, List[str]] = {}
    label_map = {
        "delivery": ROLE_DELIVERY_LABELS[profile],
        "platform": ROLE_PLATFORM_LABELS[profile],
        "operations": ROLE_OPERATIONS_LABELS[profile],
        "automation": ROLE_AUTOMATION_LABELS[profile],
    }

    for group_key in ROLE_SKILL_ORDER[profile]:
        ranked_skills = order_technologies(
            grouped_skills[group_key],
            role,
            job_description,
            detected_tech_stack,
        )[:MAX_SKILLS_PER_CATEGORY]
        if ranked_skills:
            rendered_skills[label_map[group_key]] = ranked_skills
        if len(rendered_skills) == MAX_SKILL_CATEGORIES:
            break

    supported_rendered_skills = filter_supported_skills(rendered_skills, bullet_pairs)
    fallback_rendered_skills = filter_supported_skills(
        normalize_tailored_skills(fallback_skills, role),
        bullet_pairs,
    )
    selected_skills = supported_rendered_skills or fallback_rendered_skills
    template_skill_source = template_skills if template_skills is not None else fallback_skills
    return preserve_exact_target_skills(
        selected_skills,
        template_skill_source,
        role,
        must_have_keyword_targets,
    )


def preserve_exact_target_skills(
    rendered_skills: Dict[str, List[str]],
    template_skills: object,
    role: str,
    must_have_keyword_targets: Sequence[str],
) -> Dict[str, List[str]]:
    """Keep exact JD target skills from the source template when they are missing from bullets."""
    if not must_have_keyword_targets or not isinstance(template_skills, dict):
        return rendered_skills

    updated_skills = {category: list(values) for category, values in rendered_skills.items()}
    existing_skill_keys = {
        normalize_skill_for_sidebar(skill).casefold()
        for values in updated_skills.values()
        for skill in values
    }
    target_skill_keys = {
        normalize_skill_for_sidebar(str(target)).casefold()
        for target in must_have_keyword_targets
        if normalize_skill_for_sidebar(str(target))
    }
    if not target_skill_keys:
        return updated_skills

    for raw_category, raw_values in template_skills.items():
        category = normalize_skill_category_label(str(raw_category), role)
        if not category:
            continue
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        for value in values:
            normalized_value = normalize_skill_for_sidebar(value)
            value_key = normalized_value.casefold()
            if value_key not in target_skill_keys or value_key in existing_skill_keys:
                continue
            target_category = category
            if target_category not in updated_skills:
                if len(updated_skills) == MAX_SKILL_CATEGORIES:
                    target_category = next(
                        (
                            existing_category
                            for existing_category, category_values in sorted(
                                updated_skills.items(),
                                key=lambda item: len(item[1]),
                            )
                            if len(category_values) < MAX_SKILLS_PER_CATEGORY
                        ),
                        "",
                    )
                    if not target_category:
                        continue
                else:
                    updated_skills[target_category] = []
            if len(updated_skills[target_category]) >= MAX_SKILLS_PER_CATEGORY:
                continue
            updated_skills[target_category].append(normalized_value)
            existing_skill_keys.add(value_key)

    return updated_skills


def normalize_role_tag(role_tag: object) -> str:
    """Normalize explicit or inferred role tags to a scoreable key."""
    normalized = str(role_tag).strip().casefold().replace("-", "_")
    return re.sub(r"\s+", "_", normalized)


def infer_accomplishment_role_tags(accomplishment: Dict[str, object]) -> set[str]:
    """Infer adjacent/database/system-analyst tags when accomplishments are lightly tagged."""
    inferred_tags = {
        normalize_role_tag(item)
        for item in accomplishment.get("roles", [])
        if normalize_role_tag(item)
    }
    title_text = str(accomplishment.get("title", "")).casefold()
    bullet_text = str(accomplishment.get("bullet", "")).casefold()
    technology_text = str(accomplishment.get("technologies", "")).casefold()
    accomplishment_text = f"{title_text} {bullet_text} {technology_text}"

    for tag, markers in ROLE_ACCOMPLISHMENT_TEXT_MARKERS.items():
        marker_hits = sum(1 for marker in markers if marker in accomplishment_text)
        title_hits = sum(1 for marker in markers if marker in title_text)
        if title_hits >= 1 or marker_hits >= 2:
            inferred_tags.add(tag)

    return inferred_tags


def score_accomplishment_relevance(
    accomplishment: Dict[str, object],
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
) -> int:
    """Score unused accomplishments for optional Selected Achievements fill."""
    if is_placeholder_accomplishment(accomplishment):
        return -1

    profile = detect_role_profile(role, job_description)
    score = 0
    accomplishment_roles = infer_accomplishment_role_tags(accomplishment)
    score += 4 * len(accomplishment_roles.intersection(ROLE_ACCOMPLISHMENT_TAGS[profile]))

    title_text = str(accomplishment.get("title", ""))
    bullet_text = str(accomplishment.get("bullet", ""))
    technology_text = str(accomplishment.get("technologies", ""))
    accomplishment_text = f"{title_text} {bullet_text} {technology_text}".casefold()
    lane_marker_hits = sum(
        1
        for marker in ROLE_ACCOMPLISHMENT_TEXT_MARKERS.get(profile, ())
        if marker in accomplishment_text
    )
    score += 2 * min(3, lane_marker_hits)
    for term in extract_priority_terms(role, job_description, detected_tech_stack):
        term_lower = term.casefold()
        if term_lower in accomplishment_text:
            score += 3

    score += 4 * sum(
        1 for anchor in ROLE_RELEVANCE_ANCHORS[profile] if anchor.casefold() in accomplishment_text
    )
    if IMPACT_METRIC_PATTERN.search(bullet_text):
        score += 2

    normalized_bullet = normalize_bullet_text(bullet_text)
    if normalized_bullet:
        first_word = normalized_bullet.split(maxsplit=1)[0].casefold()
        if first_word in IMPACT_ACTION_VERBS:
            score += 1

    return score


def build_selected_achievement_candidates(
    accomplishments: Dict[str, Dict],
    selected_ids: Sequence[str],
    placed_ids: Sequence[str],
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    seen_signatures: set[str],
    seen_bullet_records: Sequence[Dict[str, object]],
) -> List[Dict[str, str]]:
    """Prepare extra achievements that the renderer can use to fill remaining space."""
    ranked_ids: List[str] = [aid for aid in selected_ids if aid not in placed_ids]
    remaining_ranked = sorted(
        [aid for aid in accomplishments if aid not in placed_ids and aid not in selected_ids],
        key=lambda aid: score_accomplishment_relevance(
            accomplishments[aid],
            role,
            job_description,
            detected_tech_stack,
        ),
        reverse=True,
    )
    ranked_ids.extend(remaining_ranked)

    candidates: List[Dict[str, str]] = []
    seen_records = list(seen_bullet_records)
    for accomplishment_id in ranked_ids:
        accomplishment = accomplishments[accomplishment_id]
        if is_placeholder_accomplishment(accomplishment):
            continue
        if accomplishment_id not in selected_ids and score_accomplishment_relevance(
            accomplishment,
            role,
            job_description,
            detected_tech_stack,
        ) <= 0:
            continue

        bullet_text, technologies = rewrite_selected_bullet(
            str(accomplishment.get("bullet", "")),
            str(accomplishment.get("technologies", "")),
            role,
            job_description,
            detected_tech_stack,
        )
        signature = make_bullet_signature(bullet_text)
        if (
            not bullet_text
            or signature in seen_signatures
            or is_duplicate_bullet(bullet_text, technologies, seen_records)
        ):
            continue

        candidates.append(
            {
                "bullet": bullet_text,
                "technologies": ", ".join(technologies),
            }
        )
        seen_signatures.add(signature)
        seen_records.append(build_bullet_record(bullet_text, technologies))
        if len(candidates) == MAX_SELECTED_ACHIEVEMENTS:
            break

    return candidates


def select_prompt_accomplishments(
    accomplishments: Dict[str, Dict],
    role: str,
    job_description: str,
) -> List[Tuple[str, Dict[str, object]]]:
    """Pick the most relevant accomplishments to send to the LLM prompt."""
    indexed_items = [
        (index, item)
        for index, item in enumerate(accomplishments.items())
        if not is_placeholder_accomplishment(item[1])
    ]
    ranked_items = sorted(
        indexed_items,
        key=lambda item: (
            -score_accomplishment_relevance(
                item[1][1],
                role,
                job_description,
                [],
            ),
            item[0],
        ),
    )
    return [entry for _, entry in ranked_items[:MAX_PROMPT_ACCOMPLISHMENTS]]


def evaluate_resume_relevance(
    role: str,
    job_description: str,
    detected_tech_stack: Sequence[str],
    bullet_pairs: Sequence[Tuple[str, str]],
) -> Dict[str, object]:
    """Reject resumes that do not mention enough job-relevant technologies."""
    relevant_terms = extract_priority_terms(role, job_description, detected_tech_stack)
    profile = detect_role_profile(role, job_description)
    if not relevant_terms:
        return {
            "status": "pass",
            "mentioned_terms": [],
            "missing_terms": [],
            "message": "No high-signal technology terms were detected for gating.",
        }

    rendered_text = " ".join(
        f"{bullet_text} {technology_text}" for bullet_text, technology_text in bullet_pairs
    ).casefold()
    mentioned_terms = [
        term for term in relevant_terms if term.casefold() in rendered_text
    ]
    missing_terms = [term for term in relevant_terms if term not in mentioned_terms]
    anchor_terms = [
        term for term in ROLE_RELEVANCE_ANCHORS[profile] if term.casefold() in rendered_text
    ]
    status = "pass" if len(mentioned_terms) >= MIN_RELEVANT_TECH_MENTIONS else "reject"
    if profile == "mainframe" and len(anchor_terms) < MIN_RELEVANT_TECH_MENTIONS:
        status = "reject"

    return {
        "status": status,
        "mentioned_terms": mentioned_terms[:6],
        "missing_terms": missing_terms[:6],
        "message": (
            "Relevant technology coverage meets the minimum threshold."
            if status == "pass"
            else "Resume rejected because fewer than two role-relevant technologies appear in the final bullets."
        ),
    }


def promote_support_title(header_line: str, role: str) -> str:
    """Preserve the source-of-truth support title in generated resume headers."""
    return header_line


def compact_inferred_bullet(text: str) -> str:
    """Tighten inferred bullets so they fit a one-page resume more reliably."""
    cleaned = re.sub(r'\s+', ' ', text.replace("**", "").replace("*", "")).strip()
    cleaned = strip_legacy_using_prefix(cleaned)

    if "Technologies:" not in cleaned:
        return normalize_bullet_text(cleaned)

    bullet_text, technology_text = cleaned.rsplit("Technologies:", 1)
    bullet_text = normalize_bullet_text(bullet_text)

    replacements = {
        r'^Designed and implemented\b': 'Led',
        r'^Developed and maintained\b': 'Managed',
        r'^Developed\b': 'Built',
        r'^Implemented\b': 'Built',
        r'^Conducted\b': 'Performed',
    }
    for pattern, replacement in replacements.items():
        bullet_text = re.sub(pattern, replacement, bullet_text, count=1)

    bullet_text = bullet_text.replace(" for transitioning legacy databases", " of legacy databases")
    bullet_text = bullet_text.replace(" during the process", "")
    bullet_text = bullet_text.replace(" resulting in ", " for ")
    bullet_text = bullet_text.replace(" ensuring ", " with ")
    bullet_text = normalize_bullet_text(bullet_text)

    technologies = [
        item.strip()
        for item in technology_text.split(',')
        if item.strip()
    ][:MAX_TECHNOLOGIES_PER_BULLET]

    if not technologies:
        return bullet_text

    return f"{bullet_text}. Technologies: {', '.join(technologies)}"


def is_placeholder_accomplishment(accomplishment: Dict[str, object]) -> bool:
    """Return True when an accomplishment still contains template placeholders."""
    accomplishment_text = " ".join(
        str(accomplishment.get(field_name, ""))
        for field_name in ("title", "bullet", "technologies")
    ).casefold()
    return any(
        placeholder in accomplishment_text
        for placeholder in ("[xx]", "add relevant tech", "replace [xx]", "**instructions:**")
    )


def parse_accomplishments_to_dict(md_content: str) -> Dict[str, Dict]:
    """Parse accomplishments.md into a dict keyed by ID."""
    accomplishments = {}
    current_id = None
    current_data = {}
    
    lines = md_content.split('\n')
    for line in lines:
        # Match accomplishment ID headers like ### CICD-001
        if line.startswith('### ') and re.match(r'^### [A-Z0-9]+-\d+', line):
            if current_id and current_data and not is_placeholder_accomplishment(current_data):
                accomplishments[current_id] = current_data
            current_id = line[4:].strip()
            current_data = {"id": current_id, "title": "", "bullet": "", "technologies": "", "roles": []}
        elif current_id:
            if line.startswith('**') and line.endswith('**'):
                current_data["title"] = line.strip('*').strip()
            elif line.startswith('- ') and not line.startswith('- **'):
                current_data["bullet"] = line[2:].strip()
            elif line.startswith('- **Technologies:**'):
                current_data["technologies"] = line.split(':', 1)[1].strip()
            elif line.startswith('- **Roles:**'):
                roles_str = line.split(':', 1)[1].strip()
                current_data["roles"] = [r.strip() for r in roles_str.split(',')]
    
    if current_id and current_data and not is_placeholder_accomplishment(current_data):
        accomplishments[current_id] = current_data
    
    return accomplishments


def build_accomplishment_bullet_text(accomplishment: Dict[str, str]) -> str:
    """Render an accomplishment dict into the final resume bullet format."""
    bullet_text = accomplishment.get("bullet", "").strip()
    technologies = accomplishment.get("technologies", "").strip()
    if technologies and "Technologies:" not in bullet_text:
        return f"{bullet_text} Technologies: {technologies}"
    return bullet_text


def apply_manual_tailoring_preferences(
    analysis: Dict,
    accomplishments: Dict[str, Dict],
    preferred_accomplishment_ids: Sequence[str],
) -> Dict:
    """Force preferred accomplishment IDs into the final analysis payload."""
    if not preferred_accomplishment_ids:
        return analysis

    preferred_ids = [aid for aid in preferred_accomplishment_ids if aid in accomplishments]
    selected_ids = list(dict.fromkeys(preferred_ids + analysis.get("selected_accomplishments", [])))

    rewritten_bullets = dict(analysis.get("rewritten_selected_bullets", {}))
    for accomplishment_id in preferred_ids:
        rewritten_bullets.setdefault(
            accomplishment_id,
            build_accomplishment_bullet_text(accomplishments[accomplishment_id]),
        )

    updated_analysis = dict(analysis)
    updated_analysis["selected_accomplishments"] = selected_ids
    updated_analysis["rewritten_selected_bullets"] = rewritten_bullets
    return updated_analysis


def analyze_job_and_select_accomplishments(
    client: OpenAI,
    job_description: str,
    accomplishments: Dict[str, Dict],
    company: str,
    role: str,
    model: str = "gpt-4o-mini",
    template_hint: Optional[str] = None,
) -> Dict:
    """
    Use LLM to analyze job and select best accomplishments.
    
    Returns:
        Dict with:
        - selected_accomplishments: List of accomplishment IDs (top 5-8)
        - rewritten_summary: Tailored professional summary
        - match_reasoning: Why these were selected
        - detected_tech_stack: Key technologies mentioned in JD
    """
    # Format the most relevant accomplishments for the LLM so newer entries are not lost to truncation.
    prompt_accomplishments = select_prompt_accomplishments(accomplishments, role, job_description)
    acc_text = "\n".join([
        f"ID: {aid}\nTitle: {data['title']}\nBullet: {data['bullet']}\nTech: {data['technologies']}"
        for aid, data in prompt_accomplishments
    ])
    
    # Build position context from template
    template = load_resume_template(role, template_hint)
    position_lines = [l.strip() for l in template.split('\n') if l.strip().startswith('### ')]
    position_context = "\n".join(position_lines) if position_lines else "See accomplishments for position context"

    role_profile = detect_role_profile(role, job_description)
    promotion_focus = ROLE_PROMOTION_FOCUS[role_profile]
    company_keyword_targets = extract_required_company_keywords(company)
    jd_phrase_targets = extract_exact_jd_phrase_targets(
        role,
        job_description,
        extract_priority_terms(role, job_description, []),
        support_text=f"{template}\n{acc_text}",
    )
    jd_phrase_targets_block = "\n".join(f"- {phrase}" for phrase in jd_phrase_targets) or "- None identified"
    jd_phrase_target_instruction = ""
    if jd_phrase_targets:
        minimum_phrase_reuse = min(4, len(jd_phrase_targets))
        jd_phrase_target_instruction = f"""
ATS PHRASE TARGETS:
{jd_phrase_targets_block}
- Reuse at least {minimum_phrase_reuse} of these exact JD phrases across rewritten_summary, rewritten_selected_bullets, inferred_accomplishments, and tailored_skills when the source material supports them
- Keep multi-word targets intact instead of paraphrasing them to broader wording
"""
    company_keyword_instruction = ""
    if company_keyword_targets:
        required_keywords = ", ".join(company_keyword_targets)
        company_keyword_instruction = f"""
ROLE-SPECIFIC ADVISORY:
- Distinctive company/platform keywords: {required_keywords}
- Mention these naturally when the JD clearly supports it, but never force repetition and never block the resume just because the employer name does not fit naturally in a bullet.
- Prefer role, platform, and technology keywords over repeating the company name.
"""

    prompt = f"""You are an expert resume tailoring specialist. Surgically select the BEST accomplishments AND infer additional ones where gaps exist.

COMPANY: {company}
ROLE: {role}

JOB DESCRIPTION:
{job_description}

CANDIDATE'S POSITIONS (from resume template):
{position_context}

CANDIDATE'S ACCOMPLISHMENTS BANK:
{acc_text}

UNIQUE PROMOTION BULLET RULE:
- Using this source-of-truth bullet as style only: "{PROMOTION_BULLET_SOURCE}"
- Generate exactly 5 completely different promotion bullets in `promotion_bullet_options`
- Every option must start with "Promoted twice", stay under 2 lines, and sound human and achievement-oriented
- Emphasize {promotion_focus}
- Never copy the source text verbatim

ATS SEMANTIC MATCH MODE:
- Assume the resume is parsed by an ATS/semantic matcher before a recruiter sees it
- Prefer exact job-description vocabulary over synonyms when the accomplishment bank truthfully supports it
- Mirror the JD's verbs and sentence construction when the source material supports it so the final resume reads like the target description instead of a generic paraphrase
- Reuse the JD's exact nouns and responsibility phrases naturally in bullets, summary, and skills such as "identity and access management", "security operations", "incident response", "CI/CD pipelines", "platform reliability", "observability", and "infrastructure as code"
- Do not paste raw requirements or keyword-stuff; every line still has to read like a real accomplishment
- If the JD names a tool the candidate does not truly have, mirror the broader concept while keeping the candidate's real tool names. Example: use "infrastructure as code" with Bicep and ARM Templates rather than falsely claiming Terraform

BULLET UNIQUENESS ENGINE:
- Rewrite every selected accomplishment into `rewritten_selected_bullets`
- At least 60% of the final experience bullets must feel unique to this resume version
- Use the source accomplishments as factual truth for metrics and technologies, but vary sentence structure, technology emphasis, and outcome framing
- Do not copy bank bullets verbatim unless there is no cleaner way to preserve the fact pattern

SKILLS CLEANUP:
- Remove duplicate or same-family skills such as repeated "SQL Server ..." variants or generic duplicates like "Security Tools" versus "Security Scanning"
- Every skill you list in `tailored_skills` must appear in at least one rewritten or inferred bullet body or Technologies suffix
- Favor compact, distinct sidebar skills over long repetitive phrases

HUMAN READABILITY SCAN:
- If more than half of the bullets would open with the same small set of verbs or the same sentence structure, rewrite them with more variety
- Use mixed sentence starters such as Containerized, Architected, Optimized, Led, Implemented, and Achieved

FINAL POLISH:
- Keep the promotion bullet options separate; the renderer will choose the final placement
- Write bullets so they can be safely reordered without losing meaning
- Scan the final bullet list and ensure no two Resurgent bullets cover the same theme; if two bullets are both mainly about monitoring, cost, security, or uptime, keep the stronger one and replace the weaker one with a different achievement
- For the main Resurgent section, aim to cover this spread when the source material supports it: promotion, CI/CD, container or IaC, security or compliance, monitoring or observability, cost or optimization, self-healing or incident response, and one unique AI or scale bullet

{company_keyword_instruction}
{jd_phrase_target_instruction}

INSTRUCTIONS:
1. Analyze the JD for key requirements, tech stack, and priorities
2. Select accomplishments from the bank that best match this role
3. REWRITE selected accomplishments for this target role and INFER realistic accomplishments for gaps — quantified, natural-sounding, with bold-worthy metrics

HARD RULES FOR BULLET COUNTS PER POSITION:
- Resurgent Capital Services (primary role, 5 years, wore many hats): MINIMUM 7 bullets, ideally 8-9
- Silco (contract database role): MINIMUM 2 bullets, ideally 3
- RIBBIT.AI (contract database role): MINIMUM 2 bullets, ideally 3
- EVERY position MUST have bullets. Zero-bullet positions are UNACCEPTABLE.
- Total across all positions: 11-15 bullets

For Silco and RIBBIT.AI (database analyst contracts):
- These are DATABASE-focused roles. Bullets must relate to: SQL optimization, ETL, reporting, data migration, stored procedures, SSIS, database performance
- If no bank accomplishments match, INFER realistic database achievements with metrics
- Example: "Developed automated ETL pipelines processing 50,000+ records daily, reducing manual data entry by 85%"
- Example: "Optimized 30+ SQL stored procedures, improving report generation time by 65% for executive dashboards"

For Resurgent (DevOps/infrastructure-heavy, junior role wearing many hats):
- This is where cloud, CI/CD, Kubernetes, monitoring, automation, security bullets go
- Select the MOST relevant from the bank, then infer additional ones to reach 7+ minimum
- Include diverse achievements: CI/CD, cloud infra, monitoring, scripting, security, cost savings

4. Write exactly 2 sentences for `rewritten_summary` with a role-specific opening. Prefer the JD's exact discipline phrases and responsibility language when truthful, and it is acceptable to reuse the core role wording naturally. Never use the phrase "infrastructure professional".
5. Identify key technologies from the JD
6. INFER ADJACENT SKILLS the candidate likely has based on experience

BULLET WRITING RULES (MANDATORY):
- Never start any bullet with "Using", "Leveraging", "Utilizing", "Responsible for", or "Worked on"
- Vary bullet openings within each company section; do not repeat the same first word unless unavoidable
- Keep the tone natural and resume-like, not explanatory or instructional
- Prefer the job description's exact wording for supported tools, domains, and responsibilities instead of looser synonyms
- Repeat the highest-value JD terms across at least two bullets, and across more than one role section when the candidate's factual history supports that spread
- Every bullet should follow an action-context-result arc: lead with the action or operational outcome, ground it in scope, and finish with the measurable result or business effect
- Front-load impact whenever the source material supports it: prefer an opening clause with a metric, outcome, or operational improvement rather than a generic task lead-in
- Avoid weak task-led openings like "Supported", "Built", or "Created" when you can lead with stronger outcome language such as "Reduced", "Stabilized", "Automated", "Hardened", or "Led"
- Make sure at least two-thirds of the final bullets carry high-signal JD terms in either the bullet text or the Technologies suffix when truthful
- Avoid awkward constructions such as "with compliance with", "For X, built ...", "with data integrity and reducing ...", or filler like "enhancing visibility for teams"
- Prefer direct action phrasing such as "Implemented ... for incident management", "Strengthened security controls across ...", or "Automated cloud resource management ..."
- If you list a technology in tailored_skills, it must also appear in at least one bullet body or Technologies suffix
- If the company or JD references a distinctive platform or product, mention it once when the JD clearly supports it

PAGE CONSTRAINT: Resume MUST fit 1 page but FILL the page (no large blank areas).
- Skills: EXACTLY 4 categories with EXACTLY 5 skills each
- Use adjacent or supporting tools if needed to fill the blue skills column naturally
- Category names MUST be SHORT (max 20 characters). Use abbreviations:
  Good: "CLOUD & INFRA", "CI/CD & AUTOMATION", "MONITORING & OPS", "SCRIPTING & DATA"
  Bad: "CLOUD & INFRASTRUCTURE", "MONITORING & OBSERVABILITY" (too long, gets clipped)
- Summary: exactly 2 sentences
- Keep bullets concise enough for a one-page resume: aim for 14-18 words before the Technologies suffix
- Limit each Technologies suffix to 2-3 tools max

Respond in this exact JSON format:
{{
    "selected_accomplishments": ["CICD-001", "K8S-001", ...],
    "rewritten_selected_bullets": {{
        "CICD-001": "Role-aligned rewritten bullet. Technologies: Tool1, Tool2, Tool3",
        "K8S-001": "Another rewritten bullet. Technologies: Tool1, Tool2, Tool3"
    }},
    "promotion_bullet_options": [
        "Promoted twice ...",
        "Promoted twice ...",
        "Promoted twice ...",
        "Promoted twice ...",
        "Promoted twice ..."
    ],
    "inferred_accomplishments": {{
        "Resurgent Capital Services": [
            "Bullet text with **quantified metrics**...",
            ...
        ],
        "Silco": [
            "Database-focused bullet with metrics...",
            ...
        ],
        "RIBBIT.AI": [
            "Database-focused bullet with metrics...",
            ...
        ]
    }},
    "rewritten_summary": "Cloud engineer with 5+ years building Azure infrastructure and delivery automation...",
    "tailored_skills": {{
        "MOST RELEVANT CATEGORY": ["Skill1", "Skill2", "Skill3", "Skill4", "Skill5"],
        "CLOUD & INFRA": ["Azure", "Kubernetes", "Terraform", "Docker", "Linux"],
        "MONITORING & OPS": ["Prometheus", "Grafana", "Azure Monitor", "Log Analytics", "Alerts"],
        "SCRIPTING & DATA": ["Python", "PowerShell", "SQL Server", "T-SQL", "Bash"]
    }},
    "match_reasoning": "Selected based on...",
    "detected_tech_stack": ["Azure", "Kubernetes", "Python", ...],
    "industry_focus": "finance|healthcare|tech|retail|other",
    "confidence_score": 85
}}

CRITICAL:
- Use EXACT company names as keys in inferred_accomplishments: "Resurgent Capital Services", "Silco", "RIBBIT.AI"
- `rewritten_selected_bullets` MUST include an entry for every selected accomplishment ID
- Every rewritten or inferred bullet MUST remain factual, human, and resume-ready
- Do NOT mark inferred skills with * or any other symbol
- Inferred bullets must sound natural with realistic metrics, NOT AI-generic
- Database positions get database achievements ONLY
- Every metric in bullets should be bold-worthy (use real numbers: percentages, counts, dollar amounts)
- DO NOT infer bullets that duplicate or overlap with selected bank accomplishments. If bank already has a "reduced provisioning time by 90%", do NOT infer another provisioning-time bullet.
- Each inferred bullet MUST include a "Technologies:" suffix in this format:
  "Bullet text achieving X% improvement. Technologies: Tool1, Tool2, Tool3"
  This ensures consistent formatting with bank accomplishments in the final resume.
- DO NOT use markdown formatting (no ** or * around metrics). Just write plain text with numbers. The renderer auto-bolds metrics.
"""
    
    try:
        response = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            token_limit=2500,
            temperature=0.3,
        )
        content = response.choices[0].message.content.strip()
        if content.upper().startswith("ERROR"):
            return {"error": content}
        
        # Parse JSON from response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        parsed_content = json.loads(content)
        if isinstance(parsed_content, dict) and parsed_content.get("error"):
            return parsed_content

        if isinstance(parsed_content, dict):
            resolved_detected_tech_stack = [
                sanitize_skill_name(str(item))
                for item in parsed_content.get("detected_tech_stack", [])
                if sanitize_skill_name(str(item))
            ]
            resolved_jd_phrase_targets = extract_exact_jd_phrase_targets(
                role,
                job_description,
                resolved_detected_tech_stack,
                support_text=f"{template}\n{acc_text}",
            )
            parsed_content["must_have_keyword_targets"] = resolved_jd_phrase_targets or jd_phrase_targets

        return parsed_content
    except Exception as e:
        print(f"  ERROR: LLM analysis error: {e}")
        return {
            "selected_accomplishments": [],
            "rewritten_summary": "",
            "match_reasoning": "Fallback to base template after analysis error",
            "detected_tech_stack": extract_priority_terms(role, job_description, [])[:8],
            "confidence_score": 50,
            "tailored_skills": {},
            "inferred_accomplishments": {},
            "rewritten_selected_bullets": {},
            "promotion_bullet_options": [],
            "must_have_keyword_targets": jd_phrase_targets,
        }


def create_tailored_resume_source(
    company: str,
    role: str,
    analysis: Dict,
    base_template: str,
    accomplishments: Dict[str, Dict],
    job_description: str,
    output_dir: Path
) -> Path:
    """
    Create a tailored resume source file with selected accomplishments.

    This creates a new resume_Company_Role.md file with:
    - Selected accomplishments injected
    - Rewritten summary
    """
    import yaml

    # Parse the base template
    if base_template.startswith('---'):
        parts = base_template.split('---', 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            body = parts[2]
        else:
            frontmatter = {}
            body = base_template
    else:
        frontmatter = {}
        body = base_template

    template_skills = frontmatter.get("skills", {}) if isinstance(frontmatter, dict) else {}

    # Update role to match job
    frontmatter["role"] = role

    # Create new experience section with selected accomplishments
    selected_ids = [
        accomplishment_id
        for accomplishment_id in analysis.get("selected_accomplishments", [])
        if accomplishment_id in accomplishments
    ]
    inferred = analysis.get("inferred_accomplishments", {})
    rewritten_selected_bullets = analysis.get("rewritten_selected_bullets", {})
    promotion_bullet_options = analysis.get("promotion_bullet_options", [])
    detected_tech_stack = [
        sanitize_skill_name(str(item))
        for item in analysis.get("detected_tech_stack", [])
        if sanitize_skill_name(str(item))
    ]
    rewritten_summary = normalize_generated_summary(
        str(analysis.get("rewritten_summary", ""))
    )
    fallback_to_template = not selected_ids
    historical_bullet_signatures = load_existing_resume_bullet_signatures(output_dir)
    existing_promotion_bullets = load_existing_promotion_bullets(output_dir)

    # Map company names to inferred accomplishments (fuzzy matching)
    inferred_map = {normalize_company_name(k): v for k, v in inferred.items()}

    def find_inferred_bullets(job_name_normalized):
        """Fuzzy match job name against inferred accomplishments keys."""
        if job_name_normalized in inferred_map:
            return inferred_map[job_name_normalized]
        for key, bullets in inferred_map.items():
            if key in job_name_normalized or job_name_normalized in key:
                return bullets
        return []

    # Find the experience section and rebuild with selected accomplishments
    job_bullets_added = {}  # Track bullets per job
    placed_ids: List[str] = []
    placed_id_set: set[str] = set()
    first_job_name = None  # Primary role gets unplaced selected IDs
    seen_signatures: set[str] = set()
    rendered_bullet_pairs: List[Tuple[str, str]] = []
    seen_bullet_records: List[Dict[str, object]] = []
    entry_priority_by_signature: Dict[str, int] = {}

    def record_entry_priority(bullet_text: str, selection_priority: int) -> None:
        """Track source priority across all structural variants of a bullet."""
        if selection_priority <= 0:
            return

        for variant in build_bullet_variants(bullet_text):
            signature = make_bullet_signature(variant)
            entry_priority_by_signature[signature] = max(
                selection_priority,
                entry_priority_by_signature.get(signature, 0),
            )

    def append_bullet_pair(
        bullet_text: str,
        technologies: Sequence[str],
        job_name_normalized: Optional[str],
        target_entries: List[Tuple[str, List[str]]],
        *,
        count_for_job: bool = True,
        selection_priority: int = 0,
    ) -> None:
        cleaned_text = normalize_bullet_text(bullet_text)
        cleaned_technologies: List[str] = []
        seen_technologies: set[str] = set()
        for technology in technologies:
            cleaned_technology = sanitize_skill_name(str(technology))
            technology_key = cleaned_technology.casefold()
            if not cleaned_technology or technology_key in seen_technologies:
                continue
            seen_technologies.add(technology_key)
            cleaned_technologies.append(cleaned_technology)

        cleaned_text = choose_best_bullet_variant(cleaned_text, historical_bullet_signatures)

        signature = make_bullet_signature(cleaned_text)
        if (
            not cleaned_text
            or signature in seen_signatures
            or is_duplicate_bullet(cleaned_text, cleaned_technologies, seen_bullet_records)
        ):
            return

        seen_signatures.add(signature)
        seen_bullet_records.append(build_bullet_record(cleaned_text, cleaned_technologies))
        target_entries.append((cleaned_text, cleaned_technologies))
        record_entry_priority(cleaned_text, selection_priority)
        if job_name_normalized and count_for_job:
            job_bullets_added[job_name_normalized] = (
                job_bullets_added.get(job_name_normalized, 0) + 1
            )

    def append_selected_accomplishment(
        accomplishment_id: str,
        job_name_normalized: Optional[str],
        target_entries: List[Tuple[str, List[str]]],
    ) -> None:
        accomplishment = accomplishments.get(accomplishment_id)
        if not accomplishment:
            return

        rewritten_bullet = ""
        if isinstance(rewritten_selected_bullets, dict):
            rewritten_bullet = str(rewritten_selected_bullets.get(accomplishment_id, "")).strip()

        if rewritten_bullet:
            bullet_text, technologies = parse_inline_bullet(rewritten_bullet)
            if not technologies:
                technologies = split_technologies(str(accomplishment.get("technologies", "")))
            technologies = order_technologies(
                technologies,
                role,
                job_description,
                detected_tech_stack,
            )[:MAX_TECHNOLOGIES_PER_BULLET]
        else:
            bullet_text, technologies = rewrite_selected_bullet(
                accomplishment.get("bullet", ""),
                accomplishment.get("technologies", ""),
                role,
                job_description,
                detected_tech_stack,
            )

        append_bullet_pair(
            bullet_text,
            technologies,
            job_name_normalized,
            target_entries,
            selection_priority=2,
        )
        if accomplishment_id not in placed_id_set:
            placed_id_set.add(accomplishment_id)
            placed_ids.append(accomplishment_id)

    def append_inferred_bullets(
        job_name_normalized: Optional[str],
        target_entries: List[Tuple[str, List[str]]],
    ) -> None:
        if not job_name_normalized:
            return
        for inferred_bullet in find_inferred_bullets(job_name_normalized):
            bullet_text, technologies = parse_inline_bullet(inferred_bullet)
            ordered_technologies = order_technologies(
                technologies,
                role,
                job_description,
                detected_tech_stack,
            )[:MAX_TECHNOLOGIES_PER_BULLET]
            append_bullet_pair(
                bullet_text,
                ordered_technologies,
                job_name_normalized,
                target_entries,
                selection_priority=1,
            )

    def append_remaining_primary_bullets(
        job_name_normalized: Optional[str],
        target_entries: List[Tuple[str, List[str]]],
    ) -> None:
        if job_name_normalized != first_job_name:
            return
        for accomplishment_id in selected_ids:
            if accomplishment_id in placed_id_set:
                continue
            if job_bullets_added.get(job_name_normalized, 0) >= PRIMARY_ROLE_BULLET_CAP:
                break
            append_selected_accomplishment(
                accomplishment_id,
                job_name_normalized,
                target_entries,
            )

    body_lines = body.split('\n')
    experience_index = next(
        (index for index, line in enumerate(body_lines) if is_experience_section_heading(line)),
        None,
    )
    if experience_index is None:
        raise ValueError("Base template is missing the experience section")

    post_experience_index = next(
        (
            index
            for index in range(experience_index + 1, len(body_lines))
            if body_lines[index].startswith('## ')
        ),
        len(body_lines),
    )

    pre_experience_lines = body_lines[:experience_index + 1]
    if pre_experience_lines:
        pre_experience_lines[-1] = STANDARD_EXPERIENCE_SECTION_HEADING
    experience_body_lines = body_lines[experience_index + 1:post_experience_index]
    post_experience_lines = body_lines[post_experience_index:]

    job_sections: List[Dict[str, object]] = []
    current_section: Optional[Dict[str, object]] = None
    for line in experience_body_lines:
        if line.startswith('### ') and '|' in line:
            normalized_header_line = build_experience_header_line(line)
            if current_section:
                job_sections.append(current_section)
            current_section = {
                "header": normalized_header_line,
                "job_name": normalize_company_name(normalized_header_line[4:].split('|')[0].strip()),
                "references": [],
                "direct_bullets": [],
            }
            continue

        if not current_section or not line.startswith('- '):
            continue

        bullet_content = line[2:].strip()
        if re.match(r'^[A-Z0-9]+-\d+$', bullet_content):
            current_section["references"].append(bullet_content)
        else:
            current_section["direct_bullets"].append(bullet_content)

    if current_section:
        job_sections.append(current_section)

    exp_lines = list(pre_experience_lines)
    if job_sections and exp_lines and is_experience_section_heading(exp_lines[-1]):
        exp_lines.append("")

    for section_index, job_section in enumerate(job_sections):
        header_line = str(job_section["header"])
        job_name_normalized = str(job_section["job_name"])
        original_header_line = header_line
        if first_job_name is None:
            first_job_name = job_name_normalized
            header_line = promote_support_title(header_line, role)

        exp_lines.append(header_line)
        job_entries: List[Tuple[str, List[str]]] = []

        if job_name_normalized == normalize_company_name("Resurgent Capital Services"):
            append_bullet_pair(
                build_resurgent_promotion_bullet_with_options(
                    role,
                    job_description,
                    existing_promotion_bullets,
                    promotion_bullet_options,
                ),
                [],
                job_name_normalized,
                job_entries,
                count_for_job=False,
            )
        elif header_line != original_header_line:
            append_bullet_pair(
                build_senior_context_bullet(role, job_description, detected_tech_stack),
                [],
                job_name_normalized,
                job_entries,
                count_for_job=False,
            )

        for accomplishment_id in job_section["references"]:
            if fallback_to_template or accomplishment_id in selected_ids:
                append_selected_accomplishment(
                    str(accomplishment_id),
                    job_name_normalized,
                    job_entries,
                )

        for direct_bullet in job_section["direct_bullets"]:
            bullet_text, technologies = parse_inline_bullet(str(direct_bullet))
            append_bullet_pair(
                bullet_text,
                order_technologies(
                    technologies,
                    role,
                    job_description,
                    detected_tech_stack,
                )[:MAX_TECHNOLOGIES_PER_BULLET],
                job_name_normalized,
                job_entries,
            )

        append_inferred_bullets(job_name_normalized, job_entries)
        append_remaining_primary_bullets(job_name_normalized, job_entries)
        section_entries = ensure_section_sentence_variety(job_entries)
        if job_name_normalized == first_job_name:
            section_entries = organize_primary_section_entries(
                section_entries,
                role,
                job_description,
                detected_tech_stack,
                f"{company}|{role}|{job_name_normalized}|theme-spread",
                priority_by_signature=entry_priority_by_signature,
            )
        else:
            section_entries = shuffle_section_entries(
                section_entries,
                f"{company}|{role}|{job_name_normalized}|{len(section_entries)}",
            )
        for bullet_text, technologies in section_entries:
            exp_lines.append(f"- {format_direct_bullet(bullet_text, technologies)}")
            rendered_bullet_pairs.append((bullet_text, ", ".join(technologies)))

        if section_index < len(job_sections) - 1:
            exp_lines.append("")

    if post_experience_lines:
        if exp_lines and exp_lines[-1] != "":
            exp_lines.append("")
        exp_lines.extend(post_experience_lines)

    frontmatter["summary"] = rewritten_summary or build_role_aware_summary(
        role,
        job_description,
        detected_tech_stack,
        rendered_bullet_pairs,
        company,
    )
    frontmatter["skills"] = build_role_aware_skills(
        role,
        job_description,
        detected_tech_stack,
        rendered_bullet_pairs,
        analysis.get("tailored_skills") or frontmatter.get("skills", {}),
        template_skills,
        analysis.get("must_have_keyword_targets", []),
    )
    technical_environment = build_technical_environment(
        role,
        job_description,
        detected_tech_stack,
        rendered_bullet_pairs,
        frontmatter["skills"],
    )
    if technical_environment:
        frontmatter["technical_environment"] = technical_environment
    else:
        frontmatter.pop("technical_environment", None)

    final_seen_signatures = {make_bullet_signature(text) for text, _ in rendered_bullet_pairs}
    final_seen_records = [
        build_bullet_record(text, split_technologies(technology_text))
        for text, technology_text in rendered_bullet_pairs
    ]

    selected_achievement_candidates = build_selected_achievement_candidates(
        accomplishments,
        selected_ids,
        placed_ids,
        role,
        job_description,
        detected_tech_stack,
        final_seen_signatures,
        final_seen_records,
    )
    if selected_achievement_candidates:
        frontmatter["selected_achievements"] = selected_achievement_candidates
    else:
        frontmatter.pop("selected_achievements", None)

    # Build new content
    new_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True, width=1000)}---\n"
    )
    new_content += '\n'.join(exp_lines)

    # Create safe filename
    safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:30]
    safe_role = re.sub(r'[^\w\s-]', '', role).replace(' ', '_')[:30]

    output_path = output_dir / f"resume_{safe_company}_{safe_role}.md"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return output_path


def generate_analysis_report(
    company: str,
    role: str,
    job_url: str,
    match_score: int,
    analysis: Dict,
    output_dir: Path,
    relevance_check: Optional[Dict[str, object]] = None,
    section_coverage_check: Optional[Dict[str, object]] = None,
    page_length_check: Optional[Dict[str, object]] = None,
    must_have_keyword_check: Optional[Dict[str, object]] = None,
    uniqueness_check: Optional[Dict[str, object]] = None,
    company_keyword_check: Optional[Dict[str, object]] = None,
    keyword_density_check: Optional[Dict[str, object]] = None,
    keyword_role_spread_check: Optional[Dict[str, object]] = None,
    impact_check: Optional[Dict[str, object]] = None,
    action_context_result_check: Optional[Dict[str, object]] = None,
) -> Path:
    """Generate a markdown analysis report for the job match."""

    relevance_section = ""
    if relevance_check:
        mentioned_terms = ", ".join(relevance_check.get("mentioned_terms", [])) or "None"
        missing_terms = ", ".join(relevance_check.get("missing_terms", [])) or "None"
        relevance_section = f"""
## Relevance Check
- **Status:** {str(relevance_check.get('status', 'unknown')).upper()}
- **Mentioned Terms:** {mentioned_terms}
- **Missing Terms:** {missing_terms}
- **Message:** {relevance_check.get('message', 'No relevance diagnostics captured')}
"""

    section_coverage_section = ""
    if section_coverage_check:
        present_sections = ", ".join(section_coverage_check.get("present_sections", [])) or "None"
        missing_sections = ", ".join(section_coverage_check.get("missing_sections", [])) or "None"
        recommended_missing = ", ".join(
            section_coverage_check.get("recommended_missing_sections", [])
        ) or "None"
        section_coverage_section = f"""
## ATS Section Coverage
- **Status:** {str(section_coverage_check.get('status', 'unknown')).upper()}
- **Present Sections:** {present_sections}
- **Missing Required Sections:** {missing_sections}
- **Missing Recommended Sections:** {recommended_missing}
- **Source Experience Heading:** {section_coverage_check.get('experience_heading', 'unknown')}
- **Message:** {section_coverage_check.get('message', 'No section-coverage diagnostics captured')}
"""

    page_length_section = ""
    if page_length_check:
        page_length_section = f"""
## ATS Page Length
- **Status:** {str(page_length_check.get('status', 'unknown')).upper()}
- **Estimated Pages:** {page_length_check.get('estimated_pages', 'N/A')}/{page_length_check.get('page_limit', 'N/A')}
- **Export-Time Compaction:** {'Yes' if page_length_check.get('compacted_to_fit') else 'No'}
- **Trimmed Bullets:** {page_length_check.get('trimmed_bullets', 'N/A')}
- **Summary Shortened:** {'Yes' if page_length_check.get('summary_shortened') else 'No'}
- **Removed Selected Achievements:** {'Yes' if page_length_check.get('selected_achievements_removed') else 'No'}
- **Removed Technical Environment:** {'Yes' if page_length_check.get('technical_environment_removed') else 'No'}
- **Message:** {page_length_check.get('message', 'No page-length diagnostics captured')}
"""

    must_have_keyword_section = ""
    if must_have_keyword_check:
        target_phrases = ", ".join(must_have_keyword_check.get("target_phrases", [])) or "None"
        matched_phrases = ", ".join(must_have_keyword_check.get("matched_phrases", [])) or "None"
        missing_phrases = ", ".join(must_have_keyword_check.get("missing_phrases", [])) or "None"
        must_have_keyword_section = f"""
## ATS Must-Have Keyword Coverage
- **Status:** {str(must_have_keyword_check.get('status', 'unknown')).upper()}
- **Exact JD Targets:** {target_phrases}
- **Matched Targets:** {matched_phrases}
- **Still Missing:** {missing_phrases}
- **Coverage:** {must_have_keyword_check.get('matched_count', 'N/A')}/{len(must_have_keyword_check.get('target_phrases', []))} targets (minimum {must_have_keyword_check.get('required_matches', 'N/A')})
- **Message:** {must_have_keyword_check.get('message', 'No must-have keyword diagnostics captured')}
"""

    uniqueness_section = ""
    if uniqueness_check:
        uniqueness_section = f"""
## Bullet Uniqueness
- **Status:** {str(uniqueness_check.get('status', 'unknown')).upper()}
- **Unique Ratio:** {uniqueness_check.get('unique_ratio', 'N/A')}
- **Unique Count:** {uniqueness_check.get('unique_count', 'N/A')}
- **Reused Count:** {uniqueness_check.get('reused_count', 'N/A')}
- **Message:** {uniqueness_check.get('message', 'No uniqueness diagnostics captured')}
"""

    company_keyword_section = ""
    if company_keyword_check:
        mentions = company_keyword_check.get("mentions", {})
        mention_summary = ", ".join(
            f"{keyword}: {count}" for keyword, count in mentions.items()
        ) or "None"
        required_keywords = ", ".join(company_keyword_check.get("required_keywords", [])) or "None"
        company_keyword_section = f"""
## Company Keyword Advisory
- **Status:** {str(company_keyword_check.get('status', 'unknown')).upper()}
- **Required Keywords:** {required_keywords}
- **Mentions:** {mention_summary}
- **Message:** {company_keyword_check.get('message', 'No company keyword diagnostics captured')}
"""

    keyword_density_section = ""
    if keyword_density_check:
        priority_terms = ", ".join(keyword_density_check.get("priority_terms", [])) or "None"
        mentioned_terms = ", ".join(keyword_density_check.get("mentioned_terms", [])) or "None"
        missing_terms = ", ".join(keyword_density_check.get("missing_terms", [])) or "None"
        keyword_density_section = f"""
## ATS Keyword Density
- **Status:** {str(keyword_density_check.get('status', 'unknown')).upper()}
- **Priority Terms:** {priority_terms}
- **Mentioned In Bullets:** {mentioned_terms}
- **Still Missing:** {missing_terms}
- **Coverage:** {keyword_density_check.get('bullets_with_priority_terms', 'N/A')}/{keyword_density_check.get('total_bullets', 'N/A')} bullets ({keyword_density_check.get('density', 'N/A')})
- **Message:** {keyword_density_check.get('message', 'No ATS keyword-density diagnostics captured')}
"""

    keyword_role_spread_section = ""
    if keyword_role_spread_check:
        multi_role_terms = ", ".join(keyword_role_spread_check.get("multi_role_terms", [])) or "None"
        single_role_terms = ", ".join(keyword_role_spread_check.get("single_role_terms", [])) or "None"
        keyword_role_spread_section = f"""
## ATS Keyword Placement
- **Status:** {str(keyword_role_spread_check.get('status', 'unknown')).upper()}
- **Multi-Role Terms:** {multi_role_terms}
- **Single-Role Terms:** {single_role_terms}
- **Role Sections Checked:** {keyword_role_spread_check.get('sections_with_bullets', 'N/A')}
- **Message:** {keyword_role_spread_check.get('message', 'No keyword-placement diagnostics captured')}
"""

    impact_section = ""
    if impact_check:
        weak_openers = ", ".join(impact_check.get("weak_openers", [])) or "None"
        impact_section = f"""
## Impact-First Bullet Audit
- **Status:** {str(impact_check.get('status', 'unknown')).upper()}
- **Impact-Led Bullets:** {impact_check.get('impact_led_bullets', 'N/A')}/{impact_check.get('total_bullets', 'N/A')} ({impact_check.get('impact_ratio', 'N/A')})
- **Metric-Led Openings:** {impact_check.get('metric_opening_bullets', 'N/A')}
- **Weak Openers:** {weak_openers}
- **Message:** {impact_check.get('message', 'No impact-first diagnostics captured')}
"""

    action_context_result_section = ""
    if action_context_result_check:
        missing_context_examples = "; ".join(
            action_context_result_check.get("missing_context_examples", [])
        ) or "None"
        action_context_result_section = f"""
## Action-Context-Result Audit
- **Status:** {str(action_context_result_check.get('status', 'unknown')).upper()}
- **ACR Bullets:** {action_context_result_check.get('action_context_result_bullets', 'N/A')}/{action_context_result_check.get('total_bullets', 'N/A')} ({action_context_result_check.get('acr_ratio', 'N/A')})
- **Missing Context Examples:** {missing_context_examples}
- **Message:** {action_context_result_check.get('message', 'No action-context-result diagnostics captured')}
"""

    report = f"""# Job Analysis Report

## Job Details
- **Company:** {company}
- **Role:** {role}
- **URL:** {job_url}
- **Match Score:** {match_score}%
- **Confidence:** {analysis.get('confidence_score', 'N/A')}%
- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Detected Tech Stack
{', '.join(analysis.get('detected_tech_stack', ['Not analyzed']))}

## Industry Focus
{analysis.get('industry_focus', 'General').title()}

## Selected Accomplishments
{chr(10).join(['- ' + aid for aid in analysis.get('selected_accomplishments', [])])}

## Match Reasoning
{analysis.get('match_reasoning', 'No reasoning provided')}

## Tailored Summary
{analysis.get('final_summary', analysis.get('rewritten_summary', 'Using default summary'))}

{relevance_section}
{section_coverage_section}
{page_length_section}
{must_have_keyword_section}
{uniqueness_section}
{company_keyword_section}
{keyword_density_section}
{keyword_role_spread_section}
{impact_section}
{action_context_result_section}

---
*Generated by ATS Sniper v3 - AI Resume Tailoring Engine*
"""

    safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:30]
    safe_role = re.sub(r'[^\w\s-]', '', role).replace(' ', '_')[:30]

    report_path = output_dir / f"{safe_company}_{safe_role}_Analysis.md"

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    return report_path


def generate_tailored_resume_for_job(
    job_url: str,
    job_description: str,
    company: str,
    role: str,
    match_score: int = 80,
    dry_run: bool = False,
    template_hint: Optional[str] = None,
    preferred_accomplishment_ids: Optional[Sequence[str]] = None,
    model_override: Optional[str] = None,
    allow_must_have_keyword_override: bool = False,
) -> Optional[Dict]:
    """
    Main entry point: Generate a tailored resume for a hot job.

    Args:
        job_url: URL of the job posting
        job_description: Full text of the job description
        company: Company name
        role: Job title
        match_score: Pre-calculated match score
        dry_run: If True, don't generate files
        model_override: Optional explicit model for tailoring analysis

    Returns:
        Dict with paths to generated files or None on failure
    """
    print(f"\nGenerating tailored resume for: {role} @ {company}")
    print(f"   Match Score: {match_score}%")

    config = load_config()
    client = OpenAI(api_key=config.get("openai_key"))
    model = (
        str(model_override or "").strip()
        or config.get("settings", {}).get("openai_model", "gpt-4o-mini")
    )

    # Load accomplishments
    accomplishments_md = load_accomplishments()
    accomplishments = parse_accomplishments_to_dict(accomplishments_md)
    print(f"   Loaded {len(accomplishments)} accomplishments")

    # Load base template
    base_template = load_resume_template(role, template_hint)

    if dry_run:
        print("   [DRY RUN] Would analyze and generate resume")
        return {"status": "dry_run"}

    # Create output directory
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:20]
    safe_role = re.sub(r'[^\w\s-]', '', role).replace(' ', '_')[:20]
    output_dir = OUTPUTS_DIR / f"{date_str}_{safe_company}_{safe_role}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Analyze job and select accomplishments
    print("   Analyzing job description...")
    analysis = analyze_job_and_select_accomplishments(
        client,
        job_description,
        accomplishments,
        company,
        role,
        model,
        template_hint,
    )
    analysis = apply_manual_tailoring_preferences(
        analysis,
        accomplishments,
        tuple(preferred_accomplishment_ids or ()),
    )

    if analysis.get("error"):
        print(f"   WARNING: LLM blocked resume generation: {analysis.get('error')}")
        return {
            "status": "analysis_error",
            "output_dir": str(output_dir),
            "analysis_report": None,
            "resume_source": None,
            "resume_pdf": None,
            "resume_docx": None,
            "match_score": match_score,
            "confidence": 0,
            "selected_accomplishments": [],
            "error": analysis.get("error"),
        }

    selected_count = len(analysis.get("selected_accomplishments", []))
    print(f"   Selected {selected_count} accomplishments")

    # Step 2: Generate analysis report
    print("   Creating tailored resume source...")
    resume_source_path = create_tailored_resume_source(
        company,
        role,
        analysis,
        base_template,
        accomplishments,
        job_description,
        output_dir,
    )

    # Step 3: Parse final bullet content and enforce relevance threshold
    from generate_resumes import (
        calculate_pdf_layout_metrics,
        condense_resume_data_for_one_page,
        parse_accomplishments,
        parse_resume_source,
    )

    parsed_accomplishments = parse_accomplishments(ACCOMPLISHMENTS_PATH)
    resume_data = parse_resume_source(resume_source_path, parsed_accomplishments)
    resume_source_text = resume_source_path.read_text(encoding="utf-8")
    condensed_resume_data = condense_resume_data_for_one_page(resume_data)
    page_length_check = evaluate_page_length(
        resume_data,
        condensed_resume_data,
        calculate_pdf_layout_metrics(condensed_resume_data).estimated_pages,
    )
    bullet_pairs = [
        (bullet_text, technology_text)
        for experience in resume_data.get("experience", [])
        for bullet_text, technology_text in experience.get("bullets", [])
    ]
    relevance_check = evaluate_resume_relevance(
        role,
        job_description,
        analysis.get("detected_tech_stack", []),
        bullet_pairs,
    )
    section_coverage_check = evaluate_standard_section_coverage(
        resume_data,
        resume_source_text,
    )
    must_have_keyword_check = evaluate_must_have_keyword_coverage(
        role,
        job_description,
        analysis.get("detected_tech_stack", []),
        resume_data,
        bullet_pairs,
        analysis.get("must_have_keyword_targets"),
    )
    uniqueness_check = evaluate_bullet_uniqueness(
        bullet_pairs,
        load_existing_resume_bullet_signatures(output_dir),
    )
    company_keyword_check = evaluate_company_keyword_mentions(
        company,
        resume_data.get("summary", ""),
        bullet_pairs,
    )
    keyword_density_check = evaluate_keyword_bullet_density(
        role,
        job_description,
        analysis.get("detected_tech_stack", []),
        bullet_pairs,
    )
    keyword_role_spread_check = evaluate_keyword_role_spread(
        role,
        job_description,
        analysis.get("detected_tech_stack", []),
        resume_data,
    )
    impact_check = evaluate_impact_first_bullet_structure(bullet_pairs)
    action_context_result_check = evaluate_action_context_result_bullets(bullet_pairs)
    analysis["final_summary"] = resume_data.get("summary", "")

    # Step 4: Generate analysis report
    print("   Generating analysis report...")
    report_path = generate_analysis_report(
        company,
        role,
        job_url,
        match_score,
        analysis,
        output_dir,
        relevance_check,
        section_coverage_check,
        page_length_check,
        must_have_keyword_check,
        uniqueness_check,
        company_keyword_check,
        keyword_density_check,
        keyword_role_spread_check,
        impact_check,
        action_context_result_check,
    )

    if relevance_check.get("status") == "reject":
        print("   WARNING: Relevance check rejected this resume draft before export")
        for stale_file in output_dir.glob("*_Resume.pdf"):
            stale_file.unlink(missing_ok=True)
        for stale_file in output_dir.glob("*_Resume.docx"):
            stale_file.unlink(missing_ok=True)
        for stale_file in output_dir.glob("*_Resume_ATS.docx"):
            stale_file.unlink(missing_ok=True)
        return {
            "status": "rejected_low_relevance",
            "output_dir": str(output_dir),
            "analysis_report": str(report_path),
            "resume_source": str(resume_source_path),
            "resume_pdf": None,
            "resume_docx": None,
            "resume_ats_docx": None,
            "match_score": match_score,
            "confidence": analysis.get("confidence_score", 0),
            "selected_accomplishments": analysis.get("selected_accomplishments", []),
            "relevance": relevance_check,
            "page_length": page_length_check,
            "must_have_keyword_gate": must_have_keyword_check,
            "uniqueness": uniqueness_check,
            "company_keyword_gate": company_keyword_check,
            "keyword_density": keyword_density_check,
            "keyword_role_spread": keyword_role_spread_check,
            "impact_first_bullets": impact_check,
            "action_context_result": action_context_result_check,
        }

    if page_length_check.get("status") == "reject":
        print("   WARNING: Page-length gate rejected this resume draft before export")
        for stale_file in output_dir.glob("*_Resume.pdf"):
            stale_file.unlink(missing_ok=True)
        for stale_file in output_dir.glob("*_Resume.docx"):
            stale_file.unlink(missing_ok=True)
        for stale_file in output_dir.glob("*_Resume_ATS.docx"):
            stale_file.unlink(missing_ok=True)
        return {
            "status": "rejected_page_length_gate",
            "output_dir": str(output_dir),
            "analysis_report": str(report_path),
            "resume_source": str(resume_source_path),
            "resume_pdf": None,
            "resume_docx": None,
            "resume_ats_docx": None,
            "match_score": match_score,
            "confidence": analysis.get("confidence_score", 0),
            "selected_accomplishments": analysis.get("selected_accomplishments", []),
            "relevance": relevance_check,
            "page_length": page_length_check,
            "must_have_keyword_gate": must_have_keyword_check,
            "uniqueness": uniqueness_check,
            "company_keyword_gate": company_keyword_check,
            "keyword_density": keyword_density_check,
            "keyword_role_spread": keyword_role_spread_check,
            "impact_first_bullets": impact_check,
            "action_context_result": action_context_result_check,
        }

    must_have_keyword_override_applied = False
    if must_have_keyword_check.get("status") == "reject":
        if allow_must_have_keyword_override:
            print("   WARNING: Must-have keyword gate rejected this draft; continuing for manual review packaging")
            must_have_keyword_override_applied = True
        else:
            print("   WARNING: Must-have keyword gate rejected this resume draft before export")
            for stale_file in output_dir.glob("*_Resume.pdf"):
                stale_file.unlink(missing_ok=True)
            for stale_file in output_dir.glob("*_Resume.docx"):
                stale_file.unlink(missing_ok=True)
            for stale_file in output_dir.glob("*_Resume_ATS.docx"):
                stale_file.unlink(missing_ok=True)
            return {
                "status": "rejected_must_have_keyword_gate",
                "output_dir": str(output_dir),
                "analysis_report": str(report_path),
                "resume_source": str(resume_source_path),
                "resume_pdf": None,
                "resume_docx": None,
                "resume_ats_docx": None,
                "match_score": match_score,
                "confidence": analysis.get("confidence_score", 0),
                "selected_accomplishments": analysis.get("selected_accomplishments", []),
                "relevance": relevance_check,
                "page_length": page_length_check,
                "must_have_keyword_gate": must_have_keyword_check,
                "uniqueness": uniqueness_check,
                "company_keyword_gate": company_keyword_check,
                "keyword_density": keyword_density_check,
                "keyword_role_spread": keyword_role_spread_check,
                "impact_first_bullets": impact_check,
                "action_context_result": action_context_result_check,
            }

    if company_keyword_check.get("status") == "reject":
        print("   WARNING: Company keyword gate rejected this resume draft before export")
        for stale_file in output_dir.glob("*_Resume.pdf"):
            stale_file.unlink(missing_ok=True)
        for stale_file in output_dir.glob("*_Resume.docx"):
            stale_file.unlink(missing_ok=True)
        for stale_file in output_dir.glob("*_Resume_ATS.docx"):
            stale_file.unlink(missing_ok=True)
        return {
            "status": "rejected_company_keyword_gate",
            "output_dir": str(output_dir),
            "analysis_report": str(report_path),
            "resume_source": str(resume_source_path),
            "resume_pdf": None,
            "resume_docx": None,
            "resume_ats_docx": None,
            "match_score": match_score,
            "confidence": analysis.get("confidence_score", 0),
            "selected_accomplishments": analysis.get("selected_accomplishments", []),
            "relevance": relevance_check,
            "page_length": page_length_check,
            "must_have_keyword_gate": must_have_keyword_check,
            "uniqueness": uniqueness_check,
            "company_keyword_gate": company_keyword_check,
            "keyword_density": keyword_density_check,
            "keyword_role_spread": keyword_role_spread_check,
            "impact_first_bullets": impact_check,
            "action_context_result": action_context_result_check,
        }

    # Step 5: Generate PDF plus styled and ATS-friendly DOCX variants
    print("   Generating PDF and DOCX variants...")
    pdf_path, docx_path, ats_docx_path = generate_resume_outputs(resume_source_path, output_dir)

    result = {
        "status": "success",
        "output_dir": str(output_dir),
        "analysis_report": str(report_path),
        "resume_source": str(resume_source_path),
        "resume_pdf": str(pdf_path) if pdf_path else None,
        "resume_docx": str(docx_path) if docx_path else None,
        "resume_ats_docx": str(ats_docx_path) if ats_docx_path else None,
        "match_score": match_score,
        "confidence": analysis.get("confidence_score", 0),
        "selected_accomplishments": analysis.get("selected_accomplishments", []),
        "relevance": relevance_check,
        "page_length": page_length_check,
        "must_have_keyword_gate": must_have_keyword_check,
        "uniqueness": uniqueness_check,
        "company_keyword_gate": company_keyword_check,
        "keyword_density": keyword_density_check,
        "keyword_role_spread": keyword_role_spread_check,
        "impact_first_bullets": impact_check,
        "action_context_result": action_context_result_check,
        "must_have_keyword_override_applied": must_have_keyword_override_applied,
    }

    print(f"   Generated: {output_dir.name}/")
    return result


def generate_resume_outputs(
    resume_source_path: Path,
    output_dir: Path,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Generate PDF plus styled and ATS-friendly DOCX files from resume source."""
    try:
        # Import the resume generator
        from generate_resumes import (
            condense_resume_data_for_one_page,
            create_ats_docx_resume,
            create_docx_resume,
            create_pdf_resume,
            parse_accomplishments,
            parse_resume_source,
        )

        accomplishments = parse_accomplishments(ACCOMPLISHMENTS_PATH)
        data = parse_resume_source(resume_source_path, accomplishments)
        data = condense_resume_data_for_one_page(data)

        # Generate outputs
        role_key = resume_source_path.stem.replace("resume_", "")

        pdf_path = output_dir / f"{role_key}_Resume.pdf"
        docx_path = output_dir / f"{role_key}_Resume.docx"
        ats_docx_path = output_dir / f"{role_key}_Resume_ATS.docx"

        create_pdf_resume(role_key, data, str(pdf_path))
        create_docx_resume(role_key, data, str(docx_path))
        create_ats_docx_resume(role_key, data, str(ats_docx_path))

        return pdf_path, docx_path, ats_docx_path
    except Exception as e:
        print(f"   WARNING: Resume generation error: {e}")
        return None, None, None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate tailored resume for a job")
    parser.add_argument("--url", help="Job URL")
    parser.add_argument("--company", help="Company name")
    parser.add_argument("--role", help="Job title")
    parser.add_argument("--score", type=int, default=80, help="Match score")
    parser.add_argument("--dry-run", action="store_true", help="Don't generate files")
    parser.add_argument("--test", action="store_true", help="Run with test data")

    args = parser.parse_args()

    if args.test:
        # Test with sample data
        result = generate_tailored_resume_for_job(
            job_url="https://example.com/job/123",
            job_description="""
            We are looking for a Senior DevOps Engineer to join our cloud infrastructure team.
            Requirements:
            - 3+ years of experience with Azure or AWS
            - Strong knowledge of Kubernetes and Docker
            - Experience with CI/CD pipelines (Azure DevOps, GitHub Actions)
            - Infrastructure as Code (Terraform, Bicep)
            - Python or PowerShell scripting
            """,
            company="Test Company",
            role="Senior DevOps Engineer",
            match_score=85,
            dry_run=args.dry_run
        )
        print(f"\nResult: {json.dumps(result, indent=2)}")
    elif args.url and args.company and args.role:
        # Fetch job description (would need job_scraper integration)
        print("Full integration requires job_scraper.fetch_job_description()")
    else:
        parser.print_help()

