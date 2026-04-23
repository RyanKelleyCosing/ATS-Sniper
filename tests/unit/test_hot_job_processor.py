"""Unit tests for hot-job early screening behavior."""

from datetime import datetime
from pathlib import Path
import sys
import types


sys.modules["openai"] = types.SimpleNamespace(OpenAI=lambda *args, **kwargs: object())

ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

import hot_job_processor  # noqa: E402


def test_categorize_jobs_by_score_screens_noise_before_full_scoring(monkeypatch) -> None:
    """Noise jobs should be screened out before expensive full scoring runs."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Frontend React role focused on product UI delivery.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "NOISE",
            "reason": "Generic frontend application work with no infra or security scope.",
            "confidence": 0.99,
            "should_skip": True,
        },
    )

    def fail_full_scoring(*_args, **_kwargs):
        raise AssertionError("full scoring should not run for screened noise jobs")

    monkeypatch.setattr(hot_job_processor, "analyze_job_match", fail_full_scoring)
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    jobs = [
        {
            "title": "Frontend Engineer",
            "company": "NoiseCo",
            "url": "https://example.com/jobs/frontend",
            "source": "web_discovery",
        }
    ]
    config = {
        "openai_key": "test-key",
        "settings": {
            "openai_model": "gpt-4o-mini",
            "early_classifier_enabled": True,
            "early_classifier_model": "gpt-4o-mini",
        },
    }

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        jobs,
        "resume text",
        config,
    )

    assert hot_jobs == []
    assert regular_jobs == []
    assert len(screened_out_jobs) == 1
    assert screened_out_jobs[0]["screening_category"] == "NOISE"
    assert screened_out_jobs[0]["match_score"] == 0


def test_categorize_jobs_by_score_routes_adjacent_tech_90_plus_to_high_confidence(monkeypatch) -> None:
    """Adjacent-tech roles should reach the natural automation lane when overlap is strong."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": (
                "Implementation engineer role focused on APIs, SQL troubleshooting, deployment workflows, "
                "PowerShell automation, and technical support."
            ),
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "ADJACENT_TECH",
            "reason": "Hands-on adjacent technical role.",
            "confidence": 0.93,
            "should_skip": False,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {
            "match_score": 91,
            "gap_analysis": "Strong adjacent fit.",
            "adjacent_fit_lane": "ADJACENT_TECH",
            "adjacent_fit_title_match": True,
            "adjacent_fit_signal_score": 7,
            "adjacent_fit_bonus": 9,
        },
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Implementation Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/implementation-engineer",
                "source": "web_discovery",
            }
        ],
        "resume text",
        {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
                "early_classifier_enabled": True,
                "early_classifier_model": "gpt-4o-mini",
                "phase6_high_confidence_match_floor": 90,
            },
        },
    )

    assert len(hot_jobs) == 1
    assert hot_jobs[0]["generation_lane"] == "high_confidence"
    assert hot_jobs[0]["screening_category"] == "ADJACENT_TECH"
    assert regular_jobs == []
    assert screened_out_jobs == []


def test_get_regular_review_bucket_marks_adjacent_roles_actionable() -> None:
    """Adjacent-tech strong-fit jobs should stay in an actionable review bucket."""

    job = {
        "title": "System Software Support Engineer",
        "generation_lane": "strong_fit",
        "screening_category": "ADJACENT_TECH",
        "adjacent_fit_title_match": True,
        "adjacent_fit_signal_score": 6,
        "match_score": 86,
        "freshness_bucket": "fresh_under_6h",
        "export_priority": "priority_review",
    }

    assert hot_job_processor.get_regular_review_bucket(job) == "adjacent_strong_fit"
    assert hot_job_processor.is_actionable_review_job(job) is True


