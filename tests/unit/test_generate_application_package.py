"""Unit tests for manual application package generation CLI helpers."""

import json
from pathlib import Path
import sys
import types


sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=object))

ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

from generate_application_package import (  # noqa: E402
    ApplicationGenerationContext,
    DEFAULT_APPLICATION_PACKAGE_MODEL,
    ManualApplicationRequest,
    build_request_from_review_csv_row,
    build_ats_audit_summary,
    build_generation_context,
    build_request_from_batch_job,
    filter_review_csv_rows,
    filter_batch_jobs_by_match_score,
    format_ats_audit_statuses,
    generate_manual_application,
    parse_queue_ranks,
    resolve_review_job_description,
    resolve_package_root_override,
    write_ats_audit_summary,
    write_batch_apply_links,
)
from utils.application_packages import ApplicationPackageArtifacts  # noqa: E402
from utils.application_batch import ApplicationBatchJob, ApplicationBatchManifest  # noqa: E402


def test_build_request_from_batch_job_respects_batch_package_root_override() -> None:
    job = ApplicationBatchJob(
        company="Example Co",
        role="Cloud Engineer",
        job_url="manual://job",
        match_score=84,
        template_hint="cloud",
        preferred_accomplishment_ids=("CLOUD-001",),
        industry_focus="regulated healthcare",
        cover_letter_focus="connect cloud reliability to the mission",
        job_description="Cloud role description.",
        fallback_job_description="",
        tailoring_notes=("Keep the two promotions visible.",),
        package_root=None,
        include_supporting_artifacts=None,
    )
    manifest = ApplicationBatchManifest(
        package_root=Path("resumes to make manually/Application Packs/Base"),
        include_supporting_artifacts=False,
        jobs=(job,),
    )

    request = build_request_from_batch_job(
        job,
        manifest,
        package_root_override=Path("comparison/gpt-4o"),
        include_supporting_artifacts_override=True,
    )

    assert request.package_root == Path("comparison/gpt-4o")
    assert request.include_supporting_artifacts is True


def test_application_generation_context_dataclass_keeps_model() -> None:
    context = ApplicationGenerationContext(client=object(), model="gpt-4o")

    assert context.model == "gpt-4o"


def test_build_generation_context_prefers_application_package_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "generate_application_package.load_config",
        lambda: {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
                "application_package_model": "gpt-5.4",
            },
        },
    )
    monkeypatch.setattr(
        "generate_application_package.OpenAI",
        lambda api_key: {"api_key": api_key},
    )

    context = build_generation_context()

    assert context.model == "gpt-5.4"
    assert context.client == {"api_key": "test-key"}


def test_build_generation_context_uses_default_when_specific_setting_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "generate_application_package.load_config",
        lambda: {
            "openai_key": "test-key",
            "settings": {
                "openai_model": "gpt-4o-mini",
            },
        },
    )
    monkeypatch.setattr(
        "generate_application_package.OpenAI",
        lambda api_key: {"api_key": api_key},
    )

    context = build_generation_context()

    assert context.model == DEFAULT_APPLICATION_PACKAGE_MODEL


def test_resolve_package_root_override_returns_none_for_default_root() -> None:
    assert resolve_package_root_override("resumes to make manually/Application Packs") is None


def test_resolve_package_root_override_keeps_explicit_root() -> None:
    assert resolve_package_root_override("resumes to make manually/Application Packs/2026-04-07 Remaining Jobs") == Path(
        "resumes to make manually/Application Packs/2026-04-07 Remaining Jobs"
    )


