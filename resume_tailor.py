#!/usr/bin/env python3
"""
Resume Tailor v3 - AI-Powered Resume Matching & Generation
Uses OpenAI GPT-4o-mini for:
- Match scoring (0-100%)
- Gap analysis (missing skills/experience)
- Achievement generation with placeholder metrics
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Add parent dir for generate_resumes imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from job_scraper import fetch_job_description
from generate_tailored_resume import extract_exact_jd_phrase_targets, is_experience_section_heading

from utils.openai_chat import create_chat_completion, extract_completion_usage
from utils.state import load_config, load_state, STATE_PATH

REPORTS_DIR = Path(__file__).parent / "reports"
RESUMES_DIR = Path(__file__).parent.parent / "word_files"
ACCOMPLISHMENTS_FILE = Path(__file__).parent.parent / "accomplishments.md"
EARLY_SCREEN_CATEGORIES = {
    "SECURITY",
    "IAM",
    "DEVOPS_SRE_CLOUD",
    "ADJACENT_TECH",
    "NOISE",
}
ADJACENT_TECH_LANE = "ADJACENT_TECH"
ROLE_SCOPE_EXECUTIVE_TITLE_MARKERS = (
    " director ",
    " head of ",
    " vp ",
    " vice president ",
    " chief ",
)
ROLE_SCOPE_MANAGER_TITLE_MARKERS = (" manager ",)
ROLE_SCOPE_STRETCH_TITLE_MARKERS = (" lead ", " principal ", " staff ")
ROLE_SCOPE_OWNER_TITLE_MARKERS = (" owner ",)
ROLE_SCOPE_PEOPLE_MANAGEMENT_MARKERS = (
    " manage a team ",
    " managing a team ",
    " people manager ",
    " direct reports ",
    " lead and grow a distributed team ",
    " leadership of high performing ",
)
ROLE_SCOPE_STRATEGY_MARKERS = (
    " multi year ",
    " technical authority ",
    " set technical direction ",
    " architecture leadership ",
    " own aws strategy ",
    " roadmap aligned with company strategy ",
    " technical oversight ",
)
ROLE_SCOPE_IC_HINT_MARKERS = (
    " individual contributor ",
    " no direct reports ",
    " hands on ",
    " hands-on ",
)
EXACT_FIT_TITLE_LANES = {
    "SECURITY": (
        "application security engineer",
        "product security engineer",
        "infrastructure security engineer",
        "cloud security engineer",
        "security platform engineer",
        "security operations engineer",
        "secops engineer",
        "devsecops engineer",
    ),
    "IAM": (
        "iam engineer",
        "identity engineer",
        "identity security engineer",
        "identity and access management engineer",
        "identity and access management analyst",
        "access management engineer",
        "customer identity engineer",
        "ciam engineer",
        "azure iam engineer",
    ),
    "DEVOPS_SRE_CLOUD": (
        "devops engineer",
        "site reliability engineer",
        "platform reliability engineer",
        "platform engineer",
        "production engineer",
        "cloud platform engineer",
        "cloud reliability engineer",
        "azure cloud engineer",
        "azure devops engineer",
        "infrastructure automation engineer",
        "cloud operations engineer",
        "infrastructure administration engineer",
    ),
}
EXACT_FIT_REQUIREMENT_MARKERS = {
    "SECURITY": (
        "application security",
        "product security",
        "infrastructure security",
        "cloud security",
        "security operations",
        "secops",
        "devsecops",
        "vulnerability",
        "security scanning",
        "static analysis",
        "siem",
        "incident response",
        "threat detection",
        "code review",
        "container security",
        "compliance",
        "rbac",
        "policy",
        "policies",
    ),
    "IAM": (
        "identity",
        "identity and access management",
        "iam",
        "access management",
        "authentication",
        "authorization",
        "federation",
        "sso",
        "oauth",
        "oidc",
        "saml",
        "scim",
        "okta",
        "entra",
        "active directory",
        "sailpoint",
        "provisioning",
        "access review",
        "access reviews",
        "pam",
        "rbac",
    ),
    "DEVOPS_SRE_CLOUD": (
        "devops",
        "site reliability",
        "sre",
        "platform",
        "platform engineering",
        "platform reliability",
        "cloud",
        "infrastructure",
        "kubernetes",
        "terraform",
        "bicep",
        "ci/cd",
        "observability",
        "monitoring",
        "incident response",
        "incident management",
        "on-call",
        "reliability",
        "automation",
        "azure",
        "aws",
        "gcp",
        "prometheus",
        "grafana",
    ),
}
ADJACENT_TECH_TITLE_PATTERNS = (
    "software support engineer",
    "systems software support engineer",
    "application support engineer",
    "platform support engineer",
    "technical support engineer",
    "implementation engineer",
    "technical implementation engineer",
    "integration engineer",
    "systems integration engineer",
    "automation engineer",
    "workflow automation engineer",
    "agentic engineer",
    "ai tooling engineer",
    "developer productivity engineer",
    "internal tools engineer",
)
ADJACENT_TECH_REQUIREMENT_MARKERS = (
    "api",
    "apis",
    "sdk",
    "integration",
    "integrations",
    "implementation",
    "automation",
    "workflow",
    "workflows",
    "deployment",
    "deployments",
    "configuration",
    "configure",
    "configured",
    "troubleshooting",
    "troubleshoot",
    "debugging",
    "debug",
    "sql",
    "python",
    "powershell",
    "json",
    "yaml",
    "cli",
    "testing",
    "test automation",
    "technical support",
    "platform support",
    "system configuration",
    "log analysis",
    "llm",
    "agent",
    "prompt",
    "tooling",
    "developer productivity",
)


def load_base_resume() -> str:
    """Load the base resume content (accomplishments.md)."""
    if ACCOMPLISHMENTS_FILE.exists():
        with open(ACCOMPLISHMENTS_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


COVER_LETTERS_DIR = Path(__file__).parent / "cover_letters"


@dataclass(frozen=True)
class CoverLetterGuidance:
    """Optional guidance that sharpens a generated cover letter."""

    industry_focus: str = ""
    company_context: str = ""
    emphasis_points: tuple[str, ...] = ()


def _extract_completion_text(response: Any) -> str:
    """Extract the raw text body from an OpenAI chat completion response."""
    if hasattr(response, "content"):
        content = response.content[0].text
    else:
        content = response.choices[0].message.content

    cleaned = content.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]

    return cleaned.strip()


def _load_json_completion(response: Any) -> dict[str, Any]:
    """Parse a JSON payload from an OpenAI chat completion response."""
    return json.loads(_extract_completion_text(response))


def _normalize_scoring_text(*parts: str) -> str:
    """Normalize freeform text for deterministic scoring adjustments."""
    return " ".join(
        re.sub(r"\s+", " ", str(part)).strip().casefold()
        for part in parts
        if str(part).strip()
    )


def _contains_scoring_marker(normalized_text: str, marker: str) -> bool:
    """Match a normalized scoring marker as a whole phrase."""
    normalized_marker = re.sub(r"\s+", " ", str(marker)).strip().casefold()
    if not normalized_marker:
        return False

    return re.search(
        rf"(?<![A-Za-z0-9]){re.escape(normalized_marker)}(?![A-Za-z0-9])",
        normalized_text,
    ) is not None


def _evaluate_exact_fit_signal(job_title: str, job_desc: str) -> dict[str, Any]:
    """Measure whether a role is an exact-fit target with strong requirement overlap."""
    normalized_title = f" {_normalize_scoring_text(job_title)} "
    lane = ""
    title_phrase = ""

    for candidate_lane, title_patterns in EXACT_FIT_TITLE_LANES.items():
        for pattern in title_patterns:
            if _contains_scoring_marker(normalized_title, pattern):
                lane = candidate_lane
                title_phrase = pattern
                break
        if lane:
            break

    if not lane:
        return {
            "lane": "",
            "title_match": False,
            "title_phrase": "",
            "signal_score": 0,
            "matched_markers": [],
        }

    normalized_desc = f" {_normalize_scoring_text(job_desc)} "
    matched_markers = [
        marker
        for marker in EXACT_FIT_REQUIREMENT_MARKERS[lane]
        if _contains_scoring_marker(normalized_desc, marker)
    ]
    if title_phrase and _contains_scoring_marker(normalized_desc, title_phrase):
        matched_markers.insert(0, title_phrase)

    deduped_markers: list[str] = []
    seen_markers: set[str] = set()
    for marker in matched_markers:
        marker_key = marker.casefold()
        if marker_key in seen_markers:
            continue
        seen_markers.add(marker_key)
        deduped_markers.append(marker)

    return {
        "lane": lane,
        "title_match": True,
        "title_phrase": title_phrase,
        "signal_score": len(deduped_markers),
        "matched_markers": deduped_markers[:8],
    }


def _evaluate_adjacent_fit_signal(job_title: str, job_desc: str) -> dict[str, Any]:
    """Measure whether a broader adjacent-tech role has strong technical overlap."""
    normalized_title = f" {_normalize_scoring_text(job_title)} "
    title_phrase = ""
    for pattern in ADJACENT_TECH_TITLE_PATTERNS:
        if _contains_scoring_marker(normalized_title, pattern):
            title_phrase = pattern
            break

    if not title_phrase:
        return {
            "lane": "",
            "title_match": False,
            "title_phrase": "",
            "signal_score": 0,
            "matched_markers": [],
        }

    normalized_desc = f" {_normalize_scoring_text(job_desc)} "
    matched_markers = [
        marker
        for marker in ADJACENT_TECH_REQUIREMENT_MARKERS
        if _contains_scoring_marker(normalized_desc, marker)
    ]
    if title_phrase and _contains_scoring_marker(normalized_desc, title_phrase):
        matched_markers.insert(0, title_phrase)

    deduped_markers: list[str] = []
    seen_markers: set[str] = set()
    for marker in matched_markers:
        marker_key = marker.casefold()
        if marker_key in seen_markers:
            continue
        seen_markers.add(marker_key)
        deduped_markers.append(marker)

    return {
        "lane": ADJACENT_TECH_LANE,
        "title_match": True,
        "title_phrase": title_phrase,
        "signal_score": len(deduped_markers),
        "matched_markers": deduped_markers[:10],
    }


def _calculate_exact_fit_bonus(
    base_match_score: int,
    exact_fit_signal: Mapping[str, Any],
) -> int:
    """Apply a modest deterministic boost for strong exact-fit IC roles."""
    adjusted_score = max(0, min(100, int(base_match_score or 0)))
    if adjusted_score < 72 or adjusted_score >= 90:
        return 0
    if not bool(exact_fit_signal.get("title_match")):
        return 0

    signal_score = int(exact_fit_signal.get("signal_score", 0) or 0)
    if signal_score >= 8:
        return 8
    if signal_score >= 6:
        return 6
    if signal_score >= 4:
        return 5 if adjusted_score >= 75 else 4
    return 0


def _calculate_adjacent_fit_bonus(
    base_match_score: int,
    adjacent_fit_signal: Mapping[str, Any],
) -> int:
    """Apply a deterministic boost for strong adjacent-tech roles with deep technical overlap."""
    adjusted_score = max(0, min(100, int(base_match_score or 0)))
    if adjusted_score < 78 or adjusted_score >= 90:
        return 0
    if not bool(adjacent_fit_signal.get("title_match")):
        return 0

    signal_score = int(adjacent_fit_signal.get("signal_score", 0) or 0)
    if signal_score >= 10:
        return 12
    if signal_score >= 8:
        return 10
    if signal_score >= 6:
        return 8
    if signal_score >= 4 and adjusted_score >= 84:
        return 6
    return 0


def _extract_min_required_years(job_desc: str) -> int | None:
    """Extract the minimum explicitly required years of experience, when present."""
    normalized_desc = _normalize_scoring_text(job_desc)
    matches = [
        int(match.group(1))
        for match in re.finditer(
            r"\b(\d{1,2})\s*(?:\+|plus)?\s*years?(?:\s+of)?\s+experience\b",
            normalized_desc,
        )
    ]
    if not matches:
        return None
    return min(matches)


def _apply_role_scope_adjustments(match_score: int, *, job_title: str, job_desc: str) -> int:
    """Apply deterministic caps for management-heavy or stretch-level roles."""
    adjusted_score = max(0, min(100, int(match_score or 0)))
    normalized_title = f" {_normalize_scoring_text(job_title)} "
    normalized_desc = f" {_normalize_scoring_text(job_desc)} "
    if not normalized_title.strip():
        return adjusted_score

    min_required_years = _extract_min_required_years(job_desc)
    has_people_management = any(marker in normalized_desc for marker in ROLE_SCOPE_PEOPLE_MANAGEMENT_MARKERS)
    has_strategy_scope = any(marker in normalized_desc for marker in ROLE_SCOPE_STRATEGY_MARKERS)
    has_ic_hint = any(marker in normalized_desc for marker in ROLE_SCOPE_IC_HINT_MARKERS)

    if any(marker in normalized_title for marker in ROLE_SCOPE_EXECUTIVE_TITLE_MARKERS):
        return min(adjusted_score, 25)

    if any(marker in normalized_title for marker in ROLE_SCOPE_MANAGER_TITLE_MARKERS) or has_people_management:
        return min(adjusted_score, 35)

    if any(marker in normalized_title for marker in ROLE_SCOPE_OWNER_TITLE_MARKERS):
        if has_people_management or has_strategy_scope:
            return min(adjusted_score, 45)
        return min(adjusted_score, 60)

    if any(marker in normalized_title for marker in ROLE_SCOPE_STRETCH_TITLE_MARKERS):
        if has_ic_hint or (min_required_years is not None and min_required_years <= 6 and not has_people_management):
            return min(adjusted_score, 60)
        return min(adjusted_score, 45)

    if min_required_years is not None and min_required_years >= 8:
        return min(adjusted_score, 55)

    return adjusted_score


def classify_job_for_screening(
    client: OpenAI,
    job: Mapping[str, Any],
    job_desc: str,
    model: str = "gpt-4o-mini",
    *,
    include_usage: bool = False,
) -> dict[str, Any]:
    """Classify a job into a target lane before running full resume scoring."""
    prompt = f"""You are the first-pass screener for ATS Sniper.

