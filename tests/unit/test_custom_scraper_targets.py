"""Unit tests for dedicated non-Workday target parsers."""

import asyncio
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from custom_scraper import (
    fetch_phenom_jobs,
    get_non_workday_targets,
    parse_activate_search_results,
    parse_neogov_listing_html,
    parse_phenom_search_results,
    parse_peopleadmin_atom_feed,
    run_custom_scraper,
    parse_successfactors_rss_feed,
)


def test_parse_activate_search_results_keeps_relevant_uc_health_role() -> None:
    payload = {
        "Records": [
            {
                "ID": "8635901d-1dc7-42b5-a57a-714a012649e7",
                "Title": "<span>Pharmacy Informatics Analyst, First Shift, Pharmacy Administration</span>",
                "LocationName": "<span>Hybrid / UC Business Center</span>",
                "CityStateDataAbbrev": "<span>Cincinnati, OH</span>",
                "DepartmentName": "<span>Digital Technology Solutions</span>",
                "ScheduleName": "<span>Full-Time, 40 Hours/Weekly</span>",
                "TypeName": "<span>Full time</span>",
                "PostedDate": "<span>3/31/2026</span>",
                "TrackingObject": {
                    "TitleJson": "Information Security Analyst 2"
                },
            },
            {
                "ID": "ignore-me",
                "Title": "<span>Registered Nurse</span>",
                "LocationName": "<span>UC Medical Center</span>",
                "CityStateDataAbbrev": "<span>Cincinnati, OH</span>",
                "TrackingObject": {
                    "TitleJson": "Registered Nurse"
                },
            },
        ]
    }
    config = {
        "url": "https://careers.uchealth.com/search/searchjobs",
        "ats": "Activate",
    }

    jobs = parse_activate_search_results(payload, "UC Health", config)

    assert jobs == [
        {
            "title": "Information Security Analyst 2",
            "url": "https://careers.uchealth.com/search/jobdetails/information-security-analyst-2/8635901d-1dc7-42b5-a57a-714a012649e7",
            "job_id": "8635901d-1dc7-42b5-a57a-714a012649e7",
            "location": "Cincinnati, OH",
            "workplace_type": "Hybrid / UC Business Center",
            "department": "Digital Technology Solutions",
            "schedule": "Full-Time, 40 Hours/Weekly",
            "description": "Digital Technology Solutions Full-Time, 40 Hours/Weekly Full time",
            "posted_date": "3/31/2026",
            "company": "UC Health",
            "ats": "Activate",
        }
    ]


def test_parse_successfactors_rss_feed_keeps_uc_identity_role() -> None:
    rss_text = """
    <rss>
      <channel>
        <item>
          <title>Identity &amp; Access Management Analyst 2, Digital Technology Solutions, Hybrid (Cincinnati, OH, US, 45221)</title>
          <link>https://jobs.uc.edu/job/Cincinnati-Identity-&amp;-Access-Management-Analyst-2/1378834900/?feedId=null&amp;utm_source=J2WRSS</link>
          <description>Hybrid role supporting access management and security operations.</description>
        </item>
        <item>
          <title>Associate Professor of Nursing (Cincinnati, OH, US, 45221)</title>
          <link>https://jobs.uc.edu/job/Cincinnati-Associate-Professor-of-Nursing/1370000000/?feedId=null</link>
          <description>Faculty role.</description>
        </item>
      </channel>
    </rss>
    """
    config = {"ats": "SuccessFactors RSS"}

    jobs = parse_successfactors_rss_feed(rss_text, "University of Cincinnati", config)

    assert jobs == [
        {
            "title": "Identity & Access Management Analyst 2, Digital Technology Solutions, Hybrid",
            "url": "https://jobs.uc.edu/job/Cincinnati-Identity-&-Access-Management-Analyst-2/1378834900/",
            "job_id": "1378834900",
            "location": "Cincinnati, OH, US, 45221",
            "description": "Hybrid role supporting access management and security operations.",
            "company": "University of Cincinnati",
            "ats": "SuccessFactors RSS",
        }
    ]


def test_parse_peopleadmin_atom_feed_extracts_nku_entry() -> None:
        atom_text = """
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>IT Security Analyst</title>
                <updated>2026-04-03T12:00:00Z</updated>
                <summary>Security operations and identity management.</summary>
                <link href="https://jobs.nku.edu/postings/15341" />
            </entry>
        </feed>
        """

        entries = parse_peopleadmin_atom_feed(atom_text)

        assert entries == [
                {
                        "title": "IT Security Analyst",
                        "url": "https://jobs.nku.edu/postings/15341",
                        "summary": "Security operations and identity management.",
                        "updated": "2026-04-03T12:00:00Z",
                }
        ]


