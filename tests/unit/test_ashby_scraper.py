"""Unit tests for Ashby board parsing and endpoint merging."""

import asyncio
from pathlib import Path
import sys

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import ashby_scraper

from ashby_scraper import extract_ashby_app_data, get_ashby_endpoints, parse_ashby_jobs


class FakeAshbyResponse:
    """Minimal httpx-compatible response for Ashby scraper tests."""

    def __init__(self, status_code: int, text: str, url: str) -> None:
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("HTTP error", request=self.request, response=response)


def test_fetch_ashby_board_html_retries_request_issue_before_success(monkeypatch) -> None:
    call_counter = {"count": 0}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None, timeout=None):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise httpx.ConnectError("dns failed", request=httpx.Request("GET", url))
            return FakeAshbyResponse(200, "<html></html>", url)

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(ashby_scraper.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(ashby_scraper.asyncio, "sleep", fake_sleep)

    html = asyncio.run(ashby_scraper.fetch_ashby_board_html("homevision"))

    assert call_counter["count"] == 2
    assert html == "<html></html>"


def test_extract_ashby_app_data_reads_embedded_payload() -> None:
    html = """
    <html>
      <head>
        <script>
          window.__appData = {"jobBoard": {"jobPostings": [{"id": "abc123", "title": "Platform Engineer"}]}};
        </script>
      </head>
    </html>
    """

    app_data = extract_ashby_app_data(html)

    assert app_data["jobBoard"]["jobPostings"][0]["id"] == "abc123"


def test_parse_ashby_jobs_keeps_remote_platform_roles() -> None:
    app_data = {
        "jobBoard": {
            "jobPostings": [
                {
                    "id": "fb31c9cd-89c8-4001-8f51-303e504123e3",
                    "title": "Associate Site Reliability Engineer  - US - Remote",
                    "locationName": "United States",
                    "workplaceType": "Remote",
                    "employmentType": "FullTime",
                    "departmentName": "Engineering",
                    "teamName": "US Engineering",
                    "teamNames": ["Engineering", "US Engineering"],
                    "publishedDate": "2026-04-08",
                    "isListed": True,
                },
                {
                    "id": "ignore-me",
                    "title": "Sales Development Representative",
                    "locationName": "United States",
                    "workplaceType": "Remote",
                    "isListed": True,
                },
            ]
        }
    }

    jobs = parse_ashby_jobs(
        app_data,
        "HomeVision",
        "https://jobs.ashbyhq.com/homevision",
        "HIGH",
    )

    assert jobs == [
        {
            "title": "Associate Site Reliability Engineer  - US - Remote",
            "company": "HomeVision",
            "url": "https://jobs.ashbyhq.com/homevision/fb31c9cd-89c8-4001-8f51-303e504123e3",
            "location": "United States",
            "posted_date": "2026-04-08",
            "job_id": "fb31c9cd-89c8-4001-8f51-303e504123e3",
            "source": "ashby_board",
            "ats": "Ashby",
            "priority": "HIGH",
            "scraped_at": jobs[0]["scraped_at"],
            "description": "",
            "workplace_type": "Remote",
            "employment_type": "FullTime",
            "department": "Engineering",
            "team": "US Engineering",
            "team_names": ["Engineering", "US Engineering"],
        }
    ]


def test_parse_ashby_jobs_can_allow_global_remote_override() -> None:
    app_data = {
        "jobBoard": {
            "jobPostings": [
                {
                    "id": "7f314de8-c2e0-4944-ab47-b3f9dd370ef8",
                    "title": "Platform Engineer",
                    "locationName": "Remote",
                    "workplaceType": "Remote",
                    "publishedDate": "2026-04-09",
                    "isListed": True,
                }
            ]
        }
    }

    jobs = parse_ashby_jobs(
        app_data,
        "Lean TECHniques",
        "https://jobs.ashbyhq.com/leantechniques",
        "HIGH",
        allow_global_remote=True,
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Platform Engineer"
    assert jobs[0]["location"] == "Remote"


def test_get_ashby_endpoints_merges_defaults_and_config(monkeypatch) -> None:
    monkeypatch.setattr(
        ashby_scraper,
        "load_config",
        lambda: {
            "ashby_endpoints": {
                "homevision": {"priority": "MEDIUM"},
                "custom": {
                    "name": "Custom Ashby",
                    "company_slug": "customashby",
                    "priority": "LOW",
                },
            }
        },
    )

    endpoints = get_ashby_endpoints()

    assert endpoints["homevision"]["company_slug"] == "homevision"
    assert endpoints["homevision"]["priority"] == "MEDIUM"
    assert endpoints["custom"]["company_slug"] == "customashby"
    assert "leantechniques" in endpoints