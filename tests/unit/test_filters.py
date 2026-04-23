"""Unit tests for shared job filtering helpers."""

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import utils.filters as filters_module

from utils.filters import (
    clear_role_filter_cache,
    explain_job_targeting,
    infer_reporting_role_clusters,
    matches_preferred_location,
    should_keep_job,
)


def setup_function() -> None:
    clear_role_filter_cache()


def test_should_keep_job_accepts_cincinnati_hybrid_cloud_role() -> None:
    assert should_keep_job(
        "Cloud Engineer II",
        location="Hybrid - Blue Ash, OH",
    )


def test_should_keep_job_accepts_remote_sre_role() -> None:
    assert should_keep_job(
        "Site Reliability Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_rejects_cincinnati_data_platform_role() -> None:
    assert not should_keep_job(
        "Data Platform Engineer",
        location="Cincinnati, OH",
    )


def test_should_keep_job_rejects_non_target_location_even_with_flexible_arrangements() -> None:
    assert not should_keep_job(
        "AI Engineer",
        location="Warsaw Downtown Office",
        description="Flexible working arrangements and global team collaboration.",
    )


def test_should_keep_job_rejects_non_it_engineering_title() -> None:
    assert not should_keep_job(
        "Mechanical Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_accepts_identity_access_role() -> None:
    assert should_keep_job(
        "Identity & Access Management Analyst 2",
        location="Cincinnati, OH",
    )


def test_should_keep_job_accepts_adjacent_implementation_role() -> None:
    assert should_keep_job(
        "Implementation Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_accepts_adjacent_integration_role() -> None:
    assert should_keep_job(
        "Systems Integration Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_accepts_adjacent_support_role() -> None:
    assert should_keep_job(
        "Software Support Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_rejects_senior_ic_role_by_default() -> None:
    assert not should_keep_job(
        "Senior Cloud Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_can_explicitly_allow_senior_titles(monkeypatch) -> None:
    monkeypatch.setattr(filters_module, "load_config", lambda: {"seniority_allowlist": ["senior"]})
    clear_role_filter_cache()

    assert should_keep_job(
        "Senior Cloud Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_accepts_information_security_analyst_role() -> None:
    assert should_keep_job(
        "Information Security Analyst 2",
        location="Hybrid / Cincinnati, OH",
    )


def test_should_keep_job_rejects_ai_engineer_role() -> None:
    assert not should_keep_job(
        "AI Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_rejects_data_engineer_role() -> None:
    assert not should_keep_job(
        "Data Engineer",
        location="Cincinnati, OH",
    )


def test_should_keep_job_accepts_ambiguous_software_title_when_description_is_infra_heavy() -> None:
    assert should_keep_job(
        "Software Engineer, Secrets Infrastructure",
        location="Remote - United States",
        description=(
            "Build Terraform modules, Kubernetes platforms, AWS Secrets Manager patterns, "
            "CI/CD pipelines, on-call automation, and Prometheus/Grafana observability."
        ),
    )


def test_should_keep_job_rejects_ambiguous_software_title_when_description_is_product_only() -> None:
    assert not should_keep_job(
        "Software Engineer, Consumer Payments",
        location="Remote - United States",
        description=(
            "Build customer-facing checkout features, backend APIs, Java services, and product "
            "experiments for payment flows."
        ),
    )


def test_should_keep_job_rejects_not_remote_phrase_outside_region() -> None:
    assert not should_keep_job(
        "Cloud Engineer",
        description="This is not a remote position and is based in Columbus, Ohio.",
    )


def test_should_keep_job_accepts_us_remote_role_with_global_office_mentions() -> None:
    assert should_keep_job(
        "Security Engineer I",
        location="United States - Remote",
        description="We collaborate across teams in Toronto, Berlin, and London.",
    )


def test_should_keep_job_accepts_us_remote_hybrid_role_with_global_description() -> None:
    assert should_keep_job(
        "Cloud Security Engineer",
        location="United States - Hybrid / United States (East Coast Time Zone) - Remote",
        description="MoonPay has teams in London, Barcelona, and Toronto.",
    )


def test_should_keep_job_rejects_remote_label_when_description_negates_remote() -> None:
    assert not should_keep_job(
        "Cloud Engineer",
        location="Remote",
        description="This is not a remote position and is based in Columbus, Ohio.",
    )


def test_should_keep_job_rejects_staff_platform_role() -> None:
    assert not should_keep_job(
        "Staff Platform Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_rejects_staff_ai_engineer_role() -> None:
    assert not should_keep_job(
        "Staff AI Engineer",
        location="Remote - United States",
    )


def test_should_keep_job_rejects_canada_remote_role() -> None:
    assert not should_keep_job(
        "Cloud Security Engineer",
        location="Remote - Canada",
    )


def test_should_keep_job_rejects_london_hybrid_role() -> None:
    assert not should_keep_job(
        "Site Reliability Engineer",
        location="Hybrid - London, United Kingdom",
    )


def test_should_keep_job_rejects_remote_spain_role() -> None:
    assert not should_keep_job(
        "Senior Site Reliability Engineer",
        location="Remote Spain",
    )


def test_should_keep_job_accepts_united_states_remote_role() -> None:
    assert should_keep_job(
        "DevOps Engineer",
        location="United States",
        workplace_type="Remote",
    )


def test_should_keep_job_accepts_remote_us_shorthand_role() -> None:
    assert should_keep_job(
        "Cloud Security Engineer",
        location="Remote, US",
        workplace_type="remote",
    )


def test_should_keep_job_accepts_us_nationwide_remote_role() -> None:
    assert should_keep_job(
        "Cyber Resilience Engineer",
        location="US Nationwide - Remote",
    )


def test_should_keep_job_rejects_out_of_region_job_even_with_us_description_markers() -> None:
    assert not should_keep_job(
        "DevOps Engineer",
        location="Winchester, VA, US",
        description=(
            "Applicants must work onsite 2-3x per week in Arlington, VA. "
            "We support the United States federal government."
        ),
    )


def test_matches_preferred_location_uses_description_only_when_location_missing() -> None:
    assert matches_preferred_location(
        "",
        description="United States - Hybrid / United States (East Coast Time Zone) - Remote",
    )


def test_matches_preferred_location_uses_cincinnati_metro_markers() -> None:
    assert matches_preferred_location("Fort Mitchell, KY")
    assert matches_preferred_location("Highland Heights, KY")
    assert matches_preferred_location("Hamilton Campus")
    assert not matches_preferred_location("Columbus, Ohio")


def test_explain_job_targeting_reports_seniority_rejection() -> None:
    explanation = explain_job_targeting(
        "Staff Platform Engineer",
        location="Remote - United States",
    )

    assert explanation["keep"] is False
    assert explanation["decision_reason"] == "excluded_seniority_staff"
    assert "excluded_seniority_staff" in explanation["rejection_reasons"]


def test_explain_job_targeting_reports_senior_rejection() -> None:
    explanation = explain_job_targeting(
        "Senior Cloud Engineer",
        location="Remote - United States",
    )

    assert explanation["keep"] is False
    assert explanation["decision_reason"] == "excluded_seniority_senior"
    assert "excluded_seniority_senior" in explanation["rejection_reasons"]


def test_explain_job_targeting_reports_blocked_location() -> None:
    explanation = explain_job_targeting(
        "Cloud Security Engineer",
        location="Remote - Canada",
    )

    assert explanation["keep"] is False
    assert "blocked_location" in explanation["rejection_reasons"]


def test_infer_reporting_role_clusters_maps_identity_and_cloud() -> None:
    clusters = infer_reporting_role_clusters(
        "Identity and Access Management Engineer",
        description="Azure cloud security and access automation",
    )

    assert "iam" in clusters
    assert "cloud" in clusters