Classify this job into exactly one category:
- SECURITY: security engineering, cloud security, application security, detection/response, vulnerability, security automation
- IAM: identity and access management, authentication, authorization, PAM, IGA, Okta, Entra ID, SSO, federation
- DEVOPS_SRE_CLOUD: DevOps, SRE, cloud, platform, infrastructure, reliability, incident management, systems automation, CI/CD, Kubernetes, Terraform
- ADJACENT_TECH: hands-on software support, implementation, integration, automation, developer tooling, agentic tooling, or technical platform support roles with APIs, SQL, scripting, deployment, troubleshooting, or system configuration depth
- NOISE: generic software engineering, backend-only app work, frontend/UI/UX, product, data, QA, sales, support, internships, consulting, or people-management roles

Rules:
- Use NOISE only when the role is clearly outside the target lanes.
- Generic software, backend, or full-stack roles without meaningful infrastructure, reliability, security, identity, or cloud-operations scope are NOISE.
- Data engineer, data platform, AI engineer, ML platform, and AI infrastructure roles are NOISE unless the work is primarily cloud infrastructure, security engineering, enterprise IAM, or SRE.
- Generic titles such as "Systems Engineer", "Technical Operations Engineer", "Software Engineer", or "Platform Owner" are NOT automatically NOISE when the title or description clearly centers on cloud infrastructure, platform operations, site reliability, security operations, IAM, incident response, or enterprise systems automation.
- Do not treat product/backend engineering as IAM just because the title mentions identity, auth, governance, or decisioning. IAM is for enterprise identity and access work such as provisioning, SSO, federation, RBAC, access reviews, PAM, Okta, Entra ID, SailPoint, or access-certification operations.
- Platform, infrastructure, cloud, reliability, and incident-focused roles belong in DEVOPS_SRE_CLOUD only when the role is about internal platforms, CI/CD, Kubernetes, Terraform, cloud operations, observability, incident response, or infrastructure automation.
- ADJACENT_TECH is for technical implementation, integration, automation, software support, agentic tooling, internal tools, or developer-productivity roles when the work is clearly hands-on and centers on APIs, deployments, troubleshooting, scripting, SQL, workflow automation, testing, or technical system configuration.
- Generic customer support, customer success, sales engineering, or non-technical implementation roles are NOISE.
- Titles centered on data platform, loans platform, consumer platform, AI governance, or backend engineering are usually NOISE unless the description is explicitly about infrastructure, SRE, cloud operations, security engineering, or enterprise IAM operations.
- Recent examples from this repo's manual review:
    - Spotify | Backend Engineer - Data Platform -> NOISE
    - GitLab | Staff Backend Engineer, SSCS: AI Governance -> NOISE
    - SoFi | Staff Software Engineer, Loans Platform -> NOISE
    - Affirm | Staff Software Engineer, Back-end (Identity Engineering) -> NOISE unless the job is truly enterprise IAM with Okta/Entra/SailPoint/SSO/RBAC/access-review scope
    - Generic Data Engineer or AI Platform Engineer -> NOISE
    - University of Cincinnati | Identity & Access Management Analyst 2 -> IAM
    - The Christ Hospital | Information Security Analyst - Vulnerability & Testing Coordinator -> SECURITY
    - Relevate | Associate Agentic Engineer -> ADJACENT_TECH when the role is internal tooling, workflow automation, APIs, or LLM tooling rather than generic product AI
    - Makino | System Software Support Engineer -> ADJACENT_TECH when the role is hands-on software support, troubleshooting, deployment, automation, APIs, or technical system configuration
