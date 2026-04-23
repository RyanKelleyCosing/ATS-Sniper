"""Helpers for manifest-driven manual application generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SUPPORTING_ARTIFACTS = False
BLOCKED_PAGE_MARKERS = (
    "sign in to see who you already know",
    "sign in to view more jobs",
    "new to linkedin? join now",
    "linkedin’s user agreement",
)


@dataclass(frozen=True)
class ApplicationBatchJob:
    """A single job entry loaded from a batch manifest."""

    company: str
    role: str
    job_url: str
    match_score: int
    template_hint: str | None
    preferred_accomplishment_ids: tuple[str, ...]
    industry_focus: str
    cover_letter_focus: str
    job_description: str
    fallback_job_description: str
    tailoring_notes: tuple[str, ...]
    package_root: Path | None
    include_supporting_artifacts: bool | None


@dataclass(frozen=True)
class ApplicationBatchManifest:
    """Defaults and jobs loaded from a batch manifest file."""

    package_root: Path
    include_supporting_artifacts: bool
    jobs: tuple[ApplicationBatchJob, ...]


def _normalize_string_list(value: Any) -> tuple[str, ...]:
    """Normalize manifest string-or-list fields into a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(',')]
        return tuple(item for item in items if item)
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError(f"Expected a string or list value, got {type(value).__name__}")


def _extract_manifest_yaml(markdown_text: str) -> str:
    """Extract YAML from markdown front matter or return the raw text."""
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != '---':
        return markdown_text

    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            return '\n'.join(lines[1:index])

    raise ValueError("Markdown manifest is missing a closing front matter delimiter.")


def parse_application_batch_manifest(manifest_path: Path) -> ApplicationBatchManifest:
    """Parse a markdown or YAML batch manifest into strongly typed job entries."""
    raw_text = manifest_path.read_text(encoding='utf-8')
    payload = yaml.safe_load(_extract_manifest_yaml(raw_text)) or {}
    if not isinstance(payload, dict):
        raise ValueError("Application batch manifest must contain a YAML object.")

    raw_jobs = payload.get('jobs', [])
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError("Application batch manifest must define a non-empty jobs list.")

    package_root = Path(str(payload.get('package_root', 'resumes to make manually/Application Packs')))
    include_supporting_artifacts = bool(
        payload.get('include_supporting_artifacts', DEFAULT_SUPPORTING_ARTIFACTS)
    )
    default_match_score = int(payload.get('default_match_score', 80))

    jobs: list[ApplicationBatchJob] = []
    for index, raw_job in enumerate(raw_jobs, start=1):
        if not isinstance(raw_job, dict):
            raise ValueError(f"Job entry {index} must be a YAML object.")

        company = str(raw_job.get('company', '')).strip()
        role = str(raw_job.get('role', '')).strip()
        if not company or not role:
            raise ValueError(f"Job entry {index} must include company and role.")

        jobs.append(
            ApplicationBatchJob(
                company=company,
                role=role,
                job_url=str(raw_job.get('job_url', 'manual://batch-manifest')).strip(),
                match_score=int(raw_job.get('match_score', default_match_score)),
                template_hint=(str(raw_job.get('template_hint', '')).strip() or None),
                preferred_accomplishment_ids=_normalize_string_list(
                    raw_job.get('preferred_accomplishment_ids')
                ),
                industry_focus=str(raw_job.get('industry_focus', '')).strip(),
                cover_letter_focus=str(raw_job.get('cover_letter_focus', '')).strip(),
                job_description=str(raw_job.get('job_description', '')).strip(),
                fallback_job_description=str(raw_job.get('fallback_job_description', '')).strip(),
                tailoring_notes=_normalize_string_list(raw_job.get('tailoring_notes')),
                package_root=(
                    Path(str(raw_job['package_root']).strip())
                    if str(raw_job.get('package_root', '')).strip()
                    else None
                ),
                include_supporting_artifacts=(
                    bool(raw_job['include_supporting_artifacts'])
                    if 'include_supporting_artifacts' in raw_job
                    else None
                ),
            )
        )

    return ApplicationBatchManifest(
        package_root=package_root,
        include_supporting_artifacts=include_supporting_artifacts,
        jobs=tuple(jobs),
    )


def is_usable_job_description(job_description: str) -> bool:
    """Return True when fetched job text looks like a real posting, not a gate page."""
    normalized = job_description.strip()
    if len(normalized) < 250:
        return False

    lowered = normalized.casefold()
    return not any(marker in lowered for marker in BLOCKED_PAGE_MARKERS)


def build_tailored_job_description(
    base_description: str,
    tailoring_notes: tuple[str, ...],
    industry_focus: str,
) -> str:
    """Append structured resume-tailoring notes to the base job description."""
    sections = [base_description.strip()]
    guidance_lines = [note for note in tailoring_notes if note]
    if industry_focus:
        guidance_lines.append(f"Industry context: {industry_focus}.")

    if guidance_lines:
        sections.append("Resume tailoring priorities:\n- " + "\n- ".join(guidance_lines))

    return '\n\n'.join(section for section in sections if section)