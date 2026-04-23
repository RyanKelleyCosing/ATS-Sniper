# Jobs Inbox

Drop one or more `.md` or `.txt` job description files here, then run:

```powershell
python tailor_batch.py
```

Recommended format for best results:

```md
---
company: Medpace
role: Systems Engineer (Azure Cloud Engineer)
job_url: https://careers.medpace.com/information-technology/jobs/12284?lang=en-us
template_hint: cloud
preferred_accomplishment_ids:
  - CLOUD-001
  - CLOUD-003
industry_focus: healthcare and clinical research infrastructure
cover_letter_focus: connect Azure reliability and IaC work to Medpace's mission
tailoring_notes:
  - Keep the 5-year Resurgent tenure and two promotions prominent.
  - Prefer Bicep and ARM over unsupported Terraform claims.
---

Paste the full job description below the front matter.
```

Fast fallback format:

- Name the file `Company - Role.md` or `Company - Role.txt`
- Paste the full job description into the file body

The fast path uses a single LLM call per job to generate:

- tailored resume source markdown
- rendered resume PDF
- rendered resume DOCX
- cover letter TXT

Processed files move to `jobs_processed/` by default.

Default model for this submission-focused path: `gpt-5.4`

If you want a cheaper or faster run for comparison, use `python tailor_batch.py --model gpt-4o` or `python tailor_batch.py --model gpt-4o-mini`.