def test_write_batch_apply_links_includes_urls_and_package_dirs(tmp_path: Path) -> None:
    manifest = ApplicationBatchManifest(
        package_root=tmp_path,
        include_supporting_artifacts=True,
        jobs=(
            ApplicationBatchJob(
                company="Example Co",
                role="Cloud Engineer",
                job_url="https://example.com/jobs/1",
                match_score=84,
                template_hint=None,
                preferred_accomplishment_ids=(),
                industry_focus="",
                cover_letter_focus="",
                job_description="Example description.",
                fallback_job_description="",
                tailoring_notes=(),
                package_root=None,
                include_supporting_artifacts=None,
            ),
        ),
    )

    summary_path = write_batch_apply_links(
        [
            {
                "company": "Example Co",
                "role": "Cloud Engineer",
                "status": "success",
                "package_dir": str(tmp_path / "Example Co - Cloud Engineer"),
                "ats_audit": {
                    "checks": {
                        "keyword_density": {"status": "pass"},
                        "impact_first_bullets": {"status": "warn"},
                    }
                },
            }
        ],
        manifest,
        tmp_path,
    )

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Requested jobs: 1" in summary_text
    assert "Successful packages: 1" in summary_text
    assert "URL: https://example.com/jobs/1" in summary_text
    assert "Package: " in summary_text
    assert "ATS Audit: keyword_density=pass, impact_first_bullets=warn" in summary_text


def test_filter_batch_jobs_by_match_score_respects_bounds() -> None:
    jobs = (
        ApplicationBatchJob(
            company="Example 87",
            role="Role A",
            job_url="manual://87",
            match_score=87,
            template_hint=None,
            preferred_accomplishment_ids=(),
            industry_focus="",
            cover_letter_focus="",
            job_description="A",
            fallback_job_description="",
            tailoring_notes=(),
            package_root=None,
            include_supporting_artifacts=None,
        ),
        ApplicationBatchJob(
            company="Example 88",
            role="Role B",
            job_url="manual://88",
            match_score=88,
            template_hint=None,
            preferred_accomplishment_ids=(),
            industry_focus="",
            cover_letter_focus="",
            job_description="B",
            fallback_job_description="",
            tailoring_notes=(),
            package_root=None,
            include_supporting_artifacts=None,
        ),
        ApplicationBatchJob(
            company="Example 89",
            role="Role C",
            job_url="manual://89",
            match_score=89,
            template_hint=None,
            preferred_accomplishment_ids=(),
            industry_focus="",
            cover_letter_focus="",
            job_description="C",
            fallback_job_description="",
            tailoring_notes=(),
            package_root=None,
            include_supporting_artifacts=None,
        ),
        ApplicationBatchJob(
            company="Example 90",
            role="Role D",
            job_url="manual://90",
            match_score=90,
            template_hint=None,
            preferred_accomplishment_ids=(),
            industry_focus="",
            cover_letter_focus="",
            job_description="D",
            fallback_job_description="",
            tailoring_notes=(),
            package_root=None,
            include_supporting_artifacts=None,
        ),
    )

    filtered = filter_batch_jobs_by_match_score(
        jobs,
        min_match_score=88,
        max_match_score=89,
    )

    assert [job.company for job in filtered] == ["Example 88", "Example 89"]


def test_parse_queue_ranks_reads_comma_separated_values() -> None:
    assert parse_queue_ranks("1, 3,5") == (1, 3, 5)


def test_filter_review_csv_rows_respects_actionable_queue_and_limits() -> None:
    rows = [
        {"Queue Rank": "3", "Actionable Review": "yes", "Match Score": "55", "Company": "C", "Title": "Role C"},
        {"Queue Rank": "1", "Actionable Review": "", "Match Score": "75", "Company": "A", "Title": "Role A"},
        {"Queue Rank": "2", "Actionable Review": "yes", "Match Score": "65", "Company": "B", "Title": "Role B"},
    ]

    filtered = filter_review_csv_rows(
        rows,
        queue_ranks=(2, 3),
        actionable_only=True,
        max_match_score=60,
        limit=1,
    )

    assert [row["Queue Rank"] for row in filtered] == ["3"]


