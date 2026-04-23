"""Unit tests for the iCIMS HTML parsing fallback."""

import asyncio
from pathlib import Path
import sys

import httpx


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

import icims_scraper

from icims_scraper import fetch_icims_json, get_icims_endpoints, parse_icims_html_jobs


class FakeIcimsResponse:
  """Minimal httpx-compatible response for iCIMS scraper tests."""

  def __init__(self, status_code: int, *, text: str, content_type: str, payload, url: str) -> None:
    self.status_code = status_code
    self.text = text
    self._payload = payload
    self.headers = {"content-type": content_type}
    self.request = httpx.Request("GET", url)

  def json(self):
    return self._payload


def test_fetch_icims_json_retries_request_issue_before_success(monkeypatch) -> None:
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
      return FakeIcimsResponse(
        200,
        text='{"jobs": []}',
        content_type="application/json",
        payload={"jobs": []},
        url=url,
      )

  async def fake_sleep(_seconds: float) -> None:
    return None

  monkeypatch.setattr(icims_scraper.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
  monkeypatch.setattr(icims_scraper.asyncio, "sleep", fake_sleep)

  data, html = asyncio.run(
    fetch_icims_json(
      {
        "name": "Medical Solutions",
        "json_url": "https://careers-medicalsolutions.icims.com/jobs/search?mode=json",
      }
    )
  )

  assert call_counter["count"] == 2
  assert data == {"jobs": []}
  assert html is None


def test_parse_icims_html_jobs_reads_server_rendered_results() -> None:
    html = """
    <html>
      <body>
        <div class="row">
          <div class="col-xs-6 header left">
            <span class="sr-only field-label">Job Locations</span>
            <span>US-OH-CINCINNATI</span>
          </div>
          <div class="col-xs-12 title">
            <a href="https://careers-westernsouthern.icims.com/jobs/24765/platform-engineer/job" class="iCIMS_Anchor">
              <h3>Platform Engineer</h3>
            </a>
          </div>
          <div class="col-xs-12 description">
            Builds and supports enterprise applications in information technology.
          </div>
        </div>
        <div class="row">
          <div class="col-xs-6 header left">
            <span class="sr-only field-label">Job Locations</span>
            <span>US-OH-CINCINNATI</span>
          </div>
          <div class="col-xs-12 title">
            <a href="https://careers-westernsouthern.icims.com/jobs/24710/customer-service-advocate/job" class="iCIMS_Anchor">
              <h3>Customer Service Advocate</h3>
            </a>
          </div>
          <div class="col-xs-12 description">
            Supports customers.
          </div>
        </div>
      </body>
    </html>
    """

    jobs = parse_icims_html_jobs(
        html,
        {
            "name": "Western & Southern Financial Group",
            "base_url": "https://careers-westernsouthern.icims.com",
            "priority": "HIGH",
        },
        ["software", "engineer", "cloud"],
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Platform Engineer"
    assert jobs[0]["job_id"] == "24765"
    assert jobs[0]["location"] == "US-OH-CINCINNATI"
    assert jobs[0]["source"] == "icims_html"


def test_parse_icims_html_jobs_reads_location_from_right_header_and_strips_iframe_param() -> None:
    html = """
    <html>
      <body>
        <div class="row">
          <div class="col-xs-6 header left"></div>
          <div class="col-xs-6 header right">
            <span class="sr-only field-label">Location</span>
            <span>US-Remote</span>
          </div>
          <div class="col-xs-12 title">
            <a href="https://careers-medicalsolutions.icims.com/jobs/4324/devops-engineer-ii/job?in_iframe=1" class="iCIMS_Anchor">
              <h3>DevOps Engineer II</h3>
            </a>
          </div>
          <div class="col-xs-12 description">
            DevOps Engineer II - build and maintain infrastructure automation.
          </div>
        </div>
      </body>
    </html>
    """

    jobs = parse_icims_html_jobs(
        html,
        {
            "name": "Medical Solutions",
            "base_url": "https://careers-medicalsolutions.icims.com",
            "priority": "HIGH",
        },
        ["software", "engineer", "cloud", "devops"],
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "DevOps Engineer II"
    assert jobs[0]["location"] == "US-Remote"
    assert jobs[0]["url"] == "https://careers-medicalsolutions.icims.com/jobs/4324/devops-engineer-ii/job"


def test_get_icims_endpoints_merges_defaults_and_config(monkeypatch) -> None:
    monkeypatch.setattr(
        icims_scraper,
        "load_config",
        lambda: {
            "icims_endpoints": {
                "medical_solutions": {"priority": "MEDIUM"},
                "custom": {
                    "name": "Custom iCIMS",
                    "base_url": "https://custom.icims.com",
                    "json_url": "https://custom.icims.com/jobs/search?mode=json",
                    "priority": "LOW",
                },
            }
        },
    )

    endpoints = get_icims_endpoints()

    assert endpoints["medical_solutions"]["search_url"].endswith("in_iframe=1")
    assert endpoints["medical_solutions"]["priority"] == "MEDIUM"
    assert endpoints["custom"]["base_url"] == "https://custom.icims.com"