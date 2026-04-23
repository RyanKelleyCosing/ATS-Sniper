#!/usr/bin/env python3
"""
ATS Sniper - Full Pipeline v3
Runs: Scrapers → AI Scoring → Hot Job Processing → Resume Tailoring → Email

v3 Features:
- Platform-specific scrapers (iCIMS, Oracle HCM)
- AI-powered resume tailoring for hot jobs (>=80% match)
- Automatic resume attachment to email notifications
- Organized artifact output structure
"""

import sys
import os
import json
import csv
import logging
import smtplib
import asyncio
import re
import traceback
import io
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from io import StringIO

# Add script dir to path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from params.logging_config import setup_logging
from workday_scraper import run_workday_scrape
from custom_scraper import get_non_workday_targets, run_custom_scraper
from usajobs_scraper import run_usajobs_scraper
from resume_tailor import process_enterprise_jobs
from startup_discovery_scraper import run_web_discovery_scrape
from utils.contacts import format_contact_emails, primary_contact_email
from utils.benchmark_validator import (
    build_discovery_benchmark_summary,
    compact_discovery_benchmark_summary,
    find_previous_benchmark_summary,
    load_discovery_benchmark_set,
)
from utils.filters import get_web_discovery_settings
from utils.job_identity import deduplicate_jobs_by_identity, ensure_job_identity_index, find_existing_job_url, store_job_identity_record
from utils.notifications import send_status_email
from utils.pipeline_health import get_monitoring_config
from utils.pipeline_freshness import (
    apply_freshness_metadata,
    count_jobs_by_freshness_bucket,
    freshness_badge_label,
    get_freshness_settings,
    sort_jobs_by_freshness,
)
from utils.pipeline_telemetry import (
    apply_feedback_signals,
    record_source_health_snapshot,
    sort_jobs_for_reporting,
    write_discovery_audit_report,
)
from utils.runtime_paths import master_jobs_csv_path
from utils.state import load_config, load_state, save_state, upsert_pipeline_run_record

logger = logging.getLogger(__name__)
EMAIL_TIMEOUT_SECONDS = 30

# v3 imports
try:
    from ashby_scraper import get_ashby_endpoints, run_ashby_scrape
    from icims_scraper import run_icims_scrape
    from oracle_hcm_scraper import run_oracle_hcm_scrape
    from greenhouse_scraper import get_greenhouse_endpoints, run_greenhouse_scrape
    from lever_scraper import get_lever_endpoints, run_lever_scrape
    from smartrecruiters_scraper import (
        get_smartrecruiters_endpoints,
        run_smartrecruiters_scrape,
    )
    from workable_scraper import get_workable_endpoints, run_workable_scrape
    from hot_job_processor import run_hot_job_pipeline, get_hot_job_attachments
    from email_with_attachments import send_hot_job_email
    V3_ENABLED = True
except ImportError as e:
    print(f"⚠️ v3 modules not available: {e}")
    V3_ENABLED = False


# Enterprise companies (for resume tailor)
ENTERPRISE_COMPANIES = ['p&g', 'procter', 'medpace', 'ge', 'fidelity', 'cvs', 'worldpay',
                        'cintas', 'cchmc', 'gaig', 'fifth third', 'kroger', 'first financial',
                        'mercy', 'bon secours', 'st. elizabeth']
MONITORED_RUN_TYPES = {"morning", "afternoon"}
LIGHTWEIGHT_RUN_TYPE = "lightweight"


def should_track_pipeline_run(run_type: str, dry_run: bool) -> bool:
    """Return True when the run should be recorded for monitoring."""
    return not dry_run and run_type in MONITORED_RUN_TYPES