def test_apply_daily_goal_promotions_can_promote_adjacent_strong_fit_roles() -> None:
    """Fresh adjacent-tech strong fits can be promoted into the automation lane."""

    hot_jobs, regular_jobs, summary = hot_job_processor.apply_daily_goal_promotions(
        hot_jobs=[],
        regular_jobs=[
            {
                "title": "Implementation Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/adjacent-promote",
                "match_score": 88,
                "freshness_bucket": "fresh_under_6h",
                "screening_category": "ADJACENT_TECH",
                "screening_confidence": 0.95,
                "feedback_signal_label": "neutral",
                "generation_lane": "strong_fit",
                "export_priority": "priority_review",
                "adjacent_fit_title_match": True,
                "adjacent_fit_signal_score": 6,
                "discovery_confidence": 84,
            }
        ],
        state={"jobs": {}},
        config={
            "settings": {
                "phase6_min_generated_packages_per_day": 1,
                "phase6_auto_promote_enabled": True,
                "phase6_adjacent_auto_promote_match_floor": 88,
                "phase6_adjacent_signal_floor": 5,
                "phase6_auto_promote_screening_confidence": 0.85,
                "phase6_auto_promote_discovery_confidence": 70,
            }
        },
    )

    assert len(hot_jobs) == 1
    assert hot_jobs[0]["phase6_auto_promoted"] is True
    assert hot_jobs[0]["automation_status"] == "auto_promoted"
    assert summary["auto_promoted_count"] == 1
    assert regular_jobs == []


def test_run_hot_job_pipeline_excludes_screened_noise_from_regular_export(monkeypatch) -> None:
    """Only manually reviewable regular jobs should be exported to CSV."""

    regular_job = {"title": "DevOps Engineer", "company": "TargetCo", "match_score": 52}
    screened_job = {"title": "Frontend Engineer", "company": "NoiseCo", "screening_category": "NOISE"}
    exported_jobs: list[dict[str, object]] = []

    monkeypatch.setattr(
        hot_job_processor,
        "load_config",
        lambda: {"openai_key": "test-key", "settings": {"openai_model": "gpt-4o-mini"}},
    )
    monkeypatch.setattr(hot_job_processor, "load_master_resume", lambda: "resume text")
    monkeypatch.setattr(
        hot_job_processor,
        "categorize_jobs_by_score",
        lambda _jobs, _resume, _config: ([], [regular_job], [screened_job]),
    )
    monkeypatch.setattr(
        hot_job_processor,
        "export_regular_jobs_to_csv",
        lambda jobs: exported_jobs.extend(jobs),
    )

    result = hot_job_processor.run_hot_job_pipeline([regular_job, screened_job], dry_run=True)

    assert exported_jobs == [regular_job]
    assert result["regular_jobs"] == [regular_job]
    assert result["screened_out_jobs"] == [screened_job]
    assert result["stats"]["regular_count"] == 1
    assert result["stats"]["screened_out_noise"] == 1


