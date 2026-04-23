"""Unit tests for discovery benchmark validation helpers."""

from pathlib import Path
import json
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.benchmark_validator import (
    build_discovery_benchmark_summary,
    compact_discovery_benchmark_summary,
    find_previous_benchmark_summary,
    load_discovery_benchmark_set,
    normalize_benchmark_url,
)


def test_load_discovery_benchmark_set_and_normalize_url(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "name": "Test benchmark",
                "source_report": "reports/example.md",
                "targets": [
                    {
                        "company": "Acme",
                        "title": "Cloud Security Engineer",
                        "url": "https://jobs.example.com/roles/123/apply?utm_source=test",
                        "source_family": "company_career_site",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    benchmark_set = load_discovery_benchmark_set(benchmark_path)

    assert benchmark_set.name == "Test benchmark"
    assert benchmark_set.targets[0].company == "Acme"
    assert (
        normalize_benchmark_url("https://jobs.example.com/roles/123/apply?utm_source=test")
        == "https://jobs.example.com/roles/123"
    )


def test_build_discovery_benchmark_summary_tracks_overlap_and_drift(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "name": "April test benchmark",
                "source_report": "reports/example.md",
                "targets": [
                    {
                        "company": "Leidos",
                        "title": "Site Reliability Engineer",
                        "url": "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815",
                        "source_family": "workday",
                    },
                    {
                        "company": "Contoso",
                        "title": "Identity Engineer",
                        "url": "https://jobs.example.com/contoso/2",
                        "source_family": "workday",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    benchmark_set = load_discovery_benchmark_set(benchmark_path)

    summary = build_discovery_benchmark_summary(
        [
            {
                "company": "Leidos",
                "title": "Site Reliability Engineer",
                "url": "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/remote-us/site-reliability-engineer_r-00180815",
                "source_family": "workday",
            },
            {
                "company": "Fabrikam",
                "title": "Platform Engineer",
                "url": "https://jobs.example.com/fabrikam/3",
                "source_family": "lever_board",
            },
        ],
        benchmark_set,
        previous_summary={
            "generated_at": "2026-04-19T10:00:00",
            "hit_count": 0,
            "miss_count": 2,
            "extra_count": 0,
            "overlap_rate": 0.0,
            "source_family_summary": {
                "workday": {"targets": 2, "hits": 0, "misses": 2, "overlap_rate": 0.0},
            },
        },
    )

    assert summary["hit_count"] == 1
    assert summary["miss_count"] == 1
    assert summary["extra_count"] == 1
    assert summary["overlap_rate"] == 0.5
    assert summary["source_family_summary"]["workday"]["hits"] == 1
    assert summary["source_family_summary"]["workday"]["misses"] == 1
    assert summary["drift"]["available"] is True
    assert summary["drift"]["delta_hit_count"] == 1
    assert summary["drift"]["source_family_deltas"]["workday"]["delta_hits"] == 1

    compact_summary = compact_discovery_benchmark_summary(summary)
    assert compact_summary["hit_count"] == 1
    assert len(compact_summary["misses"]) == 1


def test_build_discovery_benchmark_summary_includes_scope_comparison(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "name": "April test benchmark",
                "source_report": "reports/example.md",
                "targets": [
                    {
                        "company": "Leidos",
                        "title": "Site Reliability Engineer",
                        "url": "https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815",
                        "source_family": "workday",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    benchmark_set = load_discovery_benchmark_set(benchmark_path)

    summary = build_discovery_benchmark_summary(
        [
            {
                "company": "Fabrikam",
                "title": "Platform Engineer",
                "url": "https://jobs.example.com/fabrikam/3",
                "source_family": "lever_board",
            }
        ],
        benchmark_set,
        comparison_scopes={
            "state_after_run": [
                {
                    "company": "Leidos",
                    "title": "Site Reliability Engineer",
                    "url": "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/remote-us/site-reliability-engineer_r-00180815",
                    "source_family": "workday",
                },
                {
                    "company": "Fabrikam",
                    "title": "Platform Engineer",
                    "url": "https://jobs.example.com/fabrikam/3",
                    "source_family": "lever_board",
                },
            ]
        },
    )

    assert summary["scope_name"] == "net_new_jobs"
    assert summary["candidate_job_count"] == 1
    assert summary["hit_count"] == 0
    assert summary["comparison_scopes"]["state_after_run"]["candidate_job_count"] == 2
    assert summary["comparison_scopes"]["state_after_run"]["hit_count"] == 1

    compact_summary = compact_discovery_benchmark_summary(summary)
    assert compact_summary["comparison_scopes"]["state_after_run"]["hit_count"] == 1


def test_find_previous_benchmark_summary_returns_latest_matching_run_type() -> None:
    previous_summary = find_previous_benchmark_summary(
        {
            "pipeline_runs": {
                "2026-04-18": {
                    "morning": {
                        "benchmark_summary": {"hit_count": 6, "generated_at": "2026-04-18T08:00:00"}
                    }
                },
                "2026-04-19": {
                    "afternoon": {
                        "benchmark_summary": {"hit_count": 8, "generated_at": "2026-04-19T14:00:00"}
                    },
                    "morning": {
                        "benchmark_summary": {"hit_count": 9, "generated_at": "2026-04-19T08:00:00"},
                        "completed_at": "2026-04-19T08:30:00",
                    }
                },
            }
        },
        "morning",
    )

    assert previous_summary is not None
    assert previous_summary["hit_count"] == 9
    assert previous_summary["record_date"] == "2026-04-19"
    assert previous_summary["completed_at"] == "2026-04-19T08:30:00"