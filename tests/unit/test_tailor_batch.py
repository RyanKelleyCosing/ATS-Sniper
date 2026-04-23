"""Unit tests for the fast inbox-driven tailoring workflow."""

from pathlib import Path
import sys
import types


sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=object))

ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

from tailor_batch import (  # noqa: E402
    DEFAULT_FAST_MODEL,
    InboxJob,
    build_fast_tailor_prompt,
    build_model_name,
    infer_company_and_role,
    load_jobs,
    parse_job_file,
    process_job,
)


def test_parse_job_file_reads_front_matter_metadata(tmp_path: Path) -> None:
    job_file = tmp_path / "medpace.md"
    job_file.write_text(
                "---\r\n"
                "company: Medpace\r\n"
                "role: Systems Engineer (Azure Cloud Engineer)\r\n"
                "job_url: https://example.com/jobs/123\r\n"
                "template_hint: cloud\r\n"
                "preferred_accomplishment_ids:\r\n"
                "  - CLOUD-001\r\n"
                "  - K8S-001\r\n"
                "industry_focus: healthcare infrastructure\r\n"
                "cover_letter_focus: tie cloud reliability to clinical research\r\n"
                "tailoring_notes:\r\n"
                "  - Keep the two promotions visible.\r\n"
                "---\r\n\r\n"
                "Azure cloud engineering job description text.\r\n",
        encoding="utf-8",
    )

    job = parse_job_file(job_file)

    assert job.company == "Medpace"
    assert job.role == "Systems Engineer (Azure Cloud Engineer)"
    assert job.job_url == "https://example.com/jobs/123"
    assert job.template_hint == "cloud"
    assert job.preferred_accomplishment_ids == ("CLOUD-001", "K8S-001")
    assert "Resume tailoring priorities:" in job.job_description


def test_load_jobs_ignores_inbox_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Instructions only.", encoding="utf-8")
    (tmp_path / "Example Co - Cloud Engineer.md").write_text(
        "Job description body.",
        encoding="utf-8",
    )

    jobs = load_jobs(tmp_path, limit=0)

    assert len(jobs) == 1
    assert jobs[0].company == "Example Co"


def test_infer_company_and_role_uses_filename_fallback() -> None:
    company, role = infer_company_and_role(
        Path("Goldstone Partners - Senior Azure Cloud Engineer.txt"),
        {},
        "Plain job description",
    )

    assert company == "Goldstone Partners"
    assert role == "Senior Azure Cloud Engineer"


def test_build_fast_tailor_prompt_includes_one_call_rules() -> None:
    class DummyJob:
        company = "Example Co"
        role = "Cloud Engineer"
        template_hint = "cloud"
        preferred_accomplishment_ids = ("CLOUD-001",)
        industry_focus = "regulated healthcare"
        cover_letter_focus = "connect cloud reliability to the company's mission"
        tailoring_notes = ("Keep the two promotions visible.",)
        job_description = (
            "Azure, Kubernetes, Bicep, infrastructure as code, CI/CD pipelines, "
            "and incident response."
        )

    prompt = build_fast_tailor_prompt(
        DummyJob(),
        "## Experience\n\n### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026",
        [
            (
                "CLOUD-001",
                {
                    "title": "Azure platform",
                    "bullet": "Built Azure services.",
                    "technologies": "Azure, Bicep",
                },
            )
        ],
    )

    assert "single LLM call" not in prompt.lower()
    assert "never fabricate unsupported tools, companies, or metrics" in prompt.lower()
    assert '"cover_letter": "Final cover letter text"' in prompt
    assert "Mention the company's mission or industry once" not in prompt
    assert "cover letter must be story-first" in prompt
    assert "ATS PHRASE TARGETS:" in prompt
    assert "infrastructure as code" in prompt
    assert "incident response" in prompt



def test_build_model_name_prefers_submission_quality_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        "tailor_batch.load_config",
        lambda: {
            "settings": {
                "tailor_batch_model": "",
                "application_package_model": "gpt-5.4",
            }
        },
    )

    assert build_model_name(None) == "gpt-5.4"


def test_build_model_name_uses_default_when_settings_missing(monkeypatch) -> None:
    monkeypatch.setattr("tailor_batch.load_config", lambda: {"settings": {}})

    assert build_model_name(None) == DEFAULT_FAST_MODEL


def test_process_job_creates_fast_application_pack(monkeypatch, tmp_path: Path) -> None:
    resume_source = tmp_path / "resume_source.md"
    resume_source.write_text("---\nrole: Cloud Engineer\n---", encoding="utf-8")
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 test")
    resume_docx = tmp_path / "resume.docx"
    resume_docx.write_bytes(b"docx-bytes")
    resume_ats_docx = tmp_path / "resume_ats.docx"
    resume_ats_docx.write_bytes(b"ats-docx-bytes")

    monkeypatch.setattr("tailor_batch.load_accomplishments", lambda: "")
    monkeypatch.setattr("tailor_batch.parse_accomplishments_to_dict", lambda _text: {})
    monkeypatch.setattr("tailor_batch.load_resume_template", lambda _role, _hint: "## Experience")
    monkeypatch.setattr("tailor_batch.build_prompt_accomplishment_block", lambda _job, _accomplishments: [])
    monkeypatch.setattr(
        "tailor_batch.request_application_content",
        lambda *_args, **_kwargs: {
            "selected_accomplishments": [],
            "rewritten_selected_bullets": {},
            "inferred_accomplishments": {},
            "tailored_skills": {},
            "detected_tech_stack": ["Azure DevOps"],
            "match_reasoning": "Strong overlap.",
            "confidence_score": 92,
            "summary": "Cloud platform engineer summary.",
            "cover_letter": "Dear Hiring Manager,\n\nThanks for your time.\n\nSincerely,\nCandidate Name",
        },
    )
    monkeypatch.setattr("tailor_batch.apply_manual_tailoring_preferences", lambda response, *_args, **_kwargs: response)
    monkeypatch.setattr("tailor_batch.create_tailored_resume_source", lambda **_kwargs: resume_source)
    monkeypatch.setattr(
        "tailor_batch.generate_resume_outputs",
        lambda _resume_source_path, _output_dir: (resume_pdf, resume_docx, resume_ats_docx),
    )
    monkeypatch.setattr("tailor_batch.build_fast_output_dir", lambda _job: tmp_path / "raw-output")
    monkeypatch.setattr("tailor_batch.DEFAULT_FAST_PACKAGE_ROOT", tmp_path / "packs")

    result = process_job(
        object(),
        "gpt-5.4",
        InboxJob(
            source_path=tmp_path / "Example Co - Cloud Engineer.md",
            company="Example Co",
            role="Cloud Engineer",
            job_description="Cloud engineering role.",
            job_url="https://example.com/jobs/1",
            template_hint=None,
            preferred_accomplishment_ids=(),
            industry_focus="",
            cover_letter_focus="",
            tailoring_notes=(),
            match_score=85,
        ),
    )

    assert Path(result["package_dir"]).exists()
    assert Path(result["preferred_resume_docx"]).exists()
    assert Path(result["cover_letter_txt"]).exists()
    assert Path(result["cover_letter_docx"]).exists()
    assert Path(result["resume_pdf"]).exists()
    assert Path(result["resume_ats_docx"]).exists()
    assert Path(result["raw_cover_letter_txt"]).exists()