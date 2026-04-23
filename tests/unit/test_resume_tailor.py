"""Unit tests for cover-letter guidance prompt building."""

from pathlib import Path
import sys
import types


sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=object))

ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

from resume_tailor import (  # noqa: E402
    CoverLetterGuidance,
    analyze_job_match,
    build_cover_letter_prompt,
    classify_job_for_screening,
    extract_primary_experience_facts,
)


def test_extract_primary_experience_facts_prefers_first_role_bullets() -> None:
    resume = """---
summary: Example
---

## Experience

### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026
- Implemented Bicep templates, cutting provisioning time by 90%. Technologies: Bicep, ARM Templates
- Reduced monthly Azure spend by 22% through rightsizing. Technologies: Azure Cost Management, Azure Advisor

### Silco | Database Analyst (Contract) | Jan 2021 - May 2021
- Built ETL pipelines. Technologies: SQL Server, SSIS
"""

    facts = extract_primary_experience_facts(resume)

    assert facts == [
        "Implemented Bicep templates, cutting provisioning time by 90%. Technologies: Bicep, ARM Templates",
        "Reduced monthly Azure spend by 22% through rightsizing. Technologies: Azure Cost Management, Azure Advisor",
    ]


def test_extract_primary_experience_facts_accepts_work_experience_heading() -> None:
    resume = """---
summary: Example
---

## Work Experience

### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026
- Implemented Bicep templates, cutting provisioning time by 90%. Technologies: Bicep, ARM Templates
"""

    facts = extract_primary_experience_facts(resume)

    assert facts == [
        "Implemented Bicep templates, cutting provisioning time by 90%. Technologies: Bicep, ARM Templates",
    ]


def test_build_cover_letter_prompt_includes_guidance() -> None:
    prompt = build_cover_letter_prompt(
        'Azure engineering role in a regulated healthcare environment.',
        """---
summary: Example
---

## Experience

### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026
- Implemented Bicep templates, cutting provisioning time by 90%. Technologies: Bicep, ARM Templates
- Reduced monthly Azure spend by 22% through rightsizing. Technologies: Azure Cost Management, Azure Advisor
""",
        'Example Health',
        'Azure Cloud Engineer',
        CoverLetterGuidance(
            industry_focus='healthcare impact and compliance-sensitive delivery',
            company_context='connect cloud reliability to clinical research platforms',
            emphasis_points=(
                'Highlight Azure monitoring and DR wins.',
                'Keep the two promotions visible.',
            ),
        ),
    )

    assert 'healthcare impact and compliance-sensitive delivery' in prompt
    assert 'connect cloud reliability to clinical research platforms' in prompt
    assert 'Highlight Azure monitoring and DR wins.' in prompt
    assert 'Keep the 5-year Resurgent tenure and two promotions visible' in prompt
    assert 'FACTS TO CITE FROM THE FINAL RESUME VERSION' in prompt
    assert 'ATS PHRASE TARGETS:' in prompt
    assert 'Azure Cloud Engineer' in prompt
    assert 'Reduced monthly Azure spend by 22% through rightsizing.' in prompt
    assert "Avoid filler phrases like 'align closely with the demands of your role'" in prompt
    assert 'Reuse at least' in prompt
    assert 'Mention only the target company name, exactly as provided: Example Health.' in prompt
    assert 'Do not add phone numbers, email addresses, LinkedIn URLs, GitHub URLs, markdown links, or any contact block after the sign-off.' in prompt


def test_classify_job_for_screening_includes_llm_usage(monkeypatch) -> None:
    usage = types.SimpleNamespace(
        prompt_tokens=700,
        completion_tokens=80,
        total_tokens=780,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=0),
    )
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content='{"category":"SECURITY","reason":"Target lane","confidence":0.91}'
                )
            )
        ],
        usage=usage,
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = classify_job_for_screening(
        client=object(),
        job={"title": "Cloud Security Engineer", "company": "Acme", "source": "greenhouse_api"},
        job_desc="Azure security engineering role.",
        include_usage=True,
    )

    assert result["category"] == "SECURITY"
    assert result["llm_usage"]["prompt_tokens"] == 700
    assert result["llm_usage"]["estimated_cost_usd"] == 0.000153


def test_classify_job_for_screening_accepts_adjacent_tech_roles(monkeypatch) -> None:
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content='{"category":"ADJACENT_TECH","reason":"Hands-on implementation and automation role.","confidence":0.89}'
                )
            )
        ]
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = classify_job_for_screening(
        client=object(),
        job={"title": "Implementation Engineer", "company": "Example", "source": "web_discovery"},
        job_desc="Hands-on implementation role covering APIs, SQL, deployment workflows, troubleshooting, and PowerShell automation.",
    )

    assert result["category"] == "ADJACENT_TECH"
    assert result["should_skip"] is False


