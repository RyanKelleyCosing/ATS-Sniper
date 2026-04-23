"""
ATS Sniper v3.0 - Automated Job Hunter
Finds fresh jobs on Greenhouse, Lever, Workable, Workday + enterprise ATS.
Emails results with tier-based categorization + AI-tailored resumes.

Usage:
    python ats_sniper.py                    # Full run (all queries)
    python ats_sniper.py --morning          # Morning run (Enterprise focus)
    python ats_sniper.py --afternoon        # Afternoon run (Startup focus)
    python ats_sniper.py --test             # Single query test (1 API call)
    python ats_sniper.py --dry-run          # Show queries without executing
    python ats_sniper.py --estimate         # Show query count only
    python ats_sniper.py --send-all         # Email ALL tracked jobs
    python ats_sniper.py --export-csv       # Export all jobs to CSV
"""

import json
import os
import csv
import smtplib
import hashlib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "job_state.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "sniper.log")
CSV_PATH = os.path.join(SCRIPT_DIR, "jobs_export.csv")

# Cutoff date - only jobs after this date are considered fresh
CUTOFF_DATE = datetime(2026, 3, 23)

def log(msg, level="INFO"):
    """Print timestamped log message and write to log file"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERR": "❌", "SEARCH": "🔍"}.get(level, "")
    line = f"[{timestamp}] {prefix} {msg}"
    print(line)

    # Also write to log file
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def load_config():
    """Load configuration from config.json"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state():
    """Load state from job_state.json"""
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"last_run": None, "seen_jobs": {}, "stats": {"total_jobs_found": 0, "total_emails_sent": 0, "queries_run": 0}}


def save_state(state):
    """Save state to job_state.json"""
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def get_job_hash(url):
    """Generate a unique hash for a job URL"""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def calculate_date_range(state, config):
    """Calculate the date range for searching"""
    max_lookback = config['settings']['max_days_lookback']
    
    if state['last_run']:
        last_run = datetime.fromisoformat(state['last_run'])
        days_since = (datetime.now() - last_run).days
        lookback_days = min(days_since + 1, max_lookback)
    else:
        lookback_days = max_lookback
    
    return lookback_days


def build_queries(config, run_type=None):
    """
    Build MEGA-QUERIES using OR operators for maximum efficiency.
    Supports tiered scheduling (morning=enterprise, afternoon=startups).

    Strategy: Combine multiple sites + roles into single queries
    """
    queries = []

    # Get query groups based on run type
    if run_type and 'schedules' in config:
        schedule = config['schedules'].get(run_type, {})
        active_groups = schedule.get('query_groups', list(config.get('query_groups', {}).keys()))
    else:
        # Full run - use all query groups
        active_groups = list(config.get('query_groups', {}).keys())

    # Build location filter string
    location_str = ' OR '.join([f'"{loc}"' for loc in config['location_filters']])

    # Define role clusters for mega-queries
    role_clusters = [
        {
            'name': 'Core Ops + AI',
            'terms': config['role_groups'].get('core_ops', []) + config['role_groups'].get('ai_ml', [])
        },
        {
            'name': 'Infra + FinOps',
            'terms': config['role_groups'].get('core_infra', []) + config['role_groups'].get('finops', [])
        }
    ]

    # Build queries for each active query group
    for group_name in active_groups:
        group = config.get('query_groups', {}).get(group_name, {})
        sites = group.get('sites', [])
        tier = group.get('tier', 3)
        label = group.get('label', group_name)

        if not sites:
            continue

        # Build site mega-query: (site:x OR site:y)
        site_str = ' OR '.join([f'site:{site}' for site in sites])

        # Create queries for each role cluster
        for cluster in role_clusters:
            role_str = ' OR '.join([f'"{role}"' for role in cluster['terms'][:8]])  # Limit to prevent query too long

            query = f'({site_str}) ({role_str})'

            queries.append({
                'query': query,
                'sites': sites,
                'role_group': cluster['name'],
                'tier': tier,
                'tier_label': label,
                'query_group': group_name,
                'location': 'Combined'
            })

    return queries


