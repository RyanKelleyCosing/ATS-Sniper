# ATS Sniper

An automated job-hunting pipeline that combines direct ATS scraping, ATS-hosted web discovery, controlled board discovery, AI screening and scoring, resume tailoring, and application-pack generation.

Built first for **security / IAM / DevOps / SRE / cloud / platform** roles, with a separate adjacent-tech lane for good technical implementation, integration, automation, support, and internal-tools roles.

---

## Features

- **Multi-platform scraping** — Workday, iCIMS, Oracle HCM, USAJobs, Greenhouse, Lever, Workable, Ashby, and custom career pages
- **Layered discovery** — Direct ATS feeds, ATS-hosted web discovery, selected company career domains, and capped JobSpy board discovery can work together in the same pipeline
- **Exact-fit plus adjacent-tech lanes** — The pipeline keeps the core security/IAM/DevOps/SRE/cloud lane separate from broader implementation, integration, automation, and support-style roles
- **AI-powered early screening + match scoring** — A cheap first pass removes obvious noise and routes jobs into the right lane before full scoring runs
- **Automated resume tailoring and application packs** — Strong matches can trigger tailored resume generation, and one-off or batch application packs can be generated for manual submission workflows
- **Scheduled run model** — On-login morning coverage, 10-minute fresh-watch checks, lightweight freshness passes, afternoon sweeps, and a run monitor keep the queue moving through the day
- **Optional HTTP trigger** — Run the same pipeline through an Azure Function instead of Windows Task Scheduler
- **Deduplication** — `job_state.json` tracks every URL seen; nothing is emailed twice within the configured lookback window
- **Silent Windows scheduling** — The scheduler uses a hidden launcher path so runs can execute without flashing visible console windows

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| AI / LLM | OpenAI API (`gpt-4o-mini` for broad scoring, `gpt-5.4` for submitted application packages) |
| Browser automation | Playwright |
| HTTP clients | Requests, httpx |
| HTML parsing | BeautifulSoup4 |
| Resume generation | python-docx |
| Email delivery | smtplib (Gmail SMTP) |
| State persistence | JSON flat files |

---

## Project Structure

