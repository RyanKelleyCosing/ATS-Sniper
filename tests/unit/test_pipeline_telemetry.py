"""Unit tests for discovery audit telemetry helpers."""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.pipeline_telemetry import (
    apply_feedback_signals,
    build_discovery_audit_payload,
    ensure_discovery_feedback_csv,
    write_discovery_audit_report,
)


def test_build_discovery_audit_payload_counts_sources_and_feedback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATS_SNIPER_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.pipeline_telemetry.load_state",
        lambda: {
            "source_health_history": [
                {
                    "stage_summary": {
                        "workday": {"attempted": True, "new_jobs": 2, "issue_count": 0, "status": "ok"},
                        "greenhouse": {"attempted": True, "new_jobs": 1, "issue_count": 1, "status": "issues"},
                    }
                },
                {
                    "stage_summary": {
                        "workday": {"attempted": True, "new_jobs": 1, "issue_count": 0, "status": "ok_no_results"},
                        "greenhouse": {"attempted": True, "new_jobs": 3, "issue_count": 0, "status": "ok"},
                    }
                },
            ],
            "jobs": {
                "https://example.com/jobs/1": {
                    "title": "Staff Platform Engineer",
                    "company": "NoiseCo",
                    "source_family": "company_career_site",
                    "query_profile": "company_career_domains",
                    "job_description": "Platform role above the target seniority lane.",
                }
            },
        },
    )
    feedback_path = ensure_discovery_feedback_csv()
    feedback_path.write_text(
        "url,decision,notes,reviewed_at\nhttps://example.com/jobs/1,noise,too broad,2026-04-15\n",
        encoding="utf-8",
    )

    payload = build_discovery_audit_payload(
        run_type="lightweight",
        stats={
            "workday": 1,
            "greenhouse": 1,
            "web_discovery": 1,
            "fresh_under_6h": 2,
            "fresh_under_24h": 1,
            "screened_out_noise": 1,
            "early_classifier_calls": 3,
            "full_scoring_calls": 2,
            "llm_usage": {
                "early_classifier": {
                    "model": "gpt-4o-mini",
                    "calls": 3,
                    "prompt_tokens": 900,
                    "completion_tokens": 150,
                    "total_tokens": 1050,
                    "cached_tokens": 0,
                    "estimated_cost_usd": 0.000225,
                    "pricing_available": True,
                },
                "full_scoring": {
                    "model": "gpt-4o-mini",
                    "calls": 2,
                    "prompt_tokens": 2400,
                    "completion_tokens": 500,
                    "total_tokens": 2900,
                    "cached_tokens": 0,
                    "estimated_cost_usd": 0.00066,
                    "pricing_available": True,
                },
            },
        },
        all_new_jobs=[
            {
                "title": "Cloud Security Engineer",
                "company": "Acme",
                "source": "greenhouse_api",
                "source_family": "greenhouse_board",
                "query_profile": "direct_board_api",
                "freshness_bucket": "fresh_under_6h",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
            },
            {
                "title": "Identity Engineer",
                "company": "Contoso",
                "source": "web_google",
                "source_family": "company_career_site",
                "query_profile": "company_career_domains",
                "freshness_bucket": "fresh_under_24h",
                "url": "https://example.com/jobs/2",
            },
        ],
        hot_job_results={
            "hot_jobs": [{"title": "Cloud Security Engineer", "job_description": "Azure security"}],
            "regular_jobs": [],
            "screened_out_jobs": [
                {
                    "title": "Staff Platform Engineer",
                    "screening_category": "NOISE",
                    "screening_reason": "Seniority is above the target lane.",
                }
            ],
        },
        pipeline_issues=["[web-discovery] upstream timeout"],
        web_discovery_telemetry={
            "query_profile_yield": {"company_career_domains": 1},
            "rejected_reasons": {"blocked_location": 2, "excluded_seniority_staff": 1},
        },
        direct_scraper_telemetry={
            "greenhouse": {"rejected_reasons": {"excluded_seniority_staff": 2}, "kept_reasons": {}},
            "workday": {"rejected_reasons": {"blocked_location": 1}, "kept_reasons": {}},
        },
        stage_attempts={"workday": True, "greenhouse": True, "web_discovery": True},
    )

    assert payload["counts"]["total_new_jobs"] == 2
    assert payload["jobs_by_source"]["greenhouse_api"] == 1
    assert payload["jobs_by_query_profile"]["company_career_domains"] == 1
    assert payload["jobs_by_role_cluster"]["cloud"] >= 1
    assert payload["manual_feedback"]["false_positive_count"] == 1
    assert payload["stage_summary"]["web_discovery"]["issue_count"] == 1
    assert payload["stage_summary"]["workday"]["attempted"] is True
    assert payload["source_health_rates"]["workday"]["success_rate"] == 1.0
    assert payload["source_health_rates"]["greenhouse"]["failure_rate"] == 0.5
    assert payload["direct_scraper_rejections_by_reason"]["blocked_location"] == 1
    assert payload["ignored_due_seniority_or_location"]["blocked_location"] == 3
    assert payload["llm_usage"]["early_classifier_calls"] == 3
    assert payload["llm_usage"]["full_scoring"]["prompt_tokens"] == 2400
    assert payload["llm_usage"]["totals"]["estimated_cost_usd"] == 0.000885
    assert payload["counts"]["actionable_jobs"] == 1
    assert payload["found_vs_actionable_by_source_family"]["greenhouse_board"]["actionable"] == 1


