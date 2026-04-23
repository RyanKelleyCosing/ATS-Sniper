"""Unit tests for the SmartRecruiters scraper parser."""

from pathlib import Path
import sys


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

import smartrecruiters_scraper  # noqa: E402


def test_parse_smartrecruiters_jobs_keeps_relevant_role_and_filters_others() -> None:
    postings = [
        {
            "id": "abc123",
            "name": "Cloud Security Engineer",
            "location": {
                "city": "Austin",
                "region": "TX",
                "country": "United States",
                "remote": True,
            },
            "ref": "https://jobs.smartrecruiters.com/Visa/abc123",
            "releasedDate": "2026-04-22T10:00:00Z",
            "company": {"identifier": "Visa"},
            "jobAd": {
                "sections": {
                    "jobDescription": {"text": "Cloud security platform work."}
                }
            },
        },
        {
            "id": "def456",
            "name": "Sales Account Executive",
            "location": {"city": "Austin", "region": "TX", "country": "United States"},
            "ref": "https://jobs.smartrecruiters.com/Visa/def456",
            "company": {"identifier": "Visa"},
        },
    ]

    jobs = smartrecruiters_scraper.parse_smartrecruiters_jobs(
        postings, company_name="Visa", priority="MEDIUM"
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Cloud Security Engineer"
    assert job["company"] == "Visa"
    assert job["url"] == "https://jobs.smartrecruiters.com/Visa/abc123"
    assert job["job_id"] == "abc123"
    assert job["source"] == "smartrecruiters_api"
    assert job["ats"] == "SmartRecruiters"
    assert "Remote" in job["location"]


def test_parse_smartrecruiters_jobs_synthesizes_url_when_ref_missing() -> None:
    postings = [
        {
            "id": "xyz789",
            "name": "Site Reliability Engineer",
            "location": {"city": "Remote", "country": "United States", "remote": True},
            "company": {"identifier": "BoschGroup"},
        }
    ]

    jobs = smartrecruiters_scraper.parse_smartrecruiters_jobs(
        postings, company_name="Bosch", priority="MEDIUM"
    )

    assert jobs and jobs[0]["url"] == "https://jobs.smartrecruiters.com/BoschGroup/xyz789"
