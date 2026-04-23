#!/usr/bin/env python3
"""Fast inbox-driven resume and cover-letter generation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Any, Optional

from openai import OpenAI
import yaml

from generate_tailored_resume import (
    apply_manual_tailoring_preferences,
    create_tailored_resume_source,
    extract_exact_jd_phrase_targets,
    generate_resume_outputs,
    load_accomplishments,
    load_resume_template,
    parse_accomplishments_to_dict,
    select_prompt_accomplishments,
)
from params.logging_config import setup_logging
from utils.application_batch import build_tailored_job_description
from utils.application_packages import package_application_artifacts
from utils.openai_chat import create_chat_completion
from utils.runtime_paths import outputs_dir
from utils.state import load_config

DEFAULT_JOBS_INBOX = Path("jobs_inbox")
DEFAULT_JOBS_PROCESSED = Path("jobs_processed")
DEFAULT_FAST_PACKAGE_ROOT = Path("resumes to make manually") / "Application Packs" / "Fast Inbox"
DEFAULT_FAST_MODEL = "gpt-5.4"
MAX_FAST_PROMPT_ACCOMPLISHMENTS = 18
SUPPORTED_JOB_EXTENSIONS = {".md", ".txt"}
IGNORED_JOB_FILENAMES = {"README.md", "README.txt"}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboxJob:
    """Normalized job request loaded from a file in jobs_inbox."""

    source_path: Path
    company: str
    role: str
    job_description: str
    job_url: str
    template_hint: str | None
    preferred_accomplishment_ids: tuple[str, ...]
    industry_focus: str
    cover_letter_focus: str
    tailoring_notes: tuple[str, ...]
    match_score: int


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments for the fast inbox workflow."""
    parser = argparse.ArgumentParser(
        description=(
            "Scan jobs_inbox for job description files, generate a tailored resume and "
            "cover letter for each, then move processed files to jobs_processed."
        )
    )
    parser.add_argument(
        "--jobs-dir",
        default=str(DEFAULT_JOBS_INBOX),
        help="Folder containing new .md or .txt job description files",
    )
    parser.add_argument(
        "--processed-dir",
        default=str(DEFAULT_JOBS_PROCESSED),
        help="Folder where processed job files should be moved",
    )
    parser.add_argument(
        "--model",
        help="Override the OpenAI model for this run. Defaults to settings.tailor_batch_model, settings.application_package_model, or gpt-5.4.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional maximum number of inbox files to process",
    )
    parser.add_argument(
        "--keep-inputs",
        action="store_true",
        help="Do not move processed files out of jobs_inbox",
    )
    return parser.parse_args()


