"""Unit tests for manual application-packaging helpers."""

from pathlib import Path
import sys
from unittest.mock import patch

from docx import Document


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

from utils.application_packages import (  # noqa: E402
    build_application_pack_dir,
    package_application_artifacts,
    split_cover_letter_paragraphs,
    write_cover_letter_docx,
)


def test_build_application_pack_dir_sanitizes_invalid_characters(tmp_path: Path) -> None:
    package_dir = build_application_pack_dir(
        tmp_path,
        'Example: Company?',
        'Cloud/DevOps Engineer',
    )

    assert package_dir == tmp_path / 'Example Company - CloudDevOps Engineer'


def test_build_application_pack_dir_shortens_long_names_for_windows_limits(tmp_path: Path) -> None:
    package_root = tmp_path / ('nested-folder-' * 6)
    package_dir = build_application_pack_dir(
        package_root,
        'The Christ Hospital Health Network',
        'Identity & Access Management Analyst 2, Digital Technology Solutions, Hybrid',
    )

    assert len(str(package_dir)) + 1 + len('Candidate Name Cover Letter.docx') <= 240
    assert package_dir.name.endswith(']')


def test_split_cover_letter_paragraphs_uses_blank_lines() -> None:
    paragraphs = split_cover_letter_paragraphs(
        'Dear Hiring Manager,\n\nParagraph one.\n\nParagraph two.\n\nSincerely,\nCandidate Name\n'
    )

    assert paragraphs == [
        'Dear Hiring Manager,',
        'Paragraph one.',
        'Paragraph two.',
        'Sincerely,\nCandidate Name',
    ]


def test_write_cover_letter_docx_preserves_paragraphs(tmp_path: Path) -> None:
    output_path = write_cover_letter_docx(
        'Dear Hiring Manager,\n\nFirst body paragraph.\n\nSincerely,\nCandidate Name',
        tmp_path / 'cover_letter.docx',
    )

    document = Document(output_path)
    paragraph_text = [paragraph.text for paragraph in document.paragraphs]

    assert output_path.exists()
    assert paragraph_text == [
        'Dear Hiring Manager,',
        'First body paragraph.',
        'Sincerely,\nCandidate Name',
    ]


def test_package_application_artifacts_creates_expected_files(tmp_path: Path) -> None:
    resume_pdf = tmp_path / 'resume.pdf'
    resume_docx = tmp_path / 'resume.docx'
    resume_ats_docx = tmp_path / 'resume_ats.docx'
    analysis_report = tmp_path / 'analysis.md'
    resume_source = tmp_path / 'resume_source.md'

    resume_pdf.write_bytes(b'%PDF-1.4 test')
    resume_docx.write_bytes(b'docx-bytes')
    resume_ats_docx.write_bytes(b'ats-docx-bytes')
    analysis_report.write_text('# Analysis', encoding='utf-8')
    resume_source.write_text('---\nrole: DevOps Engineer\n---', encoding='utf-8')

    artifacts = package_application_artifacts(
        package_root=tmp_path / 'packs',
        company='Example Co',
        role='DevOps Engineer',
        resume_pdf=resume_pdf,
        resume_docx=resume_docx,
        resume_ats_docx=resume_ats_docx,
        cover_letter_text='Dear Hiring Manager,\n\nThanks for your time.',
        job_url='https://example.com/apply',
        analysis_report=analysis_report,
        resume_source=resume_source,
    )

    assert artifacts.package_dir.exists()
    assert artifacts.preferred_resume_docx == artifacts.package_dir / 'Candidate Name Resume.docx'
    assert artifacts.resume_pdf == artifacts.package_dir / 'Candidate Name Resume.pdf'
    assert artifacts.resume_docx == artifacts.package_dir / 'Candidate Name Resume.docx'
    assert artifacts.resume_ats_docx == artifacts.package_dir / 'Candidate Name Resume.docx'
    assert artifacts.cover_letter_txt.exists()
    assert artifacts.cover_letter_docx.exists()
    assert artifacts.analysis_report == artifacts.package_dir / 'Analysis.md'
    assert artifacts.resume_source == artifacts.package_dir / 'Tailored Resume Source.md'
    assert artifacts.apply_shortcut == artifacts.package_dir / 'Apply.url'
    assert artifacts.manual_review == artifacts.package_dir / 'Manual Review Required.txt'
    assert artifacts.cover_letter_txt.read_text(encoding='utf-8').startswith('Dear Hiring Manager,')
    assert 'Manual review required before submitting.' in artifacts.manual_review.read_text(encoding='utf-8')
    assert 'Default ATS upload: Candidate Name Resume.docx' in artifacts.manual_review.read_text(encoding='utf-8')
    assert not (artifacts.package_dir / '00 Upload This - Candidate Name Resume.docx').exists()
    assert not (artifacts.package_dir / 'Candidate Name Resume ATS.docx').exists()


