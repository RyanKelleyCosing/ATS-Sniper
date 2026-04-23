"""Unit tests for the web discovery scraper helpers."""

import json
from pathlib import Path
import sys
import types


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from startup_discovery_scraper import (
    build_jobs_from_result,
    build_job_from_jobspy_record,
    build_jobspy_search_term,
    build_jobspy_search_terms,
    build_web_discovery_query_specs,
    collect_jobspy_discovery_jobs,
    build_notion_queries,
    build_web_discovery_queries,
    extract_company_name,
    extract_role_lines,
    format_jobspy_location,
    get_role_terms,
    run_jobspy_queries,
)


def test_build_notion_queries_skips_senior_role_terms() -> None:
    config = {
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Senior DevOps Engineer", "Site Reliability Engineer", "Staff SRE"],
            "core_infra": ["Cloud Engineer", "Infrastructure Engineer"],
            "security": ["Cloud Security Engineer"],
        },
    }

    queries = build_notion_queries(config, max_queries=2)

    assert queries
    assert all("Senior DevOps Engineer" not in query for query in queries)
    assert all("Staff SRE" not in query for query in queries)
    assert any('"devops engineer"' in query.lower() for query in queries)
    assert any("careers at" in query.lower() for query in queries)
    assert any('"platform engineer"' in query.lower() for query in queries)


def test_build_web_discovery_queries_include_company_and_remote_profiles() -> None:
    queries = build_web_discovery_queries({}, max_queries=5)

    assert any("site:jobs.ashbyhq.com" in query.lower() for query in queries)
    assert any("site:jobs.cardinalhealth.com" in query.lower() for query in queries)
    assert all("site:careers.leidos.com" not in query.lower() for query in queries)
    assert any(query.lower().startswith("(\"remote\" or \"remote us\"") or "(\"remote\" or \"remote us\"" in query.lower() for query in queries)


def test_build_web_discovery_queries_include_board_partner_domains_when_budget_allows() -> None:
    queries = [query.casefold() for query in build_web_discovery_queries({}, max_queries=5)]

    assert any("site:www.linkedin.com/jobs/view" in query for query in queries)
    assert any("site:www.dice.com/job-detail" in query for query in queries)
    assert any("site:jobs.medixteam.com" in query for query in queries)


def test_build_web_discovery_queries_prioritize_exact_high_fit_lanes() -> None:
    queries = [query.casefold() for query in build_web_discovery_queries({}, max_queries=5)]

    assert any("application security engineer" in query for query in queries)
    assert any("infrastructure security engineer" in query for query in queries)
    assert any("iam engineer" in query for query in queries)
    assert any("platform reliability engineer" in query for query in queries)
    assert any("azure platform engineer" in query for query in queries)


def test_extract_role_lines_returns_matching_job_titles() -> None:
    page_text = """
    We're hiring
    DevOps Engineer
    Site Reliability Engineer
    Reach out to hiring@example.com
    """
    role_terms = ["DevOps Engineer", "Site Reliability Engineer", "Cloud Engineer"]

    role_lines = extract_role_lines(page_text, role_terms)

    assert role_lines == ["DevOps Engineer", "Site Reliability Engineer"]


def test_extract_role_lines_splits_serp_style_snippet_segments() -> None:
    page_text = (
        "Careers at Crossmint. Last updated April 1st 2026. Open Roles. Engineering. "
        "Head of Engineering (NYC / MIA) · IT / Client Platform Engineer (Spain)."
    )
    role_terms = ["Platform Engineer", "Cloud Engineer", "DevOps Engineer"]

    role_lines = extract_role_lines(page_text, role_terms)

    assert role_lines == ["IT / Client Platform Engineer (Spain)"]


def test_extract_company_name_removes_hiring_boilerplate() -> None:
    company = extract_company_name(
        "Acme Labs | We're hiring | Notion",
        "https://acme-labs.notion.site/we-re-hiring",
    )

    assert company == "Acme Labs"