def test_analyze_job_match_includes_llm_usage(monkeypatch) -> None:
    usage = types.SimpleNamespace(
        prompt_tokens=2100,
        completion_tokens=300,
        total_tokens=2400,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=0),
    )
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"match_score":82,"matching_skills":["Azure"],'
                        '"missing_skills":["Okta"],"gap_analysis":"Good fit.",'
                        '"suggested_achievements":["Automated Azure deployments."]}'
                    )
                )
            )
        ],
        usage=usage,
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = analyze_job_match(
        client=object(),
        job_desc="Azure platform engineering and IAM integrations.",
        resume="Azure DevOps and infrastructure experience.",
        include_usage=True,
    )

    assert result["match_score"] == 82
    assert result["llm_usage"]["completion_tokens"] == 300
    assert result["llm_usage"]["estimated_cost_usd"] == 0.000495


def test_analyze_job_match_prompt_does_not_flat_cap_hands_on_senior_titles(monkeypatch) -> None:
    captured_prompt: dict[str, str] = {}

    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"match_score":72,"matching_skills":["AWS"],'
                        '"missing_skills":["Terraform"],"gap_analysis":"Good fit.",'
                        '"suggested_achievements":["Automated AWS deployments."]}'
                    )
                )
            )
        ]
    )

    def fake_create_chat_completion(*_args, **kwargs):
        captured_prompt["prompt"] = kwargs["messages"][0]["content"]
        return response

    monkeypatch.setattr("resume_tailor.create_chat_completion", fake_create_chat_completion)

    result = analyze_job_match(
        client=object(),
        job_desc="Senior DevOps role with AWS, CI/CD, Kubernetes, and incident response.",
        resume="Azure DevOps and infrastructure experience.",
        job_title="Senior DevOps Engineer",
    )

    assert result["match_score"] == 76
    assert result["exact_fit_title_match"] is True
    assert result["exact_fit_bonus"] == 4
    assert "Do not default to 30 just because a title sounds senior." in captured_prompt["prompt"]
    assert "CAP the match_score at a MAXIMUM of 30" not in captured_prompt["prompt"]


def test_analyze_job_match_caps_people_management_owner_roles(monkeypatch) -> None:
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"match_score":82,"matching_skills":["AWS"],'
                        '"missing_skills":["People leadership"],"gap_analysis":"Stretch role.",'
                        '"suggested_achievements":["Automated AWS deployments."]}'
                    )
                )
            )
        ]
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = analyze_job_match(
        client=object(),
        job_desc=(
            "Own a multi-year AWS infrastructure roadmap and lead and grow a distributed team of "
            "cloud, platform, and SRE engineers."
        ),
        resume="Azure DevOps and infrastructure experience.",
        job_title="Public Cloud Platform Owner",
    )

    assert result["match_score"] == 35


def test_analyze_job_match_boosts_exact_fit_titles_with_strong_overlap(monkeypatch) -> None:
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"match_score":75,"matching_skills":["Azure"],'
                        '"missing_skills":["Terraform"],"gap_analysis":"Strong fit.",'
                        '"suggested_achievements":["Implemented application security controls."]}'
                    )
                )
            )
        ]
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = analyze_job_match(
        client=object(),
        job_desc=(
            "Application security engineering role covering application security, vulnerability "
            "management, security scanning, incident response, Kubernetes, and CI/CD pipelines."
        ),
        resume="Azure DevOps and infrastructure experience.",
        job_title="Application Security Engineer",
    )

    assert result["match_score"] > 75
    assert result["exact_fit_lane"] == "SECURITY"
    assert result["exact_fit_title_match"] is True
    assert result["exact_fit_bonus"] > 0


def test_analyze_job_match_boosts_adjacent_tech_titles_with_strong_overlap(monkeypatch) -> None:
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"match_score":82,"matching_skills":["Python","PowerShell"],'
                        '"missing_skills":["Specific vendor SDK"],"gap_analysis":"Strong adjacent fit.",'
                        '"suggested_achievements":["Automated customer environment workflows."]}'
                    )
                )
            )
        ]
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = analyze_job_match(
        client=object(),
        job_desc=(
            "Hands-on implementation engineer role focused on API integrations, SQL troubleshooting, "
            "PowerShell automation, deployment workflows, JSON configuration, and technical support for "
            "customer environments."
        ),
        resume="Automation, support, and delivery experience.",
        job_title="Implementation Engineer",
    )

    assert result["adjacent_fit_lane"] == "ADJACENT_TECH"
    assert result["adjacent_fit_title_match"] is True
    assert result["adjacent_fit_bonus"] > 0
    assert result["match_score"] >= 90


def test_analyze_job_match_keeps_management_caps_for_exact_fit_roles(monkeypatch) -> None:
    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"match_score":88,"matching_skills":["SIEM"],'
                        '"missing_skills":["People leadership"],"gap_analysis":"Stretch role.",'
                        '"suggested_achievements":["Led security operations improvements."]}'
                    )
                )
            )
        ]
    )
    monkeypatch.setattr("resume_tailor.create_chat_completion", lambda *_args, **_kwargs: response)

    result = analyze_job_match(
        client=object(),
        job_desc=(
            "Manage a team of security engineers, own the multi year roadmap, and lead security "
            "operations, incident response, vulnerability management, and SIEM programs."
        ),
        resume="Security operations and Azure experience.",
        job_title="Security Operations Engineer",
    )

    assert result["exact_fit_title_match"] is True
    assert result["match_score"] <= 35