def export_to_csv(state, config):
    """Export all tracked jobs to CSV for manual tracking"""
    jobs_data = []

    for job_hash, job_data in state.get('seen_jobs', {}).items():
        if isinstance(job_data, dict):
            url = job_data.get('url', '')

            # Determine tier from URL
            tier = categorize_job_tier(url, config)

            jobs_data.append({
                'Title': job_data.get('title', 'Unknown'),
                'Company': extract_company(url, detect_platform(url)),
                'URL': url,
                'Found Date': job_data.get('found_at', '')[:10] if job_data.get('found_at') else '',
                'Tier': tier,
                'Platform': detect_platform(url),
                'Applied': '',  # Empty for user to fill
                'Status': '',   # Empty for user to fill
                'Notes': ''     # Empty for user to fill
            })

    # Sort by found date (newest first)
    jobs_data.sort(key=lambda x: x['Found Date'], reverse=True)

    # Write CSV
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        if jobs_data:
            writer = csv.DictWriter(f, fieldnames=jobs_data[0].keys())
            writer.writeheader()
            writer.writerows(jobs_data)

    log(f"Exported {len(jobs_data)} jobs to {CSV_PATH}", "OK")
    return len(jobs_data)


def detect_platform(url):
    """Detect ATS platform from URL"""
    if 'greenhouse' in url:
        return 'greenhouse.io'
    elif 'lever' in url:
        return 'lever.co'
    elif 'workable' in url:
        return 'workable.com'
    elif 'ashby' in url:
        return 'ashbyhq.com'
    elif 'myworkdayjobs' in url or 'workday' in url:
        return 'workday'
    elif 'wellfound' in url:
        return 'wellfound.com'
    elif 'workatastartup' in url:
        return 'yc'
    return 'unknown'


def categorize_job_tier(url, config):
    """Categorize a job into a tier based on URL patterns"""
    company_tiers = config.get('company_tiers', {})

    # Check enterprise
    for pattern in company_tiers.get('enterprise', []):
        if pattern in url:
            return 'Enterprise'

    # Check regional
    for pattern in company_tiers.get('cincy_regional', []):
        if pattern in url.lower():
            return 'Regional'

    # Check startup
    for pattern in company_tiers.get('startup', []):
        if pattern in url:
            return 'Startup'

    # Check niche
    for pattern in company_tiers.get('niche', []):
        if pattern in url:
            return 'Niche'

    return 'Other'


def is_hot_job(found_at, config):
    """Check if job was found within hot_badge_hours"""
    hot_hours = config.get('settings', {}).get('hot_badge_hours', 6)
    if not found_at:
        return False
    try:
        found_time = datetime.fromisoformat(found_at)
        return (datetime.now() - found_time).total_seconds() < hot_hours * 3600
    except:
        return False


def is_remote_job(title, snippet):
    """Check if job appears to be remote"""
    text = f"{title} {snippet}".lower()
    remote_keywords = ['remote', 'anywhere', 'distributed', 'work from home', 'wfh']
    return any(kw in text for kw in remote_keywords)


def is_regional_job(title, snippet, config):
    """Check if job appears to be in target region (Cincinnati metro + surrounding)"""
    text = f"{title} {snippet}".lower()
    regional_keywords = [
        'cincinnati', 'dayton', 'columbus', 'indianapolis', 'louisville', 'ohio', 'hybrid',
        'blue ash', 'mason', 'west chester', 'evendale', 'covington', 'sharonville',
        'montgomery', 'kenwood', 'norwood', 'florence', 'fort mitchell', 'milford', 'loveland',
        'springdale', 'fairfield', 'union township'
    ]
    return any(kw in text for kw in regional_keywords)


