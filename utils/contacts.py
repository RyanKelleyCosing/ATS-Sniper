"""Contact extraction helpers for ATS Sniper."""

from __future__ import annotations

import re
from typing import Iterable

EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])",
    re.IGNORECASE,
)


def normalize_contact_email(email: str) -> str:
    """Normalize a contact email for consistent storage and matching."""
    return email.strip().strip(".,;:()<>[]{}").lower()


def merge_contact_emails(*email_groups: Iterable[str]) -> list[str]:
    """Merge email groups into a unique, normalized list."""
    merged: list[str] = []
    seen: set[str] = set()
    for email_group in email_groups:
        for raw_email in email_group:
            email = normalize_contact_email(str(raw_email))
            if not email or "@" not in email or email in seen:
                continue
            seen.add(email)
            merged.append(email)
    return merged


def extract_contact_emails_from_text(text: str) -> list[str]:
    """Extract visible email addresses from free text."""
    return merge_contact_emails(EMAIL_PATTERN.findall(text or ""))


def extract_contact_emails_from_links(links: Iterable[str]) -> list[str]:
    """Extract email addresses from mailto links."""
    emails: list[str] = []
    for link in links:
        if not link or not str(link).lower().startswith("mailto:"):
            continue
        emails.append(str(link).split(":", 1)[1].split("?", 1)[0])
    return merge_contact_emails(emails)


def primary_contact_email(contact_emails: Iterable[str]) -> str:
    """Return the first normalized email in a collection."""
    emails = merge_contact_emails(contact_emails)
    return emails[0] if emails else ""


def format_contact_emails(contact_emails: Iterable[str] | str, separator: str = "; ") -> str:
    """Format contact emails for CSV rows and email bodies."""
    if isinstance(contact_emails, str):
        emails = extract_contact_emails_from_text(contact_emails)
        if not emails and contact_emails.strip():
            emails = merge_contact_emails([contact_emails])
        return separator.join(emails)
    return separator.join(merge_contact_emails(contact_emails))