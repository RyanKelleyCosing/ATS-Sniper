"""Unit tests for tailored resume cleanup helpers."""

from pathlib import Path
import sys
import types

import yaml


sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=object))

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from generate_tailored_resume import (
    analyze_job_and_select_accomplishments,
    apply_manual_tailoring_preferences,
    build_bullet_variants,
    build_accomplishment_bullet_text,
    build_resurgent_promotion_bullet_with_options,
    build_role_aware_skills,
    build_role_aware_summary,
    build_resurgent_promotion_bullet,
    compact_inferred_bullet,
    create_tailored_resume_source,
    detect_role_profile,
    evaluate_action_context_result_bullets,
    evaluate_company_keyword_mentions,
    evaluate_impact_first_bullet_structure,
    evaluate_keyword_bullet_density,
    evaluate_keyword_role_spread,
    evaluate_must_have_keyword_coverage,
    evaluate_page_length,
    evaluate_resume_relevance,
    evaluate_standard_section_coverage,
    extract_exact_jd_phrase_targets,
    load_resume_template,
    make_bullet_signature,
    normalize_bullet_text,
    normalize_tailored_skills,
    organize_primary_section_entries,
    parse_accomplishments_to_dict,
    prefer_outcome_led_variant,
    prefer_scope_led_variant,
    promote_support_title,
    resolve_resume_template_file,
    rewrite_selected_bullet,
    select_prompt_accomplishments,
)
from params.experience_dates import build_experience_header_line


def test_build_experience_header_line_normalizes_known_resume_dates() -> None:
    assert build_experience_header_line(
        "### Resurgent Capital Services | Progressive Roles | Aug 2021 - Mar 2026"
    ) == "### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026"
    assert build_experience_header_line(
        "### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026"
    ) == "### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026"
    assert build_experience_header_line(
        "### Silco | Database Analyst (Contract) | Aug 2020 - May 2021"
    ) == "### Silco | Database Analyst (Contract) | Jan 2021 - May 2021"
    assert build_experience_header_line(
        "### RIBBIT.AI | Database Analyst (Contract) | Feb 2020 - Aug 2020"
    ) == "### RIBBIT.AI | Database Analyst (Contract) | Aug 2020 - Jan 2021"


def test_build_resurgent_promotion_bullet_includes_actual_role_dates() -> None:
    bullet = build_resurgent_promotion_bullet("Azure Cloud Engineer I", "Azure, AKS, monitoring")

    assert "IT Analyst (Aug 2021 - Sep 2022)" in bullet
    assert "Operations Analyst (Aug 2022 - Jun 2024)" in bullet
    assert "Software Support Analyst II (Jun 2024 - Mar 2026)" in bullet


def test_resolve_resume_template_file_honors_explicit_hint() -> None:
    assert resolve_resume_template_file('Completely Custom Role', 'cloud') == 'resume_cloud.md'
    assert resolve_resume_template_file('Another Role', 'resume_infrastructure.md') == 'resume_infrastructure.md'


def test_resolve_resume_template_file_routes_adjacent_roles_to_adjacent_template() -> None:
    assert resolve_resume_template_file('Implementation Engineer') == 'resume_adjacent.md'
    assert resolve_resume_template_file('System Software Support Engineer') == 'resume_adjacent.md'


def test_resolve_resume_template_file_routes_database_roles_to_system_analyst_template() -> None:
    assert resolve_resume_template_file('SQL DBA') == 'resume_system_analyst.md'
    assert resolve_resume_template_file('Database Administrator') == 'resume_system_analyst.md'


def test_apply_manual_tailoring_preferences_adds_missing_bullets() -> None:
    analysis = {
        'selected_accomplishments': ['K8S-001'],
        'rewritten_selected_bullets': {
            'K8S-001': 'Designed AKS platform. Technologies: Kubernetes, AKS'
        },
    }
    accomplishments = {
        'CLOUD-001': {
            'bullet': 'Implemented Azure landing zone guardrails.',
            'technologies': 'Azure, Bicep, RBAC',
        },
        'K8S-001': {
            'bullet': 'Existing bullet',
            'technologies': 'Kubernetes, AKS',
        },
    }

    updated = apply_manual_tailoring_preferences(
        analysis,
        accomplishments,
        ('CLOUD-001', 'K8S-001'),
    )

    assert updated['selected_accomplishments'] == ['CLOUD-001', 'K8S-001']
    assert updated['rewritten_selected_bullets']['CLOUD-001'] == (
        'Implemented Azure landing zone guardrails. Technologies: Azure, Bicep, RBAC'
    )


def test_build_accomplishment_bullet_text_appends_technologies() -> None:
    assert build_accomplishment_bullet_text(
        {
            'bullet': 'Reduced deployment time by 90%.',
            'technologies': 'Azure DevOps, YAML Pipelines',
        }
    ) == 'Reduced deployment time by 90%. Technologies: Azure DevOps, YAML Pipelines'


def test_load_resume_template_uses_infrastructure_template_for_security_titles() -> None:
    security_template = load_resume_template("Information Security Analyst I")
    identity_template = load_resume_template("Identity & Access Management Analyst 2")

    assert "role: Infrastructure Engineer" in security_template
    assert "INFRA-001" in security_template
    assert "role: Infrastructure Engineer" in identity_template
    assert "BACKUP-001" in identity_template


def test_load_resume_template_uses_adjacent_template_for_implementation_titles() -> None:
    adjacent_template = load_resume_template("Implementation Engineer")

    assert "role: Implementation & Support Engineer" in adjacent_template
    assert "IMPLEMENTATION DELIVERY:" in adjacent_template


def test_normalize_tailored_skills_keeps_whole_words_and_role_labels() -> None:
    raw_skills = {
        "CI/CD & AUTOMATION": ["Azure DevOps", "Ansible*", "Git", "Git", "Bash", "Terraform"],
        "CLOUD & INFRASTRUCTURE": ["AWS", "Kubernetes", "Docker", "Linux", "Azure", "GCP"],
        "MONITORING & OPS": ["Prometheus", "Grafana", "Azure Monitor", "Log Analytics", "Alerts"],
        "SCRIPTING & DATABASES": ["Python", "PowerShell", "SQL Server", "T-SQL", "SSIS"],
        "EXTRA CATEGORY": ["Should", "Be", "Dropped"],
    }

    normalized = normalize_tailored_skills(raw_skills, role="Cloud Engineer II")

    assert list(normalized) == [
        "CI/CD Automation",
        "Cloud Architecture",
        "Operations",
        "Scripting & Data",
    ]
    assert normalized["CI/CD Automation"] == ["Azure DevOps", "Ansible", "Git", "Bash", "Terraform"]
    assert all("INFRASTRUCTU" not in key for key in normalized)
    assert len(normalized) == 4


