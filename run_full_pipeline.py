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
import smtplib
import asyncio
import re
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

from workday_scraper import run_workday_scrape
from custom_scraper import run_custom_scraper
from usajobs_scraper import run_usajobs_scraper
from resume_tailor import process_enterprise_jobs

# v3 imports
try:
    from icims_scraper import run_icims_scrape
    from oracle_hcm_scraper import run_oracle_hcm_scrape
    from greenhouse_scraper import run_greenhouse_scrape
    from lever_scraper import run_lever_scrape
    from hot_job_processor import run_hot_job_pipeline, get_hot_job_attachments
    from email_with_attachments import send_hot_job_email
    V3_ENABLED = True
except ImportError as e:
    print(f"⚠️ v3 modules not available: {e}")
    V3_ENABLED = False

CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "job_state.json"

# Enterprise companies (for resume tailor)
ENTERPRISE_COMPANIES = ['p&g', 'procter', 'medpace', 'ge', 'fidelity', 'cvs', 'worldpay',
                        'cintas', 'cchmc', 'gaig', 'fifth third', 'kroger']


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state():
    with open(STATE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


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
    writer.writerow(['Company', 'Title', 'Location', 'Salary', 'Source', 'URL', 'Date Found'])

    for job in jobs:
        writer.writerow([
            job.get('company', 'Unknown'),
            job.get('title', 'Unknown'),
            job.get('location', ''),
            job.get('salary', ''),
            job.get('source', 'unknown'),
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
    hot_jobs = [j for j in new_jobs if is_hot_job(j, match_results)]
    other_jobs = [j for j in new_jobs if not is_hot_job(j, match_results)]

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

        <h2>🔥 Hot Jobs ({len(hot_jobs)})</h2>
        <p><em>Criteria: Match ≥80% OR Salary ≥$80k OR Enterprise + Relevant Title</em></p>
    """

    if hot_jobs:
        for job in hot_jobs[:20]:  # Limit to 20 hot jobs
            source = job.get('source', 'unknown')
            salary = job.get('salary', '')
            salary_badge = f'<span class="badge badge-salary">{salary}</span>' if salary else ''
            html += f"""
            <div class="job hot">
                <strong>{job.get('title', 'Unknown')}</strong> @ {job.get('company', 'Unknown')} {salary_badge}<br>
                <a href="{job.get('url', '#')}">{job.get('url', 'No URL')[:70]}...</a><br>
                <small>Source: {source} | Location: {job.get('location', 'N/A')}</small>
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
        with smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port']) as server:
            server.starttls()
            server.login(email_config['sender_email'], email_config['sender_password'])
            server.sendmail(email_config['sender_email'], email_config['recipient_email'], msg.as_string())
        print("  ✅ Email sent successfully!")
        return True
    except Exception as e:
        print(f"  ❌ Email failed: {e}")
        return False


def run_pipeline(dry_run: bool = False, skip_tailor: bool = False, v3_mode: bool = True):
    """Run the full ATS Sniper pipeline."""
    print("=" * 60)
    print(f"🎯 ATS SNIPER - FULL PIPELINE {'v3' if v3_mode and V3_ENABLED else 'v2'}")
    print("=" * 60)

    config = load_config()
    all_new_jobs = []
    stats = {'workday': 0, 'custom': 0, 'usajobs': 0, 'icims': 0, 'oracle': 0, 'greenhouse': 0, 'lever': 0, 'total': 0, 'analyzed': 0}

    # 1. Workday Scraper
    print("\n" + "=" * 60)
    workday_jobs = run_workday_scrape(dry_run=dry_run)
    stats['workday'] = len(workday_jobs)
    all_new_jobs.extend([{**j, 'source': 'workday_api'} for j in workday_jobs])

    # 2. Custom Scraper (Medpace, Kroger, etc.)
    print("\n" + "=" * 60)
    # Get jobs seen before running custom scraper
    pre_custom_state = load_state()
    pre_custom_urls = set(pre_custom_state.get('seen_jobs', {}).keys())

    custom_jobs = asyncio.run(run_custom_scraper())

    # After running, check which jobs are actually new
    post_custom_state = load_state()
    post_custom_urls = set(post_custom_state.get('seen_jobs', {}).keys())
    new_custom_urls = post_custom_urls - pre_custom_urls

    # Filter to only new jobs
    new_custom_jobs = [j for j in (custom_jobs or []) if j.get('url') in new_custom_urls]
    stats['custom'] = len(new_custom_jobs)
    if new_custom_jobs:
        all_new_jobs.extend([{**j, 'source': 'custom_scraper'} for j in new_custom_jobs])

    # 2.5. v3 Platform-Specific Scrapers (iCIMS, Oracle HCM)
    if v3_mode and V3_ENABLED:
        print("\n" + "=" * 60)
        print("🆕 V3 SCRAPERS - Platform-Specific APIs")
        print("=" * 60)

        # iCIMS (Western & Southern)
        try:
            icims_jobs = asyncio.run(run_icims_scrape(dry_run=dry_run))
            stats['icims'] = len(icims_jobs)
            all_new_jobs.extend([{**j, 'source': 'icims_api'} for j in icims_jobs])
        except Exception as e:
            print(f"   ⚠️ iCIMS scraper error: {e}")

        # Oracle HCM (UC Health)
        try:
            oracle_jobs = asyncio.run(run_oracle_hcm_scrape(dry_run=dry_run))
            stats['oracle'] = len(oracle_jobs)
            all_new_jobs.extend([{**j, 'source': 'oracle_hcm_api'} for j in oracle_jobs])
        except Exception as e:
            print(f"   ⚠️ Oracle HCM scraper error: {e}")

        # Greenhouse (84.51, GitLab, Cloudflare, etc.)
        try:
            greenhouse_jobs = asyncio.run(run_greenhouse_scrape(dry_run=dry_run))
            stats['greenhouse'] = len(greenhouse_jobs)
            all_new_jobs.extend([{**j, 'source': 'greenhouse_api'} for j in greenhouse_jobs])
        except Exception as e:
            print(f"   ⚠️ Greenhouse scraper error: {e}")

        # Lever (Restaurant365, H1, Spotify, etc.)
        try:
            lever_jobs = asyncio.run(run_lever_scrape(dry_run=dry_run))
            stats['lever'] = len(lever_jobs)
            all_new_jobs.extend([{**j, 'source': 'lever_api'} for j in lever_jobs])
        except Exception as e:
            print(f"   ⚠️ Lever scraper error: {e}")

    # 3. USAJobs Scraper (Wright-Patt)
    print("\n" + "=" * 60)
    usajobs = run_usajobs_scraper()
    stats['usajobs'] = len(usajobs) if usajobs else 0
    if usajobs:
        all_new_jobs.extend([{**j, 'source': 'usajobs_api'} for j in usajobs])

    # Load total job count
    state = load_state()
    stats['total'] = len(state.get('jobs', {}))

    # 4. v3 Hot Job Processing (AI Resume Tailoring for >=80% matches)
    hot_job_results = None
    match_results = {}

    if v3_mode and V3_ENABLED and all_new_jobs and not dry_run and not skip_tailor:
        print("\n" + "=" * 60)
        print("🔥 V3 HOT JOB PROCESSOR - AI Resume Tailoring")
        print("=" * 60)
        try:
            hot_job_results = run_hot_job_pipeline(all_new_jobs, dry_run=dry_run)
            stats['hot_jobs'] = hot_job_results['stats']['hot_count']
            stats['resumes_generated'] = hot_job_results['stats']['resumes_generated']
            stats['analyzed'] = hot_job_results['stats']['total_processed']

            # Build match results dict for compatibility
            for job in hot_job_results.get('hot_jobs', []):
                if job.get('url'):
                    match_results[job['url']] = job

            print(f"   ✅ Hot jobs: {stats['hot_jobs']}, Resumes generated: {stats['resumes_generated']}")
        except Exception as e:
            print(f"   ⚠️ Hot job processor error: {e}")
            import traceback
            traceback.print_exc()

    # Fallback to v2 resume tailor for enterprise jobs
    elif not v3_mode or not V3_ENABLED:
        new_enterprise_count = sum(1 for j in all_new_jobs
                                   if any(ec in j.get('company', '').lower() for ec in ENTERPRISE_COMPANIES))

        if new_enterprise_count > 0 and not dry_run and not skip_tailor:
            print("\n" + "=" * 60)
            print("🎯 RESUME TAILOR - AI Analysis for Enterprise Jobs (v2)")
            print("=" * 60)
            try:
                tailor_results = process_enterprise_jobs(
                    dry_run=False,
                    limit=min(new_enterprise_count, 10),
                    gen_cover_letters=False
                )
                stats['analyzed'] = len(tailor_results) if tailor_results else 0

                for r in (tailor_results or []):
                    if 'url' in r:
                        match_results[r['url']] = r

                print(f"   ✅ Analyzed {stats['analyzed']} enterprise jobs")
            except Exception as e:
                print(f"   ⚠️ Resume tailor error: {e}")

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
    print(f"   New from USAJobs: {stats['usajobs']}")
    print(f"   🔥 Hot Jobs: {stats.get('hot_jobs', 0)}")
    print(f"   📄 Resumes Generated: {stats.get('resumes_generated', 0)}")

    # 5. Update Master CSV with all jobs
    if not dry_run:
        update_master_csv(state)

    # 6. Send email (v3 with attachments or v2 basic)
    if all_new_jobs and not dry_run:
        if v3_mode and V3_ENABLED and hot_job_results:
            # v3: Send with tailored resume attachments
            hot_jobs = hot_job_results.get('hot_jobs', [])
            regular_jobs = hot_job_results.get('regular_jobs', [])
            attachments = get_hot_job_attachments(hot_jobs)

            send_hot_job_email(
                hot_jobs=hot_jobs,
                regular_jobs=regular_jobs,
                stats=hot_job_results.get('stats', {}),
                attachments=attachments
            )
        else:
            # v2: Basic email
            send_pipeline_email(all_new_jobs, stats, config, match_results)
    elif not all_new_jobs:
        print("\n📭 No new jobs found - skipping email")

    # 7. Generate resumes (run generate_resumes.py) - v2 only
    if not dry_run and not (v3_mode and V3_ENABLED):
        generate_resume_files()

    return all_new_jobs


def update_master_csv(state: dict):
    """Update master CSV with all tracked jobs."""
    print("\n📊 Updating master CSV...")
    master_csv_path = SCRIPT_DIR / "jobs_export.csv"

    jobs = state.get('jobs', {})
    if not jobs:
        print("   No jobs to export")
        return

    with open(master_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Company', 'Title', 'Location', 'Salary', 'Source', 'URL', 'Date Added', 'Priority'])

        for url, job in jobs.items():
            writer.writerow([
                job.get('company', 'Unknown'),
                job.get('title', 'Unknown'),
                job.get('location', ''),
                job.get('salary', ''),
                job.get('source', 'unknown'),
                url,
                job.get('scraped_at', job.get('posted_date', '')),
                job.get('priority', 'MEDIUM')
            ])

    print(f"   ✅ Exported {len(jobs)} jobs to {master_csv_path.name}")


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
            ['python', 'generate_resumes.py'],
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

    args = parser.parse_args()

    run_pipeline(
        dry_run=args.dry_run,
        skip_tailor=args.skip_tailor,
        v3_mode=not args.v2
    )