def search_serpapi(query_info, config, lookback_days):
    """Execute a single SerpApi search"""
    # tbs=qdr:d = past 24 hours, qdr:w = past week
    tbs_map = {1: 'qdr:d', 2: 'qdr:d2', 3: 'qdr:d3', 7: 'qdr:w'}
    tbs = tbs_map.get(lookback_days, 'qdr:w')
    
    params = {
        'engine': 'google',
        'q': query_info['query'],
        'api_key': config['serpapi_key'],
        'tbs': tbs,
        'num': config['settings']['max_results_per_query']
    }
    
    try:
        response = requests.get('https://serpapi.com/search', params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  ❌ Error searching: {e}")
        return None


def parse_results(serpapi_response, query_info, config, state):
    """Parse SerpApi results and filter out seen jobs"""
    jobs = []

    if not serpapi_response or 'organic_results' not in serpapi_response:
        return jobs

    exclude_keywords = [kw.lower() for kw in config.get('exclude_keywords', [])]

    for result in serpapi_response['organic_results']:
        url = result.get('link', '')
        title = result.get('title', '')
        snippet = result.get('snippet', '')

        # Skip if already seen
        job_hash = get_job_hash(url)
        if job_hash in state['seen_jobs']:
            continue

        # Skip if contains exclude keywords
        title_lower = title.lower()
        if any(kw in title_lower for kw in exclude_keywords):
            continue

        # Detect platform from URL
        platform = detect_platform(url)

        # Extract company name from URL
        company = extract_company(url, platform)

        # Determine tier
        tier = categorize_job_tier(url, config)
        tier_label = query_info.get('tier_label', tier)

        # Check special categories
        found_at = datetime.now().isoformat()
        is_hot = True  # New jobs are always hot
        is_remote = is_remote_job(title, snippet)
        is_regional = is_regional_job(title, snippet, config)

        jobs.append({
            'title': title,
            'company': company,
            'url': url,
            'snippet': snippet,
            'platform': platform,
            'role_matched': query_info.get('role_group', 'Unknown'),
            'location_filter': query_info.get('location', 'Any'),
            'hash': job_hash,
            'found_at': found_at,
            'tier': tier,
            'tier_label': tier_label,
            'tier_num': query_info.get('tier', 3),
            'is_hot': is_hot,
            'is_remote': is_remote,
            'is_regional': is_regional
        })

    return jobs


def extract_company(url, platform):
    """Extract company name from ATS URL"""
    try:
        if 'greenhouse' in platform or 'greenhouse' in url:
            # https://boards.greenhouse.io/companyname/jobs/123
            parts = url.split('/')
            if 'boards.greenhouse.io' in url and len(parts) > 3:
                return parts[3].replace('-', ' ').title()
        elif 'lever' in platform or 'lever' in url:
            # https://jobs.lever.co/companyname/job-id
            parts = url.split('/')
            if 'jobs.lever.co' in url and len(parts) > 3:
                return parts[3].replace('-', ' ').title()
        elif 'workable' in platform or 'workable' in url:
            # https://apply.workable.com/companyname/j/jobid
            parts = url.split('/')
            if 'apply.workable.com' in url and len(parts) > 3:
                return parts[3].replace('-', ' ').title()
        elif 'ashby' in platform or 'ashby' in url:
            parts = url.split('/')
            if len(parts) > 3:
                return parts[3].replace('-', ' ').title()
        elif 'workday' in platform or 'myworkdayjobs' in url:
            # https://pg.wd5.myworkdayjobs.com/...
            if 'pg.wd' in url:
                return 'Procter & Gamble'
            elif 'geaerospace' in url:
                return 'GE Aerospace'
            elif '53.wd' in url:
                return 'Fifth Third Bank'
            elif 'kroger' in url:
                return 'Kroger'
            else:
                # Try to extract from subdomain
                parts = url.split('.')
                if len(parts) > 0:
                    return parts[0].replace('https://', '').replace('-', ' ').title()
        elif 'wellfound' in url:
            return 'Wellfound Startup'
        elif 'workatastartup' in url:
            return 'YC Startup'
    except:
        pass
    return "Unknown Company"


def build_html_email(jobs, stats, run_type=None):
    """Build a sectioned HTML email with jobs grouped by tier"""

    # Categorize jobs
    enterprise_jobs = [j for j in jobs if j.get('tier') == 'Enterprise']
    startup_jobs = [j for j in jobs if j.get('tier') == 'Startup' and not j.get('is_remote') and not j.get('is_regional')]
    remote_jobs = [j for j in jobs if j.get('is_remote', False)]
    regional_jobs = [j for j in jobs if j.get('is_regional', False) and not j.get('is_remote')]
    other_jobs = [j for j in jobs if j not in enterprise_jobs + startup_jobs + remote_jobs + regional_jobs]

    run_label = "Morning (Enterprise)" if run_type == "morning" else "Afternoon (Startups)" if run_type == "afternoon" else "Full Run"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
        .container {{ max-width: 700px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
        .header {{ background: linear-gradient(135deg, #1e3a5f 0%, #0d2137 100%); color: white; padding: 30px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 26px; letter-spacing: -0.5px; }}
        .header .run-type {{ font-size: 14px; opacity: 0.8; margin-top: 8px; }}
        .header .timestamp {{ font-size: 13px; opacity: 0.7; margin-top: 4px; }}
        .stats-bar {{ background: #f0f7ff; padding: 16px 24px; display: flex; justify-content: space-around; border-bottom: 1px solid #e0e0e0; }}
        .stat {{ text-align: center; }}
        .stat-num {{ font-size: 24px; font-weight: 700; color: #1e3a5f; }}
        .stat-label {{ font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .section {{ padding: 20px 24px; border-bottom: 1px solid #eee; }}
        .section:last-child {{ border-bottom: none; }}
        .section-header {{ display: flex; align-items: center; margin-bottom: 16px; }}
        .section-title {{ font-size: 16px; font-weight: 600; color: #1e3a5f; margin: 0; }}
        .section-count {{ background: #1e3a5f; color: white; padding: 2px 10px; border-radius: 12px; font-size: 12px; margin-left: 10px; }}
        .job {{ background: #fafafa; border: 1px solid #e8e8e8; border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }}
        .job:hover {{ border-color: #1e3a5f; }}
        .job-row {{ display: flex; justify-content: space-between; align-items: flex-start; }}
        .job-info {{ flex: 1; }}
        .job-title {{ font-size: 15px; font-weight: 600; color: #1a1a1a; margin: 0 0 4px 0; }}
        .job-company {{ font-size: 13px; color: #1e3a5f; margin: 0 0 6px 0; }}
        .job-badges {{ margin-top: 6px; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 500; margin-right: 6px; }}
        .badge-hot {{ background: #fee2e2; color: #dc2626; }}
        .badge-remote {{ background: #dbeafe; color: #2563eb; }}
        .badge-regional {{ background: #d1fae5; color: #059669; }}
        .badge-platform {{ background: #f3f4f6; color: #4b5563; }}
        .apply-btn {{ display: inline-block; background: #1e3a5f; color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 12px; font-weight: 500; white-space: nowrap; }}
        .apply-btn:hover {{ background: #0d2137; }}
        .footer {{ background: #f8f9fa; padding: 20px; text-align: center; font-size: 11px; color: #888; }}
        .empty-section {{ color: #999; font-style: italic; padding: 10px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎯 ATS Sniper Report</h1>
            <div class="run-type">{run_label}</div>
            <div class="timestamp">{datetime.now().strftime('%B %d, %Y at %I:%M %p')}</div>
        </div>
        <div class="stats-bar">
            <div class="stat"><div class="stat-num">{len(jobs)}</div><div class="stat-label">New Jobs</div></div>
            <div class="stat"><div class="stat-num">{len(enterprise_jobs)}</div><div class="stat-label">Enterprise</div></div>
            <div class="stat"><div class="stat-num">{len(remote_jobs)}</div><div class="stat-label">Remote</div></div>
            <div class="stat"><div class="stat-num">{stats.get('total_jobs_found', 0)}</div><div class="stat-label">Total Tracked</div></div>
        </div>
"""

    def render_job(job):
        badges = ""
        if job.get('is_hot'):
            badges += '<span class="badge badge-hot">🆕 HOT</span>'
        if job.get('is_remote'):
            badges += '<span class="badge badge-remote">🏠 Remote</span>'
        if job.get('is_regional'):
            badges += '<span class="badge badge-regional">📍 Regional</span>'
        badges += f'<span class="badge badge-platform">{job.get("platform", "").replace(".io", "").replace(".co", "").replace(".com", "")}</span>'

        return f'''<div class="job">
            <div class="job-row">
                <div class="job-info">
                    <p class="job-title">{job.get("title", "Unknown")[:60]}</p>
                    <p class="job-company">🏢 {job.get("company", "Unknown")}</p>
                    <div class="job-badges">{badges}</div>
                </div>
                <a href="{job.get("url", "#")}" class="apply-btn">Apply →</a>
            </div>
        </div>'''

    def render_section(title, section_jobs, emoji="📋"):
        if not section_jobs:
            return ""
        section_html = f'''<div class="section">
            <div class="section-header">
                <h3 class="section-title">{emoji} {title}</h3>
                <span class="section-count">{len(section_jobs)}</span>
            </div>'''
        for job in section_jobs[:15]:  # Limit to 15 per section
            section_html += render_job(job)
        if len(section_jobs) > 15:
            section_html += f'<p class="empty-section">... and {len(section_jobs) - 15} more</p>'
        section_html += '</div>'
        return section_html

    # Add sections
    if not jobs:
        html += '<div class="section"><p class="empty-section">No new jobs found. Check back later!</p></div>'
    else:
        html += render_section("ENTERPRISE - Apply within 24 hrs", enterprise_jobs, "🔥")
        html += render_section("HIGH-GROWTH STARTUPS", startup_jobs, "🚀")
        html += render_section("REMOTE OPPORTUNITIES", remote_jobs, "🏠")
        html += render_section("REGIONAL (Cincinnati/Midwest)", regional_jobs, "📍")
        html += render_section("OTHER OPPORTUNITIES", other_jobs, "💼")

    html += f"""
        <div class="footer">
            <p>ATS Sniper v3.0 • Finding jobs before they hit the boards</p>
            <p>Platforms: Greenhouse, Lever, Workable, Ashby, Workday, Wellfound</p>
        </div>
    </div>
</body>
</html>"""
    return html


def send_email(jobs, config, stats, run_type=None):
    """Send HTML email via SMTP"""
    email_config = config['email']

    # Build subject based on run type
    run_label = ""
    if run_type == "morning":
        run_label = "🌅 Morning"
    elif run_type == "afternoon":
        run_label = "🌆 Afternoon"

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🎯 ATS Sniper {run_label}: {len(jobs)} New Jobs - {datetime.now().strftime('%b %d, %I:%M %p')}"
    msg['From'] = email_config['sender_email']
    msg['To'] = email_config['recipient_email']

    html_content = build_html_email(jobs, stats, run_type)
    msg.attach(MIMEText(html_content, 'html'))

    try:
        print(f"\n📧 Sending email to {email_config['recipient_email']}...")
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
        # Try fallback email
        if email_config.get('fallback_recipient'):
            print(f"  🔄 Trying fallback: {email_config['fallback_recipient']}...")
            try:
                msg['To'] = email_config['fallback_recipient']
                with smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port']) as server:
                    server.starttls()
                    server.login(email_config['sender_email'], email_config['sender_password'])
                    server.sendmail(
                        email_config['sender_email'],
                        email_config['fallback_recipient'],
                        msg.as_string()
                    )
                print("  ✅ Fallback email sent!")
                return True
            except Exception as e2:
                print(f"  ❌ Fallback also failed: {e2}")
        return False


def clean_old_jobs(state, config):
    """Remove jobs older than dedup_window_days from seen_jobs"""
    dedup_days = config['settings']['dedup_window_days']
    cutoff = datetime.now() - timedelta(days=dedup_days)

    cleaned = {}
    for job_hash, job_data in state['seen_jobs'].items():
        if isinstance(job_data, dict) and 'found_at' in job_data:
            found_at = datetime.fromisoformat(job_data['found_at'])
            if found_at > cutoff:
                cleaned[job_hash] = job_data
        else:
            # Legacy format, keep it
            cleaned[job_hash] = job_data

    removed = len(state['seen_jobs']) - len(cleaned)
    state['seen_jobs'] = cleaned
    return removed


def run_sniper(dry_run=False, run_type=None):
    """Main function to run the ATS Sniper"""
    run_label = {
        'morning': '🌅 MORNING (Enterprise Focus)',
        'afternoon': '🌆 AFTERNOON (Startup Sweep)'
    }.get(run_type, '🎯 FULL RUN')

    print("=" * 60)
    print(f"🎯 ATS SNIPER v3.0 - {run_label}")
    print("=" * 60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load config and state
    config = load_config()
    state = load_state()

    # Clean old jobs
    removed = clean_old_jobs(state, config)
    if removed:
        log(f"Cleaned {removed} old job entries", "OK")

    # Calculate date range
    lookback_days = calculate_date_range(state, config)
    log(f"Cutoff date: {CUTOFF_DATE.strftime('%Y-%m-%d')} (jobs after this are fresh)")
    log(f"Searching last {lookback_days} day(s) of Google index")
    if state['last_run']:
        log(f"Last run: {state['last_run']}")

    # Build queries based on run type
    queries = build_queries(config, run_type)
    log(f"Generated {len(queries)} mega-queries (run_type={run_type or 'all'})")

    # Show queries in dry run
    if dry_run:
        log("DRY RUN - Showing queries without executing:", "WARN")
        for i, q in enumerate(queries):
            print(f"   [{i+1}] {q.get('query_group', 'unknown')} | {q['role_group']}")
            print(f"       {q['query'][:100]}...")
        return []

    # Execute searches
    all_jobs = []
    queries_run = 0

    active_groups = [q.get('query_group', 'unknown') for q in queries]
    log(f"Query groups: {', '.join(set(active_groups))}", "SEARCH")

    for i, query_info in enumerate(queries):
        group_name = query_info.get('query_group', 'unknown')
        log(f"[{i+1}/{len(queries)}] {group_name} → {query_info['role_group']}", "SEARCH")

        result = search_serpapi(query_info, config, lookback_days)
        queries_run += 1

        if result:
            jobs = parse_results(result, query_info, config, state)
            all_jobs.extend(jobs)
            log(f"   Found {len(jobs)} new jobs", "OK" if jobs else "INFO")

            # Mark jobs as seen with enriched data
            for job in jobs:
                state['seen_jobs'][job['hash']] = {
                    'url': job['url'],
                    'title': job['title'],
                    'found_at': job['found_at'],
                    'tier': job.get('tier', 'Other'),
                    'company': job.get('company', 'Unknown')
                }
        else:
            log(f"   Query returned no results or error", "WARN")

    # Deduplicate (same job might appear in multiple queries)
    seen_urls = set()
    unique_jobs = []
    for job in all_jobs:
        if job['url'] not in seen_urls:
            seen_urls.add(job['url'])
            unique_jobs.append(job)

    # Sort by tier priority (Enterprise first)
    tier_order = {'Enterprise': 1, 'Startup': 2, 'Regional': 3, 'Niche': 4, 'Other': 5}
    unique_jobs.sort(key=lambda j: tier_order.get(j.get('tier', 'Other'), 5))

    print()
    log(f"RESULTS SUMMARY:", "INFO")
    log(f"   New jobs found: {len(unique_jobs)}")
    log(f"   API calls used: {queries_run}")
    log(f"   Total jobs tracked: {len(state['seen_jobs'])}")

    # Show tier breakdown
    enterprise = len([j for j in unique_jobs if j.get('tier') == 'Enterprise'])
    remote = len([j for j in unique_jobs if j.get('is_remote')])
    regional = len([j for j in unique_jobs if j.get('is_regional')])
    log(f"   Enterprise: {enterprise} | Remote: {remote} | Regional: {regional}")

    # Update stats
    state['stats']['queries_run'] = queries_run
    state['stats']['total_jobs_found'] = len(state['seen_jobs'])

    # Send email if we have jobs
    if unique_jobs:
        log("Sending email with job listings...", "INFO")
        email_sent = send_email(unique_jobs, config, state['stats'], run_type)
        if email_sent:
            state['stats']['total_emails_sent'] += 1
    else:
        log("No new jobs to email", "INFO")

    # Update state
    state['last_run'] = datetime.now().isoformat()
    save_state(state)

    log("Complete! State saved.", "OK")
    print("=" * 60)

    return unique_jobs


def run_test_mode():
    """Run a single test query to verify API connectivity"""
    print("=" * 60)
    print("🧪 TEST MODE - Single query to verify everything works")
    print("=" * 60)

    config = load_config()

    # Single test query
    test_query = 'site:boards.greenhouse.io "DevOps Engineer" "Remote"'
    log(f"Test query: {test_query}", "SEARCH")

    params = {
        'engine': 'google',
        'q': test_query,
        'api_key': config['serpapi_key'],
        'tbs': 'qdr:w',  # Past week
        'num': 5
    }

    try:
        log("Calling SerpApi...", "INFO")
        response = requests.get('https://serpapi.com/search', params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        log(f"API response received!", "OK")

        if 'organic_results' in data:
            results = data['organic_results']
            log(f"Found {len(results)} results", "OK")
            print()
            for r in results[:3]:
                print(f"  📌 {r.get('title', 'No title')[:60]}")
                print(f"     {r.get('link', '')[:70]}")
                print()
        else:
            log("No organic_results in response", "WARN")

        # Check API credits
        if 'search_metadata' in data:
            log(f"Search ID: {data['search_metadata'].get('id', 'N/A')}", "INFO")

        log("Test successful! API is working.", "OK")
        return True

    except Exception as e:
        log(f"Test failed: {e}", "ERR")
        return False


def show_estimate(run_type=None):
    """Show how many queries would be run without executing"""
    config = load_config()

    # Show all schedules
    print("=" * 60)
    print("📊 QUERY ESTIMATE - ATS Sniper v3.0")
    print("=" * 60)

    # Morning queries
    morning_queries = build_queries(config, 'morning')
    afternoon_queries = build_queries(config, 'afternoon')
    all_queries = build_queries(config, None)

    print(f"\n🌅 MORNING RUN (09:30 AM - Enterprise Focus):")
    print(f"   Queries: {len(morning_queries)}")
    for q in morning_queries:
        print(f"     • {q.get('query_group', 'unknown')} → {q['role_group']}")

    print(f"\n🌆 AFTERNOON RUN (04:30 PM - Startup Sweep):")
    print(f"   Queries: {len(afternoon_queries)}")
    for q in afternoon_queries:
        print(f"     • {q.get('query_group', 'unknown')} → {q['role_group']}")

    print(f"\n📊 BUDGET SUMMARY:")
    print(f"   Morning queries/run: {len(morning_queries)}")
    print(f"   Afternoon queries/run: {len(afternoon_queries)}")
    print(f"   Daily total: {len(morning_queries) + len(afternoon_queries)}")
    print(f"   Monthly usage: ~{(len(morning_queries) + len(afternoon_queries)) * 30} credits")
    print(f"\n💡 Starter plan (1,000 credits) covers this easily!")


def send_all_tracked_jobs():
    """Send email with ALL tracked jobs (not just new ones)"""
    print("=" * 60)
    print("📧 SENDING ALL TRACKED JOBS")
    print("=" * 60)

    config = load_config()
    state = load_state()

    # Convert seen_jobs dict to list format for email
    all_jobs = []
    for _, job_data in state['seen_jobs'].items():
        if isinstance(job_data, dict):
            url = job_data.get('url', '')
            platform = detect_platform(url)
            tier = job_data.get('tier', categorize_job_tier(url, config))

            all_jobs.append({
                'title': job_data.get('title', 'Unknown Title'),
                'company': job_data.get('company', extract_company(url, platform)),
                'url': url,
                'snippet': '',
                'platform': platform,
                'role_matched': 'All',
                'location_filter': 'All',
                'found_at': job_data.get('found_at', ''),
                'tier': tier,
                'is_hot': is_hot_job(job_data.get('found_at'), config),
                'is_remote': False,
                'is_regional': False
            })

    log(f"Total tracked jobs: {len(all_jobs)}", "INFO")

    if all_jobs:
        stats = state.get('stats', {'queries_run': 0, 'total_jobs_found': len(all_jobs)})
        email_sent = send_email(all_jobs, config, stats)
        if email_sent:
            log("All tracked jobs sent!", "OK")
        return email_sent
    else:
        log("No jobs tracked yet", "WARN")
        return False


if __name__ == "__main__":
    import sys

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("\nOptions:")
        print("  --morning     Run morning schedule (Enterprise focus)")
        print("  --afternoon   Run afternoon schedule (Startup sweep)")
        print("  --test        Run single test query (1 API call)")
        print("  --dry-run     Show queries without executing")
        print("  --estimate    Show query count and exit")
        print("  --send-all    Email ALL tracked jobs (not just new)")
        print("  --export-csv  Export all jobs to CSV for manual tracking")
        sys.exit(0)

    if "--estimate" in sys.argv:
        show_estimate()
        sys.exit(0)

    if "--test" in sys.argv:
        success = run_test_mode()
        sys.exit(0 if success else 1)

    if "--send-all" in sys.argv:
        send_all_tracked_jobs()
        sys.exit(0)

    if "--export-csv" in sys.argv:
        config = load_config()
        state = load_state()
        export_to_csv(state, config)
        sys.exit(0)

    # Determine run type
    run_type = None
    if "--morning" in sys.argv:
        run_type = "morning"
    elif "--afternoon" in sys.argv:
        run_type = "afternoon"

    dry_run = "--dry-run" in sys.argv
    jobs = run_sniper(dry_run=dry_run, run_type=run_type)

    if jobs:
        print(f"\n🎉 Found {len(jobs)} new job opportunities!")
        print("\nTop matches by tier:")
        for job in jobs[:5]:
            tier_emoji = {"Enterprise": "🔥", "Startup": "🚀", "Regional": "📍"}.get(job.get('tier', ''), "📋")
            hot = "🆕" if job.get('is_hot') else ""
            print(f"  {tier_emoji} {hot} {job['title']} @ {job['company']}")
            print(f"     {job['url']}")

