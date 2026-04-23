"""Unit tests for manifest-driven application batch helpers."""

from pathlib import Path
import sys


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

from utils.application_batch import (  # noqa: E402
    build_tailored_job_description,
    is_usable_job_description,
    parse_application_batch_manifest,
)


def test_parse_application_batch_manifest_reads_markdown_front_matter(tmp_path: Path) -> None:
    manifest_path = tmp_path / 'jobs.md'
    manifest_path.write_text(
        """---
package_root: resumes to make manually/Application Packs
include_supporting_artifacts: false
default_match_score: 84
jobs:
  - company: Example Co
    role: Cloud Engineer
    job_url: https://example.com/jobs/1
    template_hint: cloud
    preferred_accomplishment_ids:
      - CLOUD-001
      - K8S-001
    industry_focus: healthcare delivery
    cover_letter_focus: emphasize healthcare platform reliability
    tailoring_notes:
      - Keep the 5-year Resurgent tenure prominent.
      - Highlight Bicep and Azure Monitor work.
    job_description: |
      Azure cloud engineering role with monitoring and IaC ownership.
---

Notes below the front matter should be ignored.
""",
        encoding='utf-8',
    )

    manifest = parse_application_batch_manifest(manifest_path)

    assert manifest.package_root == Path('resumes to make manually/Application Packs')
    assert manifest.include_supporting_artifacts is False
    assert len(manifest.jobs) == 1
    job = manifest.jobs[0]
    assert job.company == 'Example Co'
    assert job.role == 'Cloud Engineer'
    assert job.match_score == 84
    assert job.template_hint == 'cloud'
    assert job.preferred_accomplishment_ids == ('CLOUD-001', 'K8S-001')
    assert job.tailoring_notes == (
        'Keep the 5-year Resurgent tenure prominent.',
        'Highlight Bicep and Azure Monitor work.',
    )


def test_is_usable_job_description_filters_linkedin_gate_pages() -> None:
    assert is_usable_job_description('Real job description text ' * 30) is True
    assert is_usable_job_description(
        'Sign in to see who you already know at Example Co. New to LinkedIn? Join now.'
    ) is False


def test_build_tailored_job_description_appends_structured_notes() -> None:
    description = build_tailored_job_description(
        'Core job description.',
        (
            'Use CLOUD-001 and K8S-001.',
            'Keep the two promotions visible.',
        ),
        'regulated fintech environment',
    )

    assert 'Core job description.' in description
    assert 'Resume tailoring priorities:' in description
    assert 'Use CLOUD-001 and K8S-001.' in description
    assert 'Industry context: regulated fintech environment.' in description