def test_parse_neogov_listing_html_keeps_relevant_city_role() -> None:
        html = """
        <ul>
            <li class="list-item" data-job-id="5299999">
                <h3 class="job-item-link-container">
                    <a
                        class="item-details-link"
                        data-department-name="Enterprise Technology Solutions"
                        href="/careers/cincinnati/jobs/5299999/information-security-analyst"
                    >
                        Information Security Analyst
                    </a>
                </h3>
                <div class="list-job-description">Security operations and infrastructure protection.</div>
            </li>
            <li class="list-item" data-job-id="5300000">
                <h3 class="job-item-link-container">
                    <a
                        class="item-details-link"
                        data-department-name="Public Safety"
                        href="/careers/cincinnati/jobs/5300000/police-officer"
                    >
                        Police Officer
                    </a>
                </h3>
            </li>
        </ul>
        """
        config = {
                "url": "https://www.governmentjobs.com/careers/cincinnati",
                "ats": "NEOGOV",
                "location": "Cincinnati, OH",
        }

        jobs = parse_neogov_listing_html(html, "City of Cincinnati", config)

        assert jobs == [
                {
                        "title": "Information Security Analyst",
                        "url": "https://www.governmentjobs.com/careers/cincinnati/jobs/5299999/information-security-analyst",
                        "job_id": "5299999",
                        "location": "Cincinnati, OH",
                        "department": "Enterprise Technology Solutions",
                        "description": "Information Security Analyst Security operations and infrastructure protection.",
                        "company": "City of Cincinnati",
                        "ats": "NEOGOV",
                }
        ]


def test_get_non_workday_targets_adds_cardinal_activate_target(monkeypatch) -> None:
        monkeypatch.setattr(
                "custom_scraper.load_config",
                lambda: {
                        "custom_ats": {
                                "cardinal_health": {
                                        "name": "Cardinal Health",
                                        "url": "https://jobs.cardinalhealth.com/search/jobs",
                                        "type": "activate",
                                        "priority": "HIGH",
                                }
                        }
                },
        )

        targets = get_non_workday_targets()

        cardinal = targets["cardinal_health"] if "cardinal_health" in targets else targets["Cardinal_Health"]
        assert cardinal["scraper"] == "activate_api"
        assert cardinal["search_api"] == "https://jobs.cardinalhealth.com/Search/SearchResults"
        assert cardinal["priority"] == "HIGH"


def test_parse_phenom_search_results_keeps_atsg_security_role() -> None:
        html = """
        <script>
        var phApp = phApp || {};
        phApp.ddo = {
            "eagerLoadRefineSearch": {
            "data": {
                "jobs": [
                {
                    "jobId": "ATSGI004431",
                    "title": "ATSG IS SECURITY ANALYST",
                    "location": "Wilmington, Ohio 45177,United States",
                    "postedDate": "2026-04-15T12:14:15.384+0000",
                    "category": "Others",
                    "applyUrl": "https://recruiting.ultipro.com/example",
                    "descriptionTeaser_keyword": "Information Security Analyst handling vulnerability scans, Active Directory, and NIST remediation."
                },
                {
                    "jobId": "ATSGI004373",
                    "title": "ATSG IT INTERN",
                    "location": "Wilmington, Ohio 45177,United States",
                    "postedDate": "2026-04-14T12:14:15.384+0000",
                    "category": "Others",
                    "descriptionTeaser_keyword": "Internship role for students."
                }
                ],
                "totalHits": 125,
                "size": "10"
            }
            }
        };
        phApp.experimentData = {};
        </script>
        """
        config = {
            "url": "https://careers.atsginc.com/us/en/search-results",
            "ats": "Phenom",
        }

        jobs = parse_phenom_search_results(html, "ATSG", config)

        assert jobs == [
            {
                "title": "ATSG IS SECURITY ANALYST",
                "url": "https://careers.atsginc.com/us/en/job/ATSGI004431/ATSG-IS-SECURITY-ANALYST",
                "job_id": "ATSGI004431",
                "location": "Wilmington, Ohio 45177,United States",
                "description": "Information Security Analyst handling vulnerability scans, Active Directory, and NIST remediation.",
                "posted_date": "2026-04-15T12:14:15.384+0000",
                "apply_url": "https://recruiting.ultipro.com/example",
                "category": "Others",
                "company": "ATSG",
                "ats": "Phenom",
            }
        ]