- Return a short root-cause reason, not a long explanation.

JOB TITLE: {job.get("title", "")}
COMPANY: {job.get("company", "")}
LOCATION: {job.get("location", "")}
SOURCE: {job.get("source", "")}

JOB DESCRIPTION:
{job_desc}

Return ONLY valid JSON with:
1. "category": one of SECURITY, IAM, DEVOPS_SRE_CLOUD, ADJACENT_TECH, NOISE
2. "reason": short string
3. "confidence": number between 0 and 1
"""

    try:
        response = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            token_limit=250,
            temperature=0,
        )
        payload = _load_json_completion(response)
    except Exception as e:
        print(f"  WARNING: Early classifier error: {e}")
        error_result = {
            "category": "UNKNOWN",
            "reason": str(e),
            "confidence": 0.0,
            "should_skip": False,
        }
        if include_usage:
            error_result["llm_usage"] = extract_completion_usage(None, model=model)
        return error_result

    category = str(payload.get("category", "")).strip().upper()
    if category not in EARLY_SCREEN_CATEGORIES:
        category = "UNKNOWN"

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    confidence = max(0.0, min(1.0, confidence))
    result = {
        "category": category,
        "reason": str(payload.get("reason", "")).strip(),
        "confidence": confidence,
        "should_skip": category == "NOISE",
    }
    if include_usage:
        result["llm_usage"] = extract_completion_usage(response, model=model)
    return result


def extract_primary_experience_facts(resume: str, max_items: int = 6) -> list[str]:
    """Extract the first role's experience bullets from the final resume source."""
    lines = resume.splitlines()
    in_experience = False
    in_primary_role = False
    facts: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if is_experience_section_heading(line):
            in_experience = True
            continue
        if not in_experience:
            continue
        if line.startswith("## "):
            break
        if line.startswith("### "):
            if in_primary_role:
                break
            in_primary_role = True
            continue
        if in_primary_role and line.startswith("- "):
            facts.append(line[2:].strip())
            if len(facts) >= max_items:
                break

    return facts