def test_extract_company_name_strips_leading_at_prefix() -> None:
    company = extract_company_name(
        "Careers at Crossmint",
        "https://crossmint.notion.site/Careers-at-Crossmint",
    )

    assert company == "Crossmint"


def test_extract_company_name_uses_suffix_for_direct_job_result() -> None:
    company = extract_company_name(
        "Senior Application Security Engineer - Chime",
        "https://boards.greenhouse.io/chime/jobs/8373535002?gh_jid=8373535002",
        job_title_hint="Senior Application Security Engineer",
    )

    assert company == "Chime"


def test_build_jobspy_search_term_uses_explicit_override() -> None:
    config = {
        "jobspy_discovery": {
            "search_term": '"cloud security" OR iam',
        },
    }

    assert build_jobspy_search_term(config) == '"cloud security" OR iam'


def test_get_role_terms_merges_config_role_groups_with_shared_clusters() -> None:
    config = {
        "role_groups": {
            "core_ops": ["Site Reliability Engineer", "Platform Engineer"],
            "security": ["DevSecOps Engineer"],
        }
    }

    role_terms = get_role_terms(config)

    assert any(term.casefold() == "site reliability engineer" for term in role_terms)
    assert any(term.casefold() == "devsecops engineer" for term in role_terms)


def test_build_jobspy_search_terms_generates_focused_title_queries() -> None:
    config = {
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"],
            "core_infra": ["Cloud Engineer", "Infrastructure Engineer"],
            "security": [
                "Application Security Engineer",
                "Cloud Security Engineer",
                "Infrastructure Security Engineer",
                "Security Engineer",
                "IAM Engineer",
                "Platform Reliability Engineer",
            ],
            "adjacent": ["AI Engineer", "Data Engineer", "ML Platform Engineer"],
        }
    }

    search_terms = [term.casefold() for term in build_jobspy_search_terms(config)]

    assert "application security engineer" in search_terms
    assert "cloud security engineer" in search_terms
    assert "infrastructure security engineer" in search_terms
    assert "iam engineer" in search_terms
    assert "devops engineer" in search_terms
    assert "site reliability engineer" in search_terms
    assert "platform reliability engineer" in search_terms
    assert "ai engineer" not in search_terms
    assert "data engineer" not in search_terms
    assert "identity" not in search_terms
    assert "platform" not in search_terms


def test_build_web_discovery_queries_drop_data_and_ai_terms() -> None:
    config = {
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Site Reliability Engineer"],
            "adjacent": ["AI Engineer", "Data Engineer", "Data Platform Engineer"],
            "security": ["Cloud Security Engineer"],
        }
    }

    queries = [query.casefold() for query in build_web_discovery_queries(config, max_queries=4)]

    assert queries
    assert all("ai engineer" not in query for query in queries)
    assert all("data engineer" not in query for query in queries)
    assert all("data platform engineer" not in query for query in queries)
    assert any("cloud security engineer" in query for query in queries)


def test_build_web_discovery_queries_include_adjacent_profile_when_enabled() -> None:
    config = {
        "startup_discovery": {"include_adjacent_roles": True},
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Site Reliability Engineer"],
            "adjacent": [
                "Implementation Engineer",
                "Integration Engineer",
                "System Software Support Engineer",
            ],
            "security": ["Cloud Security Engineer"],
        },
    }

    queries = [query.casefold() for query in build_web_discovery_queries(config, max_queries=5)]

    assert any("implementation engineer" in query for query in queries)
    assert any("integration engineer" in query for query in queries)
    assert any("system software support engineer" in query for query in queries)


def test_build_web_discovery_query_specs_honor_profile_allowlist() -> None:
    config = {
        "startup_discovery": {
            "include_adjacent_roles": True,
            "query_profile_allowlist": [
                "ats_board_pages",
                "ats_pages_extended",
                "company_career_domains",
                "remote_us_roles",
            ],
        },
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"],
            "security": ["Cloud Security Engineer", "Application Security Engineer"],
            "adjacent": ["Implementation Engineer", "Platform Support Engineer"],
        },
    }

    query_specs = build_web_discovery_query_specs(config, max_queries=6)

    assert [spec["name"] for spec in query_specs] == [
        "ats_board_pages",
        "ats_pages_extended",
        "company_career_domains",
        "remote_us_roles",
    ]