def normalize_string_list(value: Any) -> tuple[str, ...]:
    """Normalize a comma-separated or list-like metadata field into a tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(',')]
        return tuple(item for item in parts if item)
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError(f"Unsupported list value type: {type(value).__name__}")


def split_front_matter(raw_text: str) -> tuple[dict[str, Any], str]:
    """Return YAML front matter and body text when a markdown file includes it."""
    normalized_text = raw_text.replace("\r\n", "\n")
    if not normalized_text.startswith("---\n"):
        return {}, raw_text

    parts = normalized_text.split("---", 2)
    if len(parts) < 3:
        return {}, raw_text

    metadata = yaml.safe_load(parts[1]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Front matter must be a YAML object.")
    return metadata, parts[2].lstrip("\n")


def extract_inline_metadata(raw_text: str) -> tuple[dict[str, str], str]:
    """Parse simple leading key-value lines from plain text files."""
    metadata: dict[str, str] = {}
    body_lines: list[str] = []
    metadata_active = True

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip()
        if metadata_active and not line.strip():
            metadata_active = False
            continue

        if metadata_active:
            match = re.match(r"^(Company|Role|Job URL|Template Hint|Industry Focus|Cover Letter Focus|Match Score):\s*(.+)$", line, re.IGNORECASE)
            if match:
                key = match.group(1).strip().casefold().replace(' ', '_')
                metadata[key] = match.group(2).strip()
                continue

        body_lines.append(raw_line)

    return metadata, "\n".join(body_lines).strip()


def infer_company_and_role(file_path: Path, metadata: dict[str, Any], body_text: str) -> tuple[str, str]:
    """Infer company and role from metadata, filename, or simple text markers."""
    company = str(metadata.get("company", "")).strip()
    role = str(metadata.get("role", metadata.get("title", ""))).strip()
    if company and role:
        return company, role

    filename_parts = [part.strip() for part in file_path.stem.split(" - ", 1)]
    if len(filename_parts) == 2:
        company = company or filename_parts[0]
        role = role or filename_parts[1]

    if company and role:
        return company, role

    company_match = re.search(r"(?im)^company:\s*(.+)$", body_text)
    role_match = re.search(r"(?im)^(role|title):\s*(.+)$", body_text)
    if company_match:
        company = company or company_match.group(1).strip()
    if role_match:
        role = role or role_match.group(2).strip()

    if company and role:
        return company, role

    raise ValueError(
        f"Could not infer company and role from {file_path.name}. Use YAML front matter or name the file 'Company - Role.md'."
    )


def parse_job_file(file_path: Path) -> InboxJob:
    """Parse a job inbox file into a normalized request object."""
    raw_text = file_path.read_text(encoding="utf-8")
    metadata, body_text = split_front_matter(raw_text)
    if not metadata:
        metadata, body_text = extract_inline_metadata(raw_text)

    company, role = infer_company_and_role(file_path, metadata, body_text)
    tailoring_notes = normalize_string_list(metadata.get("tailoring_notes") or metadata.get("notes"))
    industry_focus = str(metadata.get("industry_focus", "")).strip()

    return InboxJob(
        source_path=file_path,
        company=company,
        role=role,
        job_description=build_tailored_job_description(body_text.strip(), tailoring_notes, industry_focus),
        job_url=str(metadata.get("job_url", metadata.get("url", "manual://jobs-inbox"))).strip(),
        template_hint=(str(metadata.get("template_hint", "")).strip() or None),
        preferred_accomplishment_ids=normalize_string_list(metadata.get("preferred_accomplishment_ids")),
        industry_focus=industry_focus,
        cover_letter_focus=str(metadata.get("cover_letter_focus", "")).strip(),
        tailoring_notes=tailoring_notes,
        match_score=int(str(metadata.get("match_score", "85")).strip() or 85),
    )


def build_prompt_accomplishment_block(job: InboxJob, accomplishments: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Build a compact accomplishment subset for the one-call prompt."""
    preferred = [
        (accomplishment_id, accomplishments[accomplishment_id])
        for accomplishment_id in job.preferred_accomplishment_ids
        if accomplishment_id in accomplishments
    ]
    ranked = select_prompt_accomplishments(accomplishments, job.role, job.job_description)

    combined: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for accomplishment_id, data in preferred + ranked:
        if accomplishment_id in seen_ids:
            continue
        seen_ids.add(accomplishment_id)
        combined.append((accomplishment_id, data))
        if len(combined) >= MAX_FAST_PROMPT_ACCOMPLISHMENTS:
            break

    return combined