def build_cover_letter_prompt(
    job_desc: str,
    resume: str,
    company: str,
    role: str,
    guidance: Optional[CoverLetterGuidance] = None,
) -> str:
    """Build the cover-letter prompt with optional industry and company guidance."""
    fact_pool = extract_primary_experience_facts(resume)
    jd_phrase_targets = extract_exact_jd_phrase_targets(role, job_desc, [])
    jd_phrase_targets_block = "\n".join(f"- {phrase}" for phrase in jd_phrase_targets) or "- None identified"
    guidance_lines: list[str] = [
        "- Keep the tone direct, specific, and human.",
        "- Use only resume facts and reasonable inferences from the provided background.",
        "- Keep the 5-year Resurgent tenure and two promotions visible when they support the story.",
        "- Avoid generic openings like 'I am writing to express my interest'.",
        "- Avoid filler phrases like 'align closely with the demands of your role', 'strong fit', or 'well-positioned'.",
        "- Keep the letter to roughly 170-220 words.",
        "- Close with confidence, not desperation.",
    ]
    if guidance and guidance.industry_focus:
        guidance_lines.append(
            f"- Include one sentence connecting the background to this industry context: {guidance.industry_focus}."
        )
    if guidance and guidance.company_context:
        guidance_lines.append(
            f"- Reflect this company or role nuance naturally: {guidance.company_context}."
        )
    if guidance and guidance.emphasis_points:
        guidance_lines.append(
            "- Emphasize these themes when truthful: "
            + "; ".join(point for point in guidance.emphasis_points if point)
            + "."
        )

    guidance_block = "\n".join(guidance_lines)
    fact_block = "\n".join(f"- {fact}" for fact in fact_pool) if fact_pool else "- No fact pool extracted"
    return f"""Write a professional cover letter for this job application.

JOB: {role} at {company}

JOB DESCRIPTION:
{job_desc}

CANDIDATE BACKGROUND:
{resume}

FACTS TO CITE FROM THE FINAL RESUME VERSION:
{fact_block}

ATS PHRASE TARGETS:
{jd_phrase_targets_block}

GUIDANCE:
{guidance_block}

Write a concise, compelling cover letter in 3 short paragraphs plus a sign-off that:
1. Opens with interest in the specific role and company.
2. Highlights 2-3 directly relevant achievements from the candidate background.
3. Connects the candidate's cloud, DevOps, SRE, or infrastructure experience to the role's environment and closes with clear interest.

Hard rules:
- When citing a metric, technology, or accomplishment, use the fact pool above rather than nearby but different resume details.
- Prefer exact technologies named in the fact pool such as Bicep, ARM Templates, AKS, Azure Monitor, or Azure DevOps when they are relevant.
- Reuse at least {min(3, len(jd_phrase_targets)) if jd_phrase_targets else 0} exact JD phrases from the ATS PHRASE TARGETS block when the fact pool truthfully supports them.
- Keep multi-word JD phrases intact where possible instead of paraphrasing them into broader wording.
- Use one sentence that connects the work to the company's mission or industry context.
- Keep sentences compact and avoid repeating the job title or company name more than necessary.
- Mention only the target company name, exactly as provided: {company}. Do not invent or substitute another company, client, or employer name.
- Do not add phone numbers, email addresses, LinkedIn URLs, GitHub URLs, markdown links, or any contact block after the sign-off.
- End exactly with:
Sincerely,
Candidate Name

Start directly with "Dear Hiring Manager," and end with:
Sincerely,
Candidate Name"""