def test_normalize_tailored_skills_uses_adjacent_lane_labels() -> None:
    raw_skills = {
        "CI/CD & AUTOMATION": ["Azure DevOps", "GitHub Actions"],
        "CLOUD & INFRASTRUCTURE": ["Azure", "App Services"],
        "MONITORING & OPS": ["Incident Triage", "Troubleshooting"],
        "SCRIPTING & DATABASES": ["Python", "PowerShell", "SQL Server"],
    }

    normalized = normalize_tailored_skills(raw_skills, role="Implementation Engineer")

    assert list(normalized) == [
        "Implementation Delivery",
        "Implementation Platforms",
        "Support Operations",
        "Automation & Integrations",
    ]


def test_compact_inferred_bullet_shortens_filler_and_caps_technologies() -> None:
    bullet = (
        "Designed and implemented a data migration strategy for transitioning legacy databases, "
        "ensuring 100% data integrity during the process. Technologies: SQL Server, Data Migration, "
        "SSIS, Azure Data Factory"
    )

    compacted = compact_inferred_bullet(bullet)

    assert compacted == (
        "Led a data migration strategy of legacy databases, with 100% data integrity. "
        "Technologies: SQL Server, Data Migration, SSIS"
    )


def test_normalize_bullet_text_polishes_common_ai_phrasing() -> None:
    awkward_security_bullet = (
        "Built automated security scanning for CI/CD pipelines, with compliance with "
        "SOC2 and FedRAMP standards, reducing vulnerabilities by 70%."
    )
    awkward_scope_bullet = (
        "For incident management, built automated runbooks, reducing Mean Time to Resolution "
        "by 40% across 10 critical services."
    )
    awkward_monitoring_bullet = (
        "Built centralized monitoring dashboards with Azure Monitor, improving real-time "
        "visibility and cutting mean time to detection by 85%."
    )
    awkward_provisioning_bullet = (
        "Automated infrastructure provisioning with Bicep templates, reducing setup time by "
        "90% and enabling self-service deployment for development teams."
    )
    awkward_cost_bullet = (
        "Reduced idle infrastructure costs by 35% while with peak-hour processing efficiency by "
        "building cost-aware autoscaling rules for containerized workloads."
    )

    assert normalize_bullet_text(awkward_security_bullet) == (
        "Implemented automated security scanning in CI/CD pipelines, supporting SOC2 and "
        "FedRAMP standards and reducing vulnerabilities by 70%"
    )
    assert normalize_bullet_text(awkward_scope_bullet) == (
        "Implemented automated runbooks for incident management, reducing Mean Time to Resolution "
        "by 40% across 10 critical services"
    )
    assert normalize_bullet_text(awkward_monitoring_bullet) == (
        "Built centralized monitoring dashboards with Azure Monitor, improving real-time "
        "visibility and reducing mean time to detection by 85%"
    )
    assert normalize_bullet_text(awkward_provisioning_bullet) == (
        "Automated infrastructure provisioning with Bicep templates, cutting setup time by 90% "
        "while enabling self-service deployment for development teams"
    )
    assert normalize_bullet_text(awkward_cost_bullet) == (
        "Reduced idle infrastructure costs by 35% while maintaining peak-hour processing efficiency "
        "by building cost-aware autoscaling rules for containerized workloads"
    )


def test_build_role_aware_summary_uses_title_specific_openings() -> None:
    security_summary = build_role_aware_summary(
        role="Infrastructure Security Engineer",
        job_description="Security scanning, Kubernetes hardening, and Azure policy enforcement.",
        detected_tech_stack=["Azure", "Kubernetes", "SonarQube"],
        bullet_pairs=[("Hardened delivery pipelines", "Azure, Kubernetes, SonarQube")],
    )
    sre_summary = build_role_aware_summary(
        role="Site Reliability Engineer II",
        job_description="Prometheus, Grafana, Kubernetes, and incident response.",
        detected_tech_stack=["Prometheus", "Grafana", "Kubernetes"],
        bullet_pairs=[("Reduced MTTR", "Prometheus, Grafana, Kubernetes")],
    )

    assert security_summary.startswith("Results-driven cloud security engineer")
    assert sre_summary.startswith("Site Reliability Engineer with 5+ years")
    assert "infrastructure professional" not in security_summary.lower()
    assert "infrastructure professional" not in sre_summary.lower()


def test_build_role_aware_summary_prioritizes_cloud_title_over_security_terms() -> None:
    cloud_summary = build_role_aware_summary(
        role="Cloud Engineer II",
        job_description="Azure, Kubernetes, RBAC, security controls, and cost optimization.",
        detected_tech_stack=["Azure", "Kubernetes", "Bicep"],
        bullet_pairs=[("Built Azure landing zones", "Azure, Kubernetes, Bicep")],
    )

    assert cloud_summary.startswith("Cloud engineer with 5+ years")
    assert "cloud security engineer" not in cloud_summary.lower()


def test_detect_role_profile_marks_adjacent_technical_roles() -> None:
    profile = detect_role_profile(
        "System Software Support Engineer",
        "Hands-on implementation, API integrations, SQL troubleshooting, deployment workflows, and technical support.",
    )

    assert profile == "adjacent"


def test_build_role_aware_summary_uses_adjacent_profile_language() -> None:
    summary = build_role_aware_summary(
        role="Implementation Engineer",
        job_description=(
            "Hands-on implementation role covering API integrations, SQL troubleshooting, "
            "workflow automation, deployments, and technical support."
        ),
        detected_tech_stack=["Python", "SQL", "PowerShell"],
        bullet_pairs=[("Improved implementation workflows", "Python, SQL, PowerShell")],
    )

    assert summary.startswith("Technical support and implementation engineer with 5+ years")
    assert "devops engineer" not in summary.lower()


def test_build_role_aware_summary_uses_dba_profile_language() -> None:
    summary = build_role_aware_summary(
        role="SQL DBA",
        job_description=(
            "SQL Server, Azure SQL migrations, CI/CD pipelines, Terraform, insurance reporting, "
            "stored procedures, ETL, and database reliability."
        ),
        detected_tech_stack=["SQL Server", "Azure SQL Database", "T-SQL", "Terraform"],
        bullet_pairs=[
            (
                "Optimized SQL Server stored procedures, reducing report latency by 65%",
                "SQL Server, T-SQL, Azure SQL Database",
            )
        ],
    )

    assert summary.startswith("SQL DBA with 5+ years")
    assert "Azure SQL Database" in summary
    assert "devops engineer" not in summary.lower()


def test_extract_exact_jd_phrase_targets_preserves_multiword_terms() -> None:
    phrases = extract_exact_jd_phrase_targets(
        role="Identity and Access Management Engineer",
        job_description=(
            "Own identity and access management, incident response, infrastructure as code, "
            "and CI/CD pipelines across Azure environments."
        ),
        detected_tech_stack=["Azure", "CI/CD pipelines", "PowerShell"],
    )

    lowered_phrases = {phrase.casefold() for phrase in phrases}

    assert "identity and access management" in lowered_phrases
    assert "incident response" in lowered_phrases
    assert "infrastructure as code" in lowered_phrases
    assert "ci/cd pipelines" in lowered_phrases