def test_process_hot_jobs_generates_cover_letter_artifacts(monkeypatch, tmp_path) -> None:
    """Successful hot-job processing should emit cover letter files alongside resumes."""

    resume_source_path = tmp_path / "resume_source.md"
    resume_source_path.write_text("## Experience\n\n### Example Role\n- Built pipelines.", encoding="utf-8")
    resume_pdf_path = tmp_path / "resume.pdf"
    resume_pdf_path.touch()
    resume_docx_path = tmp_path / "resume.docx"
    resume_docx_path.touch()
    analysis_report_path = tmp_path / "analysis.md"
    analysis_report_path.touch()

    monkeypatch.setattr(
        hot_job_processor,
        "generate_tailored_resume_for_job",
        lambda **_kwargs: {
            "status": "success",
            "resume_pdf": str(resume_pdf_path),
            "resume_docx": str(resume_docx_path),
            "analysis_report": str(analysis_report_path),
            "output_dir": str(tmp_path),
            "resume_source": str(resume_source_path),
            "resume_ats_docx": None,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "generate_cover_letter",
        lambda *_args, **_kwargs: "Dear Hiring Manager,\n\nA concise cover letter.\n\nSincerely,\nCandidate Name",
    )

    config = {"openai_key": "test-key", "settings": {"openai_model": "gpt-4o-mini"}}
    hot_jobs = [
        {
            "title": "DevOps Engineer",
            "company": "TargetCo",
            "url": "https://example.com/jobs/devops",
            "job_description": "Role description",
            "match_score": 88,
        }
    ]

    processed = hot_job_processor.process_hot_jobs(hot_jobs, config, dry_run=False)

    assert len(processed) == 1
    assert processed[0]["cover_letter_txt"]
    assert processed[0]["cover_letter_docx"]
    assert Path(processed[0]["cover_letter_txt"]).exists()
    assert Path(processed[0]["cover_letter_docx"]).exists()


def test_process_hot_jobs_preserves_dry_run_jobs_without_warning(monkeypatch) -> None:
    """Dry-run hot jobs should stay in results without being treated as failures."""

    monkeypatch.setattr(
        hot_job_processor,
        "generate_tailored_resume_for_job",
        lambda **_kwargs: {"status": "dry_run"},
    )

    config = {"openai_key": "test-key", "settings": {"openai_model": "gpt-4o-mini"}}
    hot_jobs = [
        {
            "title": "Application Security Engineer",
            "company": "TargetCo",
            "url": "https://example.com/jobs/appsec",
            "job_description": "Role description",
            "match_score": 85,
        }
    ]

    processed = hot_job_processor.process_hot_jobs(hot_jobs, config, dry_run=True)

    assert len(processed) == 1
    assert processed[0]["automation_status"] == "dry_run"
    assert processed[0]["resume_pdf"] is None
    assert processed[0]["resume_docx"] is None
    assert processed[0]["resume_ats_docx"] is None


def test_process_hot_jobs_reports_company_keyword_gate_reason(monkeypatch, capsys) -> None:
    """Rejected resume exports should log the specific gate instead of a generic failure."""

    monkeypatch.setattr(
        hot_job_processor,
        "generate_tailored_resume_for_job",
        lambda **_kwargs: {"status": "rejected_company_keyword_gate"},
    )

    config = {"openai_key": "test-key", "settings": {"openai_model": "gpt-4o-mini"}}
    hot_jobs = [
        {
            "title": "Cloud Security Engineer",
            "company": "MoonPay",
            "url": "https://example.com/jobs/cloud-security",
            "job_description": "Role description",
            "match_score": 85,
        }
    ]

    processed = hot_job_processor.process_hot_jobs(hot_jobs, config, dry_run=False)
    captured = capsys.readouterr().out

    assert processed == []
    assert "Resume export blocked by company keyword gate for MoonPay" in captured


def test_get_hot_job_attachments_includes_cover_letter_docx(tmp_path) -> None:
    """Hot-job email attachments should include ATS DOCX and cover letter DOCX files."""

    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.touch()
    resume_ats_docx = tmp_path / "resume_ats.docx"
    resume_ats_docx.touch()
    cover_letter_docx = tmp_path / "cover_letter.docx"
    cover_letter_docx.touch()

    attachments = hot_job_processor.get_hot_job_attachments(
        [
            {
                "title": "DevOps Engineer",
                "company": "TargetCo",
                "match_score": 88,
                "resume_pdf": str(resume_pdf),
                "resume_ats_docx": str(resume_ats_docx),
                "cover_letter_docx": str(cover_letter_docx),
            }
        ]
    )

    filenames = {attachment["filename"] for attachment in attachments}
    assert "resume.pdf" in filenames
    assert "resume_ats.docx" in filenames
    assert "cover_letter.docx" in filenames


def test_get_hot_job_attachments_prefers_ats_docx_before_pdf(tmp_path) -> None:
    """The ATS DOCX should be surfaced before styled files in email attachments."""

    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.touch()
    resume_ats_docx = tmp_path / "resume_ats.docx"
    resume_ats_docx.touch()
    cover_letter_docx = tmp_path / "cover_letter.docx"
    cover_letter_docx.touch()

    attachments = hot_job_processor.get_hot_job_attachments(
        [
            {
                "title": "Cloud Security Engineer",
                "company": "MoonPay",
                "match_score": 85,
                "resume_pdf": str(resume_pdf),
                "resume_ats_docx": str(resume_ats_docx),
                "cover_letter_docx": str(cover_letter_docx),
            }
        ]
    )

    assert [attachment["filename"] for attachment in attachments[:3]] == [
        "resume_ats.docx",
        "cover_letter.docx",
        "resume.pdf",
    ]


def test_categorize_jobs_by_score_routes_exact_fit_75s_to_strong_fit(monkeypatch) -> None:
    """Fresh exact-fit roles can enter the strong-fit lane at 75+."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Application security, vulnerability management, and CI/CD security.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "SECURITY",
            "reason": "In target lane.",
            "confidence": 0.98,
            "should_skip": False,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {
            "match_score": 75,
            "gap_analysis": "Strong fit.",
            "exact_fit_lane": "SECURITY",
            "exact_fit_title_match": True,
            "exact_fit_signal_score": 6,
            "exact_fit_bonus": 6,
        },
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Application Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/appsec-strong-fit",
                "source": "greenhouse_api",
            }
        ],
        "resume text",
        {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
                "early_classifier_enabled": True,
                "early_classifier_model": "gpt-4o-mini",
                "phase6_high_confidence_match_floor": 90,
                "phase6_exact_fit_strong_fit_floor": 75,
                "phase6_exact_fit_signal_floor": 5,
            },
        },
    )

    assert hot_jobs == []
    assert len(regular_jobs) == 1
    assert regular_jobs[0]["generation_lane"] == "strong_fit"
    assert regular_jobs[0]["strong_fit_threshold"] == 75
    assert screened_out_jobs == []


def test_apply_daily_goal_promotions_can_promote_exact_fit_75_roles() -> None:
    """Daily backfill should include fresh exact-fit roles once they hit the lowered floor."""

    hot_jobs, regular_jobs, summary = hot_job_processor.apply_daily_goal_promotions(
        hot_jobs=[],
        regular_jobs=[
            {
                "title": "Application Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/exact-fit-75",
                "match_score": 75,
                "freshness_bucket": "fresh_under_6h",
                "screening_category": "SECURITY",
                "screening_confidence": 0.96,
                "feedback_signal_label": "neutral",
                "generation_lane": "strong_fit",
                "export_priority": "standard_review",
                "exact_fit_lane": "SECURITY",
                "exact_fit_title_match": True,
                "exact_fit_signal_score": 6,
            }
        ],
        state={"jobs": {}},
        config={
            "settings": {
                "phase6_min_generated_packages_per_day": 1,
                "phase6_auto_promote_enabled": True,
                "phase6_auto_promote_match_floor": 80,
                "phase6_exact_fit_auto_promote_match_floor": 75,
                "phase6_exact_fit_signal_floor": 5,
                "phase6_auto_promote_screening_confidence": 0.85,
            }
        },
    )

    assert len(hot_jobs) == 1
    assert hot_jobs[0]["phase6_auto_promoted"] is True
    assert hot_jobs[0]["automation_status"] == "auto_promoted"
    assert summary["auto_promoted_count"] == 1
    assert regular_jobs == []


def test_categorize_jobs_by_score_reuses_prefetched_jobspy_description(monkeypatch) -> None:
    """JobSpy leads with captured descriptions should not re-fetch blocked board pages."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: (_ for _ in ()).throw(AssertionError("fetch should not run for JobSpy leads with descriptions")),
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "SECURITY",
            "reason": "Security role.",
            "confidence": 0.95,
            "should_skip": False,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {"match_score": 55, "reasoning": "Solid fit."},
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    config = {
        "openai_key": "test-key",
        "settings": {
            "openai_model": "gpt-4o-mini",
            "early_classifier_enabled": True,
            "early_classifier_model": "gpt-4o-mini",
        },
    }

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Security Analyst III",
                "company": "Johnson County Kansas",
                "url": "https://www.indeed.com/viewjob?jk=3fd92a063ed08eaa",
                "source": "jobspy_indeed",
                "location": "Remote - United States",
                "job_description": "Security analyst role focused on incident response, vulnerability management, and SIEM operations.",
            }
        ],
        "resume text",
        config,
    )

    assert hot_jobs == []
    assert screened_out_jobs == []
    assert len(regular_jobs) == 1
    assert regular_jobs[0]["match_score"] == 55