def test_build_jobspy_search_terms_reserve_space_for_adjacent_roles_when_enabled() -> None:
    config = {
        "jobspy_discovery": {
            "include_adjacent_roles": True,
            "max_search_terms": 6,
            "adjacent_max_search_terms": 2,
        },
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"],
            "security": ["Cloud Security Engineer", "IAM Engineer"],
            "adjacent": ["Implementation Engineer", "Platform Support Engineer"],
        },
    }

    search_terms = [term.casefold() for term in build_jobspy_search_terms(config)]

    assert len(search_terms) == 6
    assert "devops engineer" in search_terms
    assert "cloud security engineer" in search_terms
    assert "implementation engineer" in search_terms
    assert "platform support engineer" in search_terms


def test_build_jobspy_search_terms_keep_explicit_terms_authoritative_over_role_group_order() -> None:
    config = {
        "jobspy_discovery": {
            "include_adjacent_roles": True,
            "max_search_terms": 10,
            "adjacent_max_search_terms": 2,
            "search_terms": [
                "application security engineer",
                "cloud security engineer",
                "iam engineer",
                "devops engineer",
            ],
        },
        "role_groups": {
            "core_ops": [
                "DevOps Engineer",
                "Senior DevOps Engineer",
                "DevOps Specialist",
                "Site Reliability Engineer",
                "Senior Site Reliability Engineer",
                "Platform Engineer",
            ],
            "security": [
                "Application Security Engineer",
                "Cloud Security Engineer",
                "IAM Engineer",
            ],
            "adjacent": ["Implementation Engineer", "Platform Support Engineer"],
        },
    }

    search_terms = [term.casefold() for term in build_jobspy_search_terms(config)]

    assert search_terms[:4] == [
        "application security engineer",
        "cloud security engineer",
        "iam engineer",
        "devops engineer",
    ]
    assert "senior devops engineer" not in search_terms
    assert "senior site reliability engineer" not in search_terms
    assert search_terms == [
        "application security engineer",
        "cloud security engineer",
        "iam engineer",
        "devops engineer",
    ]


def test_build_jobspy_search_terms_keep_long_explicit_list_within_budget() -> None:
    config = {
        "jobspy_discovery": {
            "include_adjacent_roles": True,
            "max_search_terms": 10,
            "adjacent_max_search_terms": 3,
            "search_terms": [
                "application security engineer",
                "product security engineer",
                "infrastructure security engineer",
                "cloud security engineer",
                "security operations engineer",
                "devsecops engineer",
                "iam engineer",
                "identity engineer",
                "azure iam engineer",
                "devops engineer",
                "site reliability engineer",
                "platform engineer",
            ],
        },
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"],
            "security": ["Application Security Engineer", "IAM Engineer"],
            "adjacent": ["Implementation Engineer", "Platform Support Engineer"],
        },
    }

    search_terms = [term.casefold() for term in build_jobspy_search_terms(config)]

    assert search_terms == [
        "application security engineer",
        "product security engineer",
        "infrastructure security engineer",
        "cloud security engineer",
        "security operations engineer",
        "devsecops engineer",
        "iam engineer",
        "identity engineer",
        "azure iam engineer",
        "devops engineer",
    ]
    assert "implementation engineer" not in search_terms
    assert "platform support engineer" not in search_terms


def test_format_jobspy_location_uses_nested_location_fields() -> None:
    record = {
        "location": {
            "city": "Cincinnati",
            "state": "OH",
            "country": "United States",
        },
        "is_remote": True,
    }

    assert format_jobspy_location(record) == "Remote - Cincinnati, OH, United States"