def build_fast_tailor_prompt(
    job: InboxJob,
    base_template: str,
    prompt_accomplishments: list[tuple[str, dict[str, Any]]],
) -> str:
    """Build the single-call prompt that returns resume tailoring plus a cover letter."""
    template_headers = "\n".join(
        line.strip() for line in base_template.splitlines() if line.strip().startswith("### ")
    )
    accomplishment_text = "\n\n".join(
        (
            f"ID: {accomplishment_id}\n"
            f"Title: {data.get('title', '')}\n"
            f"Bullet: {data.get('bullet', '')}\n"
            f"Technologies: {data.get('technologies', '')}"
        )
        for accomplishment_id, data in prompt_accomplishments
    )
    preferred_ids_text = ", ".join(job.preferred_accomplishment_ids) or "None"
    notes_text = "; ".join(job.tailoring_notes) or "None"
    template_hint = job.template_hint or "auto"
    jd_phrase_targets = extract_exact_jd_phrase_targets(job.role, job.job_description, [])
    jd_phrase_targets_block = "\n".join(f"- {phrase}" for phrase in jd_phrase_targets) or "- None identified"
    jd_phrase_instruction = ""
    if jd_phrase_targets:
        minimum_phrase_reuse = min(4, len(jd_phrase_targets))
        jd_phrase_instruction = f"""
ATS PHRASE TARGETS:
{jd_phrase_targets_block}

- Reuse at least {minimum_phrase_reuse} of these exact JD phrases across the summary, rewritten bullets, tailored skills, technical_environment, and cover letter when the source facts support them.
- Keep multi-word targets intact where possible instead of paraphrasing them away.
"""

    return f"""You are tailoring a truthful, one-page resume and a story-first cover letter for a fast job application workflow.

TARGET COMPANY: {job.company}
TARGET ROLE: {job.role}
TEMPLATE HINT: {template_hint}
PREFERRED ACCOMPLISHMENT IDS: {preferred_ids_text}
INDUSTRY FOCUS: {job.industry_focus or 'None'}
COVER LETTER FOCUS: {job.cover_letter_focus or 'None'}
ADDITIONAL NOTES: {notes_text}

JOB DESCRIPTION:
{job.job_description}

BASE RESUME HEADERS:
{template_headers}

ACCOMPLISHMENT BANK EXCERPT:
{accomplishment_text}

{jd_phrase_instruction}

Rules:
- Use the accomplishments excerpt as the source of truth for tools, metrics, and scope.
- You may infer adjacent achievements within reason, but never fabricate unsupported tools, companies, or metrics.
- If a JD asks for Terraform and the source truth supports Bicep/ARM only, write about infrastructure-as-code with Bicep/ARM or similar IaC patterns. Do not claim Terraform hands-on work.
- Reuse the JD's exact verbs, nouns, and sentence structure when truthful so the resume and cover letter sound like the target description rather than a generic paraphrase.
- Keep the Resurgent timeline and promotions explicit and truthful.
- Keep the resume one-page friendly: concise bullets, 2-sentence summary, 4 short skill categories, 5 skills max per category.
- Spread the highest-value JD keywords across at least two bullets, and across more than one role when the factual history supports that.
- Write every resume bullet in action-context-result form: lead with action or outcome, ground it in scope, and close with measurable result or business impact.
- The cover letter must be story-first, weave together 2-3 resume facts, mention the company's mission or industry once, avoid generic filler, and end exactly with:
Sincerely,
Candidate Name
- Do not add a contact block, links, or markdown formatting in the cover letter.

Return JSON only in this shape:
{{
  "company": "{job.company}",
  "role": "{job.role}",
  "summary": "Two-sentence tailored summary",
  "selected_accomplishments": ["ID-1", "ID-2"],
  "rewritten_selected_bullets": {{
    "ID-1": "Tailored resume bullet. Technologies: Tool1, Tool2, Tool3"
  }},
  "inferred_accomplishments": {{
    "Resurgent Capital Services": ["Tailored bullet. Technologies: Tool1, Tool2"],
    "Silco": ["Database bullet. Technologies: Tool1, Tool2"],
    "RIBBIT.AI": ["Database bullet. Technologies: Tool1, Tool2"]
  }},
  "tailored_skills": {{
    "Cloud Architecture": ["Skill1", "Skill2", "Skill3", "Skill4", "Skill5"],
    "CI/CD Automation": ["Skill1", "Skill2", "Skill3", "Skill4", "Skill5"],
    "Operations": ["Skill1", "Skill2", "Skill3", "Skill4", "Skill5"],
    "Scripting & Data": ["Skill1", "Skill2", "Skill3", "Skill4", "Skill5"]
  }},
  "technical_environment": "Comma-separated tools from the final resume",
  "detected_tech_stack": ["Azure", "Kubernetes"],
  "match_reasoning": "Short explanation of why these facts fit the role",
  "confidence_score": 0,
  "cover_letter": "Final cover letter text"
}}"""