def test_categorize_jobs_by_score_uses_feedback_adjusted_hot_threshold(monkeypatch) -> None:
    """Boosted feedback should improve review priority without bypassing the 90+ lane."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Cloud security engineering with Azure and IAM ownership.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "SECURITY",
            "reason": "In target lane.",
            "confidence": 0.98,
            "should_skip": False,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {"match_score": 76, "gap_analysis": "Strong fit."},
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    config = {
        "openai_key": "test-key",
        "settings": {
            "openai_model": "gpt-4o-mini",
            "early_classifier_enabled": True,
            "early_classifier_model": "gpt-4o-mini",
        },
    }

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Cloud Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/cloud-security",
                "source": "greenhouse_api",
                "feedback_signal_label": "boosted",
            }
        ],
        "resume text",
        config,
    )

    assert hot_jobs == []
    assert len(regular_jobs) == 1
    assert regular_jobs[0]["hot_job_threshold"] == 80
    assert regular_jobs[0]["generation_lane"] == "review"
    assert screened_out_jobs == []


def test_sort_regular_jobs_for_export_prioritizes_boosted_feedback() -> None:
    """Regular exports should present boosted leads before neutral and penalized ones."""

    ordered = hot_job_processor.sort_regular_jobs_for_export(
        [
            {
                "title": "Penalized Role",
                "company": "NoiseCo",
                "match_score": 79,
                "feedback_signal_label": "penalized",
                "export_priority": "deprioritized_review",
                "freshness_bucket": "fresh_under_6h",
            },
            {
                "title": "Boosted Role",
                "company": "SignalCo",
                "match_score": 74,
                "feedback_signal_label": "boosted",
                "export_priority": "priority_review",
                "freshness_bucket": "fresh_under_24h",
            },
            {
                "title": "Neutral Role",
                "company": "TargetCo",
                "match_score": 78,
                "feedback_signal_label": "neutral",
                "export_priority": "standard_review",
                "freshness_bucket": "fresh_under_6h",
            },
        ]
    )

    assert [job["title"] for job in ordered] == ["Boosted Role", "Neutral Role", "Penalized Role"]


def test_sort_regular_jobs_for_export_prioritizes_exact_fit_strong_fit_queue() -> None:
    """Regular exports should behave like an apply queue for strong exact-fit roles."""

    ordered = hot_job_processor.sort_regular_jobs_for_export(
        [
            {
                "title": "Fresh Generic Review",
                "company": "NoiseCo",
                "match_score": 78,
                "feedback_signal_label": "neutral",
                "export_priority": "standard_review",
                "freshness_bucket": "fresh_under_6h",
                "generation_lane": "review",
                "screening_category": "DEVOPS_SRE_CLOUD",
            },
            {
                "title": "Exact Fit Strong Review",
                "company": "SignalCo",
                "match_score": 75,
                "feedback_signal_label": "neutral",
                "export_priority": "priority_review",
                "freshness_bucket": "fresh_under_24h",
                "generation_lane": "strong_fit",
                "screening_category": "SECURITY",
                "exact_fit_title_match": True,
                "exact_fit_signal_score": 6,
            },
        ]
    )

    assert [job["title"] for job in ordered] == ["Exact Fit Strong Review", "Fresh Generic Review"]


def test_get_export_priority_deprioritizes_stretch_titles() -> None:
    """Stretch or architect-heavy titles should sink below the actionable queue."""

    job = {
        "title": "Cloud Architect",
        "company": "StretchCo",
        "match_score": 79,
        "generation_lane": "review",
        "screening_category": "DEVOPS_SRE_CLOUD",
        "screening_confidence": 0.72,
        "freshness_bucket": "fresh_under_24h",
        "feedback_signal_label": "neutral",
    }

    assert hot_job_processor.get_review_deprioritization_reason(job) == "stretch_title_scope"
    assert hot_job_processor.get_export_priority(job) == "deprioritized_review"


def test_is_actionable_review_job_excludes_deprioritized_reviews() -> None:
    """Actionable review tracking should exclude deprioritized queue rows."""

    actionable_job = {
        "title": "Identity Engineer",
        "generation_lane": "review",
        "review_bucket": "target_lane_review",
        "export_priority": "standard_review",
    }
    deprioritized_job = {
        "title": "Principal Platform Engineer",
        "generation_lane": "review",
        "review_bucket": "deprioritized_review",
        "export_priority": "deprioritized_review",
    }

    assert hot_job_processor.is_actionable_review_job(actionable_job) is True
    assert hot_job_processor.is_actionable_review_job(deprioritized_job) is False


def test_export_regular_jobs_to_csv_rewrites_queue_file(monkeypatch, tmp_path) -> None:
    """Regular export should rewrite the queue CSV with the current header schema."""

    export_path = tmp_path / "regular_jobs_export.csv"
    export_path.write_text("Title,Company\nOld Role,Old Co\n", encoding="utf-8")
    monkeypatch.setattr(hot_job_processor, "CSV_PATH", export_path)

    hot_job_processor.export_regular_jobs_to_csv(
        [
            {
                "title": "Exact Fit Strong Review",
                "company": "SignalCo",
                "url": "https://example.com/jobs/1",
                "match_score": 75,
                "feedback_signal_label": "neutral",
                "export_priority": "priority_review",
                "freshness_bucket": "fresh_under_24h",
                "generation_lane": "strong_fit",
                "screening_category": "SECURITY",
                "exact_fit_title_match": True,
                "exact_fit_signal_score": 6,
                "job_description": "Application security role with SAST, DAST, CI/CD, and secure code review.",
            }
        ]
    )

    lines = export_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Queue Rank,Review Bucket,Queue Score,Title,Company")
    assert "Actionable Review" in lines[0]
    assert "Job Description Snapshot" in lines[0]
    assert "Old Role" not in export_path.read_text(encoding="utf-8")
    assert "Application security role with SAST, DAST, CI/CD, and secure code review." in export_path.read_text(encoding="utf-8")


def test_update_state_job_record_persists_job_description() -> None:
    state = {"jobs": {}}
    job = {
        "url": "https://example.com/jobs/appsec",
        "title": "Application Security Engineer",
        "company": "Example Co",
        "job_description": "Application security role with threat modeling and secure SDLC ownership.",
    }

    hot_job_processor.update_state_job_record(state, job, [], 85)

    assert state["jobs"][job["url"]]["job_description"] == job["job_description"]
    assert state["jobs"][job["url"]]["description"] == job["job_description"]


def test_categorize_jobs_by_score_aggregates_llm_usage(monkeypatch) -> None:
    """Hot-job scoring should aggregate token and cost telemetry by stage."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Cloud security engineering with Azure, IAM, and incident response ownership.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "SECURITY",
            "reason": "Strong target role.",
            "confidence": 0.97,
            "should_skip": False,
            "llm_usage": {
                "model": "gpt-4o-mini",
                "prompt_tokens": 600,
                "completion_tokens": 90,
                "total_tokens": 690,
                "cached_tokens": 0,
                "estimated_cost_usd": 0.000144,
                "pricing_available": True,
            },
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {
            "match_score": 72,
            "gap_analysis": "Good fit.",
            "llm_usage": {
                "model": "gpt-4o-mini",
                "prompt_tokens": 1800,
                "completion_tokens": 320,
                "total_tokens": 2120,
                "cached_tokens": 0,
                "estimated_cost_usd": 0.000462,
                "pricing_available": True,
            },
        },
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    config = {
        "openai_key": "test-key",
        "settings": {
            "openai_model": "gpt-4o-mini",
            "early_classifier_enabled": True,
            "early_classifier_model": "gpt-4o-mini",
        },
    }

    _hot_jobs, regular_jobs, _screened_out_jobs, llm_usage = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Cloud Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/cloud-security-2",
                "source": "greenhouse_api",
            }
        ],
        "resume text",
        config,
        return_usage=True,
    )

    assert len(regular_jobs) == 1
    assert llm_usage["early_classifier"]["calls"] == 1
    assert llm_usage["full_scoring"]["calls"] == 1
    assert llm_usage["totals"]["prompt_tokens"] == 2400
    assert llm_usage["totals"]["completion_tokens"] == 410
    assert llm_usage["totals"]["estimated_cost_usd"] == 0.000606


