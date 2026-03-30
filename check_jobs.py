"""Quick check of job_state.json contents."""
import json

data = json.load(open('job_state.json', encoding='utf-8'))
jobs = data.get('jobs', {})

# Jobs is a dict keyed by job_id
if isinstance(jobs, dict):
    jobs_list = list(jobs.values())
else:
    jobs_list = jobs

# Group by company
by_company = {}
for j in jobs_list:
    if isinstance(j, dict):
        company = j.get('company', 'Unknown')
        if company not in by_company:
            by_company[company] = []
        by_company[company].append(j)

print(f"Total jobs: {len(jobs_list)}")
print("\nBy Company:")
for company, company_jobs in sorted(by_company.items()):
    print(f"\n{company} ({len(company_jobs)} jobs):")
    for j in company_jobs[:5]:
        print(f"  • {j.get('title', 'NO TITLE')}")
    if len(company_jobs) > 5:
        print(f"  ... and {len(company_jobs)-5} more")