def record_pipeline_run(
    run_type: str,
    status: str,
    *,
    stats: dict | None = None,
    error_message: str = "",
    email_sent: bool | None = None,
    total_new_jobs: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    """Persist pipeline-run status for the monitor task."""
    state = load_state()
    updates = {"status": status, "error_message": error_message}
    if started_at:
        updates["started_at"] = started_at
    if completed_at:
        updates["completed_at"] = completed_at
    if stats is not None:
        updates["stats"] = stats
    if email_sent is not None:
        updates["email_sent"] = email_sent
    if total_new_jobs is not None:
        updates["total_new_jobs"] = total_new_jobs
    upsert_pipeline_run_record(state, run_type, updates)
    save_state(state)


def build_source_status_stats(stats: dict) -> dict[str, int]:
    """Return per-source counters used in monitoring status emails."""
    return {
        "Workday": stats.get("workday", 0),
        "Custom": stats.get("custom", 0),
        "Greenhouse": stats.get("greenhouse", 0),
        "Lever": stats.get("lever", 0),
        "SmartRecruiters": stats.get("smartrecruiters", 0),
        "Workable": stats.get("workable", 0),
        "Web Discovery": stats.get("web_discovery", 0),
        "USAJobs": stats.get("usajobs", 0),
        "Noise Screened": stats.get("screened_out_noise", 0),
    }


def send_no_jobs_email(run_type: str, stats: dict, config: dict) -> bool:
    """Send a status email when a monitored run completes with no new jobs."""
    monitoring_config = get_monitoring_config(config)
    if not monitoring_config.get("send_no_jobs_email", True):
        return False

    subject = f"ATS Sniper: {run_type.title()} run completed with no new jobs"
    heading = f"ATS Sniper {run_type.title()} Run Completed"
    message_lines = [
        f"The {run_type} run completed successfully.",
        "No new jobs were found during this run, so no job-alert email would have been sent otherwise.",
    ]
    status_stats = build_source_status_stats(stats)
    return send_status_email(config, subject, heading, message_lines, status_stats)


def build_screened_role_reference_lines(
    screened_out_jobs: list[dict] | None,
    *,
    limit: int = 10,
) -> list[str]:
    """Build status-email lines that show which roles were screened out."""
    if not screened_out_jobs:
        return []

    reference_lines = ["Screened-out roles from this run:"]
    for job in screened_out_jobs[:limit]:
        company = str(job.get("company", "Unknown")).strip() or "Unknown"
        title = str(job.get("title", "Unknown")).strip() or "Unknown"
        screening_reason = str(job.get("screening_reason", "")).strip()
        job_url = str(job.get("url", "")).strip()

        parts = [f"{company} - {title}"]
        if screening_reason:
            parts.append(screening_reason)
        if job_url:
            parts.append(job_url)
        reference_lines.append(" | ".join(parts))

    remaining_count = len(screened_out_jobs) - limit
    if remaining_count > 0:
        reference_lines.append(f"... plus {remaining_count} more screened-out roles.")
    return reference_lines


def send_no_reviewable_jobs_email(
    run_type: str,
    stats: dict,
    config: dict,
    *,
    total_new_jobs: int,
    screened_out_noise: int,
    screened_out_jobs: list[dict] | None = None,
) -> bool:
    """Send a status email when new jobs are found but none survive screening."""
    monitoring_config = get_monitoring_config(config)
    if not monitoring_config.get("send_no_jobs_email", True):
        return True

    subject = f"ATS Sniper: {run_type.title()} run completed with no reviewable jobs"
    heading = f"ATS Sniper {run_type.title()} Run Completed"
    job_phrase = "1 new job was" if total_new_jobs == 1 else f"{total_new_jobs} new jobs were"
    message_lines = [
        f"The {run_type} run completed successfully.",
        f"{job_phrase} discovered, but none remained after screening for target-role relevance.",
    ]
    if screened_out_noise:
        message_lines.append(f"Noise-screened jobs: {screened_out_noise}.")
    message_lines.extend(build_screened_role_reference_lines(screened_out_jobs))

    status_stats = {
        "New Jobs Found": total_new_jobs,
        "Reviewable Jobs": stats.get("reviewable_jobs", 0),
        "Noise Screened": screened_out_noise,
        **build_source_status_stats(stats),
    }
    return send_status_email(config, subject, heading, message_lines, status_stats)


def send_failure_email(run_type: str, stats: dict, config: dict, error_message: str) -> bool:
    """Send an alert email when a monitored run fails inside the pipeline."""
    monitoring_config = get_monitoring_config(config)
    if not monitoring_config.get("send_failure_email", True):
        return False

    subject = f"ATS Sniper alert: {run_type.title()} run failed"
    heading = f"ATS Sniper {run_type.title()} Run Failed"
    message_lines = [
        f"The {run_type} run started but did not complete successfully.",
        f"Error: {error_message}",
    ]
    failure_stats = build_source_status_stats(stats)
    return send_status_email(config, subject, heading, message_lines, failure_stats)


def send_issue_email(run_type: str, stats: dict, config: dict, issues: list[str]) -> bool:
    """Send a status email when the pipeline completes with partial issues."""
    monitoring_config = get_monitoring_config(config)
    if not monitoring_config.get("send_issue_email", True):
        return False

    subject = f"ATS Sniper warning: {run_type.title()} run completed with issues"
    heading = f"ATS Sniper {run_type.title()} Run Completed With Issues"
    trimmed_issues = issues[:12]
    message_lines = [
        f"The {run_type} run completed, but one or more stages reported warnings or recoverable errors.",
        "Review the issue summary below.",
        *trimmed_issues,
    ]
    issue_stats = {"Issue Count": len(issues), **build_source_status_stats(stats)}
    return send_status_email(config, subject, heading, message_lines, issue_stats)


def extract_stage_issues(stage_name: str, captured_output: str) -> list[str]:
    """Extract warning and error lines from captured stage output."""
    issues: list[str] = []
    seen_lines: set[str] = set()
    for raw_line in captured_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lowered = line.casefold()
        looks_like_issue = (
            "⚠" in line
            or "❌" in line
            or "traceback" in lowered
            or " error" in f" {lowered}"
            or " failed" in f" {lowered}"
        )
        if not looks_like_issue:
            continue
        if "status email sent successfully" in lowered:
            continue

        issue_line = f"[{stage_name}] {line}"
        if issue_line in seen_lines:
            continue
        seen_lines.add(issue_line)
        issues.append(issue_line)
    return issues


def capture_stage_output(stage_name: str, runner):
    """Run a stage while capturing stdout/stderr for issue detection."""
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        result = runner()
    captured_output = stream.getvalue()
    if captured_output:
        print(captured_output, end="")
    return result, extract_stage_issues(stage_name, captured_output)


def configure_console_streams() -> None:
    """Use UTF-8 console streams when supported to avoid Windows encoding failures."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def normalize_site_selector(site: str) -> str:
    """Normalize a query-group site selector for matching against ATS endpoints."""
    normalized = re.sub(r"^https?://", "", str(site).strip().casefold())
    return normalized.rstrip("/")


def get_run_type_site_selectors(config: dict, run_type: str) -> set[str]:
    """Return normalized site selectors for the active scheduled run profile."""
    schedule = config.get("schedules", {}).get(run_type, {})
    if not isinstance(schedule, dict):
        return set()

    query_groups = config.get("query_groups", {})
    selectors: set[str] = set()

    for group_name in schedule.get("query_groups", []):
        for site in query_groups.get(group_name, {}).get("sites", []):
            normalized = normalize_site_selector(site)
            if normalized:
                selectors.add(normalized)

    return selectors


def filter_platform_endpoints_for_run(
    config: dict,
    run_type: str,
    platform_domain: str,
    token_field: str,
    endpoints: dict,
) -> dict:
    """Filter ATS endpoints to those that match the active scheduled run profile."""
    selectors = get_run_type_site_selectors(config, run_type)
    if not selectors:
        return endpoints

    normalized_domain = normalize_site_selector(platform_domain)
    if normalized_domain in selectors:
        return endpoints

    filtered: dict = {}
    for endpoint_key, endpoint_config in endpoints.items():
        token = normalize_site_selector(endpoint_config.get(token_field, endpoint_key))
        if any(
            selector == token or selector.endswith(f"/{token}")
            for selector in selectors
        ):
            filtered[endpoint_key] = endpoint_config

    return filtered


def is_lightweight_run(run_type: str, config: dict) -> bool:
    """Return True when the current run should use the lightweight freshness pass."""
    settings = get_freshness_settings(config)
    lightweight_run_types = {
        str(value).strip().casefold()
        for value in settings.get("lightweight_run_types", [LIGHTWEIGHT_RUN_TYPE])
        if str(value).strip()
    }
    return run_type.casefold() in lightweight_run_types


def should_run_web_discovery(run_type: str) -> bool:
    """Return True when web discovery is configured for the run type."""
    discovery_settings = get_web_discovery_settings()
    allowed_run_types = {
        str(value).strip().casefold()
        for value in discovery_settings.get("allowed_run_types", [])
        if str(value).strip()
    }
    if not allowed_run_types:
        return True
    return run_type.casefold() in allowed_run_types


def get_lightweight_custom_targets(config: dict) -> list[str]:
    """Return low-cost custom targets for the lightweight discovery pass."""
    settings = get_freshness_settings(config)
    allowed_scrapers = {
        str(value).strip()
        for value in settings.get("lightweight_custom_scrapers", [])
        if str(value).strip()
    }
    if not allowed_scrapers:
        return []

    targets = get_non_workday_targets()
    return [
        target_key
        for target_key, target_config in targets.items()
        if str(target_config.get("scraper", "")).strip() in allowed_scrapers
        and not target_config.get("managed_by_pipeline")
    ]


def annotate_jobs_with_freshness(
    jobs: list[dict],
    config: dict,
    *,
    persist_state: bool,
) -> dict[str, int]:
    """Normalize freshness metadata onto new jobs and persist it into state."""
    if not jobs:
        return count_jobs_by_freshness_bucket([])

    state = load_state()
    ensure_job_identity_index(state)
    now = datetime.now()
    enriched_jobs: list[dict] = []

    for job in jobs:
        existing_url = find_existing_job_url(state, job)
        existing_record = state.get("jobs", {}).get(existing_url, {}) if existing_url else {}
        enriched_job = apply_freshness_metadata(job, existing_record, config=config, now=now)
        stored_url = store_job_identity_record(state, enriched_job, stored_url=existing_url or None)
        if stored_url and stored_url != enriched_job.get("url"):
            enriched_job["url"] = stored_url
        enriched_jobs.append(enriched_job)

    jobs[:] = sort_jobs_by_freshness(enriched_jobs)
    if persist_state:
        save_state(state)

    return count_jobs_by_freshness_bucket(jobs)


def parse_salary(salary_str: str) -> int:
    """Extract salary number from string like '$80,000' or '80k-100k'."""
    if not salary_str:
        return 0
    # Remove common chars and extract numbers
    clean = salary_str.lower().replace(',', '').replace('$', '')
    # Handle 'k' notation
    if 'k' in clean:
        match = re.search(r'(\d+)k', clean)
        if match:
            return int(match.group(1)) * 1000
    # Handle plain numbers
    match = re.search(r'(\d{4,})', clean)
    if match:
        return int(match.group(1))
    return 0


def is_hot_job(job: dict, match_results: dict = None) -> bool:
    """Determine if job is 'hot' based on match rate, salary, and company tier."""
    # Check match score from AI analysis
    match_score = 0
    if match_results:
        job_url = job.get('url', '')
        if job_url in match_results:
            match_score = match_results[job_url].get('match_score', 0)

    # Check salary
    salary = parse_salary(job.get('salary', ''))

    # Check if enterprise company
    company = job.get('company', '').lower()
    is_enterprise = any(ec in company for ec in ENTERPRISE_COMPANIES)

    # Hot job criteria:
    # 1. Match score >= 80%
    # 2. Salary >= $80,000
    # 3. Enterprise company with relevant title
    relevant_titles = ['devops', 'sre', 'cloud', 'infrastructure', 'platform', 'azure', 'engineer']
    title_lower = job.get('title', '').lower()
    has_relevant_title = any(t in title_lower for t in relevant_titles)

    return (match_score >= 80 or
            salary >= 80000 or
            (is_enterprise and has_relevant_title))


def create_jobs_csv(jobs: list) -> str:
    """Create CSV string of all jobs."""
    output = StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        'Company',
        'Title',
        'Location',
        'Freshness',
        'Freshness Basis',
        'Posted Date',
        'Salary',
        'Contact Email',
        'Source',
        'Source Family',
        'Source Board',
        'Query Profile',
        'URL',
        'Date Found',
    ])

    for job in jobs:
        freshness_bucket = str(job.get('freshness_bucket', '')).strip()
        writer.writerow([
            job.get('company', 'Unknown'),
            job.get('title', 'Unknown'),
            job.get('location', ''),
            freshness_badge_label(freshness_bucket),
            job.get('freshness_basis', ''),
            job.get('posted_date', job.get('date_posted', '')),
            job.get('salary', ''),
            format_contact_emails(job.get('contact_emails', [])) or job.get('contact_email', ''),
            job.get('source', 'unknown'),
            job.get('source_family', ''),
            job.get('source_board', ''),
            job.get('query_profile', ''),
            job.get('url', ''),
            datetime.now().strftime('%Y-%m-%d')
        ])

    return output.getvalue()


def send_pipeline_email(new_jobs: list, stats: dict, config: dict, match_results: dict = None):
    """Send email with hot jobs in body and full CSV attached."""
    email_config = config.get('email', {})

    if not email_config.get('sender_email'):
        print("⚠️ Email not configured in config.json")
        return False

    # Separate hot jobs from others
    hot_jobs = sort_jobs_by_freshness([j for j in new_jobs if is_hot_job(j, match_results)])
    other_jobs = sort_jobs_by_freshness([j for j in new_jobs if not is_hot_job(j, match_results)])
    fresh_under_6h = sum(1 for job in new_jobs if job.get('freshness_bucket') == 'fresh_under_6h')
    fresh_under_24h = sum(1 for job in new_jobs if job.get('freshness_bucket') == 'fresh_under_24h')

    # Build HTML email (hot jobs only in body)
    html = f"""
    <html>
    <head><style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; }}
        .stat {{ display: inline-block; margin: 10px; padding: 10px; background: #f8f9fa; border-radius: 5px; }}
        .job {{ border-left: 3px solid #667eea; padding: 10px; margin: 10px 0; background: #f8f9fa; }}
        .hot {{ border-color: #e74c3c; background: #fff5f5; }}
        .enterprise {{ border-color: #e74c3c; }}
        .custom {{ border-color: #27ae60; }}
        .usajobs {{ border-color: #3498db; }}
        a {{ color: #667eea; text-decoration: none; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; }}
        .badge-hot {{ background: #e74c3c; color: white; }}
        .badge-salary {{ background: #27ae60; color: white; }}
        .badge-fresh {{ background: #0f766e; color: white; }}
        .badge-fresh24 {{ background: #0ea5e9; color: white; }}
    </style></head>
    <body>
        <div class="header">
            <h1>🎯 ATS Sniper Pipeline Report</h1>
            <p>Run completed: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
        </div>

        <h2>📊 Statistics</h2>
        <div class="stat">Total Jobs: <strong>{stats['total']}</strong></div>
        <div class="stat">New Workday: <strong>{stats['workday']}</strong></div>
        <div class="stat">New Custom: <strong>{stats['custom']}</strong></div>
        <div class="stat">New iCIMS: <strong>{stats.get('icims', 0)}</strong></div>
        <div class="stat">New Oracle: <strong>{stats.get('oracle', 0)}</strong></div>
        <div class="stat">New Greenhouse: <strong>{stats.get('greenhouse', 0)}</strong></div>
        <div class="stat">New Lever: <strong>{stats.get('lever', 0)}</strong></div>
        <div class="stat">New SmartRecruiters: <strong>{stats.get('smartrecruiters', 0)}</strong></div>
        <div class="stat">New Workable: <strong>{stats.get('workable', 0)}</strong></div>
        <div class="stat">New Web Discovery: <strong>{stats.get('web_discovery', 0)}</strong></div>
        <div class="stat">Noise Screened: <strong>{stats.get('screened_out_noise', 0)}</strong></div>
        <div class="stat">Fresh &lt;6h: <strong>{fresh_under_6h}</strong></div>
        <div class="stat">Fresh &lt;24h: <strong>{fresh_under_24h}</strong></div>

        <h2>🔥 Hot Jobs ({len(hot_jobs)})</h2>
        <p><em>Criteria: Match ≥80% OR Salary ≥$80k OR Enterprise + Relevant Title</em></p>
    """

    if hot_jobs:
        for job in hot_jobs[:20]:  # Limit to 20 hot jobs
            source = job.get('source', 'unknown')
            salary = job.get('salary', '')
            salary_badge = f'<span class="badge badge-salary">{salary}</span>' if salary else ''
            freshness_bucket = str(job.get('freshness_bucket', '')).strip()
            freshness_label = freshness_badge_label(freshness_bucket)
            freshness_class = 'badge-fresh' if freshness_bucket == 'fresh_under_6h' else 'badge-fresh24'
            freshness_badge = (
                f'<span class="badge {freshness_class}">{freshness_label}</span>'
                if freshness_label and freshness_bucket.startswith('fresh_under') else ''
            )
            contact_emails = format_contact_emails(job.get('contact_emails', [])) or job.get('contact_email', '')
            contact_email = primary_contact_email(job.get('contact_emails', [])) or job.get('contact_email', '')
            contact_html = (
                f'<br><small>Contact: <a href="mailto:{contact_email}">{contact_emails}</a></small>'
                if contact_email and contact_emails else ''
            )
            html += f"""
            <div class="job hot">
                <strong>{job.get('title', 'Unknown')}</strong> @ {job.get('company', 'Unknown')} {salary_badge} {freshness_badge}<br>
                <a href="{job.get('url', '#')}">{job.get('url', 'No URL')[:70]}...</a><br>
                <small>Source: {source} | Location: {job.get('location', 'N/A')} | Posted: {job.get('posted_date', job.get('date_posted', 'unknown'))}</small>
                {contact_html}
            </div>
            """
    else:
        html += "<p>No hot jobs in this batch. Check the attached CSV for all jobs.</p>"

    html += f"""
        <h2>📎 Other Jobs ({len(other_jobs)})</h2>
        <p>See attached CSV for all {len(new_jobs)} jobs found.</p>

        <h2>📋 Next Steps</h2>
        <ol>
            <li>Review hot jobs above</li>
            <li>Check attached CSV for additional opportunities</li>
            <li>Run <code>python resume_tailor.py --limit 5</code> for AI matching</li>
        </ol>
    </body>
    </html>
    """

    # Create multipart message
    msg = MIMEMultipart('mixed')
    msg['Subject'] = f"🎯 ATS Sniper: {len(hot_jobs)} Hot Jobs + {len(other_jobs)} More - {datetime.now().strftime('%b %d')}"
    msg['From'] = email_config['sender_email']
    msg['To'] = email_config['recipient_email']

    # Add HTML body
    msg.attach(MIMEText(html, 'html'))

    # Add CSV attachment
    csv_content = create_jobs_csv(new_jobs)
    csv_attachment = MIMEBase('text', 'csv')
    csv_attachment.set_payload(csv_content)
    encoders.encode_base64(csv_attachment)
    csv_filename = f"ats_sniper_jobs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    csv_attachment.add_header('Content-Disposition', f'attachment; filename="{csv_filename}"')
    msg.attach(csv_attachment)

    try:
        print(f"\n📧 Sending email to {email_config['recipient_email']}...")
        print(f"   🔥 Hot jobs: {len(hot_jobs)}")
        print(f"   📎 CSV attachment: {len(new_jobs)} jobs")
        with smtplib.SMTP(
            email_config['smtp_server'],
            email_config['smtp_port'],
            timeout=EMAIL_TIMEOUT_SECONDS,
        ) as server:
            server.starttls()
            server.login(email_config['sender_email'], email_config['sender_password'])
            server.sendmail(email_config['sender_email'], email_config['recipient_email'], msg.as_string())
        print("  ✅ Email sent successfully!")
        return True
    except Exception as e:
        print(f"  ❌ Email failed: {e}")
        return False


async def _run_all_async_scrapers(
    dry_run: bool,
    v3_mode: bool,
    run_type: str,
    direct_scraper_telemetry: dict[str, dict[str, dict[str, int]]] | None = None,
) -> dict:
    """Run all async scrapers in a single event loop."""
    results: dict = {
        "custom": [],
        "ashby": [],
        "icims": [],
        "oracle": [],
        "greenhouse": [],
        "lever": [],
    }
    stage_attempts: dict[str, bool] = {
        "custom": False,
        "ashby": False,
        "icims": False,
        "oracle": False,
        "greenhouse": False,
        "lever": False,
        "smartrecruiters": False,
        "workable": False,
    }
    stage_issues: dict[str, list[str]] = {
        "custom": [],
        "ashby": [],
        "icims": [],
        "oracle": [],
        "greenhouse": [],
        "lever": [],
        "smartrecruiters": [],
        "workable": [],
    }
    config = load_config()
    lightweight_mode = is_lightweight_run(run_type, config)
    disable_custom_scraper = os.getenv("ATS_SNIPER_DISABLE_CUSTOM_SCRAPER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Custom scraper can be disabled in environments without Playwright support.
    pre_state = load_state()
    pre_urls = set(pre_state.get("seen_jobs", {}).keys())

    if disable_custom_scraper:
        logger.info("Custom scraper disabled by ATS_SNIPER_DISABLE_CUSTOM_SCRAPER")
    else:
        try:
            custom_targets = get_lightweight_custom_targets(config) if lightweight_mode else None
            if lightweight_mode and not custom_targets:
                logger.info("No lightweight custom targets configured")
            else:
                stage_attempts["custom"] = True
                results["custom"] = await run_custom_scraper(
                    targets=custom_targets,
                    telemetry=direct_scraper_telemetry,
                    dry_run=dry_run,
                ) or []
        except Exception as e:
            logger.warning("Custom scraper error: %s", e)
            stage_issues["custom"].append(str(e))

    # v3 platform scrapers
    if v3_mode and V3_ENABLED:
        ashby_endpoints = filter_platform_endpoints_for_run(
            config,
            run_type,
            "jobs.ashbyhq.com",
            "company_slug",
            get_ashby_endpoints(config),
        )
        greenhouse_endpoints = filter_platform_endpoints_for_run(
            config,
            run_type,
            "boards.greenhouse.io",
            "board_token",
            get_greenhouse_endpoints(config),
        )
        lever_endpoints = filter_platform_endpoints_for_run(
            config,
            run_type,
            "jobs.lever.co",
            "company_slug",
            get_lever_endpoints(config),
        )
        smartrecruiters_endpoints = filter_platform_endpoints_for_run(
            config,
            run_type,
            "jobs.smartrecruiters.com",
            "company_slug",
            get_smartrecruiters_endpoints(config),
        )
        workable_endpoints = filter_platform_endpoints_for_run(
            config,
            run_type,
            "apply.workable.com",
            "subdomain",
            get_workable_endpoints(config),
        )

        scraper_plan = [
            (
                "ashby",
                run_ashby_scrape,
                {"dry_run": dry_run, "endpoints": ashby_endpoints},
            ),
            (
                "greenhouse",
                run_greenhouse_scrape,
                {"dry_run": dry_run, "endpoints": greenhouse_endpoints},
            ),
            (
                "lever",
                run_lever_scrape,
                {"dry_run": dry_run, "endpoints": lever_endpoints},
            ),
            (
                "smartrecruiters",
                run_smartrecruiters_scrape,
                {"dry_run": dry_run, "endpoints": smartrecruiters_endpoints},
            ),
            (
                "workable",
                run_workable_scrape,
                {"dry_run": dry_run, "endpoints": workable_endpoints},
            ),
        ]
        if not lightweight_mode:
            scraper_plan = [
                ("icims", run_icims_scrape, {"dry_run": dry_run}),
                ("oracle", run_oracle_hcm_scrape, {"dry_run": dry_run}),
                *scraper_plan,
            ]

        for name, coro_fn, kwargs in scraper_plan:
            stage_attempts[name] = True
            try:
                results[name] = await coro_fn(**kwargs, telemetry=direct_scraper_telemetry) or []
            except Exception as e:
                logger.warning("%s scraper error: %s", name, e)
                stage_issues[name].append(str(e))

    # Compute new custom URLs by diffing state or by comparing dry-run results.
    if dry_run:
        results["_new_custom_urls"] = {
            str(job.get("url", "")).strip()
            for job in results.get("custom", [])
            if str(job.get("url", "")).strip() and str(job.get("url", "")).strip() not in pre_urls
        }
    else:
        post_state = load_state()
        post_urls = set(post_state.get("seen_jobs", {}).keys())
        results["_new_custom_urls"] = post_urls - pre_urls
    results["_stage_attempts"] = stage_attempts
    results["_stage_issues"] = stage_issues

    return results


def run_pipeline(
    dry_run: bool = False,
    skip_tailor: bool = False,
    v3_mode: bool = True,
    run_type: str = "full",
):
    """Run the full ATS Sniper pipeline."""
    configure_console_streams()
    setup_logging()
    logger.info(
        "ATS Sniper pipeline starting (run_type=%s, mode=%s)",
        run_type,
        "v3" if v3_mode and V3_ENABLED else "v2",
    )
    print("=" * 60)
    print(f"ATS SNIPER - FULL PIPELINE {'v3' if v3_mode and V3_ENABLED else 'v2'}")
    print("=" * 60)

    config = load_config()
    lightweight_mode = is_lightweight_run(run_type, config)
    web_discovery_enabled_for_run = should_run_web_discovery(run_type)
    run_started_at = datetime.now().isoformat()
    tracked_run = should_track_pipeline_run(run_type, dry_run)
    if tracked_run:
        record_pipeline_run(run_type, "running", started_at=run_started_at, stats={})

    def update_running_phase(phase: str) -> None:
        if not tracked_run:
            return
        phase_stats = {**stats, "phase": phase}
        record_pipeline_run(
            run_type,
            "running",
            stats=phase_stats,
            started_at=run_started_at,
        )

    all_new_jobs = []
    benchmark_summary = None
    stats = {
        'workday': 0,
        'custom': 0,
        'ashby': 0,
        'usajobs': 0,
        'icims': 0,
        'oracle': 0,
        'greenhouse': 0,
        'lever': 0,
        'smartrecruiters': 0,
        'workable': 0,
        'web_discovery': 0,
        'total': 0,
        'analyzed': 0,
        'regular_jobs': 0,
        'reviewable_jobs': 0,
        'screened_out_noise': 0,
        'cover_letters_generated': 0,
        'issues': 0,
        'fresh_under_6h': 0,
        'fresh_under_24h': 0,
        'stale_unknown': 0,
        'stale_over_24h': 0,
    }
    email_sent = False
    pipeline_issues: list[str] = []
    web_discovery_telemetry: dict[str, dict[str, int]] = {}
    direct_scraper_telemetry: dict[str, dict[str, dict[str, int]]] = {}
    stage_attempts: dict[str, bool] = {
        'workday': False,
        'custom': False,
        'ashby': False,
        'usajobs': False,
        'icims': False,
        'oracle': False,
        'greenhouse': False,
        'lever': False,
        'smartrecruiters': False,
        'workable': False,
        'web_discovery': False,
    }

    try:
        # 1. Workday Scraper
        update_running_phase("workday")
        print("\n" + "=" * 60)
        stage_attempts['workday'] = True
        workday_jobs, workday_issues = capture_stage_output(
            "workday",
            lambda: run_workday_scrape(dry_run=dry_run, telemetry=direct_scraper_telemetry),
        )
        pipeline_issues.extend(workday_issues)
        stats['workday'] = len(workday_jobs)
        all_new_jobs.extend([{**j, 'source': 'workday_api'} for j in workday_jobs])

        # 2. All async scrapers (custom + v3 platform scrapers) in a single event loop
        update_running_phase("async_scrapers")
        print("\n" + "=" * 60)
        async_results, async_issues = capture_stage_output(
            "async-scrapers",
            lambda: asyncio.run(
                _run_all_async_scrapers(
                    dry_run,
                    v3_mode,
                    run_type,
                    direct_scraper_telemetry=direct_scraper_telemetry,
                )
            ),
        )
        pipeline_issues.extend(async_issues)
        stage_attempts.update(async_results.get("_stage_attempts", {}))
        for stage_name, issues in async_results.get("_stage_issues", {}).items():
            for issue in issues:
                pipeline_issues.append(f"[{stage_name}] {issue}")

        # Process custom scraper results
        new_custom_urls = async_results.get("_new_custom_urls", set())
        new_custom_jobs = [j for j in async_results["custom"] if j.get('url') in new_custom_urls]
        stats['custom'] = len(new_custom_jobs)
        if new_custom_jobs:
            all_new_jobs.extend([{**j, 'source': 'custom_scraper'} for j in new_custom_jobs])

        # Process v3 scraper results
        if v3_mode and V3_ENABLED:
            for name, source in [
                ("ashby", "ashby_board"),
                ("icims", "icims_api"),
                ("oracle", "oracle_hcm_api"),
                ("greenhouse", "greenhouse_api"),
                ("lever", "lever_api"),
                ("smartrecruiters", "smartrecruiters_api"),
                ("workable", "workable_api"),
            ]:
                jobs = async_results.get(name, [])
                stats[name] = len(jobs)
                all_new_jobs.extend([{**j, 'source': source} for j in jobs])

        # 2.5. Web discovery from ATS pages and optional job-board aggregation
        if web_discovery_enabled_for_run:
            update_running_phase("startup_discovery")
            print("\n" + "=" * 60)
            stage_attempts['web_discovery'] = True
            web_discovery_jobs, web_discovery_issues = capture_stage_output(
                "web-discovery",
                lambda: run_web_discovery_scrape(
                    dry_run=dry_run,
                    run_type=run_type,
                    telemetry=web_discovery_telemetry,
                ),
            )
            pipeline_issues.extend(web_discovery_issues)
            stats['web_discovery'] = len(web_discovery_jobs)
            if web_discovery_jobs:
                all_new_jobs.extend(web_discovery_jobs)

        # 3. USAJobs Scraper (Wright-Patt)
        if not lightweight_mode:
            update_running_phase("usajobs")
            print("\n" + "=" * 60)
            stage_attempts['usajobs'] = True
            usajobs, usajobs_issues = capture_stage_output(
                "usajobs",
                lambda: run_usajobs_scraper(telemetry=direct_scraper_telemetry),
            )
            pipeline_issues.extend(usajobs_issues)
            stats['usajobs'] = len(usajobs) if usajobs else 0
            if usajobs:
                all_new_jobs.extend([{**j, 'source': 'usajobs_api'} for j in usajobs])

        all_new_jobs[:] = deduplicate_jobs_by_identity(all_new_jobs)

        freshness_counts = annotate_jobs_with_freshness(
            all_new_jobs,
            config,
            persist_state=not dry_run,
        )
        stats['fresh_under_6h'] = freshness_counts.get('fresh_under_6h', 0)
        stats['fresh_under_24h'] = freshness_counts.get('fresh_under_24h', 0)
        stats['stale_unknown'] = freshness_counts.get('stale_unknown', 0)
        stats['stale_over_24h'] = freshness_counts.get('stale_over_24h', 0)
        feedback_signal_counts = apply_feedback_signals(all_new_jobs)
        all_new_jobs[:] = sort_jobs_for_reporting(all_new_jobs)
        stats['feedback_boosted'] = feedback_signal_counts.get('boosted', 0)
        stats['feedback_neutral'] = feedback_signal_counts.get('neutral', 0)
        stats['feedback_penalized'] = feedback_signal_counts.get('penalized', 0)

        # Load total job count
        state = load_state()
        stats['total'] = len(state.get('jobs', {}))

        # 4. v3 Hot Job Processing (AI Resume Tailoring for >=80% matches)
        hot_job_results = None
        match_results = {}

        if v3_mode and V3_ENABLED and all_new_jobs and not skip_tailor and not lightweight_mode:
            update_running_phase("hot_job_processor")
            print("\n" + "=" * 60)
            print("🔥 V3 HOT JOB PROCESSOR - AI Resume Tailoring")
            print("=" * 60)
            try:
                hot_job_results, hot_job_issues = capture_stage_output(
                    "hot-job-processor",
                    lambda: run_hot_job_pipeline(all_new_jobs, dry_run=dry_run),
                )
                pipeline_issues.extend(hot_job_issues)
                stats['hot_jobs'] = hot_job_results['stats']['hot_count']
                stats['regular_jobs'] = hot_job_results['stats'].get('regular_count', 0)
                stats['reviewable_jobs'] = stats['hot_jobs'] + stats['regular_jobs']
                stats['resumes_generated'] = hot_job_results['stats']['resumes_generated']
                stats['cover_letters_generated'] = hot_job_results['stats'].get('cover_letters_generated', 0)
                stats['analyzed'] = hot_job_results['stats']['total_processed']
                stats['screened_out_noise'] = hot_job_results['stats'].get('screened_out_noise', 0)
                stats['daily_relevant_jobs_target'] = hot_job_results['stats'].get('daily_relevant_jobs_target', 0)
                stats['already_automated_today'] = hot_job_results['stats'].get('already_automated_today', 0)
                stats['auto_promoted_count'] = hot_job_results['stats'].get('auto_promoted_count', 0)
                stats['daily_goal_remaining'] = hot_job_results['stats'].get('daily_goal_remaining', 0)
                stats['eligible_for_promotion'] = hot_job_results['stats'].get('eligible_for_promotion', 0)
                stats['early_classifier_calls'] = hot_job_results['stats'].get('early_classifier_calls', 0)
                stats['full_scoring_calls'] = hot_job_results['stats'].get('full_scoring_calls', 0)
                stats['llm_usage'] = dict(hot_job_results['stats'].get('llm_usage', {}))
                stats['estimated_llm_cost_usd'] = float(
                    hot_job_results['stats'].get('estimated_llm_cost_usd', 0.0) or 0.0
                )

                # Build match results dict for compatibility
                for job in hot_job_results.get('hot_jobs', []):
                    if job.get('url'):
                        match_results[job['url']] = job

                print(
                    f"   ✅ Hot jobs: {stats['hot_jobs']}, Regular jobs: {stats['regular_jobs']}, Auto-promoted: {stats.get('auto_promoted_count', 0)}, Resumes generated: {stats['resumes_generated']}, "
                    f"Cover letters generated: {stats['cover_letters_generated']}, Noise screened: {stats['screened_out_noise']}"
                )
            except Exception as e:
                print(f"   ⚠️ Hot job processor error: {e}")
                traceback.print_exc()
                pipeline_issues.append(f"[hot-job-processor] {e}")

        # Fallback to v2 resume tailor for enterprise jobs
        elif (not v3_mode or not V3_ENABLED) and not lightweight_mode:
            new_enterprise_count = sum(1 for j in all_new_jobs
                                       if any(ec in j.get('company', '').lower() for ec in ENTERPRISE_COMPANIES))

            if new_enterprise_count > 0 and not dry_run and not skip_tailor:
                update_running_phase("resume_tailor_v2")
                print("\n" + "=" * 60)
                print("🎯 RESUME TAILOR - AI Analysis for Enterprise Jobs (v2)")
                print("=" * 60)
                try:
                    tailor_results, tailor_issues = capture_stage_output(
                        "resume-tailor-v2",
                        lambda: process_enterprise_jobs(
                            dry_run=False,
                            limit=min(new_enterprise_count, 10),
                            gen_cover_letters=False,
                        ),
                    )
                    pipeline_issues.extend(tailor_issues)
                    stats['analyzed'] = len(tailor_results) if tailor_results else 0

                    for r in (tailor_results or []):
                        if 'url' in r:
                            match_results[r['url']] = r

                    print(f"   ✅ Analyzed {stats['analyzed']} enterprise jobs")
                except Exception as e:
                    print(f"   ⚠️ Resume tailor error: {e}")
                    pipeline_issues.append(f"[resume-tailor-v2] {e}")

        # Summary
        print("\n" + "=" * 60)
        print("📊 PIPELINE COMPLETE")
        print("=" * 60)
        print(f"   Total jobs in database: {stats['total']}")
        print(f"   New from Workday: {stats['workday']}")
        print(f"   New from Custom: {stats['custom']}")
        if v3_mode and V3_ENABLED:
            print(f"   New from iCIMS: {stats.get('icims', 0)}")
            print(f"   New from Oracle HCM: {stats.get('oracle', 0)}")
            print(f"   New from Greenhouse: {stats.get('greenhouse', 0)}")
            print(f"   New from Lever: {stats.get('lever', 0)}")
            print(f"   New from SmartRecruiters: {stats.get('smartrecruiters', 0)}")
            print(f"   New from Workable: {stats.get('workable', 0)}")
        if web_discovery_enabled_for_run:
            print(f"   New from Web Discovery: {stats.get('web_discovery', 0)}")
        print(f"   New from USAJobs: {stats['usajobs']}")
        print(f"   🕒 Fresh <6h: {stats.get('fresh_under_6h', 0)}")
        print(f"   🕓 Fresh <24h: {stats.get('fresh_under_24h', 0)}")
        print(f"   👍 Feedback Boosted: {stats.get('feedback_boosted', 0)}")
        print(f"   👎 Feedback Penalized: {stats.get('feedback_penalized', 0)}")
        print(f"   🔥 Hot Jobs: {stats.get('hot_jobs', 0)}")
        print(f"   📋 Reviewable Jobs: {stats.get('reviewable_jobs', 0)}")
        if stats.get('daily_relevant_jobs_target', 0):
            print(
                f"   🎯 Daily Goal: {stats.get('daily_relevant_jobs_target', 0)} target, "
                f"{stats.get('auto_promoted_count', 0)} auto-promoted, "
                f"{stats.get('daily_goal_remaining', 0)} remaining"
            )
        print(f"   📄 Resumes Generated: {stats.get('resumes_generated', 0)}")
        print(f"   ✉️ Cover Letters Generated: {stats.get('cover_letters_generated', 0)}")
        print(f"   🧹 Noise Screened: {stats.get('screened_out_noise', 0)}")
        if stats.get('estimated_llm_cost_usd', 0.0):
            print(f"   💰 Estimated LLM Cost: ${stats.get('estimated_llm_cost_usd', 0.0):.6f}")
        stats['issues'] = len(pipeline_issues)
        if pipeline_issues:
            print(f"   ⚠️ Issues Detected: {len(pipeline_issues)}")

        # 5. Update Master CSV with all jobs
        if not dry_run:
            update_running_phase("update_master_csv")
            state = load_state()
            update_master_csv(state)

        # 6. Generate resumes (run generate_resumes.py) - v2 only
        if not dry_run and not (v3_mode and V3_ENABLED) and not lightweight_mode:
            update_running_phase("generate_resumes")
            _, resume_generation_issues = capture_stage_output("resume-files", generate_resume_files)
            pipeline_issues.extend(resume_generation_issues)

        try:
            benchmark_set = load_discovery_benchmark_set()
            benchmark_state = load_state()
            previous_benchmark_summary = find_previous_benchmark_summary(benchmark_state, run_type)
            tracked_state_jobs = [
                {**record, 'url': str(record.get('url', job_url)).strip() or job_url}
                for job_url, record in benchmark_state.get('jobs', {}).items()
                if isinstance(record, dict)
            ]
            benchmark_summary = build_discovery_benchmark_summary(
                tracked_state_jobs,
                benchmark_set,
                previous_summary=previous_benchmark_summary,
                scope_name='state_after_run',
                comparison_scopes={'net_new_jobs': all_new_jobs},
            )
        except Exception as exc:
            pipeline_issues.append(f"[benchmark] {exc}")

        stats['issues'] = len(pipeline_issues)

        if tracked_run and benchmark_summary:
            state = load_state()
            upsert_pipeline_run_record(
                state,
                run_type,
                {"benchmark_summary": compact_discovery_benchmark_summary(benchmark_summary)},
            )
            save_state(state)

        final_run_status = "success" if all_new_jobs else "no_jobs"
        final_completed_at = datetime.now().isoformat()

        # Fold per-stage already-seen counts from scraper telemetry into stats so the
        # audit stage summary can distinguish "empty stage" from "no fresh data".
        already_seen_counters = (
            direct_scraper_telemetry.get("_already_seen", {})
            if isinstance(direct_scraper_telemetry, dict)
            else {}
        )
        for stage_key, already_seen_count in already_seen_counters.items():
            stats[f"{stage_key}_already_seen"] = int(already_seen_count or 0)

        if not dry_run:
            record_source_health_snapshot(
                run_type=run_type,
                stats=stats,
                pipeline_issues=pipeline_issues,
                stage_attempts=stage_attempts,
            )
            audit_report_paths = write_discovery_audit_report(
                run_type=run_type,
                stats=stats,
                all_new_jobs=all_new_jobs,
                hot_job_results=hot_job_results,
                pipeline_issues=pipeline_issues,
                web_discovery_telemetry=web_discovery_telemetry,
                direct_scraper_telemetry=direct_scraper_telemetry,
                stage_attempts=stage_attempts,
                benchmark_summary=benchmark_summary,
            )
            print(f"   🧾 Discovery audit JSON: {audit_report_paths['json_path']}")
            print(f"   📝 Discovery audit Markdown: {audit_report_paths['markdown_path']}")

        if tracked_run:
            record_pipeline_run(
                run_type,
                final_run_status,
                stats=stats,
                error_message="\n".join(pipeline_issues[:8]),
                email_sent=False,
                total_new_jobs=len(all_new_jobs),
                started_at=run_started_at,
                completed_at=final_completed_at,
            )

        # 7. Send email (v3 with attachments or v2 basic)
        if all_new_jobs and not dry_run:
            if v3_mode and V3_ENABLED and hot_job_results:
                # v3: Send with tailored resume attachments
                hot_jobs = hot_job_results.get('hot_jobs', []) or []
                regular_jobs = hot_job_results.get('regular_jobs', []) or []
                if hot_jobs or regular_jobs:
                    attachments = get_hot_job_attachments(hot_jobs)

                    email_sent = send_hot_job_email(
                        hot_jobs=hot_jobs,
                        regular_jobs=regular_jobs,
                        stats=hot_job_results.get('stats', {}),
                        attachments=attachments
                    )
                    if not email_sent:
                        pipeline_issues.append("[email] Hot-job notification email failed")
                else:
                    print("\n📭 No reviewable jobs remained after screening")
                    if tracked_run:
                        email_sent = send_no_reviewable_jobs_email(
                            run_type,
                            stats,
                            config,
                            total_new_jobs=len(all_new_jobs),
                            screened_out_noise=stats.get('screened_out_noise', 0),
                            screened_out_jobs=hot_job_results.get('screened_out_jobs', []),
                        )
                        if not email_sent:
                            pipeline_issues.append("[email] No-reviewable-jobs status email failed")
            else:
                # v2: Basic email
                email_sent = send_pipeline_email(all_new_jobs, stats, config, match_results)
                if not email_sent:
                    pipeline_issues.append("[email] Pipeline email failed")
        elif not all_new_jobs:
            print("\n📭 No new jobs found")
            if tracked_run:
                email_sent = send_no_jobs_email(run_type, stats, config)
                if not email_sent:
                    pipeline_issues.append("[email] No-jobs status email failed")

        stats['issues'] = len(pipeline_issues)
        if tracked_run and pipeline_issues:
            send_issue_email(run_type, stats, config, pipeline_issues)

        if tracked_run:
            record_pipeline_run(
                run_type,
                final_run_status,
                stats=stats,
                error_message="\n".join(pipeline_issues[:8]),
                email_sent=email_sent,
                total_new_jobs=len(all_new_jobs),
                started_at=run_started_at,
                completed_at=final_completed_at,
            )

        return all_new_jobs
    except Exception as exc:
        if tracked_run:
            record_pipeline_run(
                run_type,
                "failed",
                stats=stats,
                error_message=str(exc),
                email_sent=False,
                total_new_jobs=len(all_new_jobs),
                started_at=run_started_at,
                completed_at=datetime.now().isoformat(),
            )
            send_failure_email(run_type, stats, config, str(exc))
        raise


def update_master_csv(state: dict):
    """Update master CSV with all tracked jobs."""
    print("\n📊 Updating master CSV...")
    master_csv_path = master_jobs_csv_path()
    master_csv_path.parent.mkdir(parents=True, exist_ok=True)

    jobs = state.get('jobs', {})
    if not jobs:
        print("   No jobs to export")
        return

    with open(master_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Company', 'Title', 'Location', 'Salary', 'Contact Email', 'Source', 'Source Family', 'Source Board', 'Query Profile', 'Freshness Bucket', 'Freshness Basis', 'Posted Date', 'First Seen At', 'URL', 'Date Added', 'Priority'])

        for url, job in jobs.items():
            writer.writerow([
                job.get('company', 'Unknown'),
                job.get('title', 'Unknown'),
                job.get('location', ''),
                job.get('salary', ''),
                format_contact_emails(job.get('contact_emails', [])) or job.get('contact_email', ''),
                job.get('source', 'unknown'),
                job.get('source_family', ''),
                job.get('source_board', ''),
                job.get('query_profile', ''),
                job.get('freshness_bucket', ''),
                job.get('freshness_basis', ''),
                job.get('posted_date', job.get('date_posted', '')),
                job.get('first_seen_at', job.get('first_seen', '')),
                url,
                job.get('source_detected_at', job.get('scraped_at', job.get('posted_date', ''))),
                job.get('priority', 'MEDIUM')
            ])

    print(f"   ✅ Exported {len(jobs)} jobs to {master_csv_path.name}")


def summarize_jobs_for_response(jobs: list[dict], limit: int = 15) -> list[dict[str, str]]:
    """Build a lightweight JSON-safe summary of new jobs."""
    summary: list[dict[str, str]] = []
    for job in jobs[:limit]:
        summary.append(
            {
                "company": str(job.get("company", "Unknown")),
                "title": str(job.get("title", "Unknown")),
                "location": str(job.get("location", "")),
                "source": str(job.get("source", "unknown")),
                "freshness_bucket": str(job.get("freshness_bucket", "")),
                "url": str(job.get("url", "")),
            }
        )
    return summary


def generate_resume_files():
    """Run resume generation script."""
    print("\n📄 Generating resume files...")
    try:
        import subprocess
        import os as os_mod
        # Change to parent directory and run from there
        parent_dir = str(SCRIPT_DIR.parent)
        env = os_mod.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'

        result = subprocess.run(
            [sys.executable, 'generate_resumes.py'],
            cwd=parent_dir,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120,
            env=env
        )
        if result.returncode == 0:
            # Count generated files
            pdf_dir = SCRIPT_DIR.parent / 'pdf_files'
            if pdf_dir.exists():
                pdfs = list(pdf_dir.glob('*.pdf'))
                print(f"   ✅ Generated {len(pdfs)} resume PDFs")
        else:
            print(f"   ⚠️ Resume generation returned code {result.returncode}")
            if result.stderr:
                # Get last useful error line
                err_lines = [l for l in result.stderr.split('\n') if l.strip()]
                if err_lines:
                    print(f"      {err_lines[-1][:100]}")
    except Exception as e:
        print(f"   ⚠️ Resume generation error: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ATS Sniper Full Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Don't save any files")
    parser.add_argument("--skip-tailor", action="store_true", help="Skip resume tailoring")
    parser.add_argument("--v2", action="store_true", help="Use v2 mode (disable v3 features)")
    parser.add_argument(
        "--run-type",
        choices=["morning", "afternoon", "full", "lightweight"],
        default="full",
        help="Select full or lightweight behavior for scheduled runs.",
    )

    args = parser.parse_args()

    run_pipeline(
        dry_run=args.dry_run,
        skip_tailor=args.skip_tailor,
        v3_mode=not args.v2,
        run_type=args.run_type,
    )

