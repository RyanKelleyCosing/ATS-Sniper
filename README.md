# ATS Sniper 🎯

An automated job-hunting pipeline that scrapes multiple Applicant Tracking Systems (ATS), scores job matches with AI, tailors your resume for top matches, and delivers results straight to your inbox — twice a day.

Built for **DevOps / SRE / Cloud / Infrastructure** roles, with a focus on the Greater Cincinnati region and remote opportunities nationwide.

---

## Features

- **Multi-platform scraping** — Workday, iCIMS, Oracle HCM, USAJobs, Greenhouse, Lever, Workable, Ashby, and custom career pages
- **AI-powered match scoring** — OpenAI rates each job against your resume and skills; hot jobs (≥ 80% match) are flagged automatically
- **Automated resume tailoring** — For every hot job, the pipeline generates a tailored resume via OpenAI and attaches it to the email notification
- **Scheduled email reports** — Morning and afternoon runs deliver HTML digests with hot jobs in the body and a full CSV of all new postings attached
- **Deduplication** — `job_state.json` tracks every URL seen; nothing is emailed twice within the configured lookback window
- **Windows Task Scheduler integration** — PowerShell scripts to register/remove scheduled runs with a single command

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| AI / LLM | OpenAI API (`gpt-4o-mini` by default) |
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
├── workday_scraper.py          # Workday API scraper
├── icims_scraper.py            # iCIMS platform scraper
├── oracle_hcm_scraper.py       # Oracle HCM scraper
├── usajobs_scraper.py          # USAJobs (federal roles) scraper
├── custom_scraper.py           # Playwright scraper for custom career pages
├── hot_job_processor.py        # AI scoring + hot job detection pipeline
├── resume_tailor.py            # OpenAI resume tailoring (v2)
├── generate_tailored_resume.py # Resume document generation
├── email_with_attachments.py   # HTML email with resume attachments
├── check_jobs.py               # Utility: inspect job_state.json
├── config.example.json         # Configuration template (copy to config.json)
├── master_skills.json          # Your skills inventory for AI matching
├── requirements.txt            # Python dependencies
├── setup_scheduler.ps1         # Register Windows scheduled tasks
├── remove_scheduler.ps1        # Remove Windows scheduled tasks
├── run_sniper.bat              # Run pipeline manually
├── run_sniper_morning.bat      # Morning scheduled run
└── run_sniper_afternoon.bat    # Afternoon scheduled run
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

### 4. Run the pipeline

```bash
python run_full_pipeline.py
```

#### Optional flags

| Flag | Description |
|---|---|
| `--dry-run` | Scrape and score without saving state or sending email |
| `--skip-tailor` | Skip AI resume tailoring (faster) |
| `--v2` | Use v2 mode (disables iCIMS, Oracle HCM, and automated tailoring) |

---

## Scheduling (Windows)

Register morning (9:30 AM) and afternoon (4:30 PM) runs as Windows scheduled tasks:

```powershell
.\setup_scheduler.ps1
```

Remove the scheduled tasks:

```powershell
.\remove_scheduler.ps1
```

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

`query_groups` control which ATS sites are scraped in each scheduled run:

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

---

## State Management

Job state is persisted in `job_state.json` (gitignored). It tracks every scraped URL to prevent duplicate notifications. The deduplication window defaults to **30 days** (`settings.dedup_window_days` in `config.json`).

---

## Security

- `config.json` is gitignored — **never commit it**
- All API keys and credentials live exclusively in `config.json`
- Use a [Gmail App Password](https://myaccount.google.com/apppasswords), not your Google account password

---

## License

MIT