```
ATS-Sniper/
├── run_full_pipeline.py        # Main entry point — orchestrates all stages
├── run_fresh_watch.py          # 10-minute fresh-watch bridge entry point
├── azure_function_helpers.py   # Shared Azure Function handlers and status endpoint
├── host.json                   # Azure Functions host config
├── startup_discovery_scraper.py# Web discovery + capped JobSpy discovery
├── workday_scraper.py          # Workday API scraper
├── icims_scraper.py            # iCIMS platform scraper
├── oracle_hcm_scraper.py       # Oracle HCM scraper
├── usajobs_scraper.py          # USAJobs (federal roles) scraper
├── custom_scraper.py           # Playwright scraper for custom career pages
├── hot_job_processor.py        # AI scoring + hot job detection pipeline
├── resume_tailor.py            # OpenAI resume tailoring (v2)
├── generate_tailored_resume.py # Tailored resume assembly and ATS checks
├── generate_application_package.py # One-off and batch application packs
├── tailor_batch.py             # Inbox-driven fast tailoring flow
├── email_with_attachments.py   # HTML email and status delivery
├── check_jobs.py               # Utility: inspect job_state.json
├── config.example.json         # Configuration template (copy to config.json)
├── master_skills.json          # Your skills inventory for AI matching
├── requirements.txt            # Python dependencies
├── setup_scheduler.ps1         # Register Windows scheduled tasks
├── remove_scheduler.ps1        # Remove Windows scheduled tasks
├── run_scheduled_task.ps1      # Hidden scheduled-task entry point with log redirection
├── run_scheduled_task_hidden.vbs # Hidden Windows Script Host launcher for scheduled tasks
├── scripts/azure/              # Azure deployment and invocation helpers
├── scripts/maintenance/        # Maintenance and local utility scripts
├── scripts/dev/                # Ad-hoc regeneration and dev harness scripts
├── docs/                       # Reference notes and research docs
├── archive/                    # Legacy scripts kept out of the main root
├── run_sniper.bat              # Run pipeline manually
├── run_sniper_morning.bat      # Morning scheduled run
├── run_sniper_afternoon.bat    # Afternoon scheduled run
├── reports/                    # Match reports + CSV exports
└── outputs/                    # Tailored resumes, logs, runtime artifacts
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/RyanKelleyCosing/ATS-Sniper.git
cd ATS-Sniper
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

If you want to enable JobSpy-backed discovery, use Python 3.13 or earlier for that environment. The current upstream `python-jobspy` release pins `NumPy 1.26.3`, which does not install cleanly on Python 3.14.

### 3. Configure

Copy the example config and fill in your credentials:

```bash
cp config.example.json config.json
```

Open `config.json` and replace all placeholder values:

| Key | Description |
|---|---|
| `openai_key` | Your OpenAI API key |
| `usajobs_api_key` | USAJobs API key (free at [developer.usajobs.gov](https://developer.usajobs.gov/)) |
| `usajobs_email` | Email registered with USAJobs API |
| `email.sender_email` | Gmail address used to send reports |
| `email.sender_password` | Gmail [App Password](https://myaccount.google.com/apppasswords) (not your account password) |
| `email.recipient_email` | Where you want reports delivered |
| `serpapi_key` | (Optional) SerpAPI key for legacy Google search mode |
| `jobspy_discovery` | Controlled board discovery using JobSpy, typically with a small capped budget. Current upstream support is safest on Python 3.13 or earlier. |

### 4. Run the pipeline

```bash
python run_full_pipeline.py --run-type full
```

This works independently of Task Scheduler. The batch wrappers are convenience entry points, not the only way to run ATS Sniper.

Useful entry points:

```bash
python run_full_pipeline.py --run-type lightweight
python run_full_pipeline.py --run-type afternoon
python run_fresh_watch.py
```

#### Optional flags

| Flag | Description |
|---|---|
| `--dry-run` | Scrape and score without saving state or sending email |
| `--skip-tailor` | Skip AI resume tailoring (faster) |
| `--v2` | Use v2 mode (disables iCIMS, Oracle HCM, and automated tailoring) |
| `--run-type {morning,afternoon,full,lightweight}` | Select the pipeline budget and behavior for the main entry point |

## Run Modes

- **`full`** — Broadest end-to-end run with direct ATS scrapers, web discovery, capped board discovery when enabled, scoring, reporting, and downstream tailoring for the strongest jobs.
- **`morning` / `afternoon`** — Main scheduled production passes using the same core pipeline with their own source and discovery settings.
- **`lightweight`** — Faster freshness-first lane that emphasizes direct ATS feeds plus lighter discovery during the day.
- **`fresh_watch`** — Separate 10-minute alert bridge run through `run_fresh_watch.py`, with a small discovery budget and tightly capped JobSpy support when enabled.

---

## One-Off Application Packs

For manual applications where you already have a job description, you can now generate a tailored resume, cover letter, and review-ready application pack in one step.

Use a text file:

```bash
python generate_application_package.py --company "Cincinnati Children's" --role "Azure Cloud Engineer I" --job-url "https://jobs.example.com/role" --job-description-file path/to/job_description.txt
```

Or paste the job description directly into the terminal when prompted:

```bash
python generate_application_package.py --company "Creyos" --role "DevOps Engineer"
```

For a repeatable batch flow, put your target jobs into a markdown manifest with YAML front matter and run:

```bash
python generate_application_package.py --batch-file "resumes to make manually/priority_jobs_2026-04-06.md"
```

For low-score or review-queue jobs that already landed in `reports/regular_jobs_export.csv`, use the separate review-queue generator:

```bash
python generate_review_queue_packages.py --queue-ranks 1,2
```

By default it reads `reports/regular_jobs_export.csv`, reuses stored job descriptions from `job_state.json` when available, and writes packages under `resumes to make manually/Application Packs/Review Queue/`.

Each job entry can pin the template, required accomplishment IDs, cover-letter emphasis, and fallback description text. This is the recommended path when you want to update a markdown file and regenerate multiple tailored application packages in one run.

The command writes a package under `resumes to make manually/Application Packs/` that includes:

- tailored resume PDF
- tailored resume DOCX
- tailored ATS upload DOCX (single-column and parser-friendly)
- cover letter TXT
- cover letter DOCX
- analysis report
- tailored resume source markdown
- apply shortcut when a real job URL is provided
- manual review checklist

The styled PDF and styled DOCX are the template-looking outputs. The ATS upload DOCX is intentionally a simpler single-column document for parsers such as Workday, so it will not visually match the styled PDF.

If you want lean output folders with only the resume and cover-letter files, set `include_supporting_artifacts: false` in the batch manifest or omit `--include-supporting-artifacts` on one-off runs.

This flow is designed for manual review before submission, not blind auto-apply.

Default model for submitted application-package generation: `gpt-5.4` via `settings.application_package_model`, with `gpt-4o-mini` reserved for broader scoring and the new early screening pass via `settings.early_classifier_model`.

## Fast Inbox Workflow

For near-immediate applications, use the new inbox-driven fast path. Drop one or more job descriptions into `jobs_inbox/`, then run:

```bash
python tailor_batch.py
```

The script scans `jobs_inbox/`, makes a single LLM call per job, renders the tailored resume through the existing one-page resume pipeline, writes raw render outputs under `outputs/`, builds a standard application pack under `resumes to make manually/Application Packs/Fast Inbox/`, and moves processed files into `jobs_processed/`.

Each fast-inbox package now includes the tailored resume PDF, styled DOCX, ATS upload DOCX, cover letter TXT, and cover letter DOCX so ad-hoc generation uses the same resume-plus-cover-letter workflow as the manual application-pack path.

Default model recommendation for this fast path: `gpt-5.4`

Why this default:

- higher one-pass quality when the generated resume and cover letter are likely to be submitted
- works with the existing resume renderer without changing the truthfulness guardrails
- still supports explicit overrides when you want a cheaper or faster run

If you want to force a different model for a specific run, use:

```bash
python tailor_batch.py --model gpt-4o
```

For best results, include YAML front matter in each inbox file with `company`, `role`, optional `template_hint`, and any `preferred_accomplishment_ids` you want enforced.

---

## Scheduling (Windows)

Register the current silent Windows schedule:

```powershell
.\setup_scheduler.ps1
```

By default this registers:

- an on-login morning run
- fresh-watch checks every 10 minutes from 10:35 AM through 4:15 PM
- lightweight runs at 11:30 AM, 2:30 PM, and 6:30 PM
- an afternoon run at 4:30 PM
- a run monitor at 11:15 AM and 5:45 PM

Remove the scheduled tasks:

```powershell
.\remove_scheduler.ps1
```

The scheduled tasks launch through a hidden Windows Script Host path rather than a visible `cmd.exe` session, so they run silently. Task output is appended to `outputs/scheduled/morning.log`, `outputs/scheduled/fresh_watch.log`, `outputs/scheduled/lightweight.log`, `outputs/scheduled/afternoon.log`, and `outputs/scheduled/monitor.log`.

## Private vs Public Repo

Keep two repos if you want a public-facing portfolio version:

- private repo: live `config.json`, runtime state, inbox contents, processed job descriptions, reports, generated resumes, and application artifacts
- public repo: code, scrubbed docs, `config.example.json`, and demo-safe examples only

The repository defaults now ignore the highest-risk live-data paths, but tracked files still need review before pushing to the public repo.

## Azure Function Trigger

You can run ATS Sniper without Windows Task Scheduler by deploying the repo as an HTTP-trigger Azure Function.

Deploy with Azure CLI:

```powershell
.\scripts\azure\deploy_azure_function.ps1 -ResourceGroup <rg> -Location eastus -FunctionAppName <app-name> -StorageAccountName <storage-name>
```

Invoke a run after deployment:

```powershell
.\scripts\azure\invoke_azure_function_run.ps1 -ResourceGroup <rg> -FunctionAppName <app-name> -RunType full
```

Notes:

- The deploy script reads your local `config.json` and uploads it to the Function App as the `ATS_SNIPER_CONFIG_JSON` app setting.
- Runtime state, logs, reports, and generated resumes are redirected to a writable runtime directory when running in Azure.
- Default timer schedules are set in UTC for the current Eastern Daylight equivalent: morning `13:30 UTC`, afternoon `20:30 UTC`, monitors `15:15 UTC` and `21:45 UTC`.
- For longer production runs, prefer a plan that tolerates longer execution times than the default short-lived serverless timeout.

## Where Things Live

These are the files that matter when you want to update behavior or resume content quickly.

| File | Purpose |
|---|---|
| `config.json` | Live secrets, endpoints, schedules, and local runtime settings |
| `config.example.json` | Template for `config.json` |
| `master_skills.json` | ATS Sniper skills taxonomy, role mappings, and accomplishment tags |
| `../accomplishments.md` | Master achievement bank used by tailoring and the parent resume renderer |
| `job_state.json` | Deduplication + pipeline run state |
| `reports/jobs_export.csv` | Master CSV export of tracked jobs |
| `reports/regular_jobs_export.csv` | Manual-review export for non-hot jobs |
| `outputs/` | Tailored resume folders, logs, and runtime artifacts |
| `params/logging_config.py` | Logging behavior and log destination |
| `setup_scheduler.ps1` / `remove_scheduler.ps1` | Windows scheduled task registration and cleanup |
| `scripts/azure/` | Azure deployment and invocation scripts |
| `scripts/maintenance/` | Local utility scripts such as PDF collection and winget updates |

If you want to add or rotate resume bullets, edit `../accomplishments.md` first. If you want the AI selector to recognize or weight them better, update `master_skills.json`.

---

## Configuration Reference

### Adding Workday Endpoints

Add entries to `workday_endpoints` in `config.json`:

```json
"your_company": {
  "name": "Your Company Name",
  "url": "https://yourcompany.wd5.myworkdayjobs.com/wday/cxs/yourcompany/ExternalSite/jobs"
}
```

### Query Groups

`query_groups` are one source-selection layer used by the scheduled runs. They work alongside `run_type`, startup discovery query profiles, and JobSpy discovery settings:

- **`enterprise_workday`** — Large-company Workday portals (Tier 1, morning run)
- **`enterprise_consulting`** — Big Four / consulting firms (Tier 1, morning run)
- **`cincy_regional`** — Cincinnati-area employers (Tier 1, morning run)
- **`startup_ats`** — Greenhouse, Lever, Workable, Ashby boards (Tier 2, afternoon run)
- **`niche_boards`** — Wellfound, Work at a Startup (Tier 3, afternoon run)

### Hot Job Criteria

A job is flagged as "hot" if **any** of the following are true:

- AI match score ≥ 80%
- Salary ≥ $80,000
- Enterprise company with a relevant title (DevOps, SRE, Cloud, Infrastructure, Platform, Azure, Engineer)

Jobs that are not hot can still land in the regular review queue or the auto-generate queue depending on score, lane fit, and backfill settings.

---

## State Management

Job state is persisted in `job_state.json` (gitignored). It tracks every scraped URL to prevent duplicate notifications. Backups are written under `outputs/state/`. The deduplication window defaults to **30 days** (`settings.dedup_window_days` in `config.json`).

---

## Security

- `config.json` is gitignored — **never commit it**
- All API keys and credentials live exclusively in `config.json`
- Use a [Gmail App Password](https://myaccount.google.com/apppasswords), not your Google account password

---

## License

MIT