def generate_cover_letter(
    client: OpenAI,
    job_desc: str,
    resume: str,
    company: str,
    role: str,
    model: str = "gpt-4o-mini",
    guidance: Optional[CoverLetterGuidance] = None,
) -> str:
    """
    Generate a tailored cover letter using OpenAI.

    Returns:
        Cover letter text
    """
    prompt = build_cover_letter_prompt(job_desc, resume, company, role, guidance)

    try:
        response = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            token_limit=800,
            temperature=0.4,
        )

        content = response.content[0].text if hasattr(response, 'content') else response.choices[0].message.content
        return content.strip()
    except Exception as e:
        print(f"  WARNING: Cover letter error: {e}")
        return ""


def analyze_job_match(
    client: OpenAI,
    job_desc: str,
    resume: str,
    model: str = "gpt-4o-mini",
    *,
    job_title: str = "",
    include_usage: bool = False,
) -> Dict:
    """
    Analyze job-resume match using OpenAI.
    
    Returns:
        Dict with score, gaps, and suggested achievements
    """
    prompt = f"""You are an expert ATS resume optimizer. Analyze this job description against the candidate's resume.

JOB DESCRIPTION:
{job_desc}

CANDIDATE RESUME/ACCOMPLISHMENTS:
{resume}

IMPORTANT SCORING RULES:
- The candidate has ~5 years of experience and targets hands-on INDIVIDUAL CONTRIBUTOR roles.
- Senior IC titles are in scope and should be scored normally.
- Prioritize the actual day-to-day responsibilities and required technologies over title wording when the title is broad or misleading.
- Do not default to 30 just because a title sounds senior.
- Exact-title security, IAM, DevOps, SRE, and platform roles should score above the generic review floor when the responsibility overlap is strong.
- Adjacent technical roles such as software support engineer, implementation engineer, integration engineer, automation engineer, agentic engineer, or developer tooling roles can score into the 90s when the overlap is strong across APIs, SQL, Python or PowerShell, deployment, troubleshooting, testing, workflow automation, or technical platform support.
- Missing one or two named tools should not automatically trap an exact-fit role in the mid-70s when the description otherwise matches the candidate's hands-on background.
- Hands-on stretch roles (for example some Staff, Principal, Lead, or Owner titles) can still be partial matches if the description is deeply technical and clearly individual-contributor oriented.
- People-management, executive, or org-strategy roles should score low even when there is strong technical overlap.
- Roles explicitly requiring 8+ years or clear team leadership should usually stay in a review-only range, not the hot-job range.

IRRELEVANT ROLE PENALTY (MANDATORY):
- If the role is primarily Sales, Marketing, HR, Legal, Finance, Recruiting, or Customer Success: score 0.
- If the role is primarily frontend/UI/UX with no infrastructure component: cap at 20.

Provide a JSON response with:
1. "match_score": 0-100 score based on skills/experience overlap (APPLY SENIORITY PENALTY if applicable)
2. "matching_skills": List of skills the candidate has that match the job
3. "missing_skills": List of required skills the candidate is missing
4. "gap_analysis": 1-2 sentence summary of gaps (mention if role is too senior)
5. "suggested_achievements": List of 3-5 achievement bullet points that DIRECTLY ADDRESS the job requirements.

   CRITICAL INSTRUCTIONS for achievements:
   - Each achievement MUST target a specific requirement from the job description
   - Start with action verb (Led, Implemented, Designed, Automated, Reduced, etc.)
   - Include relevant technologies MENTIONED IN THE JOB (e.g., Azure, Kubernetes, Terraform, CI/CD)
   - Use placeholder metrics: **[XX]%** or **[XX]** for numbers the candidate will fill in
   - Focus on outcomes: cost savings, uptime, deployment frequency, incident reduction
   - If job mentions specific tools (Datadog, Splunk, Jenkins), include those in achievements

   Example format:
   "Implemented [TOOL_FROM_JOB] monitoring solution, reducing mean time to detection by **[XX]%**"
   "Automated deployment pipelines using [TECH_FROM_JOB], enabling **[XX]** releases per day"

Return ONLY valid JSON, no markdown code blocks."""

    try:
        response = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            token_limit=1500,
            temperature=0.3,
        )

        payload = _load_json_completion(response)
        try:
            base_match_score = int(payload.get("match_score", 0) or 0)
        except (TypeError, ValueError):
            base_match_score = 0
        exact_fit_signal = _evaluate_exact_fit_signal(job_title, job_desc)
        adjacent_fit_signal = _evaluate_adjacent_fit_signal(job_title, job_desc)
        exact_fit_bonus = _calculate_exact_fit_bonus(base_match_score, exact_fit_signal)
        adjacent_fit_bonus = _calculate_adjacent_fit_bonus(base_match_score, adjacent_fit_signal)
        boosted_match_score = base_match_score + max(exact_fit_bonus, adjacent_fit_bonus)
        if 0 < boosted_match_score < 90:
            boosted_match_score = min(boosted_match_score, 89)
        payload["exact_fit_lane"] = exact_fit_signal["lane"]
        payload["exact_fit_title_match"] = exact_fit_signal["title_match"]
        payload["exact_fit_signal_score"] = exact_fit_signal["signal_score"]
        payload["exact_fit_matched_terms"] = exact_fit_signal["matched_markers"]
        payload["exact_fit_bonus"] = exact_fit_bonus
        payload["adjacent_fit_lane"] = adjacent_fit_signal["lane"]
        payload["adjacent_fit_title_match"] = adjacent_fit_signal["title_match"]
        payload["adjacent_fit_signal_score"] = adjacent_fit_signal["signal_score"]
        payload["adjacent_fit_matched_terms"] = adjacent_fit_signal["matched_markers"]
        payload["adjacent_fit_bonus"] = adjacent_fit_bonus
        payload["match_score"] = _apply_role_scope_adjustments(
            boosted_match_score,
            job_title=job_title,
            job_desc=job_desc,
        )
        if include_usage:
            payload["llm_usage"] = extract_completion_usage(response, model=model)
        return payload
    except Exception as e:
        print(f"  WARNING: OpenAI error: {e}")
        error_payload = {"match_score": 0, "error": str(e)}
        if include_usage:
            error_payload["llm_usage"] = extract_completion_usage(None, model=model)
        return error_payload