def parse_model_response(content: str) -> dict[str, Any]:
    """Parse JSON from a model response that may include code fences."""
    cleaned = content.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Model response must be a JSON object.")
    return parsed


def request_application_content(
    client: OpenAI,
    model: str,
    job: InboxJob,
    base_template: str,
    prompt_accomplishments: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Run the single LLM call that returns both resume content and a cover letter."""
    prompt = build_fast_tailor_prompt(job, base_template, prompt_accomplishments)
    response = create_chat_completion(
        client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        token_limit=2600,
        temperature=0.3,
    )
    content = response.choices[0].message.content or "{}"
    return parse_model_response(content)


def safe_file_component(value: str) -> str:
    """Create a filesystem-safe component for output names."""
    return re.sub(r"[^\w\s-]", "", value).replace(" ", "_")[:30]


def build_fast_output_dir(job: InboxJob) -> Path:
    """Build a dedicated output directory for the fast inbox workflow."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_company = safe_file_component(job.company)
    safe_role = safe_file_component(job.role)
    base_dir = outputs_dir() / f"{date_str}_{safe_company}_{safe_role}_fast"
    output_dir = base_dir
    counter = 2
    while output_dir.exists():
        output_dir = outputs_dir() / f"{base_dir.name}_{counter}"
        counter += 1
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def save_cover_letter(output_dir: Path, company: str, role: str, cover_letter: str) -> Path:
    """Write the generated cover letter text into the output directory."""
    safe_company = safe_file_component(company)
    safe_role = safe_file_component(role)
    output_path = output_dir / f"cover_letter_{safe_company}_{safe_role}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cover_letter.strip() + "\n", encoding="utf-8")
    return output_path


def move_to_processed(source_path: Path, processed_dir: Path) -> Path:
    """Move a processed inbox file into a dated processed directory."""
    target_dir = processed_dir / datetime.now().strftime("%Y-%m-%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / source_path.name
    counter = 1
    while destination.exists():
        destination = target_dir / f"{source_path.stem}_{counter}{source_path.suffix}"
        counter += 1
    shutil.move(str(source_path), str(destination))
    return destination


def build_model_name(model_override: str | None) -> str:
    """Choose the model for the fast path, preferring the submission-quality default."""
    config = load_config()
    settings = config.get("settings", {})
    return (
        (model_override or "").strip()
        or str(settings.get("tailor_batch_model", "")).strip()
        or str(settings.get("application_package_model", "")).strip()
        or DEFAULT_FAST_MODEL
    )


def process_job(client: OpenAI, model: str, job: InboxJob) -> dict[str, str]:
    """Generate resume and cover-letter outputs for one inbox job file."""
    accomplishments_md = load_accomplishments()
    accomplishments = parse_accomplishments_to_dict(accomplishments_md)
    base_template = load_resume_template(job.role, job.template_hint)
    prompt_accomplishments = build_prompt_accomplishment_block(job, accomplishments)
    output_dir = build_fast_output_dir(job)

    response = request_application_content(client, model, job, base_template, prompt_accomplishments)
    response = apply_manual_tailoring_preferences(
        response,
        accomplishments,
        job.preferred_accomplishment_ids,
    )

    analysis = {
        "selected_accomplishments": response.get("selected_accomplishments", []),
        "rewritten_selected_bullets": response.get("rewritten_selected_bullets", {}),
        "inferred_accomplishments": response.get("inferred_accomplishments", {}),
        "tailored_skills": response.get("tailored_skills", {}),
        "detected_tech_stack": response.get("detected_tech_stack", []),
        "match_reasoning": response.get("match_reasoning", ""),
        "confidence_score": response.get("confidence_score", 0),
        "rewritten_summary": response.get("summary", ""),
    }

    resume_source_path = create_tailored_resume_source(
        company=job.company,
        role=job.role,
        analysis=analysis,
        base_template=base_template,
        accomplishments=accomplishments,
        job_description=job.job_description,
        output_dir=output_dir,
    )
    pdf_path, docx_path, ats_docx_path = generate_resume_outputs(resume_source_path, output_dir)
    cover_letter_text = str(response.get("cover_letter", "")).strip()
    if not cover_letter_text:
        raise RuntimeError(f"Cover letter generation returned empty text for {job.company} | {job.role}.")
    cover_letter_path = save_cover_letter(
        output_dir,
        job.company,
        job.role,
        cover_letter_text,
    )
    package = package_application_artifacts(
        package_root=DEFAULT_FAST_PACKAGE_ROOT,
        company=job.company,
        role=job.role,
        resume_pdf=pdf_path,
        resume_docx=docx_path,
        resume_ats_docx=ats_docx_path,
        cover_letter_text=cover_letter_text,
        job_url=job.job_url,
        resume_source=resume_source_path,
        include_supporting_artifacts=True,
    )

    return {
        "company": job.company,
        "role": job.role,
        "model": model,
        "output_dir": str(output_dir),
        "package_dir": str(package.package_dir),
        "preferred_resume_docx": str(package.preferred_resume_docx) if package.preferred_resume_docx else "",
        "resume_source": str(resume_source_path),
        "resume_pdf": str(package.resume_pdf) if package.resume_pdf else str(pdf_path) if pdf_path else "",
        "resume_docx": str(package.resume_docx) if package.resume_docx else str(docx_path) if docx_path else "",
        "resume_ats_docx": str(package.resume_ats_docx) if package.resume_ats_docx else str(ats_docx_path) if ats_docx_path else "",
        "cover_letter_txt": str(package.cover_letter_txt),
        "cover_letter_docx": str(package.cover_letter_docx),
        "raw_cover_letter_txt": str(cover_letter_path),
    }


def load_jobs(jobs_dir: Path, limit: int) -> list[InboxJob]:
    """Load inbox jobs from markdown or text files."""
    files = sorted(
        path
        for path in jobs_dir.iterdir()
        if path.is_file()
        and path.name not in IGNORED_JOB_FILENAMES
        and path.suffix.casefold() in SUPPORTED_JOB_EXTENSIONS
    )
    if limit > 0:
        files = files[:limit]
    return [parse_job_file(path) for path in files]


def main() -> int:
    """Run the fast inbox workflow."""
    args = parse_arguments()
    setup_logging()
    jobs_dir = Path(args.jobs_dir)
    processed_dir = Path(args.processed_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    jobs = load_jobs(jobs_dir, args.limit)
    if not jobs:
        logger.info("No .md or .txt job files found in %s", jobs_dir)
        return 0

    config = load_config()
    model = build_model_name(args.model)
    client = OpenAI(api_key=config.get("openai_key"))
    logger.info("Using model: %s", model)

    results: list[dict[str, str]] = []
    for job in jobs:
        logger.info("Processing %s | %s", job.company, job.role)
        result = process_job(client, model, job)
        if not args.keep_inputs:
            processed_path = move_to_processed(job.source_path, processed_dir)
            result["processed_file"] = str(processed_path)
        results.append(result)
        logger.info("Resume PDF: %s", result["resume_pdf"])
        logger.info("Resume DOCX: %s", result["resume_docx"])
        logger.info("Resume ATS DOCX: %s", result["resume_ats_docx"])
        logger.info("Cover Letter: %s", result["cover_letter_txt"])

    logger.info("FAST BATCH RESULTS")
    for result in results:
        logger.info(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())