def test_extract_exact_jd_phrase_targets_filters_unsupported_collins_network_terms() -> None:
    phrases = extract_exact_jd_phrase_targets(
        role="Cyber Security Engineer I (Onsite)",
        job_description=(
            "Cyber Security Engineer I (Onsite) responsible for security operations, incident response, "
            "Windows Server administration, Active Directory, Broadcom vSphere, Enterprise DNS, "
            "load balancing, TCP/IP, and DHCP troubleshooting."
        ),
        detected_tech_stack=[
            "Windows Server",
            "Active Directory",
            "incident response",
            "security operations",
            "Broadcom vSphere",
            "Enterprise DNS",
            "TCP/IP",
            "DHCP",
        ],
        support_text=(
            "Cyber Security Engineer with Windows Server administration, Active Directory, "
            "security operations, incident response, Azure Monitor, and Azure DevOps experience."
        ),
    )

    lowered_phrases = {phrase.casefold() for phrase in phrases}

    assert "cyber security engineer" in lowered_phrases
    assert "windows server" in lowered_phrases
    assert "active directory" in lowered_phrases
    assert "incident response" in lowered_phrases
    assert "broadcom vsphere" not in lowered_phrases
    assert "enterprise dns" not in lowered_phrases
    assert "tcp/ip" not in lowered_phrases
    assert "dhcp" not in lowered_phrases


def test_analyze_job_and_select_accomplishments_prompt_prefers_exact_jd_language(monkeypatch) -> None:
    captured_prompt: dict[str, str] = {}

    response = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=(
                        '{"selected_accomplishments":["SEC-001"],'
                        '"rewritten_selected_bullets":{"SEC-001":"Implemented security operations controls. Technologies: Azure, PowerShell"},'
                        '"promotion_bullet_options":["Promoted twice into security operations leadership."],'
                        '"inferred_accomplishments":{"Resurgent Capital Services":[],"Silco":[],"RIBBIT.AI":[]},'
                        '"rewritten_summary":"Security operations engineer with 5+ years supporting incident response and compliance automation. Builds CI/CD guardrails and Azure security controls for enterprise platforms.",'
                        '"tailored_skills":{"Security Ops":["Azure","PowerShell","RBAC","Incident Response","CI/CD"]},'
                        '"match_reasoning":"Selected based on overlap.",'
                        '"detected_tech_stack":["Azure","CI/CD"],'
                        '"industry_focus":"tech",'
                        '"confidence_score":82}'
                    )
                )
            )
        ]
    )

    def fake_create_chat_completion(*_args, **kwargs):
        captured_prompt["prompt"] = kwargs["messages"][0]["content"]
        return response

    monkeypatch.setattr("generate_tailored_resume.create_chat_completion", fake_create_chat_completion)

    analyze_job_and_select_accomplishments(
        client=object(),
        job_description="Security operations role covering incident response, CI/CD pipelines, and infrastructure as code.",
        accomplishments={
            "SEC-001": {
                "title": "Security Controls",
                "bullet": "Implemented security controls across Azure services.",
                "technologies": "Azure, PowerShell",
                "roles": ["security"],
            }
        },
        company="Example Co",
        role="Security Operations Engineer",
    )

    assert "ATS/semantic matcher" in captured_prompt["prompt"]
    assert "Prefer exact job-description vocabulary over synonyms" in captured_prompt["prompt"]
    assert "ATS PHRASE TARGETS:" in captured_prompt["prompt"]
    assert "incident response" in captured_prompt["prompt"]
    assert "CI/CD pipelines" in captured_prompt["prompt"]


def test_create_tailored_resume_source_prefers_rewritten_summary(tmp_path: Path) -> None:
    base_template = """---
name: Test Candidate
role: DevOps Engineer
contact: {}
skills: {}
certifications: []
education: Test University
summary: Base summary
---

## Experience

### Example Co | Software Support Analyst II | 2021 - Present
- CICD-001
"""
    accomplishments = {
        "CICD-001": {
            "bullet": "Implemented CI/CD automation reducing deployment time by 80%",
            "technologies": "Azure DevOps, YAML Pipelines, PowerShell",
            "roles": ["devops"],
        },
    }
    analysis = {
        "selected_accomplishments": ["CICD-001"],
        "rewritten_summary": (
            "Identity and access management engineer with 5+ years supporting SSO, federation, and access reviews across Azure environments. "
            "Delivers incident response, RBAC, and provisioning automation using PowerShell and Microsoft Entra ID."
        ),
        "inferred_accomplishments": {},
        "detected_tech_stack": ["Azure DevOps", "CI/CD", "Azure"],
        "tailored_skills": {},
    }

    output_path = create_tailored_resume_source(
        company="Example",
        role="Identity and Access Management Engineer",
        analysis=analysis,
        base_template=base_template,
        accomplishments=accomplishments,
        job_description="SSO, federation, access reviews, RBAC, and provisioning automation.",
        output_dir=tmp_path,
    )

    rendered = output_path.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(rendered.split("---", maxsplit=2)[1])

    assert (
        frontmatter["summary"]
        == "Identity and access management engineer with 5+ years supporting SSO, federation, and access reviews across Azure environments. Delivers incident response, RBAC, and provisioning automation using PowerShell and Microsoft Entra ID."
    )


def test_build_role_aware_summary_uses_iam_friendly_opening() -> None:
    iam_summary = build_role_aware_summary(
        role="Identity & Access Management Analyst 2",
        job_description=(
            "Identity and access management, Active Directory, Microsoft Entra ID, "
            "RBAC, auditing, and PowerShell automation."
        ),
        detected_tech_stack=["Active Directory", "Microsoft Entra ID", "RBAC", "PowerShell"],
        bullet_pairs=[
            ("Managed access changes", "Active Directory, Microsoft Entra ID, RBAC"),
            ("Automated security workflows", "PowerShell, Incident Response"),
        ],
    )

    assert iam_summary.startswith("Security-focused IAM analyst with 5+ years")
    assert "Active Directory" in iam_summary
    assert "RBAC" in iam_summary
    assert "cloud security engineer" not in iam_summary.lower()


def test_build_role_aware_summary_includes_company_keyword_when_required() -> None:
    gitlab_summary = build_role_aware_summary(
        role="Infrastructure Security Engineer",
        job_description="GitLab CI, cloud security, and pipeline hardening.",
        detected_tech_stack=["GitLab CI", "Kubernetes", "Security Scanning"],
        bullet_pairs=[("Implemented GitLab pipeline security", "GitLab CI, Kubernetes")],
        company="GitLab",
    )

    assert gitlab_summary.count("GitLab") >= 2
    assert "aligned to" not in gitlab_summary.lower()
    assert "targets gitlab priorities" not in gitlab_summary.lower()


