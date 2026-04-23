"""Unit tests for job identity helpers used in Phase 2 dedupe work."""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.job_identity import (
    build_job_identity_aliases,
    build_workday_api_url,
    deduplicate_jobs_by_identity,
    ensure_job_identity_index,
    find_existing_job_url,
    store_job_identity_record,
)


def test_build_job_identity_aliases_matches_workday_public_variants() -> None:
    benchmark_aliases = build_job_identity_aliases(
        {
            "company": "Leidos",
            "title": "Site Reliability Engineer",
            "url": "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815",
            "source_family": "workday",
        }
    )
    live_aliases = build_job_identity_aliases(
        {
            "company": "Leidos",
            "title": "Site Reliability Engineer",
            "url": "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/remote-us/site-reliability-engineer_r-00180815",
            "source_family": "workday",
        }
    )

    assert set(benchmark_aliases) & set(live_aliases)


def test_build_workday_api_url_supports_external_public_path() -> None:
    api_url = build_workday_api_url(
        "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815"
    )

    assert api_url == (
        "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/external/job/"
        "remote-us/site-reliability-engineer_r-00180815"
    )


def test_store_job_identity_record_finds_existing_workday_alias() -> None:
    state = {"jobs": {}, "job_identities": {}}
    store_job_identity_record(
        state,
        {
            "company": "Leidos",
            "title": "Site Reliability Engineer",
            "url": "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/remote-us/site-reliability-engineer_r-00180815",
            "source_family": "workday",
        },
    )
    ensure_job_identity_index(state)

    existing_url = find_existing_job_url(
        state,
        {
            "company": "Leidos",
            "title": "Site Reliability Engineer",
            "url": "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815",
            "source_family": "workday",
        },
    )

    assert existing_url == (
        "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/remote-us/"
        "site-reliability-engineer_r-00180815"
    )


def test_store_job_identity_record_preserves_earliest_seen_timestamps() -> None:
    job_url = "https://www.indeed.com/viewjob?jk=8042e40428b26fb4"
    state = {"jobs": {}, "job_identities": {}}

    store_job_identity_record(
        state,
        {
            "company": "GoDaddy",
            "title": "Site Reliability Engineer - Storage Engineer",
            "url": job_url,
            "source_family": "job_board",
            "source_detected_at": "2026-04-20T18:39:56.280028",
            "first_seen_at": "2026-04-20T18:39:56.280028",
            "match_score": 55,
        },
    )
    store_job_identity_record(
        state,
        {
            "company": "GoDaddy",
            "title": "Site Reliability Engineer - Storage Engineer",
            "url": job_url,
            "source_family": "job_board",
            "source_detected_at": "2026-04-21T10:15:24.321792",
            "first_seen_at": "2026-04-21T10:15:24.321792",
            "match_score": 65,
        },
    )

    stored_job = state["jobs"][job_url]
    assert stored_job["source_detected_at"] == "2026-04-20T18:39:56.280028"
    assert stored_job["first_seen_at"] == "2026-04-20T18:39:56.280028"
    assert stored_job["match_score"] == 65


def test_deduplicate_jobs_by_identity_prefers_direct_source_family() -> None:
    deduped_jobs = deduplicate_jobs_by_identity(
        [
            {
                "company": "Leidos",
                "title": "Site Reliability Engineer",
                "url": "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/remote-us/site-reliability-engineer_r-00180815",
                "source_family": "workday",
            },
            {
                "company": "Leidos",
                "title": "Site Reliability Engineer",
                "url": "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815",
                "source_family": "company_career_site",
                "source": "web_google",
            },
        ]
    )

    assert len(deduped_jobs) == 1
    assert deduped_jobs[0]["source_family"] == "workday"
    assert deduped_jobs[0]["alternate_urls"] == [
        "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815"
    ]