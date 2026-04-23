import json
import re
import sys
import traceback
from urllib.parse import urlparse

# 1. Parse manual targets from markdown
manual_targets = [
    ('WorkWave', 'Cloud Security Engineer', 'https://jobs.lever.co/workwave/f67b1d54-83a6-4783-a280-be63c09a995b'),
    ('Gametime', 'Security Engineer I', 'https://job-boards.greenhouse.io/gametimeunited/jobs/5172099008'),
    ('Medical Solutions', 'DevOps Engineer II', 'https://careers-medicalsolutions.icims.com/jobs/4324/devops-engineer-ii/job'),
    ('MoonPay', 'Cloud Security Engineer', 'https://jobs.lever.co/moonpay/bc09255a-8975-492e-93e4-ecf2f16e8e45'),
    ('HomeVision', 'Associate Site Reliability Engineer', 'https://jobs.ashbyhq.com/homevision/fb31c9cd-89c8-4001-8f51-303e504123e3'),
    ('Vannevar Labs', 'DevOps Engineer', 'https://job-boards.greenhouse.io/vannevarlabs/jobs/5106734007'),
    ('Leidos', 'Site Reliability Engineer', 'https://leidos.wd5.myworkdayjobs.com/external/job/remote-us/site-reliability-engineer_r-00180815'),
    ('Stitch Fix', 'Platform Engineer', 'https://job-boards.greenhouse.io/204951305985924/jobs/7808248'),
    ('Lean TECHniques', 'Platform Engineer', 'https://jobs.ashbyhq.com/leantechniques/7f314de8-c2e0-4944-ab47-b3f9dd370ef8'),
    ('Chainguard', 'Security Engineer, Governance and Trust', 'https://job-boards.greenhouse.io/chainguard/jobs/4669748006'),
]

# 2. Load config
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

greenhouse_endpoints = config.get('greenhouse_endpoints', [])
lever_endpoints = config.get('lever_endpoints', [])
ashby_endpoints = config.get('ashby_endpoints', [])

print('=== CONFIG ENDPOINTS ===')
print(f'Greenhouse boards ({len(greenhouse_endpoints)}): {greenhouse_endpoints[:10]}...' if len(greenhouse_endpoints) > 10 else f'Greenhouse boards ({len(greenhouse_endpoints)}): {greenhouse_endpoints}')
print(f'Lever boards ({len(lever_endpoints)}): {lever_endpoints[:10]}...' if len(lever_endpoints) > 10 else f'Lever boards ({len(lever_endpoints)}): {lever_endpoints}')
print(f'Ashby boards ({len(ashby_endpoints)}): {ashby_endpoints}')
print()

# 3. Load job_state.json
with open('job_state.json', 'r', encoding='utf-8') as f:
    job_state = json.load(f)

state_urls = set()
for job in job_state.get('jobs', []):
    url = job.get('url', '')
    if url:
        state_urls.add(url)

print(f'=== JOB STATE ===')
print(f'Total jobs in state: {len(job_state.get("jobs", []))}')
print()

# 4. Normalize URL function
def normalize_url(url):
    parsed = urlparse(url)
    path = parsed.path
    if path.endswith('/apply'):
        path = path[:-6]
    normalized = f'{parsed.scheme}://{parsed.netloc}{path}'.rstrip('/')
    return normalized.lower()

