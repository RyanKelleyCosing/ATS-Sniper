from pathlib import Path
import csv
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from run_fresh_watch import (
    FreshWatchSettings,
    build_fresh_watch_runtime_config,
    filter_fresh_watch_jobs,
    load_fresh_watch_settings,
    record_fresh_watch_history,
    select_email_jobs,
    select_alert_jobs,
    write_fresh_watch_csv,
)
from startup_discovery_scraper import build_jobspy_search_terms


def test_load_fresh_watch_settings_defaults_to_broader_bridge_budget() -> None:
    settings = load_fresh_watch_settings({})

    assert settings.max_queries == 4
    assert settings.max_results_per_query == 8
    assert settings.adjacent_jobspy_max_search_terms == 6


def test_build_fresh_watch_runtime_config_sets_web_only_budget() -> None:
    config = {
        "startup_discovery": {"enabled": False, "allowed_run_types": ["morning"]},
        "jobspy_discovery": {"enabled": True, "run_types": ["morning", "full"]},
    }

    runtime_config = build_fresh_watch_runtime_config(
        config,
        FreshWatchSettings(max_queries=2, max_results_per_query=5),
    )

    assert runtime_config["startup_discovery"] == {
        "enabled": True,
        "allowed_run_types": ["morning", "fresh_watch"],
        "max_queries": 2,
        "max_results_per_query": 5,
        "include_adjacent_roles": False,
        "query_profile_allowlist": [
            "ats_board_pages",
            "ats_pages_extended",
            "company_career_domains",
            "remote_us_roles",
        ],
    }
    assert runtime_config["jobspy_discovery"] == {
        "enabled": False,
        "run_types": ["morning", "full"],
    }


def test_build_fresh_watch_runtime_config_enables_capped_adjacent_jobspy() -> None:
    config = {
        "startup_discovery": {"enabled": False, "allowed_run_types": ["morning"]},
        "jobspy_discovery": {
            "enabled": False,
            "run_types": ["morning"],
            "results_wanted": 25,
            "results_per_search_term": 8,
        },
    }

    runtime_config = build_fresh_watch_runtime_config(
        config,
        FreshWatchSettings(
            max_queries=2,
            max_results_per_query=5,
            enable_adjacent_jobspy=True,
            adjacent_jobspy_max_search_terms=3,
            adjacent_jobspy_results_per_search_term=2,
        ),
    )

    assert runtime_config["jobspy_discovery"] == {
        "enabled": True,
        "run_types": ["morning", "fresh_watch"],
        "results_wanted": 6,
        "results_per_search_term": 2,
        "include_adjacent_roles": True,
        "max_search_terms": 3,
        "adjacent_max_search_terms": 2,
    }


def test_build_fresh_watch_runtime_config_preserves_primary_jobspy_terms() -> None:
    config = {
        "jobspy_discovery": {
            "enabled": True,
            "run_types": ["morning"],
            "max_search_terms": 10,
            "results_per_search_term": 2,
            "search_terms": [
                "application security engineer",
                "cloud security engineer",
                "iam engineer",
                "devops engineer",
            ],
        },
        "fresh_watch": {
            "enable_adjacent_jobspy": True,
            "adjacent_jobspy_max_search_terms": 6,
            "adjacent_jobspy_results_per_search_term": 2,
        },
        "role_groups": {
            "core_ops": ["DevOps Engineer", "Platform Engineer"],
            "security": ["Application Security Engineer", "Cloud Security Engineer", "IAM Engineer"],
            "adjacent": [
                "Automation Engineer",
                "Infrastructure Automation Engineer",
                "Cloud Automation Engineer",
                "Implementation Engineer",
            ],
        },
    }

    settings = load_fresh_watch_settings(config)
    runtime_config = build_fresh_watch_runtime_config(config, settings)
    search_terms = [term.casefold() for term in build_jobspy_search_terms(runtime_config)]

    assert runtime_config["jobspy_discovery"]["max_search_terms"] == 6
    assert search_terms == [
        "application security engineer",
        "cloud security engineer",
        "iam engineer",
        "devops engineer",
    ]
    assert "automation engineer" not in search_terms
    assert "implementation engineer" not in search_terms


