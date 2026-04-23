"""Unit tests for pipeline freshness helpers."""

from datetime import datetime
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.pipeline_freshness import apply_freshness_metadata, parse_job_datetime, sort_jobs_by_freshness


def test_parse_job_datetime_handles_workday_relative_text() -> None:
    reference_time = datetime(2026, 4, 15, 12, 0, 0)

    parsed = parse_job_datetime("Posted 2 Days Ago", reference_time=reference_time)

    assert parsed == datetime(2026, 4, 13, 12, 0, 0)


def test_apply_freshness_metadata_prefers_posted_date_for_bucket() -> None:
    now = datetime(2026, 4, 15, 15, 0, 0)
    job = {
        "title": "Security Engineer",
        "url": "https://boards.greenhouse.io/example/jobs/123",
        "source": "greenhouse_api",
        "posted_date": "2026-04-15T12:30:00",
        "scraped_at": "2026-04-15T14:59:00",
    }

    enriched = apply_freshness_metadata(job, config={}, now=now)

    assert enriched["source_family"] == "greenhouse_board"
    assert enriched["query_profile"] == "direct_board_api"
    assert enriched["freshness_bucket"] == "fresh_under_6h"
    assert enriched["freshness_basis"] == "posted_date"


def test_apply_freshness_metadata_preserves_existing_first_seen() -> None:
    now = datetime(2026, 4, 15, 15, 0, 0)
    job = {
        "title": "Cloud Security Engineer",
        "url": "https://jobs.example.com/roles/1",
        "source": "custom_scraper",
        "scraped_at": "2026-04-15T14:00:00",
    }
    existing = {"first_seen_at": "2026-04-14T08:00:00"}

    enriched = apply_freshness_metadata(job, existing, config={}, now=now)

    assert enriched["first_seen_at"] == "2026-04-14T08:00:00"
    assert enriched["freshness_bucket"] == "fresh_under_6h"
    assert enriched["freshness_basis"] == "source_detected_at"


def test_sort_jobs_by_freshness_orders_fresh_under_6h_first() -> None:
    jobs = [
        {"title": "Older", "freshness_bucket": "stale_unknown", "source_detected_at": "2026-04-14T10:00:00"},
        {"title": "Fresh", "freshness_bucket": "fresh_under_6h", "source_detected_at": "2026-04-15T13:00:00"},
        {"title": "Fresher Day", "freshness_bucket": "fresh_under_24h", "source_detected_at": "2026-04-15T08:00:00"},
    ]

    sorted_jobs = sort_jobs_by_freshness(jobs)

    assert [job["title"] for job in sorted_jobs] == ["Fresh", "Fresher Day", "Older"]