def test_build_discovery_audit_payload_separates_found_from_actionable_jobs(monkeypatch) -> None:
    monkeypatch.setattr("utils.pipeline_telemetry.load_state", lambda: {"source_health_history": []})

    payload = build_discovery_audit_payload(
        run_type="morning",
        stats={"workday": 1},
        all_new_jobs=[
            {
                "title": "Cloud Security Engineer",
                "company": "Acme",
                "source": "greenhouse_api",
                "source_family": "greenhouse_board",
                "query_profile": "direct_board_api",
                "freshness_bucket": "fresh_under_6h",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
            },
            {
                "title": "Identity Engineer",
                "company": "Contoso",
                "source": "web_google",
                "source_family": "company_career_site",
                "query_profile": "company_career_domains",
                "freshness_bucket": "fresh_under_24h",
                "url": "https://example.com/jobs/2",
            },
            {
                "title": "Cloud Architect",
                "company": "StretchCo",
                "source": "web_google",
                "source_family": "company_career_site",
                "query_profile": "company_career_domains",
                "freshness_bucket": "stale_over_24h",
                "url": "https://example.com/jobs/3",
            },
            {
                "title": "Frontend Engineer",
                "company": "NoiseCo",
                "source": "web_google",
                "source_family": "company_career_site",
                "query_profile": "company_career_domains",
                "freshness_bucket": "fresh_under_24h",
                "url": "https://example.com/jobs/4",
            },
        ],
        hot_job_results={
            "hot_jobs": [
                {
                    "title": "Cloud Security Engineer",
                    "company": "Acme",
                    "source_family": "greenhouse_board",
                    "generation_lane": "high_confidence",
                }
            ],
            "regular_jobs": [
                {
                    "title": "Identity Engineer",
                    "company": "Contoso",
                    "source_family": "company_career_site",
                    "review_bucket": "target_lane_review",
                    "export_priority": "standard_review",
                    "actionable_review": True,
                },
                {
                    "title": "Cloud Architect",
                    "company": "StretchCo",
                    "source_family": "company_career_site",
                    "review_bucket": "deprioritized_review",
                    "export_priority": "deprioritized_review",
                    "review_deprioritization_reason": "stretch_title_scope",
                },
            ],
            "screened_out_jobs": [
                {
                    "title": "Frontend Engineer",
                    "company": "NoiseCo",
                    "source_family": "company_career_site",
                    "screening_category": "NOISE",
                    "screening_reason": "Off lane.",
                }
            ],
        },
        pipeline_issues=[],
        web_discovery_telemetry={},
        stage_attempts={"workday": True},
    )

    assert payload["counts"]["actionable_jobs"] == 2
    assert payload["counts"]["non_actionable_review_jobs"] == 1
    assert payload["found_vs_actionable_by_source_family"]["company_career_site"] == {
        "found": 3,
        "actionable": 1,
        "hot": 0,
        "actionable_review": 1,
        "non_actionable_review": 1,
        "screened_out_noise": 1,
    }
    assert payload["review_deprioritization_reasons"] == {"stretch_title_scope": 1}