def test_filter_and_alert_selection_respect_confidence_and_freshness() -> None:
    jobs = [
        {
            "title": "Strong Signal",
            "discovery_confidence": 91,
            "freshness_age_hours": 0.4,
            "freshness_bucket": "fresh_under_6h",
        },
        {
            "title": "Fallback Freshness",
            "discovery_confidence": 86,
            "freshness_bucket": "fresh_under_6h",
        },
        {
            "title": "Too Stale",
            "discovery_confidence": 95,
            "freshness_age_hours": 2.5,
            "freshness_bucket": "fresh_under_6h",
        },
        {
            "title": "Low Confidence",
            "discovery_confidence": 68,
            "freshness_age_hours": 0.2,
            "freshness_bucket": "fresh_under_6h",
        },
    ]
    settings = FreshWatchSettings(
        min_discovery_confidence=72,
        alert_min_discovery_confidence=82,
        alert_max_age_hours=1.0,
        max_alert_jobs=5,
    )

    watch_jobs = filter_fresh_watch_jobs(jobs, settings)
    alert_jobs = select_alert_jobs(watch_jobs, settings)

    assert [job["title"] for job in watch_jobs] == [
        "Strong Signal",
        "Fallback Freshness",
        "Too Stale",
    ]
    assert [job["title"] for job in alert_jobs] == [
        "Strong Signal",
        "Fallback Freshness",
    ]


def test_write_fresh_watch_csv_writes_expected_columns(tmp_path) -> None:
    csv_path = write_fresh_watch_csv(
        [
            {
                "company": "Acme",
                "title": "Cloud Security Engineer",
                "location": "Remote",
                "source": "web_google",
                "source_family": "company_career_site",
                "query_profile": "exact_fit_remote",
                "discovery_confidence": 88,
                "freshness_bucket": "fresh_under_6h",
                "freshness_age_hours": 0.75,
                "posted_date": "2026-04-21T10:10:00",
                "first_seen_at": "2026-04-21T10:12:00",
                "url": "https://example.com/jobs/1",
            }
        ],
        tmp_path / "fresh_watch_latest.csv",
    )

    with csv_path.open("r", encoding="utf-8", newline="") as file_handle:
        rows = list(csv.reader(file_handle))

    assert rows[0] == [
        "Company",
        "Title",
        "Location",
        "Source",
        "Source Family",
        "Query Profile",
        "Discovery Confidence",
        "Freshness Bucket",
        "Freshness Age Hours",
        "Posted Date",
        "First Seen At",
        "URL",
    ]
    assert rows[1][0:4] == ["Acme", "Cloud Security Engineer", "Remote", "web_google"]
    assert rows[1][6:9] == ["88", "fresh_under_6h", "0.75"]


def test_select_email_jobs_prefers_alert_jobs_and_falls_back_to_watch_jobs() -> None:
    settings = FreshWatchSettings(max_alert_jobs=2)

    email_jobs, threshold_label = select_email_jobs(
        watch_jobs=[{"title": "Watch 1"}, {"title": "Watch 2"}],
        alert_jobs=[{"title": "Alert 1"}, {"title": "Alert 2"}, {"title": "Alert 3"}],
        settings=settings,
    )
    assert threshold_label == "alert"
    assert [job["title"] for job in email_jobs] == ["Alert 1", "Alert 2"]

    email_jobs, threshold_label = select_email_jobs(
        watch_jobs=[{"title": "Watch 1"}, {"title": "Watch 2"}, {"title": "Watch 3"}],
        alert_jobs=[],
        settings=settings,
    )
    assert threshold_label == "watch"
    assert [job["title"] for job in email_jobs] == ["Watch 1", "Watch 2"]


def test_record_fresh_watch_history_trims_old_entries() -> None:
    state: dict[str, object] = {}
    settings = FreshWatchSettings(history_limit=2)

    for index in range(3):
        record_fresh_watch_history(
            state,
            status="ok",
            new_jobs=[{"title": f"Job {index}"}],
            watch_jobs=[],
            alert_jobs=[],
            issues=[],
            report_paths={},
            csv_path=Path("reports/fresh_watch_latest.csv"),
            settings=settings,
            email_sent=False,
        )

    history = state["fresh_watch_history"]
    assert isinstance(history, list)
    assert len(history) == 2
    assert history[-1]["new_jobs"] == 1