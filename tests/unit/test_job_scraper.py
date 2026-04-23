"""Unit tests for ATS-specific job detail fetchers."""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import job_scraper


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_fetch_phenom_description_uses_jobposting_structured_data(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <a href="mailto:recruiting@atsginc.com">recruiting@atsginc.com</a>
        <script type="application/ld+json">
        {
          "@context": "http://schema.org",
          "@type": "JobPosting",
          "title": "ATSG IS SECURITY ANALYST",
          "description": "&lt;p&gt;Information Security Analyst&lt;/p&gt;&lt;p&gt;Active Directory and vulnerability scanning.&lt;/p&gt;",
          "datePosted": "2026-04-15",
          "employmentType": ["FULL_TIME"],
          "jobLocation": {
            "@type": "Place",
            "address": {
              "@type": "PostalAddress",
              "addressLocality": "Wilmington",
              "addressRegion": "Ohio",
              "postalCode": "45177",
              "addressCountry": "United States"
            }
          }
        }
        </script>
      </body>
    </html>
    """
    monkeypatch.setattr(job_scraper.requests, "get", lambda *_args, **_kwargs: _FakeResponse(html))

    job_data = job_scraper.fetch_phenom_description(
        "https://careers.atsginc.com/us/en/job/ATSGI004431/ATSG-IS-SECURITY-ANALYST"
    )

    assert job_data is not None
    assert job_data["title"] == "ATSG IS SECURITY ANALYST"
    assert "Information Security Analyst" in job_data["description"]
    assert job_data["location"] == "Wilmington, Ohio, 45177, United States"
    assert job_data["posted_date"] == "2026-04-15"
    assert job_data["job_type"] == "FULL_TIME"
    assert job_data["contact_email"] == "recruiting@atsginc.com"


def test_fetch_generic_description_uses_jobposting_structured_data(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <a href="mailto:accommodations@peraton.com">accommodations@peraton.com</a>
        <script type="application/ld+json">
        {
          "@context": "http://schema.org",
          "@type": "JobPosting",
          "title": "DevOps/ Platform Engineer",
          "description": "&lt;p&gt;Design cloud infrastructure and CI/CD automation with Terraform and Kubernetes.&lt;/p&gt;",
          "datePosted": "2026-04-20",
          "employmentType": ["FULL_TIME"],
          "jobLocation": {
            "@type": "Place",
            "address": {
              "@type": "PostalAddress",
              "addressLocality": "San Diego",
              "addressRegion": "California",
              "addressCountry": "United States"
            }
          }
        }
        </script>
      </body>
    </html>
    """
    monkeypatch.setattr(job_scraper.requests, "get", lambda *_args, **_kwargs: _FakeResponse(html))

    job_data = job_scraper.fetch_generic_description(
        "https://www.careers.peraton.com/jobs/dev-ops-platform-engineer-san-diego-california-165972-jobs--engineering--"
    )

    assert job_data is not None
    assert job_data["title"] == "DevOps/ Platform Engineer"
    assert "Terraform and Kubernetes" in job_data["description"]
    assert job_data["location"] == "San Diego, California, United States"
    assert job_data["posted_date"] == "2026-04-20"
    assert job_data["job_type"] == "FULL_TIME"
    assert job_data["contact_email"] == "accommodations@peraton.com"


def test_fetch_workday_description_supports_external_public_variant(monkeypatch) -> None:
    captured_urls: list[str] = []

    class _FakeJsonResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "jobPostingInfo": {
                    "title": "Site Reliability Engineer",
                    "jobDescription": "Support Kubernetes and Terraform automation.",
                    "location": "Remote, US",
                    "postedOn": "2026-04-20",
                    "timeType": "Full time",
                }
            }

    def _fake_get(url: str, **_kwargs):
        captured_urls.append(url)
        return _FakeJsonResponse()

    monkeypatch.setattr(job_scraper.requests, "get", _fake_get)

    job_data = job_scraper.fetch_workday_description(
        "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815"
    )

    assert job_data is not None
    assert captured_urls == [
        "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/external/job/remote-us/site-reliability-engineer_r-00180815"
    ]
    assert job_data["title"] == "Site Reliability Engineer"
    assert job_data["location"] == "Remote, US"
