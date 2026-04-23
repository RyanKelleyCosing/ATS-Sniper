#!/usr/bin/env python3
"""
Enhanced Email Module - Sends notifications with tailored resume and cover-letter attachments

For Hot Jobs (>=80% match):
- Generates and attaches tailored resumes and cover letters
- Includes match analysis in email body
- Ready-to-apply package for human review
"""

import smtplib
import json
import csv
from io import StringIO
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Dict, List, Optional

# Paths
SCRIPT_DIR = Path(__file__).parent

from utils.state import load_config
from utils.contacts import format_contact_emails, primary_contact_email
from utils.pipeline_freshness import freshness_badge_label, sort_jobs_by_freshness

EMAIL_TIMEOUT_SECONDS = 30


def build_hot_job_email_subject(hot_jobs: List[Dict], regular_jobs: List[Dict]) -> str:
    """Return subject text that matches the actual notification content."""
    timestamp = datetime.now().strftime('%b %d, %I:%M %p')
    if hot_jobs:
        return f"🔥 {len(hot_jobs)} Hot Jobs + Resumes Ready - {timestamp}"
    if regular_jobs:
        return f"📋 {len(regular_jobs)} Jobs Ready For Review - {timestamp}"
    return f"📭 No Reviewable Jobs - {timestamp}"


def get_hot_job_email_header_copy(hot_jobs: List[Dict], regular_jobs: List[Dict]) -> tuple[str, str]:
    """Return the header title and subtitle for the notification email."""
    if hot_jobs:
        return "🔥 HOT JOBS ALERT", "Tailored Resumes + Cover Letters Attached"
    if regular_jobs:
        return "📋 JOBS READY FOR REVIEW", "CSV attached for manual review"
    return "📭 NO REVIEWABLE JOBS", "All new jobs were screened out before review"