def process_enterprise_jobs(dry_run: bool = False, limit: int = 10, gen_cover_letters: bool = False, company_filter: str = None) -> List[Dict]:
    """
    Process enterprise jobs from job_state.json.

    Args:
        dry_run: If True, don't make API calls
        limit: Max jobs to process
        gen_cover_letters: If True, generate cover letters for 70%+ matches
        company_filter: Optional - only analyze jobs from this company

    Returns:
        List of match results
    """
    config = load_config()

    # Load job state
    state = load_state()
    if not state.get("jobs"):
        print("❌ No jobs in state. Run ats_sniper.py first.")
        return []

    # Filter enterprise jobs
    enterprise_patterns = config.get("company_tiers", {}).get("enterprise", [])
    enterprise_jobs = []

    for url, job in state.get("jobs", {}).items():
        if any(pattern in url.lower() for pattern in enterprise_patterns):
            # Apply company filter if specified
            if company_filter:
                if company_filter.lower() not in url.lower() and company_filter.lower() not in job.get('company', '').lower():
                    continue
            enterprise_jobs.append({"url": url, **job})

    filter_msg = f" (filtered: {company_filter})" if company_filter else ""
    print(f"📊 Found {len(enterprise_jobs)} enterprise jobs{filter_msg}")
    
    if dry_run:
        print("⚠️ DRY RUN - would process these jobs:")
        for job in enterprise_jobs[:limit]:
            print(f"   • {job.get('company', 'Unknown')}: {job.get('title', 'Unknown')}")
        return []
    
    # Initialize OpenAI
    client = OpenAI(api_key=config.get("openai_key"))
    model = config.get("settings", {}).get("openai_model", "gpt-4o-mini")
    
    # Load resume
    resume = load_base_resume()
    if not resume:
        print("❌ No accomplishments.md found")
        return []
    
    results = []
    
    print(f"\n🤖 Analyzing {min(limit, len(enterprise_jobs))} jobs with {model}...")
    
    for job in enterprise_jobs[:limit]:
        print(f"\n📄 {job.get('company', 'Unknown')}: {job.get('title', 'Unknown')}")
        
        # Fetch full job description
        job_data = fetch_job_description(job["url"])
        if not job_data:
            print("   ⚠️ Could not fetch job description")
            continue
        
        # Analyze match
        analysis = analyze_job_match(
            client,
            job_data.get("description", ""),
            resume,
            model,
            job_title=job.get("title", ""),
        )

        result = {
            "url": job["url"],
            "company": job.get("company", "Unknown"),
            "title": job.get("title", "Unknown"),
            **analysis
        }

        score = analysis.get("match_score", 0)
        emoji = "✅" if score >= 80 else "⚠️" if score >= 60 else "❌"
        print(f"   {emoji} Match: {score}%")
        if analysis.get("gap_analysis"):
            print(f"   📝 Gaps: {analysis['gap_analysis']}")

        # Generate cover letter for high-scoring matches (70%+)
        if gen_cover_letters and score >= 70:
            print(f"   📝 Generating cover letter...")
            cover_letter = generate_cover_letter(
                client,
                job_data.get("description", ""),
                resume,
                job.get("company", "Unknown"),
                job.get("title", "Unknown"),
                model
            )
            if cover_letter:
                result["cover_letter"] = cover_letter
                # Save to file
                COVER_LETTERS_DIR.mkdir(exist_ok=True)
                safe_company = "".join(c for c in job.get("company", "Unknown") if c.isalnum() or c in " _-")
                safe_title = "".join(c for c in job.get("title", "Unknown")[:30] if c.isalnum() or c in " _-")
                cl_path = COVER_LETTERS_DIR / f"cover_letter_{safe_company}_{safe_title}.txt"
                with open(cl_path, 'w', encoding='utf-8') as f:
                    f.write(cover_letter)
                print(f"   💾 Saved: {cl_path.name}")

        results.append(result)
    
    # Save results
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"match_report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    print(f"\n📊 Report saved: {report_path}")

    return results