# 5. Check endpoint coverage
def check_endpoint_coverage(company, url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    
    if 'lever.co' in host:
        match = re.match(r'^/([^/]+)', path)
        if match:
            slug = match.group(1)
            if slug in [e.lower() for e in lever_endpoints]:
                return True, f'lever:{slug}'
        return False, f'lever:{slug if match else "unknown"} NOT in endpoints'
    
    if 'greenhouse.io' in host:
        match = re.match(r'^/([^/]+)', path)
        if match:
            board = match.group(1)
            if board in [e.lower() for e in greenhouse_endpoints]:
                return True, f'greenhouse:{board}'
        return False, f'greenhouse:{board if match else "unknown"} NOT in endpoints'
    
    if 'ashbyhq.com' in host:
        match = re.match(r'^/([^/]+)', path)
        if match:
            slug = match.group(1)
            ashby_list = [e.lower() for e in ashby_endpoints]
            if slug in ashby_list:
                return True, f'ashby:{slug}'
        return False, f'ashby:{slug if match else "unknown"} NOT in endpoints'
    
    if 'icims.com' in host:
        return False, 'icims NOT configured'
    if 'myworkdayjobs.com' in host:
        return False, 'workday NOT configured'
    
    return False, 'unknown ATS'

# 6. Check state and coverage for manual targets
print('=== MANUAL TARGETS ANALYSIS ===')
manual_normalized = {}
manual_info = []
for company, role, url in manual_targets:
    norm_url = normalize_url(url)
    manual_normalized[norm_url] = (company, role, url)
    in_state = any(normalize_url(s) == norm_url for s in state_urls)
    covered, reason = check_endpoint_coverage(company, url)
    manual_info.append((company, role, url, in_state, covered, reason))
    print(f'{company} | {role}')
    print(f'  URL: {url}')
    print(f'  In state: {in_state} | Endpoint coverage: {covered} ({reason})')
    print()

# 7. Run pipeline
print('=== RUNNING PIPELINE ===')
try:
    from run_full_pipeline import run_pipeline
    pipeline_result = run_pipeline(dry_run=True, skip_tailor=True, run_type='lightweight')
    
    # Get pipeline jobs
    pipeline_jobs = pipeline_result.get('jobs', []) if isinstance(pipeline_result, dict) else []
    if not pipeline_jobs and hasattr(pipeline_result, '__iter__'):
        pipeline_jobs = list(pipeline_result) if not isinstance(pipeline_result, dict) else []
    
    print(f'\nPipeline returned: {type(pipeline_result)}')
    print(f'Pipeline jobs count: {len(pipeline_jobs)}')
    
    # Normalize pipeline URLs
    pipeline_urls = {}
    for job in pipeline_jobs:
        if isinstance(job, dict):
            url = job.get('url', '')
            title = job.get('title', 'Unknown')
            company = job.get('company', 'Unknown')
        else:
            url = getattr(job, 'url', '')
            title = getattr(job, 'title', 'Unknown')
            company = getattr(job, 'company', 'Unknown')
        if url:
            norm = normalize_url(url)
            pipeline_urls[norm] = (company, title, url)
    
    print(f'Unique pipeline URLs: {len(pipeline_urls)}')
    
    # Compare
    print('\n=== COMPARISON RESULTS ===')
    hits = []
    misses = []
    
    for norm_url, (company, role, orig_url) in manual_normalized.items():
        if norm_url in pipeline_urls:
            hits.append((company, role, orig_url))
        else:
            # Find info
            info = next((i for i in manual_info if i[0] == company and i[1] == role), None)
            in_state = info[3] if info else False
            covered = info[4] if info else False
            reason = info[5] if info else 'N/A'
            misses.append((company, role, orig_url, in_state, covered, reason))
    
    # Extras
    manual_norms = set(manual_normalized.keys())
    extras = []
    for norm_url, (company, title, orig_url) in pipeline_urls.items():
        if norm_url not in manual_norms:
            extras.append((company, title, orig_url))
    
    print(f'\n--- SUMMARY ---')
    print(f'Total pipeline jobs: {len(pipeline_jobs)}')
    print(f'Overlap with manual top-10: {len(hits)}/10')
    print()
    
    print(f'--- HITS ({len(hits)}) ---')
    for company, role, url in hits:
        print(f'  {company} | {role} | {url}')
    print()
    
    print(f'--- MISSES ({len(misses)}) ---')
    print('  Company | Role | already_in_state | direct_endpoint_coverage')
    for company, role, url, in_state, covered, reason in misses:
        print(f'  {company} | {role} | {in_state} | {covered} ({reason})')
    print()
    
    print(f'--- EXTRAS (first 10 of {len(extras)}) ---')
    for company, title, url in extras[:10]:
        print(f'  {company} | {title} | {url}')
    print()
    
    # Root cause analysis
    print('--- ROOT CAUSE OBSERVATIONS ---')
    lever_missing = [m for m in misses if 'lever' in m[5].lower() and 'NOT in endpoints' in m[5]]
    gh_missing = [m for m in misses if 'greenhouse' in m[5].lower() and 'NOT in endpoints' in m[5]]
    ashby_missing = [m for m in misses if 'ashby' in m[5].lower() and 'NOT in endpoints' in m[5]]
    icims_missing = [m for m in misses if 'icims' in m[5].lower()]
    workday_missing = [m for m in misses if 'workday' in m[5].lower()]
    
    if lever_missing:
        print(f'- {len(lever_missing)} miss(es) due to Lever endpoint not configured: {[m[0] for m in lever_missing]}')
    if gh_missing:
        print(f'- {len(gh_missing)} miss(es) due to Greenhouse endpoint not configured: {[m[0] for m in gh_missing]}')
    if ashby_missing:
        print(f'- {len(ashby_missing)} miss(es) due to Ashby endpoint not configured: {[m[0] for m in ashby_missing]}')
    if icims_missing:
        print(f'- {len(icims_missing)} miss(es) due to iCIMS not supported: {[m[0] for m in icims_missing]}')
    if workday_missing:
        print(f'- {len(workday_missing)} miss(es) due to Workday not supported: {[m[0] for m in workday_missing]}')

except Exception as e:
    print(f'\n!!! PIPELINE FAILED !!!')
    print(f'Error: {e}')
    traceback.print_exc()
