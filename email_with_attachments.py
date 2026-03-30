#!/usr/bin/env python3
"""
Enhanced Email Module - Sends notifications with tailored resume attachments

For Hot Jobs (>=80% match):
- Generates and attaches tailored PDF/DOCX resumes
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
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_hot_job_html(hot_jobs: List[Dict], regular_jobs: List[Dict], stats: Dict) -> str:
    """Build HTML email body for hot job notification."""
    
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
            <h1>🔥 HOT JOBS ALERT</h1>
            <div class="subtitle">Tailored Resumes Attached - Ready to Apply!</div>
        </div>
        <div class="stats-bar">
            <div class="stat"><div class="stat-num">{len(hot_jobs)}</div><div class="stat-label">Hot Jobs</div></div>
            <div class="stat"><div class="stat-num">{stats.get('resumes_generated', 0)}</div><div class="stat-label">Resumes Generated</div></div>
            <div class="stat"><div class="stat-num">{len(regular_jobs)}</div><div class="stat-label">In CSV</div></div>
            <div class="stat"><div class="stat-num">{stats.get('total_processed', 0)}</div><div class="stat-label">Total Found</div></div>
        </div>
"""
    
    if hot_jobs:
        html += '<div class="section"><h2>🎯 Apply Now - Resumes Attached</h2>'
        
        for job in hot_jobs:
            score = job.get("match_score", 0)
            has_resume = bool(job.get("resume_pdf") or job.get("resume_docx"))
            resume_note = '<div class="resume-note">✅ Tailored resume attached below</div>' if has_resume else ''
            
            html += f'''
            <div class="hot-job">
                <p class="title">{job.get("title", "Unknown")}</p>
                <p class="company">🏢 {job.get("company", "Unknown")}</p>
                <span class="score">{score}% Match</span>
                <div class="details">
                    📍 {job.get("location", "N/A")} | 
                    📅 Found: {datetime.now().strftime("%B %d")}
                </div>
                {resume_note}
                <a href="{job.get("url", "#")}" class="apply-btn">Apply Now →</a>
            </div>
            '''
        
        html += '</div>'
    
    # Action items section
    html += '''
        <div class="section">
            <h2>📋 Next Steps</h2>
            <div class="action-list">
                <ol>
                    <li><strong>Review attached resumes</strong> - They're tailored to each job</li>
                    <li><strong>Click "Apply Now"</strong> on jobs above and upload the matching resume</li>
                    <li><strong>Check the CSV</strong> for additional opportunities below threshold</li>
                </ol>
            </div>
        </div>
    '''
    
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

    # Header
    writer.writerow(['Company', 'Title', 'Location', 'Match Score', 'Source', 'URL', 'Date Found'])

    for job in jobs:
        writer.writerow([
            job.get('company', 'Unknown'),
            job.get('title', 'Unknown'),
            job.get('location', ''),
            job.get('match_score', ''),
            job.get('source', 'unknown'),
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
        attachments: List of {'path': str, 'filename': str} for resume attachments

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
    msg['Subject'] = f"🔥 {len(hot_jobs)} Hot Jobs + Resumes Ready - {datetime.now().strftime('%b %d, %I:%M %p')}"
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
        print(f"   📎 Resume attachments: {len(attachments)}")

        with smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port']) as server:
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