def inject_achievements_to_accomplishments(report_path: str, target_company: str = None) -> Path:
    """
    Inject AI-generated achievements from a match report into accomplishments.md.
    Creates a new section for AI-generated achievements that can be reviewed and edited.

    Args:
        report_path: Path to the match report JSON
        target_company: Optional - only inject for this company

    Returns:
        Path to the updated accomplishments.md
    """
    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Filter by company if specified
    if target_company:
        report = [r for r in report if target_company.lower() in r.get('company', '').lower()]

    if not report:
        print(f"❌ No matches found" + (f" for {target_company}" if target_company else ""))
        return None

    # Load existing accomplishments
    with open(ACCOMPLISHMENTS_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if AI section already exists
    ai_section_marker = "## AI-Generated Achievements (Review Required)"
    if ai_section_marker not in content:
        content += f"\n\n---\n\n{ai_section_marker}\n"
        content += "**Instructions:** Review each achievement below. Replace `[XX]` with real metrics.\n"
        content += "Copy approved achievements to the appropriate job section above.\n\n"

    # Add new achievements
    new_achievements = []
    for match in report:
        if match.get('match_score', 0) < 60:
            continue  # Skip low matches

        company = match.get('company', 'Unknown')
        title = match.get('title', 'Unknown')
        suggestions = match.get('suggested_achievements', [])

        if not suggestions:
            continue

        # Create unique ID based on company/title
        safe_company = ''.join(c for c in company if c.isalnum())[:10].upper()
        timestamp = datetime.now().strftime('%m%d')

        new_achievements.append(f"\n### {company} - {title} (Match: {match.get('match_score', 0)}%)\n")

        for i, achievement in enumerate(suggestions, 1):
            achievement_id = f"AI-{safe_company}-{timestamp}-{i:02d}"
            new_achievements.append(f"\n#### {achievement_id}\n")
            new_achievements.append(f"**AI-Generated Achievement**\n")
            new_achievements.append(f"- {achievement}\n")
            new_achievements.append(f"- **Technologies:** (add relevant tech)\n")
            new_achievements.append(f"- **Roles:** devops, sre, cloud\n")

    if not new_achievements:
        print("❌ No achievements to inject (all matches < 60%)")
        return None

    content += ''.join(new_achievements)

    # Write back
    with open(ACCOMPLISHMENTS_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✅ Injected {len(new_achievements) // 5} AI achievements into accomplishments.md")
    print(f"📝 Review and edit: {ACCOMPLISHMENTS_FILE}")

    return ACCOMPLISHMENTS_FILE


def generate_targeted_resume(report_path: str, job_index: int = 0) -> Path:
    """
    Generate a targeted resume for a specific job from a match report.

    Args:
        report_path: Path to the match report JSON
        job_index: Which job in the report to target (0 = best match)

    Returns:
        Path to the generated resume markdown
    """
    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Sort by match score and get target job
    report = sorted(report, key=lambda x: x.get('match_score', 0), reverse=True)

    if job_index >= len(report):
        print(f"❌ Job index {job_index} out of range (max: {len(report) - 1})")
        return None

    job = report[job_index]
    company = job.get('company', 'Unknown')
    title = job.get('title', 'Unknown')

    # Create a targeted resume source file
    safe_company = ''.join(c for c in company if c.isalnum() or c == ' ').replace(' ', '_')
    safe_title = ''.join(c for c in title if c.isalnum() or c == ' ')[:30].replace(' ', '_')

    resume_filename = f"resume_{safe_company}_{safe_title}.md"
    resume_path = Path(__file__).parent.parent / resume_filename

    # Copy from devops template and customize
    template_path = Path(__file__).parent.parent / "resume_devops.md"
    if not template_path.exists():
        print(f"❌ Template not found: {template_path}")
        return None

    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()

    # Update the role in frontmatter
    template = template.replace(
        'role: "DevOps Engineer"',
        f'role: "{title}"'
    )

    # Add a comment about the target job
    header_comment = f"""# Targeted Resume for: {company} - {title}
# Match Score: {job.get('match_score', 0)}%
# Missing Skills: {', '.join(job.get('missing_skills', [])[:3])}
#
# Review AI achievements in accomplishments.md and add relevant IDs below

"""
    template = header_comment + template

    with open(resume_path, 'w', encoding='utf-8') as f:
        f.write(template)

    print(f"✅ Created targeted resume: {resume_path}")
    print(f"   Company: {company}")
    print(f"   Role: {title}")
    print(f"   Match: {job.get('match_score', 0)}%")

    return resume_path


if __name__ == "__main__":
    import argparse
    import glob

    parser = argparse.ArgumentParser(description="Resume Tailor v3")
    parser.add_argument("--dry-run", action="store_true", help="Don't make API calls")
    parser.add_argument("--limit", type=int, default=5, help="Max jobs to process")
    parser.add_argument("--company", type=str, help="Filter analysis to specific company (e.g., --company Medpace)")
    parser.add_argument("--cover-letters", action="store_true", help="Generate cover letters for 70 percent or higher matches")
    parser.add_argument("--cost", action="store_true", help="Show cost analysis")
    parser.add_argument("--inject", action="store_true", help="Inject AI achievements into accomplishments.md")
    parser.add_argument("--inject-company", type=str, help="Only inject for specific company")
    parser.add_argument("--generate-resume", action="store_true", help="Generate targeted resume for best match")
    parser.add_argument("--report", type=str, help="Path to specific report file (default: latest)")
    args = parser.parse_args()

    if args.cost:
        print("=" * 60)
        print("💰 GPT-4o-mini COST ANALYSIS")
        print("=" * 60)
        print("""
        PRICING (as of March 2026):
        ┌────────────────────┬──────────────┬──────────────┐
        │ Model              │ Input/1M tok │ Output/1M tok│
        ├────────────────────┼──────────────┼──────────────┤
        │ GPT-4o-mini        │ $0.15        │ $0.60        │
        │ GPT-4o             │ $2.50        │ $10.00       │
        │ GPT-4-turbo        │ $10.00       │ $30.00       │
        └────────────────────┴──────────────┴──────────────┘

        ESTIMATED COST PER OPERATION (GPT-4o-mini):
        • Match analysis:     ~$0.003/job  (2K input + 500 output tokens)
        • Cover letter:       ~$0.002/job  (1.5K input + 400 output tokens)
        • TOTAL per job:      ~$0.005/job

        MONTHLY ESTIMATES:
        ┌─────────────┬───────────────┬────────────────────┐
        │ Jobs/month  │ Match only    │ Match + Cover Ltrs │
        ├─────────────┼───────────────┼────────────────────┤
        │ 50 jobs     │ $0.15         │ $0.25              │
        │ 100 jobs    │ $0.30         │ $0.50              │
        │ 200 jobs    │ $0.60         │ $1.00              │
        └─────────────┴───────────────┴────────────────────┘

        With your $120/month budget, you could process ~24,000 jobs!
        """)
        sys.exit(0)

    # Get report path (use latest if not specified)
    if args.report:
        report_path = args.report
    else:
        reports = sorted(glob.glob(str(REPORTS_DIR / "match_report_*.json")))
        report_path = reports[-1] if reports else None

    # Handle inject command
    if args.inject:
        print("=" * 60)
        print("📥 INJECTING AI ACHIEVEMENTS")
        print("=" * 60)
        if not report_path:
            print("❌ No reports found. Run analysis first: python resume_tailor.py --limit 10")
            sys.exit(1)
        print(f"📄 Using report: {report_path}")
        inject_achievements_to_accomplishments(report_path, args.inject_company)
        sys.exit(0)

    # Handle generate-resume command
    if args.generate_resume:
        print("=" * 60)
        print("📝 GENERATING TARGETED RESUME")
        print("=" * 60)
        if not report_path:
            print("❌ No reports found. Run analysis first: python resume_tailor.py --limit 10")
            sys.exit(1)
        print(f"📄 Using report: {report_path}")
        generate_targeted_resume(report_path)
        sys.exit(0)

    print("=" * 60)
    print("🎯 RESUME TAILOR v3 - AI-Powered Matching")
    print("=" * 60)

    if args.cover_letters:
        print("📝 Cover letter generation ENABLED (for 70%+ matches)")

    results = process_enterprise_jobs(
        dry_run=args.dry_run,
        limit=args.limit,
        gen_cover_letters=args.cover_letters,
        company_filter=args.company
    )

    if results:
        print("\n" + "=" * 60)
        print("📊 TOP MATCHES")
        print("=" * 60)
        for r in sorted(results, key=lambda x: x.get("match_score", 0), reverse=True)[:5]:
            cl = "📝" if r.get("cover_letter") else "  "
            print(f"   {cl} {r.get('match_score', 0):3}% | {r['company']}: {r['title']}")

        print("\n" + "=" * 60)
        print("📋 NEXT STEPS")
        print("=" * 60)
        print("  1. Inject achievements: python resume_tailor.py --inject")
        print("  2. Generate resume:     python resume_tailor.py --generate-resume")
        print("  3. Run generate_resumes.py to create DOCX/PDF")

