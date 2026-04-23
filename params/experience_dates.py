"""Canonical experience headers and promotion timeline text for resumes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExperienceHeader:
    """Canonical company, title, and date range for an experience header."""

    company: str
    title: str
    dates: str


RESURGENT_CANONICAL_HEADER = ExperienceHeader(
    company="Resurgent Capital Services",
    title="Software Support Analyst II",
    dates="Aug 2021 - Mar 2026",
)

SILCO_CANONICAL_HEADER = ExperienceHeader(
    company="Silco",
    title="Database Analyst (Contract)",
    dates="Jan 2021 - May 2021",
)

RIBBIT_CANONICAL_HEADER = ExperienceHeader(
    company="RIBBIT.AI",
    title="Database Analyst (Contract)",
    dates="Aug 2020 - Jan 2021",
)

RESURGENT_PROMOTION_BULLET_SOURCE = (
    "Promoted twice from IT Analyst (Aug 2021 - Sep 2022) to Operations Analyst "
    "(Aug 2022 - Jun 2024) to Software Support Analyst II (Jun 2024 - Mar 2026) while "
    "expanding scope from monitoring and disaster recovery into cloud architecture, "
    "API design, and AI-enabled platform delivery"
)

RESURGENT_PROMOTION_BULLET_TEMPLATES = (
    "Promoted twice from IT Analyst (Aug 2021 - Sep 2022) to Operations Analyst (Aug 2022 - Jun 2024) to Software Support Analyst II (Jun 2024 - Mar 2026), rapidly expanding scope into {focus}.",
    "Promoted twice from IT Analyst (Aug 2021 - Sep 2022) to Operations Analyst (Aug 2022 - Jun 2024) to Software Support Analyst II (Jun 2024 - Mar 2026) as responsibilities grew into {focus}.",
    "Promoted twice from IT Analyst (Aug 2021 - Sep 2022) to Operations Analyst (Aug 2022 - Jun 2024) to Software Support Analyst II (Jun 2024 - Mar 2026), broadening scope from monitoring and recovery into {focus}.",
    "Promoted twice from IT Analyst (Aug 2021 - Sep 2022) to Operations Analyst (Aug 2022 - Jun 2024) to Software Support Analyst II (Jun 2024 - Mar 2026) while evolving from frontline support into {focus}.",
    "Promoted twice from IT Analyst (Aug 2021 - Sep 2022) to Operations Analyst (Aug 2022 - Jun 2024) to Software Support Analyst II (Jun 2024 - Mar 2026) after taking ownership of {focus} alongside core production support.",
)


def normalize_experience_header(company: str, title: str, dates: str) -> ExperienceHeader:
    """Return the canonical company, title, and dates for known resume entries."""
    company_key = company.casefold().strip()
    title_key = title.casefold().strip()

    if company_key == "resurgent capital services":
        return RESURGENT_CANONICAL_HEADER
    if company_key == "silco":
        return SILCO_CANONICAL_HEADER
    if company_key in {"ribbit.ai", "ribbit"}:
        return RIBBIT_CANONICAL_HEADER

    return ExperienceHeader(company=company.strip(), title=title.strip(), dates=dates.strip())


def build_experience_header_line(header_line: str) -> str:
    """Rewrite a markdown experience header to the canonical company, title, and dates."""
    if not header_line.startswith("### ") or "|" not in header_line:
        return header_line

    parts = [part.strip() for part in header_line[4:].split("|")]
    if len(parts) < 3:
        return header_line

    header = normalize_experience_header(parts[0], parts[1], parts[2])
    return f"### {header.company} | {header.title} | {header.dates}"