def test_build_role_aware_skills_only_uses_bullet_technologies() -> None:
    bullet_pairs = [
        ("Automated Linux deployments", "Azure DevOps, Ansible, Linux"),
        ("Hardened AKS workloads", "Azure, Kubernetes, SonarQube"),
        ("Improved monitoring", "Prometheus, Grafana, Azure Monitor"),
    ]

    skills = build_role_aware_skills(
        role="Infrastructure Security Engineer",
        job_description="Security scanning for Kubernetes workloads on Azure.",
        detected_tech_stack=["Azure", "Kubernetes", "Security Scanning"],
        bullet_pairs=bullet_pairs,
        fallback_skills={"Cloud & Infrastructure": ["Terraform", "AWS"]},
    )

    flattened_skills = {skill for values in skills.values() for skill in values}

    assert "Secure Cloud" in skills
    assert "Terraform" not in flattened_skills
    assert flattened_skills.issubset(
        {
            "Azure DevOps",
            "Ansible",
            "Linux",
            "Azure",
            "Kubernetes",
            "SonarQube",
            "Prometheus",
            "Grafana",
            "Azure Monitor",
        }
    )


def test_build_role_aware_skills_preserves_exact_target_skill_from_template() -> None:
    skills = build_role_aware_skills(
        role="Cyber Security Engineer I (Onsite)",
        job_description="Windows Server administration, load balancing, and incident response.",
        detected_tech_stack=["Windows Server", "load balancing", "incident response"],
        bullet_pairs=[
            ("Supported incident response and remediation workflows", "Windows Server, Microsoft Defender"),
        ],
        fallback_skills={"Security Ops": ["Windows Server", "Microsoft Defender"]},
        template_skills={"Networking": ["Load Balancing", "DNS"]},
        must_have_keyword_targets=["Cyber Security Engineer", "Windows Server", "load balancing"],
    )

    flattened_skills = {skill for values in skills.values() for skill in values}

    assert "Load Balancing" in flattened_skills


def test_build_role_aware_skills_promotes_identity_tools_to_security_ops() -> None:
    skills = build_role_aware_skills(
        role="IT Security Analyst",
        job_description="Active Directory, Microsoft Entra ID, RBAC, incident response, and PowerShell.",
        detected_tech_stack=["Active Directory", "Microsoft Entra ID", "RBAC", "PowerShell"],
        bullet_pairs=[
            ("Managed identity changes", "Active Directory, Microsoft Entra ID, RBAC"),
            ("Automated access workflows", "PowerShell, Incident Response"),
        ],
        fallback_skills={},
    )

    assert "Security Ops" in skills
    assert "Active Directory" in skills["Security Ops"]
    assert "Microsoft Entra ID" in skills["Security Ops"]
    assert "RBAC" in skills["Security Ops"]


def test_build_role_aware_skills_collapses_same_family_sql_labels() -> None:
    bullet_pairs = [
        (
            "Architected CI/CD pipeline automation",
            "Azure DevOps, SQL Server Deployment, YAML Pipelines",
        ),
        (
            "Established centralized monitoring dashboards",
            "Azure Monitor, SQL Server Performance Monitoring, Application Insights",
        ),
        (
            "Achieved 99.99% uptime across production services",
            "Azure Monitor, SQL Server Always On, Application Insights",
        ),
        (
            "Built automated ETL pipelines processing 40,000 records daily",
            "SQL Server, PowerShell, SSIS",
        ),
    ]

    skills = build_role_aware_skills(
        role="Cloud Infrastructure Engineer",
        job_description="Azure, CI/CD, monitoring, and automation for cloud platforms.",
        detected_tech_stack=["Azure", "CI/CD", "Monitoring"],
        bullet_pairs=bullet_pairs,
        fallback_skills={},
    )

    scripting_skills = skills.get("Scripting & Data", [])

    assert "SQL" not in scripting_skills
    assert "SQL Server Deployment" not in scripting_skills
    assert "SQL Server Performance Monitoring" not in scripting_skills
    assert sum(1 for skill in scripting_skills if skill.startswith("SQL Server")) == 1
    assert "PowerShell" in scripting_skills


def test_build_role_aware_skills_strips_punctuation_and_drops_generic_fillers() -> None:
    bullet_pairs = [
        (
            "Built monitoring dashboards",
            "Azure Monitor, Application Insights., KQL Queries",
        ),
        (
            "Automated security checks",
            "Security Scanning., Compliance Tools, PowerShell.",
        ),
    ]

    skills = build_role_aware_skills(
        role="Infrastructure Security Engineer",
        job_description="Azure monitoring and security scanning.",
        detected_tech_stack=["Azure Monitor", "Security Scanning"],
        bullet_pairs=bullet_pairs,
        fallback_skills={},
    )

    flattened_skills = {skill for values in skills.values() for skill in values}

    assert "Application Insights" in flattened_skills
    assert "KQL" in flattened_skills
    assert "PowerShell" in flattened_skills
    assert "Compliance Tools" not in flattened_skills
    assert all(not skill.endswith(".") for skill in flattened_skills)


def test_organize_primary_section_entries_spreads_themes_and_dedupes_monitoring() -> None:
    entries = [
        (
            "Promoted twice while rapidly expanding from IT operations into CI/CD automation, release engineering, and self-service platform delivery",
            [],
        ),
        (
            "Architected CI/CD pipeline automation using Azure DevOps YAML, cutting deployment time from over 2 hours to under 15 minutes",
            ["Azure DevOps", "YAML Pipelines", "PowerShell"],
        ),
        (
            "Automated infrastructure provisioning with Bicep templates, cutting setup time by 90% while enabling self-service deployment for development teams",
            ["Azure DevOps", "Bicep"],
        ),
        (
            "Implemented automated security scanning in CI/CD pipelines, supporting SOC2 and FedRAMP standards and reducing vulnerabilities by 70%",
            ["Security Scanning", "Azure DevOps"],
        ),
        (
            "Built centralized monitoring dashboards with Azure Monitor, improving real-time visibility and reducing mean time to detection by 85%",
            ["Azure Monitor", "Application Insights"],
        ),
        (
            "Built comprehensive monitoring and automated alerting, achieving a 99.99% uptime SLA across 15 production applications",
            ["Azure Monitor", "Application Insights", "Log Analytics"],
        ),
        (
            "Optimized Azure costs by 22% through rightsizing and automated schedules for non-production environments",
            ["Azure Cost Management", "PowerShell"],
        ),
        (
            "Implemented automated runbooks for incident management, reducing Mean Time to Resolution by 40% across critical applications",
            ["Azure Automation", "PowerShell"],
        ),
        (
            "Engineered an AI document processing application using Azure AI Services, cutting processing time by 80% for over 50,000 documents monthly",
            ["Python", "Azure AI", "Azure Functions"],
        ),
    ]

    organized_entries = organize_primary_section_entries(
        entries,
        role="DevOps Engineer",
        job_description="Azure DevOps, Kubernetes, security scanning, monitoring, cost optimization, and incident response.",
        detected_tech_stack=["Azure DevOps", "Kubernetes", "Security Scanning", "Azure Monitor"],
        seed_text="CBTS|DevOps Engineer|theme-spread",
    )

    organized_texts = [text for text, _ in organized_entries]

    assert organized_texts[0].startswith("Promoted twice")
    assert sum(
        1
        for text in organized_texts
        if "monitor" in text.lower() or "uptime" in text.lower()
    ) == 1
    assert any("CI/CD pipeline automation" in text for text in organized_texts[1:3])
    assert any("security scanning" in text.lower() for text in organized_texts)
    assert any("Azure costs" in text for text in organized_texts)
    assert any("incident management" in text.lower() for text in organized_texts)