def test_build_request_from_review_csv_row_prefers_state_description(tmp_path: Path) -> None:
    state_description = (
        "Azure platform engineering role with Kubernetes, AKS, Terraform, Bicep, KQL, and "
        "incident response responsibilities across production services. "
    ) * 4
    request = build_request_from_review_csv_row(
        {
            "Company": "Example Co",
            "Title": "Azure Platform Engineer",
            "URL": "https://example.com/jobs/azure-platform",
            "Match Score": "55",
        },
        state={
            "jobs": {
                "https://example.com/jobs/azure-platform": {
                    "job_description": state_description,
                }
            }
        },
        package_root=tmp_path,
        include_supporting_artifacts=True,
    )

    assert request.company == "Example Co"
    assert request.role == "Azure Platform Engineer"
    assert request.job_description.strip() == state_description.strip()
    assert request.match_score == 55
    assert request.package_root == tmp_path
    assert request.include_supporting_artifacts is True
    assert request.allow_must_have_keyword_override is True


def test_resolve_review_job_description_falls_back_to_jobspy(monkeypatch) -> None:
    job_description = (
        "Automation engineering role with Azure, Kubernetes, Terraform, Bicep, KQL, and "
        "production troubleshooting responsibilities. "
    ) * 4
    monkeypatch.setattr(
        "generate_application_package.fetch_remote_job_description",
        lambda _job_url: "",
    )
    monkeypatch.setattr(
        "generate_application_package.run_jobspy_queries",
        lambda _queries: [
            {
                "records": [
                    {
                        "job_url": "https://www.indeed.com/viewjob?jk=123",
                        "company": "Example Co",
                        "title": "Automation Engineer",
                        "description": job_description,
                    }
                ],
                "error": "",
            }
        ],
    )

    resolved_description = resolve_review_job_description(
        {
            "Company": "Example Co",
            "Title": "Automation Engineer",
            "URL": "https://www.indeed.com/viewjob?jk=123",
            "Location": "Remote - United States",
            "Source Board": "indeed",
        },
        state={"jobs": {}},
    )

    assert resolved_description.strip() == job_description.strip()


def test_resolve_review_job_description_prefers_review_row_snapshot(monkeypatch) -> None:
    job_description = (
        "Application security role covering secure code review, SAST/DAST, threat modeling, "
        "CI/CD security automation, and AI/LLM security practices. "
    ) * 4
    monkeypatch.setattr(
        "generate_application_package.fetch_remote_job_description",
        lambda _job_url: (_ for _ in ()).throw(AssertionError("remote fetch should not run")),
    )
    monkeypatch.setattr(
        "generate_application_package.fetch_review_description_from_jobspy",
        lambda _review_row: (_ for _ in ()).throw(AssertionError("JobSpy fallback should not run")),
    )

    resolved_description = resolve_review_job_description(
        {
            "Company": "Nelnet",
            "Title": "Cybersecurity Application Security Engineer",
            "URL": "https://example.com/jobs/nelnet-appsec",
            "Job Description Snapshot": job_description,
        },
        state={"jobs": {}},
    )

    assert resolved_description.strip() == job_description.strip()


def test_build_ats_audit_summary_keeps_generation_checks_without_files() -> None:
    audit = build_ats_audit_summary(
        {
            "status": "success",
            "must_have_keyword_override_applied": True,
            "must_have_keyword_gate": {
                "status": "warn",
                "message": "Coverage is thin.",
                "target_phrases": ["Windows Server", "Active Directory"],
                "missing_phrases": ["Active Directory"],
                "matched_count": 1,
                "required_matches": 2,
            },
            "keyword_density": {
                "status": "pass",
                "message": "Keywords are dense enough.",
                "mentioned_terms": ["Azure", "Kubernetes"],
            },
            "impact_first_bullets": {
                "status": "warn",
                "message": "Some bullets still open weakly.",
                "impact_ratio": 0.5,
            },
        }
    )

    assert audit["generation_status"] == "success"
    assert audit["must_have_keyword_override_applied"] is True
    assert audit["checks"]["must_have_keyword_gate"]["status"] == "warn"
    assert audit["checks"]["must_have_keyword_gate"]["missing_phrases"] == ["Active Directory"]
    assert audit["checks"]["keyword_density"]["status"] == "pass"
    assert audit["checks"]["impact_first_bullets"]["impact_ratio"] == 0.5
    assert audit["artifacts"] == {}