def test_categorize_jobs_by_score_still_scores_when_early_classifier_disabled(monkeypatch) -> None:
    """Disabling the early classifier should not disable full scoring."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Platform engineering role with cloud automation and incident response.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {"match_score": 81, "gap_analysis": "Strong fit.", "llm_usage": {}},
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Platform Engineer",
                "company": "TargetCo",
                "url": "https://example.com/jobs/platform",
                "source": "greenhouse_api",
            }
        ],
        "resume text",
        {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
                "early_classifier_enabled": False,
            },
        },
    )

    assert hot_jobs == []
    assert len(regular_jobs) == 1
    assert regular_jobs[0]["generation_lane"] == "strong_fit"
    assert screened_out_jobs == []


def test_apply_daily_goal_promotions_promotes_fresh_strong_fit_matches() -> None:
    """Fresh 80-89 strong-fit jobs should backfill the daily package target."""

    hot_jobs, regular_jobs, summary = hot_job_processor.apply_daily_goal_promotions(
        hot_jobs=[],
        regular_jobs=[
            {
                "title": "Cloud Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/promote-me",
                "match_score": 84,
                "freshness_bucket": "fresh_under_6h",
                "screening_category": "SECURITY",
                "screening_confidence": 0.96,
                "feedback_signal_label": "boosted",
                "generation_lane": "strong_fit",
                "export_priority": "priority_review",
                "discovery_confidence": 82,
            },
            {
                "title": "Older DevOps Role",
                "company": "TargetCo",
                "url": "https://example.com/jobs/review-me",
                "match_score": 83,
                "freshness_bucket": "stale_unknown",
                "screening_category": "DEVOPS_SRE_CLOUD",
                "screening_confidence": 0.95,
                "feedback_signal_label": "neutral",
                "generation_lane": "strong_fit",
                "export_priority": "standard_review",
            },
        ],
        state={"jobs": {}},
        config={
            "settings": {
                "phase6_min_generated_packages_per_day": 2,
                "phase6_auto_promote_enabled": True,
                "phase6_auto_promote_match_floor": 80,
                "phase6_auto_promote_screening_confidence": 0.85,
                "phase6_auto_promote_discovery_confidence": 70,
            }
        },
    )

    assert len(hot_jobs) == 1
    assert hot_jobs[0]["phase6_auto_promoted"] is True
    assert hot_jobs[0]["automation_status"] == "auto_promoted"
    assert summary["auto_promoted_count"] == 1
    assert len(regular_jobs) == 1
    assert regular_jobs[0]["title"] == "Older DevOps Role"


def test_apply_daily_goal_promotions_respects_existing_daily_capacity() -> None:
    """No new promotions should occur once the daily automation target is already met."""

    today = datetime.now().date().isoformat()

    hot_jobs, regular_jobs, summary = hot_job_processor.apply_daily_goal_promotions(
        hot_jobs=[],
        regular_jobs=[
            {
                "title": "Identity Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/identity",
                "match_score": 83,
                "freshness_bucket": "fresh_under_6h",
                "screening_category": "IAM",
                "screening_confidence": 0.97,
                "feedback_signal_label": "neutral",
                "generation_lane": "strong_fit",
                "export_priority": "standard_review",
            }
        ],
        state={
            "jobs": {
                "https://example.com/jobs/already-1": {
                    "automation_status": "hot",
                    "last_automated_at": f"{today}T08:00:00",
                },
                "https://example.com/jobs/already-2": {
                    "automation_status": "auto_promoted",
                    "last_automated_at": f"{today}T09:00:00",
                },
            }
        },
        config={
            "settings": {
                "phase6_min_generated_packages_per_day": 2,
                "phase6_auto_promote_enabled": True,
            }
        },
    )

    assert hot_jobs == []
    assert len(regular_jobs) == 1
    assert summary["already_automated_today"] == 2
    assert summary["auto_promoted_count"] == 0


def test_categorize_jobs_by_score_routes_80s_to_strong_fit_review(monkeypatch) -> None:
    """Scores below 90 should stay in review unless promoted as daily backfill."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Cloud security engineering with Terraform, Azure, and IAM ownership.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "SECURITY",
            "reason": "In target lane.",
            "confidence": 0.98,
            "should_skip": False,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {"match_score": 86, "gap_analysis": "Strong fit."},
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Cloud Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/cloud-security-strong-fit",
                "source": "greenhouse_api",
            }
        ],
        "resume text",
        {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
                "early_classifier_enabled": True,
                "early_classifier_model": "gpt-4o-mini",
                "phase6_high_confidence_match_floor": 90,
            },
        },
    )

    assert hot_jobs == []
    assert len(regular_jobs) == 1
    assert regular_jobs[0]["generation_lane"] == "strong_fit"
    assert regular_jobs[0]["high_confidence_threshold"] == 90
    assert screened_out_jobs == []