def test_get_non_workday_targets_adds_atsg_phenom_target(monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_scraper.load_config",
            lambda: {
                "custom_ats": {
                    "atsg": {
                        "name": "ATSG",
                        "url": "https://careers.atsginc.com/us/en/search-results",
                        "type": "phenom",
                        "priority": "HIGH",
                    }
                }
            },
        )

        targets = get_non_workday_targets()

        atsg = targets["atsg"] if "atsg" in targets else targets["ATSG"]
        assert atsg["scraper"] == "phenom_search"
        assert atsg["search_results_url"] == "https://careers.atsginc.com/us/en/search-results"
        assert atsg["priority"] == "HIGH"


def test_get_non_workday_targets_sets_safe_ats_for_config_only_custom_entry(monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_scraper.load_config",
            lambda: {
                "custom_ats": {
                    "kinetic_vision": {
                        "name": "Kinetic Vision",
                        "url": "https://www.kinetic-vision.com/careers",
                        "type": "custom",
                        "priority": "LOW",
                    }
                }
            },
        )

        targets = get_non_workday_targets()

        assert targets["kinetic_vision"]["ats"] == "Custom"


def test_run_custom_scraper_dry_run_skips_state_write(monkeypatch) -> None:
    async def fake_scrape_company(company, config, telemetry=None):
        return [
            {
                "title": "Cloud Security Engineer",
                "url": "https://example.com/jobs/cloud-security",
                "location": "Remote - United States",
                "description": "Remote role in the United States.",
                "company": "Example Co",
            },
            {
                "title": "Sales Intern",
                "url": "https://example.com/jobs/sales-intern",
                "location": "Remote - United States",
                "description": "Remote role in the United States.",
                "company": "Example Co",
            },
        ]

    saved_states = []
    monkeypatch.setattr(
        "custom_scraper.get_non_workday_targets",
        lambda: {"example": {"display_name": "Example Co", "ats": "Custom"}},
    )
    monkeypatch.setattr("custom_scraper.scrape_company", fake_scrape_company)
    monkeypatch.setattr("custom_scraper.load_state", lambda: {"seen_jobs": {}, "jobs": {}})
    monkeypatch.setattr("custom_scraper.save_state", lambda state: saved_states.append(state))

    jobs = asyncio.run(run_custom_scraper(dry_run=True))

    assert saved_states == []
    assert [job["title"] for job in jobs] == ["Cloud Security Engineer"]


def test_fetch_phenom_jobs_paginates_when_first_page_has_no_kept_roles(monkeypatch) -> None:
        first_page = """
        <link rel="next" href="https://careers.atsginc.com/us/en/search-results?from=2&s=1">
        <script>
        var phApp = phApp || {};
        phApp.ddo = {
            "eagerLoadRefineSearch": {
            "data": {
                "jobs": [
                {
                    "jobId": "ATSG100",
                    "title": "Ramp Agent",
                    "location": "Wilmington, Ohio 45177,United States",
                    "descriptionTeaser_keyword": "Airport ramp support"
                },
                {
                    "jobId": "ATSG101",
                    "title": "Material Handler",
                    "location": "Wilmington, Ohio 45177,United States",
                    "descriptionTeaser_keyword": "Warehouse operations"
                }
                ]
            }
            }
        };
        phApp.experimentData = {};
        </script>
        """
        second_page = """
        <script>
        var phApp = phApp || {};
        phApp.ddo = {
            "eagerLoadRefineSearch": {
            "data": {
                "jobs": [
                {
                    "jobId": "ATSGI004431",
                    "title": "ATSG IS SECURITY ANALYST",
                    "location": "Wilmington, Ohio 45177,United States",
                    "descriptionTeaser_keyword": "Information Security Analyst handling vulnerability scans and Active Directory."
                }
                ]
            }
            }
        };
        phApp.experimentData = {};
        </script>
        """

        class _FakeResponse:
            def __init__(self, text: str) -> None:
                self.text = text

            def raise_for_status(self) -> None:
                return None

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def get(self, url: str, params=None):
                offset = int((params or {}).get("from", 0))
                if offset == 0:
                    return _FakeResponse(first_page)
                if offset == 2:
                    return _FakeResponse(second_page)
                return _FakeResponse("<html></html>")

        monkeypatch.setattr("custom_scraper.httpx.AsyncClient", _FakeAsyncClient)

        jobs = asyncio.run(
            fetch_phenom_jobs(
                "ATSG",
                {
                    "url": "https://careers.atsginc.com/us/en/search-results",
                    "search_results_url": "https://careers.atsginc.com/us/en/search-results",
                    "ats": "Phenom",
                    "page_size": 2,
                },
            )
        )

        assert [job["job_id"] for job in jobs] == ["ATSGI004431"]