def build_hot_job_html(hot_jobs: List[Dict], regular_jobs: List[Dict], stats: Dict) -> str:
    """Build HTML email body for hot job notification."""
    hot_jobs = sort_jobs_by_freshness(hot_jobs)
    regular_jobs = sort_jobs_by_freshness(regular_jobs)
    header_title, header_subtitle = get_hot_job_email_header_copy(hot_jobs, regular_jobs)
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
        .container {{ max-width: 700px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
        .header {{ background: linear-gradient(135deg, #dc2626 0%, #991b1b 100%); color: white; padding: 30px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 26px; }}
        .header .subtitle {{ font-size: 14px; opacity: 0.9; margin-top: 8px; }}
        .stats-bar {{ background: #fef2f2; padding: 16px 24px; display: flex; justify-content: space-around; border-bottom: 1px solid #fecaca; }}
        .stat {{ text-align: center; }}
        .stat-num {{ font-size: 24px; font-weight: 700; color: #dc2626; }}
        .stat-label {{ font-size: 11px; color: #666; text-transform: uppercase; }}
        .section {{ padding: 20px 24px; border-bottom: 1px solid #eee; }}
        .hot-job {{ background: #fef2f2; border: 2px solid #dc2626; border-radius: 8px; padding: 16px; margin-bottom: 12px; }}
        .hot-job .title {{ font-size: 16px; font-weight: 600; color: #1a1a1a; margin: 0 0 6px 0; }}
        .hot-job .company {{ font-size: 14px; color: #dc2626; margin: 0 0 8px 0; }}
        .hot-job .score {{ display: inline-block; background: #dc2626; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
        .hot-job .freshness {{ display: inline-block; background: #0f766e; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-left: 8px; }}
        .hot-job .freshness24 {{ background: #0ea5e9; }}
        .hot-job .details {{ font-size: 12px; color: #666; margin-top: 8px; }}
        .hot-job .resume-note {{ background: #dcfce7; color: #166534; padding: 6px 12px; border-radius: 4px; font-size: 11px; margin-top: 10px; }}
        .apply-btn {{ display: inline-block; background: #dc2626; color: white; padding: 8px 20px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 500; margin-top: 10px; }}
        .regular-summary {{ background: #f8f9fa; padding: 16px; border-radius: 8px; }}
        .footer {{ background: #f8f9fa; padding: 20px; text-align: center; font-size: 11px; color: #888; }}
        .action-list {{ background: #eff6ff; padding: 16px; border-radius: 8px; margin: 16px 0; }}
        .action-list li {{ margin: 8px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{header_title}</h1>
            <div class="subtitle">{header_subtitle}</div>
        </div>
        <div class="stats-bar">
            <div class="stat"><div class="stat-num">{len(hot_jobs)}</div><div class="stat-label">Hot Jobs</div></div>
            <div class="stat"><div class="stat-num">{stats.get('resumes_generated', 0)}</div><div class="stat-label">Resumes Generated</div></div>
            <div class="stat"><div class="stat-num">{stats.get('cover_letters_generated', 0)}</div><div class="stat-label">Cover Letters</div></div>
            <div class="stat"><div class="stat-num">{stats.get('auto_promoted_count', 0)}</div><div class="stat-label">Auto-Promoted</div></div>
            <div class="stat"><div class="stat-num">{len(regular_jobs)}</div><div class="stat-label">In CSV</div></div>
        </div>
"""
    
    if hot_jobs:
        html += '<div class="section"><h2>🎯 Apply Now - Resumes Attached</h2>'
        
        for job in hot_jobs:
            score = job.get("match_score", 0)
            has_ats_resume = bool(job.get("resume_ats_docx"))
            has_resume = bool(has_ats_resume or job.get("resume_pdf") or job.get("resume_docx"))
            has_cover_letter = bool(job.get("cover_letter_docx") or job.get("cover_letter_txt"))
            freshness_bucket = str(job.get("freshness_bucket", "")).strip()
            freshness_label = freshness_badge_label(freshness_bucket)
            freshness_badge = ""
            if freshness_label and freshness_bucket.startswith("fresh_under"):
                freshness_class = "freshness" if freshness_bucket == "fresh_under_6h" else "freshness freshness24"
                freshness_badge = f'<span class="{freshness_class}">{freshness_label}</span>'
            promotion_badge = (
                '<span class="freshness">Auto-Promoted</span>'
                if job.get("phase6_auto_promoted") else ''
            )
            artifact_parts = []
            if has_ats_resume:
                artifact_parts.append("ATS resume DOCX")
            elif has_resume:
                artifact_parts.append("resume")
            if has_cover_letter:
                artifact_parts.append("cover letter")
            artifact_note = ''
            if artifact_parts:
                if has_ats_resume:
                    artifact_note = (
                        '<div class="resume-note">✅ Tailored ' + ' + '.join(artifact_parts) + ' attached below. '
                        'Upload the ATS DOCX first for ATS forms; keep the PDF only for visual review.</div>'
                    )
                else:
                    artifact_note = (
                        '<div class="resume-note">✅ Tailored ' + ' + '.join(artifact_parts) + ' attached below</div>'
                    )
            contact_emails = format_contact_emails(job.get("contact_emails", [])) or job.get("contact_email", "")
            contact_email = primary_contact_email(job.get("contact_emails", [])) or job.get("contact_email", "")
            contact_line = (
                f' | 📧 <a href="mailto:{contact_email}">{contact_emails}</a>'
                if contact_email and contact_emails else ''
            )
            
            html += f'''
            <div class="hot-job">
                <p class="title">{job.get("title", "Unknown")}</p>
                <p class="company">🏢 {job.get("company", "Unknown")}</p>
                <span class="score">{score}% Match</span>{freshness_badge}{promotion_badge}
                <div class="details">
                    📍 {job.get("location", "N/A")} | 
                    📅 Posted: {job.get("posted_date", job.get("date_posted", "unknown"))}{contact_line}
                </div>
                {artifact_note}
                <a href="{job.get("url", "#")}" class="apply-btn">Apply Now →</a>
            </div>
            '''
        
        html += '</div>'
    
    # Action items section
    if hot_jobs:
        action_items = '''
        <div class="section">
            <h2>📋 Next Steps</h2>
            <div class="action-list">
                <ol>
                    <li><strong>Review attached resumes</strong> - They're tailored to each job</li>
                    <li><strong>Click "Apply Now"</strong> on jobs above and upload the ATS DOCX first; keep the PDF for visual review only</li>
                    <li><strong>Check the CSV</strong> for additional opportunities below threshold</li>
                </ol>
            </div>
        </div>
    '''
    elif regular_jobs:
        action_items = '''
        <div class="section">
            <h2>📋 Next Steps</h2>
            <div class="action-list">
                <ol>
                    <li><strong>Review the attached CSV</strong> for the strongest manual-review targets</li>
                    <li><strong>Open the job links</strong> for roles worth pursuing and shortlist them</li>
                    <li><strong>Generate tailored materials on demand</strong> for any job you want to pursue immediately</li>
                </ol>
            </div>
        </div>
    '''
    else:
        action_items = '''
        <div class="section">
            <h2>📋 Outcome</h2>
            <div class="action-list">
                <ol>
                    <li><strong>No manual review is required</strong> for this run</li>
                    <li><strong>All newly discovered roles</strong> were screened out before scoring or packaging</li>
                    <li><strong>Watch the next scheduled run</strong> for the next set of targets</li>
                </ol>
            </div>
        </div>
    '''

    html += action_items
    
    # Regular jobs summary
    if regular_jobs:
        html += f'''
        <div class="section">
            <h2>📊 Additional Jobs ({len(regular_jobs)})</h2>
            <div class="regular-summary">
                <p>These jobs scored below 80% match but may still be worth reviewing.</p>
                <p>See attached CSV for full details.</p>
            </div>
        </div>
        '''
    
    html += f'''
        <div class="footer">
            <p>ATS Sniper v3 - AI-Powered Resume Tailoring</p>
            <p>{datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
        </div>
    </div>
</body>
</html>'''
    
    return html


def create_jobs_csv(jobs: List[Dict]) -> str:
    """Create CSV string of jobs for attachment."""
    output = StringIO()
    writer = csv.writer(output)
    jobs = sort_jobs_by_freshness(jobs)

    # Header
    writer.writerow([
        'Company',
        'Title',
        'Location',
        'Freshness',
        'Posted Date',
        'Contact Email',
        'Match Score',
        'Automation Status',
        'Auto Promotion Reason',
        'Source',
        'Source Family',
        'Query Profile',
        'URL',
        'Date Found',
    ])

    for job in jobs:
        writer.writerow([
            job.get('company', 'Unknown'),
            job.get('title', 'Unknown'),
            job.get('location', ''),
            freshness_badge_label(job.get('freshness_bucket', '')),
            job.get('posted_date', job.get('date_posted', '')),
            format_contact_emails(job.get('contact_emails', [])) or job.get('contact_email', ''),
            job.get('match_score', ''),
            job.get('automation_status', ''),
            job.get('auto_promotion_reason', ''),
            job.get('source', 'unknown'),
            job.get('source_family', ''),
            job.get('query_profile', ''),
            job.get('url', ''),
            datetime.now().strftime('%Y-%m-%d')
        ])

    return output.getvalue()


def send_hot_job_email(
    hot_jobs: List[Dict],
    regular_jobs: List[Dict],
    stats: Dict,
    attachments: List[Dict] = None
) -> bool:
    """
    Send email notification with tailored resumes attached.

    Args:
        hot_jobs: List of hot job dicts with resume paths
        regular_jobs: List of regular job dicts
        stats: Pipeline statistics
        attachments: List of generated resume and cover-letter attachments

    Returns:
        True if email sent successfully
    """
    config = load_config()
    email_config = config.get('email', {})

    if not email_config.get('sender_email'):
        print("⚠️ Email not configured in config.json")
        return False

    # Build email
    msg = MIMEMultipart('mixed')
    msg['Subject'] = build_hot_job_email_subject(hot_jobs, regular_jobs)
    msg['From'] = email_config['sender_email']
    msg['To'] = email_config['recipient_email']

    # Add HTML body
    html_content = build_hot_job_html(hot_jobs, regular_jobs, stats)
    msg.attach(MIMEText(html_content, 'html'))

    # Attach tailored resumes
    attachments = attachments or []
    for att in attachments:
        attach_file(msg, att['path'], att.get('filename'))

    # Attach CSV of ALL jobs (hot + regular)
    all_jobs = hot_jobs + regular_jobs
    if all_jobs:
        csv_content = create_jobs_csv(all_jobs)
        csv_attachment = MIMEBase('text', 'csv')
        csv_attachment.set_payload(csv_content)
        encoders.encode_base64(csv_attachment)
        csv_filename = f"ats_sniper_jobs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        csv_attachment.add_header('Content-Disposition', f'attachment; filename="{csv_filename}"')
        msg.attach(csv_attachment)
        print(f"   📊 CSV attached: {len(all_jobs)} jobs")

    # Send email
    try:
        print(f"\n📧 Sending hot job notification to {email_config['recipient_email']}...")
        print(f"   🔥 Hot jobs: {len(hot_jobs)}")
        print(f"   📎 Generated attachments: {len(attachments)}")

        with smtplib.SMTP(
            email_config['smtp_server'],
            email_config['smtp_port'],
            timeout=EMAIL_TIMEOUT_SECONDS,
        ) as server:
            server.starttls()
            server.login(email_config['sender_email'], email_config['sender_password'])
            server.sendmail(
                email_config['sender_email'],
                email_config['recipient_email'],
                msg.as_string()
            )

        print("  ✅ Email sent successfully!")
        return True

    except Exception as e:
        print(f"  ❌ Email failed: {e}")
        return False


def attach_file(msg: MIMEMultipart, filepath: str, filename: str = None):
    """Attach a file to the email message."""
    path = Path(filepath)

    if not path.exists():
        print(f"  ⚠️ Attachment not found: {filepath}")
        return

    filename = filename or path.name

    # Determine MIME type
    if path.suffix.lower() == '.pdf':
        maintype, subtype = 'application', 'pdf'
    elif path.suffix.lower() == '.docx':
        maintype, subtype = 'application', 'vnd.openxmlformats-officedocument.wordprocessingml.document'
    elif path.suffix.lower() == '.csv':
        maintype, subtype = 'text', 'csv'
    else:
        maintype, subtype = 'application', 'octet-stream'

    with open(path, 'rb') as f:
        attachment = MIMEBase(maintype, subtype)
        attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header('Content-Disposition', f'attachment; filename="{filename}"')
        msg.attach(attachment)

    print(f"   📎 Attached: {filename}")


if __name__ == "__main__":
    # Test with sample data
    test_hot_jobs = [
        {
            "title": "Senior DevOps Engineer",
            "company": "Test Corp",
            "url": "https://example.com/job/1",
            "match_score": 85,
            "location": "Cincinnati, OH",
            "resume_pdf": None
        }
    ]

    test_regular_jobs = [
        {"title": "Junior Developer", "company": "Other Corp", "match_score": 60}
    ]

    test_stats = {
        "total_processed": 10,
        "hot_count": 1,
        "regular_count": 9,
        "resumes_generated": 1
    }

    # Print HTML for testing
    html = build_hot_job_html(test_hot_jobs, test_regular_jobs, test_stats)
    print("HTML Preview generated. Would send email with this content.")