def test_build_discovery_audit_payload_tracks_stage_drift_and_recurring_edge_cases(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ATS_SNIPER_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.pipeline_telemetry.load_state",
        lambda: {
            "source_health_history": [
                {
                    "generated_at": "2026-04-20T08:00:00",
                    "run_type": "morning",
                    "stage_summary": {
                        "workday": {
                            "attempted": True,
                            "new_jobs": 1,
                            "issue_count": 0,
                            "status": "ok",
                        },
                        "greenhouse": {
                            "attempted": True,
                            "new_jobs": 2,
                            "issue_count": 0,
                            "status": "ok",
                        },
                    },
                    "issue_records": [
                        {
                            "stage": "greenhouse",
                            "target": "gitlab",
                            "signature": "http_403",
                            "message": "Greenhouse HTTP error for 'gitlab': 403",
                        }
                    ],
                }
            ],
            "jobs": {},
        },
    )

    payload = build_discovery_audit_payload(
        run_type="morning",
        stats={"workday": 1, "greenhouse": 1},
        all_new_jobs=[],
        hot_job_results={"hot_jobs": [], "regular_jobs": [], "screened_out_jobs": []},
        pipeline_issues=["[greenhouse] Greenhouse HTTP error for 'gitlab': 403"],
        web_discovery_telemetry={},
        stage_attempts={"workday": True, "greenhouse": True},
    )

    assert payload["stage_drift_summary"]["available"] is True
    assert payload["stage_drift_summary"]["baseline_generated_at"] == "2026-04-20T08:00:00"
    assert payload["stage_drift_summary"]["changed_stages"]["greenhouse"] == {
        "previous_status": "ok",
        "current_status": "issues",
        "delta_new_jobs": -1,
        "delta_issue_count": 1,
        "regression": True,
        "improvement": False,
    }
    assert payload["board_specific_failures"] == [
        {
            "stage": "greenhouse",
            "target": "gitlab",
            "signature": "http_403",
            "count": 1,
            "sample_message": "Greenhouse HTTP error for 'gitlab': 403",
        }
    ]
    assert payload["recurring_edge_cases"] == [
        {
            "stage": "greenhouse",
            "target": "gitlab",
            "signature": "http_403",
            "count": 1,
            "sample_message": "Greenhouse HTTP error for 'gitlab': 403",
            "prior_occurrences": 1,
            "prior_runs": 1,
            "last_seen_at": "2026-04-20T08:00:00",
        }
    ]


def test_apply_feedback_signals_marks_boosted_and_penalized_jobs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATS_SNIPER_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.pipeline_telemetry.load_state",
        lambda: {
            "jobs": {
                "https://example.com/jobs/noise": {
                    "title": "Staff Platform Engineer",
                    "company": "NoiseCo",
                    "source_family": "company_career_site",
                    "query_profile": "company_career_domains",
                    "job_description": "Platform engineering role above the target lane.",
                },
                "https://example.com/jobs/good": {
                    "title": "Cloud Security Engineer",
                    "company": "SignalCo",
                    "source_family": "greenhouse_board",
                    "query_profile": "direct_board_api",
                    "job_description": "Azure cloud security and incident response.",
                },
            }
        },
    )
    feedback_path = ensure_discovery_feedback_csv()
    feedback_path.write_text(
        "url,decision,notes,reviewed_at\n"
        "https://example.com/jobs/noise,noise,too senior,2026-04-15\n"
        "https://example.com/jobs/good,good lead,strong fit,2026-04-15\n",
        encoding="utf-8",
    )

    jobs = [
        {
            "title": "Principal Platform Engineer",
            "company": "NoiseCo",
            "source_family": "company_career_site",
            "query_profile": "company_career_domains",
            "job_description": "Platform engineering leadership role.",
        },
        {
            "title": "Cloud Security Engineer",
            "company": "SignalCo",
            "source_family": "greenhouse_board",
            "query_profile": "direct_board_api",
            "job_description": "Azure cloud security and detection engineering.",
        },
    ]

    summary = apply_feedback_signals(jobs)

    assert summary == {"boosted": 1, "neutral": 0, "penalized": 1}
    assert jobs[0]["feedback_signal_label"] == "penalized"
    assert jobs[0]["feedback_signal_score"] < 0
    assert jobs[1]["feedback_signal_label"] == "boosted"
    assert jobs[1]["feedback_signal_score"] > 0