def test_package_application_artifacts_includes_manual_review_notes(tmp_path: Path) -> None:
    resume_docx = tmp_path / 'resume.docx'
    resume_docx.write_bytes(b'docx-bytes')

    artifacts = package_application_artifacts(
        package_root=tmp_path / 'packs',
        company='Example Co',
        role='Security Engineer',
        resume_pdf=None,
        resume_docx=resume_docx,
        cover_letter_text='Dear Hiring Manager,\n\nThanks for your time.',
        include_supporting_artifacts=True,
        manual_review_notes=(
            'Override applied: exported despite a must-have keyword rejection.',
            'Missing phrases at generation time: Windows Server, Active Directory',
        ),
    )

    manual_review_text = artifacts.manual_review.read_text(encoding='utf-8')

    assert 'Review Notes:' in manual_review_text
    assert 'Override applied: exported despite a must-have keyword rejection.' in manual_review_text
    assert 'Missing phrases at generation time: Windows Server, Active Directory' in manual_review_text


def test_package_application_artifacts_tolerates_locked_existing_destination(tmp_path: Path) -> None:
    resume_pdf = tmp_path / 'resume.pdf'
    resume_docx = tmp_path / 'resume.docx'

    resume_pdf.write_bytes(b'%PDF-1.4 test')
    resume_docx.write_bytes(b'docx-bytes')

    package_root = tmp_path / 'packs'
    existing_dir = package_root / 'Example Co - DevOps Engineer'
    existing_dir.mkdir(parents=True, exist_ok=True)
    existing_docx = existing_dir / 'Candidate Name Resume.docx'
    existing_docx.write_bytes(b'locked-docx')

    real_copy2 = __import__('shutil').copy2

    def flaky_copy2(source: Path, destination: Path) -> str:
        if Path(destination) == existing_docx:
            raise PermissionError('locked file')
        return real_copy2(source, destination)

    with patch('utils.application_packages.shutil.copy2', side_effect=flaky_copy2):
        artifacts = package_application_artifacts(
            package_root=package_root,
            company='Example Co',
            role='DevOps Engineer',
            resume_pdf=resume_pdf,
            resume_docx=resume_docx,
            cover_letter_text='Dear Hiring Manager,\n\nThanks for your time.',
            job_url='https://example.com/apply',
        )

    assert artifacts.resume_docx == existing_docx
    assert existing_docx.read_bytes() == b'locked-docx'
    assert artifacts.cover_letter_txt.exists()
    assert artifacts.cover_letter_docx.exists()


def test_package_application_artifacts_can_skip_supporting_files(tmp_path: Path) -> None:
    resume_pdf = tmp_path / 'resume.pdf'
    resume_docx = tmp_path / 'resume.docx'
    resume_ats_docx = tmp_path / 'resume_ats.docx'

    resume_pdf.write_bytes(b'%PDF-1.4 test')
    resume_docx.write_bytes(b'docx-bytes')
    resume_ats_docx.write_bytes(b'ats-docx-bytes')

    artifacts = package_application_artifacts(
        package_root=tmp_path / 'packs',
        company='Lean Example',
        role='Cloud Engineer',
        resume_pdf=resume_pdf,
        resume_docx=resume_docx,
        resume_ats_docx=resume_ats_docx,
        cover_letter_text='Dear Hiring Manager,\n\nThanks for your time.',
        job_url='https://example.com/apply',
        include_supporting_artifacts=False,
    )

    assert artifacts.preferred_resume_docx == artifacts.package_dir / 'Candidate Name Resume.docx'
    assert artifacts.resume_pdf == artifacts.package_dir / 'Candidate Name Resume.pdf'
    assert artifacts.resume_docx == artifacts.package_dir / 'Candidate Name Resume.docx'
    assert artifacts.resume_ats_docx == artifacts.package_dir / 'Candidate Name Resume.docx'
    assert artifacts.cover_letter_txt.exists()
    assert artifacts.cover_letter_docx.exists()
    assert artifacts.analysis_report is None
    assert artifacts.resume_source is None
    assert artifacts.apply_shortcut is None
    assert artifacts.manual_review is None
    assert not (artifacts.package_dir / 'Analysis.md').exists()
    assert not (artifacts.package_dir / 'Apply.url').exists()
    assert not (artifacts.package_dir / 'Manual Review Required.txt').exists()
    assert not (artifacts.package_dir / '00 Upload This - Candidate Name Resume.docx').exists()


def test_package_application_artifacts_writes_manual_review_note_even_in_lean_mode(tmp_path: Path) -> None:
    resume_docx = tmp_path / 'resume.docx'
    resume_docx.write_bytes(b'docx-bytes')

    artifacts = package_application_artifacts(
        package_root=tmp_path / 'packs',
        company='Lean Example',
        role='Automation Engineer',
        resume_pdf=None,
        resume_docx=resume_docx,
        cover_letter_text='Dear Hiring Manager,\n\nThanks for your time.',
        include_supporting_artifacts=False,
        manual_review_notes=('Override applied: exported despite must-have keyword rejection.',),
    )

    assert artifacts.manual_review == artifacts.package_dir / 'Manual Review Required.txt'
    assert artifacts.manual_review.exists()
    assert 'Override applied: exported despite must-have keyword rejection.' in artifacts.manual_review.read_text(encoding='utf-8')