def test_organize_primary_section_entries_prefers_selected_bullets_within_theme() -> None:
    selected_incident_bullet = (
        "Delivered 24/7 global incident response coverage for a high-volume enterprise environment, reducing escalation delays for executive stakeholders by 50%",
        ["Incident Management", "Azure Monitor", "PowerShell"],
    )
    inferred_incident_bullet = (
        "Built automated incident response protocols, achieving a 50% reduction in mean time to recovery for critical incidents",
        ["Azure Monitor", "Incident Management"],
    )
    entries = [
        (
            "Promoted twice while rapidly expanding from IT operations into enterprise cloud security, zero-trust architecture, and automated compliance tooling",
            [],
        ),
        (
            "Architected GitLab CI/CD pipeline automation with Azure DevOps, reducing deployment time from 2 hours to under 15 minutes",
            ["Azure DevOps", "YAML Pipelines"],
        ),
        (
            "Implemented Bicep-based infrastructure provisioning, reducing environment setup time by 90% while standardizing AKS deployments",
            ["Bicep", "AKS", "Azure DevOps"],
        ),
        (
            "Strengthened Azure security controls with RBAC and Key Vault guardrails, improving compliance audit readiness across production environments",
            ["Azure Key Vault", "RBAC", "Azure AD"],
        ),
        (
            "Established Azure Monitor dashboards and alerting, reducing mean time to detection by 85% for production support teams",
            ["Azure Monitor", "Application Insights"],
        ),
        (
            "Reduced monthly Azure spend by 30% through rightsizing and automated shutdown schedules for non-production environments",
            ["Azure Cost Management", "PowerShell"],
        ),
        selected_incident_bullet,
        inferred_incident_bullet,
        (
            "Developed automated Python application that OCRs non-standard file types and reduces manual document preparation time by 80%",
            ["Python", "Azure AI Document Intelligence"],
        ),
    ]

    organized_entries = organize_primary_section_entries(
        entries,
        role="Infrastructure Security Engineer",
        job_description="GitLab, security operations dashboards, vulnerability management, and incident response.",
        detected_tech_stack=["GitLab", "Power BI", "Rapid7", "Azure DevOps"],
        seed_text="GitLab|Infrastructure Security Engineer|theme-spread",
        priority_by_signature={
            make_bullet_signature(selected_incident_bullet[0]): 2,
            make_bullet_signature(inferred_incident_bullet[0]): 1,
        },
    )

    organized_texts = [text for text, _ in organized_entries]

    assert selected_incident_bullet[0] in organized_texts
    assert inferred_incident_bullet[0] not in organized_texts


def test_select_prompt_accomplishments_prioritizes_relevant_security_entries() -> None:
    accomplishments = {
        "CICD-001": {
            "title": "CI/CD Pipeline Automation",
            "bullet": "Architected CI/CD pipeline automation using Azure DevOps YAML Pipelines",
            "technologies": "Azure DevOps, YAML Pipelines, PowerShell",
            "roles": ["devops"],
        },
        "SEC-002": {
            "title": "Unified Security Operations Dashboard",
            "bullet": "Built unified Power BI security operations dashboard consolidating Microsoft Defender, Wazuh, Rapid7, and CISA threat intelligence into a single response view",
            "technologies": "Power BI, Microsoft Defender, Wazuh, Rapid7, CISA KEV",
            "roles": ["infrastructure", "sre", "cloud"],
        },
        "SRE-006": {
            "title": "24/7 Global Incident Response",
            "bullet": "Delivered 24/7/365 incident response coverage for a global enterprise, providing white-glove support for high-priority users",
            "technologies": "Incident Management, Azure Monitor, ServiceNow, PowerShell",
            "roles": ["sre", "infrastructure"],
        },
    }

    prompt_entries = select_prompt_accomplishments(
        accomplishments,
        role="Infrastructure Security Engineer",
        job_description="Wazuh, Rapid7, CISA, incident response, Power BI security dashboard.",
    )
    selected_ids = [accomplishment_id for accomplishment_id, _ in prompt_entries[:2]]

    assert "SEC-002" in selected_ids
    assert "SRE-006" in selected_ids


def test_select_prompt_accomplishments_includes_sre_incident_entry() -> None:
    accomplishments = {
        f"GEN-{index:03d}": {
            "title": f"Generic Cloud Automation {index}",
            "bullet": "Automated cloud provisioning workflows for shared platform services",
            "technologies": "Azure, PowerShell",
            "roles": ["sre"],
        }
        for index in range(24)
    }
    accomplishments["SRE-006"] = {
        "title": "24/7 Global Incident Response",
        "bullet": "Delivered 24/7 incident response coverage for a global enterprise, providing white-glove support and reducing escalation delays for high-priority users",
        "technologies": "Incident Management, Azure Monitor, ServiceNow, PowerShell",
        "roles": ["sre", "infrastructure"],
    }

    prompt_entries = select_prompt_accomplishments(
        accomplishments,
        role="Site Reliability Engineer II",
        job_description="Incident response, observability, Prometheus, Grafana, Datadog, and production reliability.",
    )

    prompt_ids = [accomplishment_id for accomplishment_id, _ in prompt_entries]

    assert "SRE-006" in prompt_ids


def test_select_prompt_accomplishments_prioritizes_adjacent_support_entries() -> None:
    accomplishments = {
        "CLOUD-001": {
            "title": "Generic Cloud Automation",
            "bullet": "Automated shared cloud provisioning workflows for internal platform services",
            "technologies": "Azure, PowerShell",
            "roles": ["cloud"],
        },
        "SUP-001": {
            "title": "API Integration Troubleshooting",
            "bullet": "Resolved API integration failures during customer onboarding, reducing implementation delays by 45% through SQL troubleshooting and deployment validation",
            "technologies": "REST APIs, SQL Server, Deployment Validation",
            "roles": ["system_analyst"],
        },
        "SUP-002": {
            "title": "Workflow Automation Runbooks",
            "bullet": "Built workflow automation runbooks for software support teams, improving issue resolution speed and release coordination across production change windows",
            "technologies": "PowerShell, Workflow Automation, Zendesk",
            "roles": [],
        },
    }

    prompt_entries = select_prompt_accomplishments(
        accomplishments,
        role="System Software Support Engineer",
        job_description=(
            "Hands-on software support role focused on API integrations, SQL troubleshooting, "
            "workflow automation, deployment validation, and technical support."
        ),
    )
    selected_ids = [accomplishment_id for accomplishment_id, _ in prompt_entries[:2]]

    assert "SUP-001" in selected_ids
    assert "SUP-002" in selected_ids