def test_build_job_from_jobspy_record_rejects_staff_role() -> None:
    record = {
        "title": "Staff Platform Engineer",
        "company": "Acme",
        "job_url": "https://example.com/jobs/staff-platform",
        "location": {"country": "United States"},
        "is_remote": True,
        "description": "Remote role in the United States.",
        "site": "indeed",
    }

    assert build_job_from_jobspy_record(record) is None


def test_build_job_from_jobspy_record_accepts_remote_us_target_role() -> None:
    record = {
        "title": "Cloud Security Engineer",
        "company": "Acme",
        "job_url": "https://example.com/jobs/cloud-security",
        "location": {"country": "United States"},
        "is_remote": True,
        "description": "Remote role in the United States.",
        "site": "indeed",
        "date_posted": "2026-04-14T08:30:00",
        "emails": ["hiring@example.com"],
    }

    job = build_job_from_jobspy_record(record)

    assert job is not None
    assert job["source"] == "jobspy_indeed"
    assert job["source_board"] == "indeed"
    assert job["location"] == "Remote - United States"
    assert job["contact_email"] == "hiring@example.com"
    assert job["posted_date"] == "2026-04-14T08:30:00"


def test_collect_jobspy_discovery_jobs_runs_for_morning_when_enabled(monkeypatch) -> None:
    config = {
        "jobspy_discovery": {
            "enabled": True,
            "run_types": ["morning"],
            "site_name": ["indeed"],
            "location": "United States",
            "hours_old": 24,
            "results_wanted": 5,
            "is_remote": True,
            "search_term": '"cloud security"',
        }
    }
    state = {"seen_jobs": {}, "jobs": {}}

    monkeypatch.setattr(
        "startup_discovery_scraper.scrape_jobspy_jobs",
        lambda **_kwargs: [
            {
                "title": "Cloud Security Engineer",
                "company": "Acme",
                "job_url": "https://example.com/jobs/cloud-security",
                "location": {"country": "United States"},
                "is_remote": True,
                "description": "Remote role in the United States.",
                "site": "indeed",
                "date_posted": "2026-04-17T08:30:00",
                "emails": ["hiring@example.com"],
            }
        ],
    )

    jobs = collect_jobspy_discovery_jobs(config, state, run_type="morning")

    assert len(jobs) == 1
    assert jobs[0]["source"] == "jobspy_indeed"
    assert jobs[0]["title"] == "Cloud Security Engineer"


def test_collect_jobspy_discovery_jobs_queries_multiple_focused_terms(monkeypatch) -> None:
    config = {
        "jobspy_discovery": {
            "enabled": True,
            "run_types": ["morning"],
            "site_name": ["indeed"],
            "location": "United States",
            "hours_old": 24,
            "results_wanted": 5,
            "is_remote": True,
            "search_terms": ["devops engineer", "cloud engineer"],
        }
    }
    state = {"seen_jobs": {}, "jobs": {}}
    calls: list[tuple[str, bool]] = []

    def fake_scrape_jobspy_jobs(**kwargs):
        calls.append((kwargs["search_term"], kwargs["is_remote"]))
        return [
            {
                "title": "Cloud Security Engineer",
                "company": "Acme",
                "job_url": "https://example.com/jobs/cloud-security",
                "location": {"country": "United States"},
                "is_remote": True,
                "description": "Remote role in the United States.",
                "site": "indeed",
                "date_posted": "2026-04-17T08:30:00",
                "emails": ["hiring@example.com"],
            }
        ]

    monkeypatch.setattr("startup_discovery_scraper.scrape_jobspy_jobs", fake_scrape_jobspy_jobs)

    jobs = collect_jobspy_discovery_jobs(config, state, run_type="morning")

    assert calls == [("devops engineer", True), ("cloud engineer", True)]
    assert len(jobs) == 1
    assert jobs[0]["source"] == "jobspy_indeed"


