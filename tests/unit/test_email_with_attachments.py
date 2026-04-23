"""Unit tests for hot-job email copy selection."""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from email_with_attachments import build_hot_job_email_subject, build_hot_job_html


def test_build_hot_job_email_subject_uses_review_copy_for_regular_only_jobs() -> None:
    """Review-only emails should not claim resumes are ready."""

    subject = build_hot_job_email_subject([], [{"title": "Cloud Engineer", "company": "Example"}])

    assert subject.startswith("📋 1 Jobs Ready For Review - ")


def test_build_hot_job_html_uses_review_header_when_only_regular_jobs_exist() -> None:
    """The email header should reflect a CSV review workflow when no hot jobs exist."""

    html = build_hot_job_html(
        [],
        [{"title": "Cloud Engineer", "company": "Example", "url": "https://example.com/jobs/cloud-engineer"}],
        {"resumes_generated": 0, "cover_letters_generated": 0, "auto_promoted_count": 0},
    )

    assert "📋 JOBS READY FOR REVIEW" in html
    assert "CSV attached for manual review" in html
    assert "Tailored Resumes + Cover Letters Attached" not in html


def test_build_hot_job_html_recommends_ats_docx_upload_when_present() -> None:
    """Hot-job email copy should point the user to the ATS DOCX first when available."""

    html = build_hot_job_html(
        [
            {
                "title": "Cloud Security Engineer",
                "company": "MoonPay",
                "url": "https://example.com/jobs/cloud-security",
                "match_score": 85,
                "location": "United States - Hybrid / East Coast - Remote",
                "resume_ats_docx": "C:/resume_ats.docx",
                "resume_pdf": "C:/resume.pdf",
                "cover_letter_docx": "C:/cover_letter.docx",
            }
        ],
        [],
        {"resumes_generated": 1, "cover_letters_generated": 1, "auto_promoted_count": 0},
    )

    assert "Upload the ATS DOCX first for ATS forms" in html