def test_select_prompt_accomplishments_prioritizes_database_entries_from_text_markers() -> None:
    accomplishments = {
        "CLOUD-001": {
            "title": "Generic Cloud Automation",
            "bullet": "Automated shared cloud provisioning workflows for internal platform services",
            "technologies": "Azure, PowerShell",
            "roles": ["cloud"],
        },
        "DBA-001": {
            "title": "SQL Server Performance Tuning",
            "bullet": "Optimized SQL Server execution plans and indexing strategy, reducing reporting latency by 65% for high-traffic operational dashboards",
            "technologies": "SQL Server, Execution Plans, Index Optimization, SSRS",
            "roles": ["system_analyst"],
        },
        "DBA-002": {
            "title": "ETL Reliability",
            "bullet": "Built SSIS ETL pipelines for a centralized data warehouse, improving overnight load reliability and reducing failed jobs by 50%",
            "technologies": "SSIS, ETL, Data Warehouse, SQL Server",
            "roles": [],
        },
    }

    prompt_entries = select_prompt_accomplishments(
        accomplishments,
        role="SQL DBA",
        job_description=(
            "SQL Server DBA role focused on stored procedures, ETL, SSIS, query tuning, reporting, "
            "and operational database reliability."
        ),
    )
    selected_ids = [accomplishment_id for accomplishment_id, _ in prompt_entries[:2]]

    assert "DBA-001" in selected_ids
    assert "DBA-002" in selected_ids


def test_parse_accomplishments_to_dict_skips_placeholder_entries() -> None:
    accomplishments = parse_accomplishments_to_dict(
        """### VALID-001
**Reliable Incident Response**
- Reduced Mean Time to Resolution by 40% through automated incident response procedures.
- **Technologies:** Azure Monitor, PowerShell
- **Roles:** sre, infrastructure

### SRE-001
**Template SRE Entry**
- Led migration of applications to Kubernetes, resulting in a [XX]% increase in scalability.
- **Technologies:** (add relevant tech)
- **Roles:** sre
"""
    )

    assert "VALID-001" in accomplishments
    assert "SRE-001" not in accomplishments


def test_rewrite_selected_bullet_foregrounds_relevant_technologies() -> None:
    rewritten_text, ordered_technologies = rewrite_selected_bullet(
        bullet_text=(
            "Architected CI/CD pipeline automation using Azure DevOps YAML Pipelines for development "
            "and QA teams, reducing deployment time from 2+ hours to under 15 minutes"
        ),
        technology_text="Azure DevOps, YAML Pipelines, PowerShell, SQL Server Deployment",
        role="DevOps Engineer",
        job_description="Azure DevOps, Linux automation, and CI/CD delivery pipelines.",
        detected_tech_stack=["Azure DevOps", "Linux", "CI/CD"],
    )

    assert rewritten_text.startswith("Reduced deployment time from 2+ hours to under 15 minutes")
    assert "Azure DevOps YAML Pipelines" in rewritten_text
    assert not rewritten_text.startswith("Using ")
    assert ordered_technologies[:2] == ["Azure DevOps", "YAML Pipelines"]


def test_prefer_scope_led_variant_ignores_in_openers() -> None:
    variant = prefer_scope_led_variant(
        "Built security best practices in CI/CD pipelines, reducing vulnerabilities by 40%"
    )

    assert variant is None


def test_prefer_scope_led_variant_skips_for_openers() -> None:
    variant = prefer_scope_led_variant(
        "Built automated runbooks for incident management, reducing Mean Time to Resolution by 40%"
    )

    assert variant is None


def test_prefer_outcome_led_variant_skips_multi_gerund_outcomes() -> None:
    variant = prefer_outcome_led_variant(
        "Built centralized monitoring dashboards with Azure Monitor, improving real-time visibility and cutting mean time to detection by 85%"
    )

    assert variant is None


def test_prefer_outcome_led_variant_handles_achieving_outcomes() -> None:
    variant = prefer_outcome_led_variant(
        "Containerized document processing applications using Docker and deployed to AKS, achieving a 300% increase in throughput"
    )

    assert variant == (
        "Achieved a 300% increase in throughput by containerizing document processing applications using Docker and deployed to AKS"
    )


def test_build_bullet_variants_prefers_outcome_led_variant_first() -> None:
    variants = build_bullet_variants(
        "Architected CI/CD pipeline automation using Azure DevOps YAML Pipelines, cutting deployment time from 2+ hours to under 15 minutes"
    )

    assert variants[0].startswith("Cut deployment time from 2+ hours to under 15 minutes")


def test_promote_support_title_for_senior_targets() -> None:
    upgraded = promote_support_title(
        "### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026",
        "Cloud Engineer II",
    )

    assert upgraded == "### Resurgent Capital Services | Software Support Analyst II | Aug 2021 - Mar 2026"


def test_build_resurgent_promotion_bullet_avoids_reusing_existing_variant() -> None:
    first_bullet = build_resurgent_promotion_bullet_with_options(
        role="Infrastructure Security Engineer",
        job_description="Cloud security, zero-trust, and compliance automation.",
        existing_bullets=[],
        candidate_options=[],
    )

    second_bullet = build_resurgent_promotion_bullet_with_options(
        role="Infrastructure Security Engineer",
        job_description="Cloud security, zero-trust, and compliance automation.",
        existing_bullets=[first_bullet],
        candidate_options=[],
    )

    assert first_bullet.startswith("Promoted twice")
    assert second_bullet.startswith("Promoted twice")
    assert first_bullet != second_bullet


def test_evaluate_company_keyword_mentions_requires_two_mentions() -> None:
    company_keyword_check = evaluate_company_keyword_mentions(
        company="GitLab",
        summary="Infrastructure security engineer with GitLab delivery controls.",
        bullet_pairs=[
            (
                "Implemented GitLab security scanning policies that reduced release risk by 40%",
                "GitLab CI, Python",
            ),
        ],
    )

    assert company_keyword_check["status"] == "pass"
    assert company_keyword_check["mentions"]["GitLab"] >= 2


def test_evaluate_company_keyword_mentions_warns_without_blocking_export() -> None:
    company_keyword_check = evaluate_company_keyword_mentions(
        company="MoonPay",
        summary="Cloud Security Engineer with experience securing multi-cloud delivery pipelines.",
        bullet_pairs=[
            (
                "Hardened cloud guardrails and automated remediation paths across production environments",
                "AWS, Azure, Terraform",
            ),
        ],
    )

    assert company_keyword_check["status"] == "warn"
    assert "advisory only" in str(company_keyword_check["message"])