def test_run_jobspy_queries_uses_sidecar_interpreter_when_direct_import_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner_path = tmp_path / "jobspy_process_runner.py"
    runner_path.write_text("# sidecar runner stub\n", encoding="utf-8")

    monkeypatch.setattr("startup_discovery_scraper.scrape_jobspy_jobs", None)
    monkeypatch.setattr(
        "startup_discovery_scraper.resolve_jobspy_python_executable",
        lambda: str(tmp_path / "python.exe"),
    )
    monkeypatch.setattr(
        "startup_discovery_scraper.JOBSPY_RUNNER_SCRIPT",
        runner_path,
    )

    def fake_run(command: list[str], **_kwargs):
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "records": [
                                {
                                    "title": "Cloud Security Engineer",
                                    "company": "Acme",
                                    "job_url": "https://example.com/jobs/cloud-security",
                                }
                            ],
                            "error": "",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("startup_discovery_scraper.subprocess.run", fake_run)

    results = run_jobspy_queries(
        [
            {
                "site_name": "indeed",
                "search_term": "cloud security engineer",
                "search_kwargs": {"site_name": ["indeed"], "search_term": "cloud security engineer"},
            }
        ]
    )

    assert results == [
        {
            "records": [
                {
                    "title": "Cloud Security Engineer",
                    "company": "Acme",
                    "job_url": "https://example.com/jobs/cloud-security",
                }
            ],
            "error": "",
        }
    ]


def test_extract_role_lines_rejects_requirement_sentences() -> None:
    page_text = (
        "Execute assigned information security projects, including leading small to moderate efforts. "
        "2-4 years of experience in Information Security or a related IT field."
    )

    role_lines = extract_role_lines(page_text, ["information security", "security analyst"])

    assert role_lines == []


def test_build_jobs_from_result_prefers_direct_page_title(monkeypatch) -> None:
    monkeypatch.setattr(
        "startup_discovery_scraper.fetch_job_description",
        lambda *_args, **_kwargs: {
            "title": "Information Security Analyst II",
            "description": (
                "Execute assigned information security projects, including leading small to moderate efforts. "
                "Knowledge of network infrastructure, cloud platforms, modern OS's, SIEM tooling and incident management."
            ),
            "location": "Remote - United States",
            "posted_date": "2026-04-15T09:15:00",
            "contact_email": "",
            "contact_emails": [],
        },
    )

    jobs = build_jobs_from_result(
        {
            "link": "https://careers-thesilverlining.icims.com/jobs/3549/information-security-analyst-ii/job",
            "title": "Information Security Analyst II - Silver Lining",
            "snippet": "Remote United States information security role.",
        },
        ["information security", "security analyst"],
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Information Security Analyst II"
    assert jobs[0]["url"] == "https://careers-thesilverlining.icims.com/jobs/3549/information-security-analyst-ii/job"
    assert jobs[0]["posted_date"] == "2026-04-15T09:15:00"
    assert jobs[0]["source_family"] == "icims"
    assert jobs[0]["discovery_confidence"] > 0


def test_build_jobs_from_result_uses_result_title_when_fetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "startup_discovery_scraper.fetch_job_description",
        lambda *_args, **_kwargs: None,
    )

    jobs = build_jobs_from_result(
        {
            "link": "https://boards.greenhouse.io/chime/jobs/8373535002?gh_jid=8373535002",
            "title": "Application Security Engineer - Chime",
            "snippet": "Remote, United States application security engineering role.",
        },
        ["application security", "security engineer"],
        query_profile="ats_board_pages",
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Application Security Engineer"
    assert jobs[0]["company"] == "Chime"
    assert jobs[0]["query_profile"] == "ats_board_pages"
    assert jobs[0]["source_family"] == "greenhouse_board"
    assert jobs[0]["source_board"] == "greenhouse"
    assert set(jobs[0]["discovery_confidence_breakdown"]) == {
        "direct_ats_page",
        "title_quality",
        "location_quality",
        "freshness_hint",
        "role_affinity",
        "skill_signal",
        "exclusion_risk",
    }