def test_write_discovery_audit_report_renders_benchmark_overlap(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATS_SNIPER_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr("utils.pipeline_telemetry.load_state", lambda: {"source_health_history": []})

    report_paths = write_discovery_audit_report(
        run_type="morning",
        stats={"workday": 1, "issues": 0},
        all_new_jobs=[
            {
                "title": "Cloud Security Engineer",
                "company": "Acme",
                "source": "greenhouse_api",
                "source_family": "greenhouse_board",
                "query_profile": "direct_board_api",
                "freshness_bucket": "fresh_under_6h",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
            }
        ],
        hot_job_results={"hot_jobs": [], "regular_jobs": [], "screened_out_jobs": []},
        pipeline_issues=[],
        web_discovery_telemetry={},
        stage_attempts={"workday": True},
        benchmark_summary={
            "benchmark_name": "April benchmark",
            "source_report": "reports/example.md",
            "scope_name": "net_new_jobs",
            "match_strategy": "normalized_url_only",
            "candidate_job_count": 1,
            "target_count": 2,
            "hit_count": 1,
            "miss_count": 1,
            "extra_count": 0,
            "overlap_rate": 0.5,
            "comparison_scopes": {
                "state_after_run": {
                    "candidate_job_count": 2,
                    "hit_count": 2,
                    "miss_count": 0,
                    "extra_count": 0,
                    "overlap_rate": 1.0,
                }
            },
            "source_family_summary": {
                "greenhouse_board": {"targets": 1, "hits": 1, "misses": 0, "overlap_rate": 1.0},
                "workday": {"targets": 1, "hits": 0, "misses": 1, "overlap_rate": 0.0},
            },
            "misses": [
                {
                    "company": "Contoso",
                    "title": "Identity Engineer",
                    "source_family": "workday",
                }
            ],
            "limitations": ["Overlap currently matches benchmark jobs by normalized URL only."],
            "drift": {"available": False, "message": "No previous benchmark summary is available for drift comparison."},
        },
    )

    markdown_path = Path(report_paths["markdown_path"])
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "## Benchmark Overlap" in markdown
    assert "- Actionable jobs: 0" in markdown
    assert "- Primary scope: net_new_jobs" in markdown
    assert "- Candidate jobs in primary scope: 1" in markdown
    assert "- Hits: 1" in markdown
    assert "- state_after_run: candidates=2, hits=2, misses=0, extras=0, overlap_rate=1.0" in markdown
    assert "- workday: targets=1, hits=0, misses=1, overlap_rate=0.0" in markdown
    assert "- Contoso | Identity Engineer | workday" in markdown


def test_write_discovery_audit_report_renders_stage_drift_and_edge_cases(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATS_SNIPER_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.pipeline_telemetry.load_state",
        lambda: {
            "source_health_history": [
                {
                    "generated_at": "2026-04-20T08:00:00",
                    "run_type": "morning",
                    "stage_summary": {
                        "greenhouse": {
                            "attempted": True,
                            "new_jobs": 2,
                            "issue_count": 0,
                            "status": "ok",
                        }
                    },
                    "issue_records": [
                        {
                            "stage": "greenhouse",
                            "target": "gitlab",
                            "signature": "http_403",
                            "message": "Greenhouse HTTP error for 'gitlab': 403",
                        }
                    ],
                }
            ]
        },
    )

    report_paths = write_discovery_audit_report(
        run_type="morning",
        stats={"greenhouse": 1, "issues": 1},
        all_new_jobs=[],
        hot_job_results={"hot_jobs": [], "regular_jobs": [], "screened_out_jobs": []},
        pipeline_issues=["[greenhouse] Greenhouse HTTP error for 'gitlab': 403"],
        web_discovery_telemetry={},
        stage_attempts={"greenhouse": True},
    )

    markdown = Path(report_paths["markdown_path"]).read_text(encoding="utf-8")

    assert "## Stage Drift" in markdown
    assert "- greenhouse: ok -> issues, delta_new_jobs=-1, delta_issue_count=1, regression=yes, improvement=no" in markdown
    assert "## Board-Specific Failures" in markdown
    assert "- greenhouse | gitlab | http_403 | count=1 | Greenhouse HTTP error for 'gitlab': 403" in markdown
    assert "## Recurring Edge Cases" in markdown
    assert "- greenhouse | gitlab | http_403 | current=1, previous_occurrences=1, prior_runs=1, last_seen=2026-04-20T08:00:00" in markdown


def test_write_discovery_audit_report_supports_custom_base_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATS_SNIPER_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr("utils.pipeline_telemetry.load_state", lambda: {"source_health_history": []})

    report_paths = write_discovery_audit_report(
        run_type="fresh_watch",
        stats={"web_discovery": 1},
        all_new_jobs=[],
        hot_job_results={"hot_jobs": [], "regular_jobs": [], "screened_out_jobs": []},
        pipeline_issues=[],
        web_discovery_telemetry={},
        stage_attempts={"web_discovery": True},
        base_name="fresh_watch_latest",
    )

    assert Path(report_paths["json_path"]).name == "fresh_watch_latest.json"
    assert Path(report_paths["markdown_path"]).name == "fresh_watch_latest.md"