def test_categorize_jobs_by_score_routes_90_plus_to_high_confidence(monkeypatch) -> None:
    """Only 90+ jobs should enter the natural auto-generation lane."""

    monkeypatch.setattr(
        hot_job_processor,
        "fetch_job_description",
        lambda _url: {
            "description": "Cloud security engineering with Terraform, Azure, IAM, CI/CD, and incident response ownership.",
            "location": "Remote, United States",
            "contact_email": "",
            "contact_emails": [],
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "classify_job_for_screening",
        lambda *_args, **_kwargs: {
            "category": "SECURITY",
            "reason": "In target lane.",
            "confidence": 0.99,
            "should_skip": False,
        },
    )
    monkeypatch.setattr(
        hot_job_processor,
        "analyze_job_match",
        lambda *_args, **_kwargs: {"match_score": 92, "gap_analysis": "Excellent fit."},
    )
    monkeypatch.setattr(hot_job_processor, "load_state", lambda: {})
    monkeypatch.setattr(hot_job_processor, "save_state", lambda _state: None)

    hot_jobs, regular_jobs, screened_out_jobs = hot_job_processor.categorize_jobs_by_score(
        [
            {
                "title": "Cloud Security Engineer",
                "company": "SignalCo",
                "url": "https://example.com/jobs/cloud-security-high-confidence",
                "source": "greenhouse_api",
            }
        ],
        "resume text",
        {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
                "early_classifier_enabled": True,
                "early_classifier_model": "gpt-4o-mini",
                "phase6_high_confidence_match_floor": 90,
            },
        },
    )

    assert len(hot_jobs) == 1
    assert hot_jobs[0]["generation_lane"] == "high_confidence"
    assert regular_jobs == []
    assert screened_out_jobs == []