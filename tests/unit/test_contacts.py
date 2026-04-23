"""Unit tests for contact extraction helpers."""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.contacts import (
    extract_contact_emails_from_links,
    extract_contact_emails_from_text,
    format_contact_emails,
    primary_contact_email,
)


def test_extract_contact_emails_from_text_and_links_dedupes_results() -> None:
    text_emails = extract_contact_emails_from_text(
        "Email Hiring@Example.com or recruiting@example.com for details."
    )
    link_emails = extract_contact_emails_from_links(
        [
            "mailto:hiring@example.com",
            "mailto:recruiting@example.com?subject=DevOps",
        ]
    )

    assert text_emails == ["hiring@example.com", "recruiting@example.com"]
    assert link_emails == ["hiring@example.com", "recruiting@example.com"]


def test_format_contact_emails_and_primary_contact_email_handle_strings() -> None:
    contact_line = format_contact_emails(
        "Reach out at talent@example.com or hr@example.com"
    )

    assert contact_line == "talent@example.com; hr@example.com"
    assert primary_contact_email(["talent@example.com", "hr@example.com"]) == "talent@example.com"