def test_write_ats_audit_summary_persists_json(tmp_path: Path) -> None:
    summary_path = write_ats_audit_summary(
        tmp_path,
        {
            "generation_status": "success",
            "checks": {"keyword_density": {"status": "pass"}},
            "artifacts": {"pdf_page_count": 1},
        },
    )

    assert summary_path == tmp_path / "ATS Audit Summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["checks"]["keyword_density"]["status"] == "pass"
    assert payload["artifacts"]["pdf_page_count"] == 1


def test_format_ats_audit_statuses_returns_ordered_summary() -> None:
    summary = format_ats_audit_statuses(
        {
            "checks": {
                "relevance": {"status": "pass"},
                "keyword_density": {"status": "pass"},
                "impact_first_bullets": {"status": "warn"},
            }
        }
    )

    assert summary == "relevance=pass, keyword_density=pass, impact_first_bullets=warn"


def test_generate_manual_application_exposes_cover_letter_artifact_paths(monkeypatch, tmp_path: Path) -> None:
    resume_source = tmp_path / "resume_source.md"
    resume_source.write_text("resume source", encoding="utf-8")
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 test")
    resume_docx = tmp_path / "resume.docx"
    resume_docx.write_bytes(b"docx-bytes")
    resume_ats_docx = tmp_path / "resume_ats.docx"
    resume_ats_docx.write_bytes(b"ats-docx-bytes")
    analysis_report = tmp_path / "analysis.md"
    analysis_report.write_text("# Analysis", encoding="utf-8")
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    packaged_cover_letter_txt = package_dir / "Candidate Name Cover Letter.txt"
    packaged_cover_letter_txt.write_text("Dear Hiring Manager,", encoding="utf-8")
    packaged_cover_letter_docx = package_dir / "Candidate Name Cover Letter.docx"
    packaged_cover_letter_docx.write_bytes(b"docx-cover")
    preferred_resume_docx = package_dir / "00 Upload This - Candidate Name Resume.docx"
    preferred_resume_docx.write_bytes(b"preferred-docx")

    monkeypatch.setattr(
        "generate_application_package.generate_tailored_resume_for_job",
        lambda **_kwargs: {
            "status": "success",
            "resume_source": str(resume_source),
            "resume_pdf": str(resume_pdf),
            "resume_docx": str(resume_docx),
            "resume_ats_docx": str(resume_ats_docx),
            "analysis_report": str(analysis_report),
        },
    )
    monkeypatch.setattr(
        "generate_application_package.generate_cover_letter",
        lambda *_args, **_kwargs: "Dear Hiring Manager,\n\nThanks for your time.\n\nSincerely,\nCandidate Name",
    )
    monkeypatch.setattr(
        "generate_application_package.package_application_artifacts",
        lambda **_kwargs: ApplicationPackageArtifacts(
            package_dir=package_dir,
            preferred_resume_docx=preferred_resume_docx,
            resume_pdf=resume_pdf,
            resume_docx=resume_docx,
            resume_ats_docx=resume_ats_docx,
            cover_letter_txt=packaged_cover_letter_txt,
            cover_letter_docx=packaged_cover_letter_docx,
            analysis_report=analysis_report,
            resume_source=resume_source,
            apply_shortcut=None,
            manual_review=None,
        ),
    )
    monkeypatch.setattr(
        "generate_application_package.write_ats_audit_summary",
        lambda _package_dir, _ats_audit: package_dir / "ATS Audit Summary.json",
    )

    result, _cover_letter_text, result_package_dir = generate_manual_application(
        ManualApplicationRequest(
            company="Example Co",
            role="Cloud Engineer",
            job_description="Cloud engineering role.",
            job_url="https://example.com/jobs/1",
            match_score=88,
            package_root=tmp_path / "packs",
        ),
        ApplicationGenerationContext(client=object(), model="gpt-5.4"),
    )

    assert result_package_dir == package_dir
    assert result["package_dir"] == str(package_dir)
    assert result["preferred_resume_docx"] == str(preferred_resume_docx)
    assert result["cover_letter_txt"] == str(packaged_cover_letter_txt)
    assert result["cover_letter_docx"] == str(packaged_cover_letter_docx)