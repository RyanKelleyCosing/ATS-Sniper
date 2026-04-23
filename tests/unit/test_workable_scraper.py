"""Unit tests for the Workable scraper parser."""

from pathlib import Path
import sys


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

import workable_scraper  # noqa: E402


def test_parse_workable_jobs_keeps_relevant_role_and_filters_others() -> None:
    postings = [
        {
            "title": "DevOps Engineer",
            "shortcode": "ABC123",
            "url": "https://apply.workable.com/deel/j/ABC123/",
            "location": {
                "city": "Remote",
                "region": "",
                "country": "United States",
                "workplace": "remote",
            },
            "description": "Join the platform team to build CI/CD pipelines.",
            "department": "Engineering",
            "published_on": "2026-04-22",
        },
        {
            "title": "Recruiter",
            "shortcode": "DEF456",
            "location": {"city": "Remote", "country": "United States"},
        },
    ]

    jobs = workable_scraper.parse_workable_jobs(
        postings, company_name="Deel", subdomain="deel", priority="MEDIUM"
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "DevOps Engineer"
    assert job["company"] == "Deel"
    assert job["url"] == "https://apply.workable.com/deel/j/ABC123/"
    assert job["job_id"] == "ABC123"
    assert job["source"] == "workable_api"
    assert job["ats"] == "Workable"
    assert "Remote" in job["location"]


def test_parse_workable_jobs_synthesizes_url_when_missing() -> None:
    postings = [
        {
            "title": "Cloud Platform Engineer",
            "shortcode": "ZZZ999",
            "location": {"country": "United States", "workplace": "remote"},
        }
    ]

    jobs = workable_scraper.parse_workable_jobs(
        postings, company_name="Remote", subdomain="remotecom", priority="MEDIUM"
    )

    assert jobs
    assert jobs[0]["url"] == "https://apply.workable.com/remotecom/j/ZZZ999/"