def test_evaluate_keyword_bullet_density_warns_when_terms_live_in_too_few_bullets() -> None:
    keyword_density_check = evaluate_keyword_bullet_density(
        role="Cloud Security Engineer",
        job_description="Cloud Security Engineer with Azure, Kubernetes, Terraform, and incident response responsibilities.",
        detected_tech_stack=["Azure", "Kubernetes", "Terraform", "incident response"],
        bullet_pairs=[
            (
                "Reduced cloud incident response time by 40% through runbook automation",
                "Azure, PowerShell",
            ),
            (
                "Coordinated weekly platform reviews for engineering stakeholders",
                "Confluence, Jira",
            ),
            (
                "Prepared release notes for change windows and maintenance events",
                "ServiceNow, Excel",
            ),
        ],
    )

    assert keyword_density_check["status"] == "warn"
    assert keyword_density_check["density"] < 0.65


def test_evaluate_keyword_role_spread_warns_when_priority_terms_live_in_one_role() -> None:
    keyword_role_spread_check = evaluate_keyword_role_spread(
        role="Platform Engineer",
        job_description="Platform Engineer focused on AKS, Terraform, Bicep, and observability.",
        detected_tech_stack=["AKS", "Terraform", "Bicep", "observability"],
        resume_data={
            "experience": [
                {
                    "company": "Resurgent Capital Services",
                    "bullets": [
                        (
                            "Automated AKS upgrades and Bicep deployments for platform services, reducing release friction by 40%",
                            "AKS, Bicep, Azure Monitor",
                        ),
                    ],
                },
                {
                    "company": "Silco",
                    "bullets": [
                        (
                            "Optimized reporting queries for finance stakeholders, cutting report generation time by 35%",
                            "SQL Server, SSRS",
                        ),
                    ],
                },
            ]
        },
    )

    assert keyword_role_spread_check["status"] == "warn"
    assert "AKS" in keyword_role_spread_check["single_role_terms"]


def test_evaluate_must_have_keyword_coverage_passes_when_exact_jd_phrases_are_reused() -> None:
    must_have_keyword_check = evaluate_must_have_keyword_coverage(
        role="Platform Engineer",
        job_description=(
            "Platform Engineer focused on site reliability, release automation, incident response, "
            "cloud platform changes, and infrastructure as code."
        ),
        detected_tech_stack=[
            "site reliability",
            "release automation",
            "incident response",
            "cloud platform",
            "infrastructure as code",
        ],
        resume_data={
            "summary": (
                "Platform Engineer with hands-on site reliability and release automation experience "
                "across cloud platform services."
            ),
            "skills": {"Cloud Platform": ["infrastructure as code", "Azure"]},
        },
        bullet_pairs=[
            (
                "Improved incident response coverage for cloud platform services during weekend support rotations",
                "Azure Monitor, PowerShell",
            ),
        ],
    )

    assert must_have_keyword_check["status"] == "pass"
    assert must_have_keyword_check["matched_count"] >= must_have_keyword_check["required_matches"]


def test_evaluate_must_have_keyword_coverage_rejects_when_exact_jd_phrases_are_missing() -> None:
    must_have_keyword_check = evaluate_must_have_keyword_coverage(
        role="Platform Engineer",
        job_description=(
            "Platform Engineer focused on site reliability, release automation, incident response, "
            "cloud platform changes, and infrastructure as code."
        ),
        detected_tech_stack=[
            "site reliability",
            "release automation",
            "incident response",
            "cloud platform",
            "infrastructure as code",
        ],
        resume_data={
            "summary": "Platform engineer with broad operations experience.",
            "skills": {"Operations": ["Excel", "ServiceNow"]},
        },
        bullet_pairs=[
            (
                "Coordinated weekly review meetings and documented maintenance updates for stakeholders",
                "Excel, Confluence",
            ),
        ],
    )

    assert must_have_keyword_check["status"] == "reject"
    assert must_have_keyword_check["matched_count"] < must_have_keyword_check["required_matches"]
    assert "site reliability" in must_have_keyword_check["missing_phrases"]


def test_evaluate_must_have_keyword_coverage_uses_supported_targets_when_provided() -> None:
    must_have_keyword_check = evaluate_must_have_keyword_coverage(
        role="Cyber Security Engineer I (Onsite)",
        job_description=(
            "Cyber Security Engineer I (Onsite) responsible for security operations, incident response, "
            "Windows Server administration, Active Directory, Broadcom vSphere, Enterprise DNS, "
            "load balancing, TCP/IP, and DHCP troubleshooting."
        ),
        detected_tech_stack=[
            "Windows Server",
            "Active Directory",
            "incident response",
            "security operations",
        ],
        resume_data={
            "summary": (
                "Cyber Security Engineer with Windows Server, security operations, and incident response "
                "experience across production systems."
            ),
            "skills": {"Security Ops": ["Active Directory", "Azure Monitor"]},
        },
        bullet_pairs=[
            (
                "Administered identity and access workflows aligned to audit expectations",
                "Active Directory, Microsoft Entra ID",
            ),
        ],
        target_phrases=[
            "Cyber Security Engineer",
            "Windows Server",
            "security operations",
            "incident response",
            "Active Directory",
        ],
    )

    assert must_have_keyword_check["status"] == "pass"
    assert must_have_keyword_check["matched_count"] >= must_have_keyword_check["required_matches"]
    assert "Broadcom vSphere" not in must_have_keyword_check["target_phrases"]


def test_evaluate_impact_first_bullet_structure_warns_on_task_led_openers() -> None:
    impact_check = evaluate_impact_first_bullet_structure(
        [
            (
                "Supported Kubernetes production incidents with alert triage and runbook-driven recovery, improving response times by 60%",
                "AKS, Azure Monitor",
            ),
            (
                "Built release dashboards for platform teams, reducing manual status checks by 35%",
                "Grafana, Azure Monitor",
            ),
        ]
    )

    assert impact_check["status"] == "warn"
    assert "supported" in impact_check["weak_openers"] or "built" in impact_check["weak_openers"]


def test_evaluate_action_context_result_bullets_warns_on_task_only_bullets() -> None:
    acr_check = evaluate_action_context_result_bullets(
        [
            (
                "Supported Kubernetes production incidents with alert triage and runbook-driven recovery, improving response times by 60%",
                "AKS, Azure Monitor",
            ),
            (
                "Created release dashboards, reducing manual status checks by 35%",
                "Grafana, Azure Monitor",
            ),
        ]
    )

    assert acr_check["status"] == "warn"
    assert acr_check["acr_ratio"] < 0.6


def test_evaluate_resume_relevance_rejects_low_signal_roles() -> None:
    relevance_check = evaluate_resume_relevance(
        role="Software Engineer I - Mainframe",
        job_description="Mainframe modernization using COBOL, z/OS, DB2, and CICS.",
        detected_tech_stack=["Mainframe", "COBOL", "z/OS", "DB2"],
        bullet_pairs=[("Automated Azure deployments", "Azure DevOps, Python")],
    )

    assert relevance_check["status"] == "reject"
    assert relevance_check["mentioned_terms"] == []


