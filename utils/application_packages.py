"""Helpers for building manual application packages and cover letter artifacts."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from docx import Document
from docx.shared import Cm, Pt


MAX_WINDOWS_PACKAGE_PATH = 240
PACKAGE_ARTIFACT_FILENAMES = (
    'Candidate Name Resume.docx',
    'Candidate Name Resume.pdf',
    'Candidate Name Cover Letter.txt',
    'Candidate Name Cover Letter.docx',
    'Analysis.md',
    'Tailored Resume Source.md',
    'Apply.url',
    'Manual Review Required.txt',
)
MAX_PACKAGE_ARTIFACT_FILENAME_LENGTH = max(len(name) for name in PACKAGE_ARTIFACT_FILENAMES)


@dataclass(frozen=True)
class ApplicationPackageArtifacts:
    """Paths for the generated application package artifacts."""

    package_dir: Path
    preferred_resume_docx: Optional[Path]
    resume_pdf: Optional[Path]
    resume_docx: Optional[Path]
    resume_ats_docx: Optional[Path]
    cover_letter_txt: Path
    cover_letter_docx: Path
    analysis_report: Optional[Path]
    resume_source: Optional[Path]
    apply_shortcut: Optional[Path]
    manual_review: Optional[Path]


def sanitize_file_component(value: str) -> str:
    """Return a Windows-safe path component."""
    cleaned = re.sub(r'[<>:"/\\|?*\r\n]+', '', value)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().rstrip('.')
    return cleaned or 'Application'


def shorten_package_name(package_root: Path, pack_name: str) -> str:
    """Shorten long package names so artifact copies stay within Windows path limits."""
    absolute_root = package_root if package_root.is_absolute() else Path.cwd() / package_root
    available_length = (
        MAX_WINDOWS_PACKAGE_PATH
        - len(str(absolute_root))
        - 2
        - MAX_PACKAGE_ARTIFACT_FILENAME_LENGTH
    )
    if available_length <= 0 or len(pack_name) <= available_length:
        return pack_name

    digest = hashlib.sha1(pack_name.encode('utf-8')).hexdigest()[:8]
    suffix = f' [{digest}]'
    trimmed_length = max(1, available_length - len(suffix))
    trimmed_name = pack_name[:trimmed_length].rstrip(' .-')
    return f'{trimmed_name}{suffix}'


def build_application_pack_dir(package_root: Path, company: str, role: str) -> Path:
    """Build the target application-pack directory path."""
    pack_name = f"{sanitize_file_component(company)} - {sanitize_file_component(role)}"
    pack_name = shorten_package_name(package_root, pack_name)
    return package_root / pack_name


def split_cover_letter_paragraphs(cover_letter_text: str) -> list[str]:
    """Split cover letter text into clean paragraphs."""
    normalized_text = cover_letter_text.replace('\r\n', '\n').strip()
    if not normalized_text:
        return []
    return [
        paragraph.strip()
        for paragraph in re.split(r'\n\s*\n', normalized_text)
        if paragraph.strip()
    ]


def write_cover_letter_docx(cover_letter_text: str, output_path: Path) -> Path:
    """Write a plain-text cover letter into a simple DOCX file."""
    document = Document()
    for section in document.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(2.54)
        section.right_margin = Cm(2.54)

    normal_style = document.styles['Normal']
    normal_style.font.name = 'Calibri'
    normal_style.font.size = Pt(11)

    for paragraph_text in split_cover_letter_paragraphs(cover_letter_text):
        document.add_paragraph(paragraph_text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    return output_path


def write_apply_shortcut(job_url: str, output_path: Path) -> Optional[Path]:
    """Write a Windows internet shortcut when a real apply URL is available."""
    if not job_url.lower().startswith(('http://', 'https://')):
        return None

    output_path.write_text(f"[InternetShortcut]\nURL={job_url}\n", encoding='utf-8')
    return output_path


def write_manual_review_checklist(
    output_path: Path,
    company: str,
    role: str,
    *,
    preferred_resume_filename: str = "Candidate Name Resume.docx",
    review_notes: Sequence[str] = (),
) -> Path:
    """Write a short manual-review checklist into the application pack."""
    normalized_review_notes = [str(note).strip() for note in review_notes if str(note).strip()]
    review_notes_block = ""
    if normalized_review_notes:
        review_notes_block = "Review Notes:\n" + "".join(
            f"- {note}\n" for note in normalized_review_notes
        ) + "\n"

    checklist_text = (
        f"Manual review required before submitting.\n\n"
        f"Company: {company}\n"
        f"Role: {role}\n\n"
        f"{review_notes_block}"
        "Checklist:\n"
        f"- Default ATS upload: {preferred_resume_filename}\n"
        "- Use the DOCX for Workday or ATS uploads and keep the PDF only for visual review or recruiter-friendly sharing.\n"
        "- Verify the resume does not overclaim direct tool or platform experience.\n"
        "- Confirm the summary and bullets emphasize the right tech stack for the role and that priority JD keywords appear inside experience bullets.\n"
        "- Open the DOCX and PDF to confirm formatting, spacing, and readable work-history parsing.\n"
        "- Review the cover letter for tone, company details, and job-specific fit.\n"
        "- Optional manual ATS scan: upload the DOCX to Jobscan, Resume Worded, or another external scanner before applying. This step is manual because those services require their own account and upload flow.\n"
        "- Confirm the apply link and final filenames before submitting.\n"
    )
    output_path.write_text(checklist_text, encoding='utf-8')
    return output_path


def copy_artifact(source_path: Optional[Path], destination_path: Path) -> Optional[Path]:
    """Copy an artifact if it exists and return the destination path."""
    if source_path is None or not source_path.exists():
        return None
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, destination_path)
    except PermissionError:
        if not destination_path.exists():
            raise
    return destination_path


def package_application_artifacts(
    package_root: Path,
    company: str,
    role: str,
    resume_pdf: Optional[Path],
    resume_docx: Optional[Path],
    cover_letter_text: str,
    resume_ats_docx: Optional[Path] = None,
    job_url: str = '',
    analysis_report: Optional[Path] = None,
    resume_source: Optional[Path] = None,
    resume_basename: str = 'Candidate Name Resume',
    cover_letter_basename: str = 'Candidate Name Cover Letter',
    include_supporting_artifacts: bool = True,
    manual_review_notes: Sequence[str] = (),
) -> ApplicationPackageArtifacts:
    """Create an application pack with resume, cover letter, and review artifacts."""
    package_dir = build_application_pack_dir(package_root, company, role)
    package_dir.mkdir(parents=True, exist_ok=True)

    preferred_resume_source = resume_ats_docx if resume_ats_docx is not None else resume_docx
    preferred_resume_docx_path = copy_artifact(
        preferred_resume_source,
        package_dir / f'{resume_basename}.docx',
    )
    resume_pdf_path = copy_artifact(resume_pdf, package_dir / f'{resume_basename}.pdf')
    resume_docx_path = preferred_resume_docx_path
    resume_ats_docx_path = preferred_resume_docx_path if resume_ats_docx is not None else None

    cover_letter_txt_path = package_dir / f'{cover_letter_basename}.txt'
    cover_letter_txt_path.write_text(cover_letter_text.strip() + '\n', encoding='utf-8')
    cover_letter_docx_path = write_cover_letter_docx(
        cover_letter_text,
        package_dir / f'{cover_letter_basename}.docx',
    )

    analysis_report_path = None
    resume_source_path = None
    apply_shortcut_path = None
    manual_review_path = None
    if include_supporting_artifacts:
        analysis_report_path = copy_artifact(analysis_report, package_dir / 'Analysis.md')
        resume_source_path = copy_artifact(resume_source, package_dir / 'Tailored Resume Source.md')
        apply_shortcut_path = write_apply_shortcut(job_url, package_dir / 'Apply.url')

    if include_supporting_artifacts or manual_review_notes:
        manual_review_path = write_manual_review_checklist(
            package_dir / 'Manual Review Required.txt',
            company,
            role,
            preferred_resume_filename=(
                preferred_resume_docx_path.name if preferred_resume_docx_path else f'{resume_basename}.docx'
            ),
            review_notes=manual_review_notes,
        )

    return ApplicationPackageArtifacts(
        package_dir=package_dir,
        preferred_resume_docx=preferred_resume_docx_path,
        resume_pdf=resume_pdf_path,
        resume_docx=resume_docx_path,
        resume_ats_docx=resume_ats_docx_path,
        cover_letter_txt=cover_letter_txt_path,
        cover_letter_docx=cover_letter_docx_path,
        analysis_report=analysis_report_path,
        resume_source=resume_source_path,
        apply_shortcut=apply_shortcut_path,
        manual_review=manual_review_path,
    )
