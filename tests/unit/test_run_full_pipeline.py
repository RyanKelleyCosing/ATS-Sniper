"""Unit tests for run-type-specific async scraper selection and notifications."""

import asyncio
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import ashby_scraper
import greenhouse_scraper
import lever_scraper
import run_full_pipeline

from ashby_scraper import get_ashby_endpoints, run_ashby_scrape
from greenhouse_scraper import get_greenhouse_endpoints, run_greenhouse_scrape
from lever_scraper import get_lever_endpoints, run_lever_scrape
from run_full_pipeline import _run_all_async_scrapers, filter_platform_endpoints_for_run, normalize_site_selector


def test_normalize_site_selector_strips_protocol_and_trailing_slash() -> None:
    assert normalize_site_selector("https://boards.greenhouse.io/array/") == (
        "boards.greenhouse.io/array"
    )


def test_filter_platform_endpoints_for_morning_keeps_all_greenhouse_boards_when_startup_ats_enabled() -> None:
    config = {
        "schedules": {
            "morning": {
                "query_groups": ["enterprise_workday", "cincy_regional", "startup_ats"],
            }
        },
        "query_groups": {
            "enterprise_workday": {
                "sites": ["pg.wd5.myworkdayjobs.com"],
            },
            "cincy_regional": {
                "sites": [
                    "https://boards.greenhouse.io/8451/",
                    "boards.greenhouse.io/array",
                    "boards.greenhouse.io/patientpoint",
                ],
            },
            "startup_ats": {
                "sites": ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"],
            },
        },
    }
    endpoints = {
        "8451": {"board_token": "8451"},
        "array": {"board_token": "array"},
        "patientpoint": {"board_token": "patientpoint"},
        "affirm": {"board_token": "affirm"},
    }

    filtered = filter_platform_endpoints_for_run(
        config,
        "morning",
        "boards.greenhouse.io",
        "board_token",
        endpoints,
    )

    assert filtered == endpoints


def test_filter_platform_endpoints_for_morning_keeps_all_lever_boards_when_startup_ats_enabled() -> None:
    config = {
        "schedules": {
            "morning": {
                "query_groups": ["enterprise_workday", "cincy_regional", "startup_ats"],
            }
        },
        "query_groups": {
            "enterprise_workday": {
                "sites": ["pg.wd5.myworkdayjobs.com"],
            },
            "cincy_regional": {
                "sites": ["boards.greenhouse.io/8451", "jobs.nku.edu"],
            },
            "startup_ats": {
                "sites": ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"],
            },
        },
    }
    endpoints = {
        "spotify": {"company_slug": "spotify"},
        "restaurant365": {"company_slug": "restaurant365"},
    }

    filtered = filter_platform_endpoints_for_run(
        config,
        "morning",
        "jobs.lever.co",
        "company_slug",
        endpoints,
    )

    assert filtered == endpoints


def test_filter_platform_endpoints_for_afternoon_keeps_all_startup_greenhouse_boards() -> None:
    config = {
        "schedules": {
            "afternoon": {
                "query_groups": ["startup_ats"],
            }
        },
        "query_groups": {
            "startup_ats": {
                "sites": ["boards.greenhouse.io", "jobs.lever.co"],
            }
        },
    }
    endpoints = {
        "gitlab": {"board_token": "gitlab"},
        "affirm": {"board_token": "affirm"},
        "stripe": {"board_token": "stripe"},
    }

    filtered = filter_platform_endpoints_for_run(
        config,
        "afternoon",
        "boards.greenhouse.io",
        "board_token",
        endpoints,
    )

    assert filtered == endpoints


def test_should_run_web_discovery_uses_allowed_run_types(monkeypatch) -> None:
    monkeypatch.setattr(
        run_full_pipeline,
        "get_web_discovery_settings",
        lambda: {"allowed_run_types": ["morning", "lightweight"]},
    )

    assert run_full_pipeline.should_run_web_discovery("morning") is True
    assert run_full_pipeline.should_run_web_discovery("afternoon") is False