def test_evaluate_resume_relevance_rejects_generic_mainframe_resume() -> None:
    relevance_check = evaluate_resume_relevance(
        role="Software Engineer I - Mainframe",
        job_description="Mainframe modernization with automation, CI/CD, and Python support.",
        detected_tech_stack=["Mainframe", "CI/CD", "Python"],
        bullet_pairs=[("Automated Azure deployments", "Azure DevOps, Python")],
    )

    assert relevance_check["status"] == "reject"


def test_evaluate_standard_section_coverage_accepts_required_resume_sections() -> None:
    coverage_check = evaluate_standard_section_coverage(
        {
            "experience": [{"company": "Example Co", "bullets": [("Did work", "Azure")]}],
            "education": "Test University",
            "skills": {"Cloud Platform": ["Azure", "Bicep"]},
            "certifications": ["Azure Administrator Associate"],
        },
        "---\nsummary: Test\n---\n\n## Experience\n",
    )

    assert coverage_check["status"] == "pass"
    assert coverage_check["experience_heading"] == "Experience"
    assert "Work Experience" in coverage_check["present_sections"]
    assert "normalize this to 'Work Experience'" in coverage_check["message"]


def test_evaluate_standard_section_coverage_warns_when_required_sections_are_missing() -> None:
    coverage_check = evaluate_standard_section_coverage(
        {
            "experience": [],
            "education": "",
            "skills": {},
            "certifications": [],
        },
        "---\nsummary: Test\n---\n",
    )

    assert coverage_check["status"] == "warn"
    assert coverage_check["missing_sections"] == ["Work Experience", "Education", "Skills"]


def test_evaluate_page_length_passes_after_export_time_compaction() -> None:
    rendered_resume_data = {
        "summary": "Sentence one. Sentence two.",
        "selected_achievements": [("Extra achievement", "Azure")],
        "technical_environment": "Azure, Kubernetes, PowerShell",
        "experience": [
            {
                "company": "Example Co",
                "bullets": [
                    ("Built pipelines", "Azure DevOps, YAML"),
                    ("Improved monitoring", "Azure Monitor, KQL"),
                ],
            }
        ],
    }
    condensed_resume_data = {
        "summary": "Sentence one.",
        "selected_achievements": [],
        "technical_environment": "",
        "experience": [
            {
                "company": "Example Co",
                "bullets": [("Built pipelines", "Azure DevOps")],
            }
        ],
    }

    page_length_check = evaluate_page_length(
        rendered_resume_data,
        condensed_resume_data,
        estimated_pages=1,
    )

    assert page_length_check["status"] == "pass"
    assert page_length_check["compacted_to_fit"] is True
    assert page_length_check["trimmed_bullets"] == 1
    assert page_length_check["summary_shortened"] is True
    assert page_length_check["selected_achievements_removed"] is True
    assert page_length_check["technical_environment_removed"] is True


def test_evaluate_page_length_rejects_when_export_still_overflows() -> None:
    page_length_check = evaluate_page_length(
        rendered_resume_data={
            "summary": "A summary",
            "selected_achievements": [],
            "technical_environment": "",
            "experience": [{"company": "Example Co", "bullets": [("Built pipelines", "Azure")]}],
        },
        condensed_resume_data={
            "summary": "A summary",
            "selected_achievements": [],
            "technical_environment": "",
            "experience": [{"company": "Example Co", "bullets": [("Built pipelines", "Azure")]}],
        },
        estimated_pages=2,
    )

    assert page_length_check["status"] == "reject"
    assert page_length_check["estimated_pages"] == 2


def test_create_tailored_resume_source_keeps_header_before_primary_bullets(tmp_path: Path) -> None:
    base_template = """---
name: Test Candidate
role: DevOps Engineer
contact: {}
skills: {}
certifications: []
education: Test University
summary: Base summary
---

## Experience

### Example Co | Software Support Analyst II | 2021 - Present
- CICD-001

### Another Co | Database Analyst | 2020 - 2021
- DB-001
"""
    accomplishments = {
        "CICD-001": {
            "bullet": "Architected CI/CD pipeline automation reducing deployment time by 80%",
            "technologies": "Azure DevOps, YAML Pipelines, PowerShell",
            "roles": ["devops"],
        },
        "DB-001": {
            "bullet": "Optimized SQL reporting workloads improving execution speed by 60%",
            "technologies": "SQL Server, T-SQL, SSRS",
            "roles": ["system_analyst"],
        },
    }
    analysis = {
        "selected_accomplishments": ["CICD-001", "DB-001"],
        "inferred_accomplishments": {},
        "detected_tech_stack": ["Azure DevOps", "CI/CD", "SQL Server"],
        "tailored_skills": {},
    }

    output_path = create_tailored_resume_source(
        company="Example",
        role="Cloud Engineer II",
        analysis=analysis,
        base_template=base_template,
        accomplishments=accomplishments,
        job_description="Azure DevOps, CI/CD, and SQL Server operations.",
        output_dir=tmp_path,
    )

    rendered = output_path.read_text(encoding="utf-8")
    header_index = rendered.index("### Example Co | Software Support Analyst II | 2021 - Present")
    bullet_index = rendered.index("- Architected CI/CD pipeline automation reducing deployment time by 80%")

    assert "## Work Experience" in rendered
    assert header_index < bullet_index
    assert "Key contributions as Senior Software Support Analyst II" not in rendered


def test_create_tailored_resume_source_adds_resurgent_promotion_bullet(tmp_path: Path) -> None:
    base_template = """---
name: Test Candidate
role: DevOps Engineer
contact: {}
skills: {}
certifications: []
education: Test University
summary: Base summary
---

## Experience

### Resurgent Capital Services | Software Support Analyst II | 2021 - Present
- CICD-001
"""
    accomplishments = {
        "CICD-001": {
            "bullet": "Implemented CI/CD automation reducing deployment time by 80%",
            "technologies": "Azure DevOps, YAML Pipelines, PowerShell",
            "roles": ["devops"],
        },
    }
    analysis = {
        "selected_accomplishments": ["CICD-001"],
        "inferred_accomplishments": {},
        "detected_tech_stack": ["Azure DevOps", "CI/CD", "Azure"],
        "tailored_skills": {},
    }

    output_path = create_tailored_resume_source(
        company="Example",
        role="DevOps Engineer",
        analysis=analysis,
        base_template=base_template,
        accomplishments=accomplishments,
        job_description="Azure DevOps, CI/CD, and cloud automation.",
        output_dir=tmp_path,
    )

    rendered = output_path.read_text(encoding="utf-8")

    assert "- Promoted twice" in rendered
    assert rendered.index("- Promoted twice") < rendered.index("- Implemented CI/CD automation")