def test_run_pipeline_executes_web_discovery_for_morning_when_allowed(monkeypatch) -> None:
    """Morning runs should execute web discovery when discovery allows it."""

    web_discovery_calls: list[dict[str, object]] = []

    async def fake_run_all_async_scrapers(*_args, **_kwargs):
        return {
            "custom": [],
            "ashby": [],
            "icims": [],
            "oracle": [],
            "greenhouse": [],
            "lever": [],
            "_new_custom_urls": set(),
            "_stage_attempts": {},
            "_stage_issues": {},
        }

    monkeypatch.setattr(run_full_pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(run_full_pipeline, "V3_ENABLED", True)
    monkeypatch.setattr(
        run_full_pipeline,
        "load_config",
        lambda: {"settings": {}, "query_groups": {}, "schedules": {}, "startup_discovery": {"enabled": True}},
    )
    monkeypatch.setattr(run_full_pipeline, "get_web_discovery_settings", lambda: {"allowed_run_types": ["morning"]})
    monkeypatch.setattr(run_full_pipeline, "should_track_pipeline_run", lambda _run_type, _dry_run: False)
    monkeypatch.setattr(run_full_pipeline, "run_workday_scrape", lambda **kwargs: [])
    monkeypatch.setattr(run_full_pipeline, "_run_all_async_scrapers", fake_run_all_async_scrapers)
    monkeypatch.setattr(run_full_pipeline, "run_usajobs_scraper", lambda **kwargs: [])
    monkeypatch.setattr(
        run_full_pipeline,
        "run_web_discovery_scrape",
        lambda dry_run=False, run_type="full", telemetry=None: web_discovery_calls.append(
            {"dry_run": dry_run, "run_type": run_type}
        )
        or [],
    )
    monkeypatch.setattr(run_full_pipeline, "annotate_jobs_with_freshness", lambda jobs, config, persist_state: {})
    monkeypatch.setattr(
        run_full_pipeline,
        "apply_feedback_signals",
        lambda jobs: {"boosted": 0, "neutral": len(jobs), "penalized": 0},
    )
    monkeypatch.setattr(run_full_pipeline, "sort_jobs_for_reporting", lambda jobs: jobs)
    monkeypatch.setattr(run_full_pipeline, "load_state", lambda: {"jobs": {}})

    jobs = run_full_pipeline.run_pipeline(dry_run=True, skip_tailor=True, v3_mode=True, run_type="morning")

    assert jobs == []
    assert web_discovery_calls == [{"dry_run": True, "run_type": "morning"}]


def test_run_pipeline_runs_hot_job_processor_during_dry_run(monkeypatch) -> None:
    """Dry-runs should still execute scoring so reviewable counts stay meaningful."""

    sample_job = {
        "title": "Cloud Security Engineer",
        "company": "ExampleCo",
        "url": "https://example.com/jobs/cloud-security",
        "source": "workday_api",
    }
    hot_job_calls: list[dict[str, object]] = []

    async def fake_run_all_async_scrapers(*_args, **_kwargs):
        return {
            "custom": [],
            "ashby": [],
            "icims": [],
            "oracle": [],
            "greenhouse": [],
            "lever": [],
            "_new_custom_urls": set(),
            "_stage_attempts": {},
            "_stage_issues": {},
        }

    monkeypatch.setattr(run_full_pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(run_full_pipeline, "V3_ENABLED", True)
    monkeypatch.setattr(run_full_pipeline, "load_config", lambda: {"settings": {}, "query_groups": {}, "schedules": {}})
    monkeypatch.setattr(run_full_pipeline, "should_run_web_discovery", lambda _run_type: False)
    monkeypatch.setattr(run_full_pipeline, "should_track_pipeline_run", lambda _run_type, _dry_run: False)
    monkeypatch.setattr(run_full_pipeline, "run_workday_scrape", lambda **kwargs: [sample_job])
    monkeypatch.setattr(run_full_pipeline, "_run_all_async_scrapers", fake_run_all_async_scrapers)
    monkeypatch.setattr(run_full_pipeline, "run_usajobs_scraper", lambda **kwargs: [])
    monkeypatch.setattr(run_full_pipeline, "annotate_jobs_with_freshness", lambda jobs, config, persist_state: {})
    monkeypatch.setattr(
        run_full_pipeline,
        "apply_feedback_signals",
        lambda jobs: {"boosted": 0, "neutral": len(jobs), "penalized": 0},
    )
    monkeypatch.setattr(run_full_pipeline, "sort_jobs_for_reporting", lambda jobs: jobs)
    monkeypatch.setattr(run_full_pipeline, "load_state", lambda: {"jobs": {sample_job["url"]: sample_job}})
    monkeypatch.setattr(run_full_pipeline, "record_source_health_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(
        run_full_pipeline,
        "write_discovery_audit_report",
        lambda **kwargs: {"json_path": "audit.json", "markdown_path": "audit.md"},
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "run_hot_job_pipeline",
        lambda jobs, dry_run=False: hot_job_calls.append({"count": len(jobs), "dry_run": dry_run})
        or {
            "hot_jobs": [],
            "regular_jobs": [sample_job],
            "screened_out_jobs": [],
            "stats": {
                "hot_count": 0,
                "regular_count": 1,
                "screened_out_noise": 0,
                "total_processed": len(jobs),
                "resumes_generated": 0,
                "cover_letters_generated": 0,
                "early_classifier_calls": 1,
                "full_scoring_calls": 1,
                "llm_usage": {},
            },
        },
    )

    jobs = run_full_pipeline.run_pipeline(dry_run=True, skip_tailor=False, v3_mode=True, run_type="morning")

    assert jobs == [sample_job]
    assert hot_job_calls == [{"count": 1, "dry_run": True}]


def test_filter_platform_endpoints_for_full_run_does_not_filter() -> None:
    endpoints = {
        "spotify": {"company_slug": "spotify"},
        "restaurant365": {"company_slug": "restaurant365"},
    }

    filtered = filter_platform_endpoints_for_run(
        {},
        "full",
        "jobs.lever.co",
        "company_slug",
        endpoints,
    )

    assert filtered == endpoints


def test_filter_platform_endpoints_for_lightweight_run_uses_schedule_selectors() -> None:
    config = {
        "schedules": {
            "lightweight": {
                "query_groups": ["startup_ats"],
            }
        },
        "query_groups": {
            "startup_ats": {
                "sites": ["boards.greenhouse.io", "jobs.lever.co"],
            }
        },
    }
    endpoints = {
        "gitlab": {"board_token": "gitlab"},
        "affirm": {"board_token": "affirm"},
    }

    filtered = filter_platform_endpoints_for_run(
        config,
        "lightweight",
        "boards.greenhouse.io",
        "board_token",
        endpoints,
    )

    assert filtered == endpoints


def test_run_greenhouse_scrape_respects_explicit_empty_endpoint_set(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        greenhouse_scraper,
        "load_config",
        lambda: {"greenhouse_endpoints": {"array": {"name": "Array", "board_token": "array"}}},
    )
    monkeypatch.setattr(greenhouse_scraper, "load_state", lambda: {"seen_jobs": {}})

    asyncio.run(run_greenhouse_scrape(dry_run=True, endpoints={}))
    captured = capsys.readouterr().out

    assert "Scanning 0 Greenhouse boards" in captured
    assert "Would scrape" not in captured


def test_run_ashby_scrape_respects_explicit_empty_endpoint_set(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        ashby_scraper,
        "load_config",
        lambda: {"ashby_endpoints": {"homevision": {"name": "HomeVision", "company_slug": "homevision"}}},
    )
    monkeypatch.setattr(ashby_scraper, "load_state", lambda: {"seen_jobs": {}})

    asyncio.run(run_ashby_scrape(dry_run=True, endpoints={}))
    captured = capsys.readouterr().out

    assert "Scanning 0 Ashby boards" in captured
    assert "Would scrape" not in captured


def test_run_lever_scrape_respects_explicit_empty_endpoint_set(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        lever_scraper,
        "load_config",
        lambda: {"lever_endpoints": {"spotify": {"name": "Spotify", "company_slug": "spotify"}}},
    )
    monkeypatch.setattr(lever_scraper, "load_state", lambda: {"seen_jobs": {}})

    asyncio.run(run_lever_scrape(dry_run=True, endpoints={}))
    captured = capsys.readouterr().out

    assert "Scanning 0 Lever boards" in captured
    assert "Would scrape" not in captured


def test_run_greenhouse_scrape_dry_run_fetches_without_saving_state(monkeypatch) -> None:
    async def fake_scrape_greenhouse_endpoint(endpoint_key, endpoint_config, telemetry=None):
        return [{
            "job_id": "123",
            "url": "https://boards.greenhouse.io/example/jobs/123",
            "title": "Cloud Security Engineer",
            "company": "Example",
            "scraped_at": "2026-04-15T15:00:00",
        }]

    monkeypatch.setattr(greenhouse_scraper, "load_config", lambda: {})
    monkeypatch.setattr(greenhouse_scraper, "load_state", lambda: {"seen_jobs": {}})
    monkeypatch.setattr(greenhouse_scraper, "save_state", lambda _state: (_ for _ in ()).throw(AssertionError("dry run should not save state")))
    monkeypatch.setattr(greenhouse_scraper, "scrape_greenhouse_endpoint", fake_scrape_greenhouse_endpoint)

    jobs = asyncio.run(
        run_greenhouse_scrape(
            dry_run=True,
            endpoints={"example": {"name": "Example", "board_token": "example"}},
        )
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Cloud Security Engineer"


def test_run_lever_scrape_dry_run_fetches_without_saving_state(monkeypatch) -> None:
    async def fake_scrape_lever_endpoint(endpoint_key, endpoint_config, telemetry=None):
        return [{
            "job_id": "123",
            "url": "https://jobs.lever.co/example/123",
            "title": "Cloud Security Engineer",
            "company": "Example",
            "scraped_at": "2026-04-15T15:00:00",
        }]

    monkeypatch.setattr(lever_scraper, "load_config", lambda: {})
    monkeypatch.setattr(lever_scraper, "load_state", lambda: {"seen_jobs": {}})
    monkeypatch.setattr(lever_scraper, "save_state", lambda _state: (_ for _ in ()).throw(AssertionError("dry run should not save state")))
    monkeypatch.setattr(lever_scraper, "scrape_lever_endpoint", fake_scrape_lever_endpoint)

    jobs = asyncio.run(
        run_lever_scrape(
            dry_run=True,
            endpoints={"example": {"name": "Example", "company_slug": "example"}},
        )
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Cloud Security Engineer"


def test_run_ashby_scrape_dry_run_fetches_without_saving_state(monkeypatch) -> None:
    async def fake_scrape_ashby_endpoint(endpoint_key, endpoint_config, telemetry=None):
        return [{
            "job_id": "123",
            "url": "https://jobs.ashbyhq.com/example/123",
            "title": "Platform Engineer",
            "company": "Example",
            "scraped_at": "2026-04-15T15:00:00",
        }]

    monkeypatch.setattr(ashby_scraper, "load_config", lambda: {})
    monkeypatch.setattr(ashby_scraper, "load_state", lambda: {"seen_jobs": {}})
    monkeypatch.setattr(ashby_scraper, "save_state", lambda _state: (_ for _ in ()).throw(AssertionError("dry run should not save state")))
    monkeypatch.setattr(ashby_scraper, "scrape_ashby_endpoint", fake_scrape_ashby_endpoint)

    jobs = asyncio.run(
        run_ashby_scrape(
            dry_run=True,
            endpoints={"example": {"name": "Example", "company_slug": "example"}},
        )
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Platform Engineer"


def test_run_all_async_scrapers_keeps_ashby_results(monkeypatch) -> None:
    async def fake_run_custom_scraper(*args, **kwargs):
        return []

    async def fake_run_ashby_scrape(*args, **kwargs):
        return [{"title": "Platform Engineer", "job_id": "1", "url": "https://jobs.ashbyhq.com/example/1"}]

    async def fake_run_greenhouse_scrape(*args, **kwargs):
        return []

    async def fake_run_lever_scrape(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "run_full_pipeline.load_config",
        lambda: {
            "schedules": {"lightweight": {"query_groups": ["startup_ats"]}},
            "query_groups": {"startup_ats": {"sites": ["jobs.ashbyhq.com", "boards.greenhouse.io", "jobs.lever.co"]}},
        },
    )
    monkeypatch.setattr("run_full_pipeline.load_state", lambda: {"seen_jobs": {}})
    monkeypatch.setattr("run_full_pipeline.run_custom_scraper", fake_run_custom_scraper)
    monkeypatch.setattr("run_full_pipeline.run_ashby_scrape", fake_run_ashby_scrape)
    monkeypatch.setattr("run_full_pipeline.run_greenhouse_scrape", fake_run_greenhouse_scrape)
    monkeypatch.setattr("run_full_pipeline.run_lever_scrape", fake_run_lever_scrape)

    results = asyncio.run(_run_all_async_scrapers(True, True, "lightweight"))

    assert len(results["ashby"]) == 1
    assert results["ashby"][0]["title"] == "Platform Engineer"
    assert results["_stage_attempts"]["ashby"] is True


def test_run_all_async_scrapers_tracks_new_custom_urls_in_dry_run(monkeypatch) -> None:
    async def fake_run_custom_scraper(*args, **kwargs):
        assert kwargs["dry_run"] is True
        return [
            {"title": "Cloud Security Engineer", "url": "https://example.com/jobs/cloud-security"},
            {"title": "Existing Role", "url": "https://example.com/jobs/existing"},
        ]

    async def fake_run_ashby_scrape(*args, **kwargs):
        return []

    async def fake_run_greenhouse_scrape(*args, **kwargs):
        return []

    async def fake_run_lever_scrape(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "run_full_pipeline.load_config",
        lambda: {
            "schedules": {"lightweight": {"query_groups": ["startup_ats"]}},
            "query_groups": {"startup_ats": {"sites": ["jobs.ashbyhq.com", "boards.greenhouse.io", "jobs.lever.co"]}},
        },
    )
    monkeypatch.setattr("run_full_pipeline.load_state", lambda: {"seen_jobs": {"https://example.com/jobs/existing": "2026-04-17T12:00:00"}})
    monkeypatch.setattr("run_full_pipeline.run_custom_scraper", fake_run_custom_scraper)
    monkeypatch.setattr("run_full_pipeline.run_ashby_scrape", fake_run_ashby_scrape)
    monkeypatch.setattr("run_full_pipeline.run_greenhouse_scrape", fake_run_greenhouse_scrape)
    monkeypatch.setattr("run_full_pipeline.run_lever_scrape", fake_run_lever_scrape)

    results = asyncio.run(_run_all_async_scrapers(True, True, "lightweight"))

    assert results["_new_custom_urls"] == {"https://example.com/jobs/cloud-security"}


def test_get_greenhouse_endpoints_merges_defaults_and_config(monkeypatch) -> None:
    monkeypatch.setattr(
        greenhouse_scraper,
        "load_config",
        lambda: {
            "greenhouse_endpoints": {
                "gitlab": {"priority": "HIGH"},
                "custom": {
                    "name": "Custom Board",
                    "board_token": "customboard",
                    "priority": "LOW",
                },
            }
        },
    )

    endpoints = get_greenhouse_endpoints()

    assert endpoints["gitlab"]["board_token"] == "gitlab"
    assert endpoints["gitlab"]["priority"] == "HIGH"
    assert endpoints["custom"]["board_token"] == "customboard"
    assert "chainguard" in endpoints


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


def test_get_lever_endpoints_merges_defaults_and_config(monkeypatch) -> None:
    monkeypatch.setattr(
        lever_scraper,
        "load_config",
        lambda: {
            "lever_endpoints": {
                "moonpay": {"priority": "MEDIUM"},
                "custom": {
                    "name": "Custom Lever",
                    "company_slug": "customlever",
                    "priority": "LOW",
                },
            }
        },
    )

    endpoints = get_lever_endpoints()

    assert endpoints["moonpay"]["company_slug"] == "moonpay"
    assert endpoints["moonpay"]["priority"] == "MEDIUM"
    assert endpoints["custom"]["company_slug"] == "customlever"
    assert "workwave" in endpoints


def test_default_phase_two_board_expansion_is_present() -> None:
    assert {
        "vannevarlabs",
        "axle",
        "seisandbox",
        "gametime",
        "stitchfix",
        "chainguard",
    }.issubset(greenhouse_scraper.DEFAULT_ENDPOINTS)
    assert {"workwave", "palantir", "moonpay"}.issubset(lever_scraper.DEFAULT_ENDPOINTS)
    assert {"homevision", "leantechniques"}.issubset(ashby_scraper.DEFAULT_ENDPOINTS)


def test_run_pipeline_sends_status_email_when_only_noise_jobs_remain(monkeypatch) -> None:
    """Screened-out-only runs should not send the hot-job email template."""

    sample_job = {
        "title": "Environmental Health and Safety Analyst",
        "company": "ExampleCo",
        "url": "https://example.com/jobs/ehs-analyst",
        "source": "workday_api",
    }
    status_calls: list[dict[str, int]] = []

    async def fake_run_all_async_scrapers(*_args, **_kwargs):
        return {
            "custom": [],
            "ashby": [],
            "icims": [],
            "oracle": [],
            "greenhouse": [],
            "lever": [],
            "_new_custom_urls": set(),
            "_stage_attempts": {},
            "_stage_issues": {},
        }

    monkeypatch.setattr(run_full_pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(run_full_pipeline, "V3_ENABLED", True)
    monkeypatch.setattr(run_full_pipeline, "load_config", lambda: {"settings": {}, "query_groups": {}, "schedules": {}})
    monkeypatch.setattr(run_full_pipeline, "should_run_web_discovery", lambda _run_type: False)
    monkeypatch.setattr(run_full_pipeline, "should_track_pipeline_run", lambda _run_type, _dry_run: True)
    monkeypatch.setattr(run_full_pipeline, "record_pipeline_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_full_pipeline, "run_workday_scrape", lambda **kwargs: [sample_job])
    monkeypatch.setattr(run_full_pipeline, "_run_all_async_scrapers", fake_run_all_async_scrapers)
    monkeypatch.setattr(run_full_pipeline, "run_usajobs_scraper", lambda **kwargs: [])
    monkeypatch.setattr(run_full_pipeline, "annotate_jobs_with_freshness", lambda jobs, config, persist_state: {})
    monkeypatch.setattr(
        run_full_pipeline,
        "apply_feedback_signals",
        lambda jobs: {"boosted": 0, "neutral": len(jobs), "penalized": 0},
    )
    monkeypatch.setattr(run_full_pipeline, "sort_jobs_for_reporting", lambda jobs: jobs)
    monkeypatch.setattr(run_full_pipeline, "load_state", lambda: {"jobs": {sample_job["url"]: sample_job}})
    monkeypatch.setattr(run_full_pipeline, "update_master_csv", lambda _state: None)
    monkeypatch.setattr(run_full_pipeline, "record_source_health_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(
        run_full_pipeline,
        "write_discovery_audit_report",
        lambda **kwargs: {"json_path": "audit.json", "markdown_path": "audit.md"},
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "run_hot_job_pipeline",
        lambda jobs, dry_run=False: {
            "hot_jobs": [],
            "regular_jobs": [],
            "screened_out_jobs": [sample_job],
            "stats": {
                "hot_count": 0,
                "regular_count": 0,
                "screened_out_noise": 1,
                "total_processed": len(jobs),
                "resumes_generated": 0,
                "cover_letters_generated": 0,
            },
        },
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "get_hot_job_attachments",
        lambda _jobs: (_ for _ in ()).throw(AssertionError("attachments should not be collected")),
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "send_hot_job_email",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("hot-job email should not be sent")),
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "send_pipeline_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline email should not be sent")),
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "send_no_reviewable_jobs_email",
        lambda run_type, stats, config, *, total_new_jobs, screened_out_noise, screened_out_jobs: status_calls.append(
            {
                "total_new_jobs": total_new_jobs,
                "screened_out_noise": screened_out_noise,
                "reviewable_jobs": stats.get("reviewable_jobs", -1),
                "screened_out_job_titles": [job.get("title") for job in screened_out_jobs],
            }
        )
        or True,
    )
    monkeypatch.setattr(run_full_pipeline, "send_issue_email", lambda *args, **kwargs: True)

    jobs = run_full_pipeline.run_pipeline(dry_run=False, skip_tailor=False, v3_mode=True, run_type="morning")

    assert jobs == [sample_job]
    assert status_calls == [{
        "total_new_jobs": 1,
        "screened_out_noise": 1,
        "reviewable_jobs": 0,
        "screened_out_job_titles": ["Environmental Health and Safety Analyst"],
    }]


def test_build_screened_role_reference_lines_includes_reason_and_url() -> None:
    """No-reviewable emails should list the rejected role details for reference."""

    lines = run_full_pipeline.build_screened_role_reference_lines(
        [
            {
                "company": "ExampleCo",
                "title": "Environmental Health and Safety Analyst",
                "screening_reason": "Role focuses on EHS compliance, not cloud or security.",
                "url": "https://example.com/jobs/ehs-analyst",
            }
        ]
    )

    assert lines[0] == "Screened-out roles from this run:"
    assert "ExampleCo - Environmental Health and Safety Analyst" in lines[1]
    assert "Role focuses on EHS compliance, not cloud or security." in lines[1]
    assert "https://example.com/jobs/ehs-analyst" in lines[1]


def test_run_pipeline_records_terminal_status_before_email_side_effects(monkeypatch) -> None:
    sample_job = {
        "title": "DevOps Engineer II",
        "company": "Medical Solutions",
        "url": "https://example.com/jobs/devops-ii",
        "source": "workday_api",
    }
    hot_job = {
        **sample_job,
        "match_score": 93,
        "resume_ats_docx": "resume.docx",
        "resume_pdf": "resume.pdf",
        "cover_letter_docx": "cover.docx",
    }
    call_order: list[tuple[str, str, object]] = []

    async def fake_run_all_async_scrapers(*_args, **_kwargs):
        return {
            "custom": [],
            "ashby": [],
            "icims": [],
            "oracle": [],
            "greenhouse": [],
            "lever": [],
            "_new_custom_urls": set(),
            "_stage_attempts": {},
            "_stage_issues": {},
        }

    monkeypatch.setattr(run_full_pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(run_full_pipeline, "V3_ENABLED", True)
    monkeypatch.setattr(run_full_pipeline, "load_config", lambda: {"settings": {}, "query_groups": {}, "schedules": {}})
    monkeypatch.setattr(run_full_pipeline, "should_run_web_discovery", lambda _run_type: False)
    monkeypatch.setattr(run_full_pipeline, "should_track_pipeline_run", lambda _run_type, _dry_run: True)
    monkeypatch.setattr(
        run_full_pipeline,
        "record_pipeline_run",
        lambda run_type, status, **kwargs: call_order.append(("record", status, kwargs.get("email_sent"))),
    )
    monkeypatch.setattr(run_full_pipeline, "run_workday_scrape", lambda **kwargs: [sample_job])
    monkeypatch.setattr(run_full_pipeline, "_run_all_async_scrapers", fake_run_all_async_scrapers)
    monkeypatch.setattr(run_full_pipeline, "run_usajobs_scraper", lambda **kwargs: [])
    monkeypatch.setattr(run_full_pipeline, "annotate_jobs_with_freshness", lambda jobs, config, persist_state: {})
    monkeypatch.setattr(
        run_full_pipeline,
        "apply_feedback_signals",
        lambda jobs: {"boosted": 0, "neutral": len(jobs), "penalized": 0},
    )
    monkeypatch.setattr(run_full_pipeline, "sort_jobs_for_reporting", lambda jobs: jobs)
    monkeypatch.setattr(run_full_pipeline, "load_state", lambda: {"jobs": {sample_job["url"]: sample_job}})
    monkeypatch.setattr(run_full_pipeline, "update_master_csv", lambda _state: None)
    monkeypatch.setattr(run_full_pipeline, "record_source_health_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(
        run_full_pipeline,
        "write_discovery_audit_report",
        lambda **kwargs: {"json_path": "audit.json", "markdown_path": "audit.md"},
    )
    monkeypatch.setattr(run_full_pipeline, "load_discovery_benchmark_set", lambda: [])
    monkeypatch.setattr(run_full_pipeline, "find_previous_benchmark_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        run_full_pipeline,
        "build_discovery_benchmark_summary",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        run_full_pipeline,
        "run_hot_job_pipeline",
        lambda jobs, dry_run=False: {
            "hot_jobs": [hot_job],
            "regular_jobs": [],
            "screened_out_jobs": [],
            "stats": {
                "hot_count": 1,
                "regular_count": 0,
                "screened_out_noise": 0,
                "total_processed": len(jobs),
                "resumes_generated": 1,
                "cover_letters_generated": 1,
                "estimated_llm_cost_usd": 0.0,
            },
        },
    )
    monkeypatch.setattr(run_full_pipeline, "get_hot_job_attachments", lambda _jobs: [])
    monkeypatch.setattr(
        run_full_pipeline,
        "send_hot_job_email",
        lambda **kwargs: call_order.append(("email", "send_hot_job_email", None)) or False,
    )
    monkeypatch.setattr(run_full_pipeline, "send_pipeline_email", lambda *args, **kwargs: True)
    monkeypatch.setattr(run_full_pipeline, "send_no_jobs_email", lambda *args, **kwargs: True)
    monkeypatch.setattr(run_full_pipeline, "send_no_reviewable_jobs_email", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        run_full_pipeline,
        "send_issue_email",
        lambda *args, **kwargs: call_order.append(("email", "send_issue_email", None)) or True,
    )

    jobs = run_full_pipeline.run_pipeline(dry_run=False, skip_tailor=False, v3_mode=True, run_type="morning")

    assert jobs == [sample_job]
    send_index = call_order.index(("email", "send_hot_job_email", None))
    assert any(
        entry[0] == "record" and entry[1] == "success"
        for entry in call_order[:send_index]
    )
    assert call_order[-1] == ("record", "success", False)
