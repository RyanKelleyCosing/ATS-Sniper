#!/usr/bin/env python3
"""
Resume Tailor v3 - AI-Powered Resume Matching & Generation
Uses OpenAI GPT-4o-mini for:
- Match scoring (0-100%)
- Gap analysis (missing skills/experience)
- Achievement generation with placeholder metrics
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add parent dir for generate_resumes imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from job_scraper import fetch_job_description

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "job_state.json"
REPORTS_DIR = Path(__file__).parent / "reports"
RESUMES_DIR = Path(__file__).parent.parent / "word_files"
ACCOMPLISHMENTS_FILE = Path(__file__).parent.parent / "accomplishments.md"


def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_base_resume() -> str:
    """Load the base resume content (accomplishments.md)."""
    if ACCOMPLISHMENTS_FILE.exists():
        with open(ACCOMPLISHMENTS_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


COVER_LETTERS_DIR = Path(__file__).parent / "cover_letters"


def generate_cover_letter(client: OpenAI, job_desc: str, resume: str, company: str, role: str, model: str = "gpt-4o-mini") -> str:
    """
    Generate a tailored cover letter using OpenAI.

    Returns:
        Cover letter text
    """
    prompt = f"""Write a professional cover letter for this job application.

JOB: {role} at {company}

JOB DESCRIPTION:
{job_desc[:3000]}

CANDIDATE BACKGROUND:
{resume[:3000]}

Write a concise, compelling cover letter (3-4 paragraphs) that:
1. Opens with enthusiasm for the specific role and company
2. Highlights 2-3 relevant achievements from the candidate's background
3. Connects the candidate's DevOps/Cloud/SRE experience to the job requirements
4. Closes with a call to action

Use professional but conversational tone. No generic phrases.
Start directly with "Dear Hiring Manager," - no preamble."""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.7
        )

        content = response.content[0].text if hasattr(response, 'content') else response.choices[0].message.content
        return content.strip()
    except Exception as e:
        print(f"  ⚠️ Cover letter error: {e}")
        return ""


def analyze_job_match(client: OpenAI, job_desc: str, resume: str, model: str = "gpt-4o-mini") -> Dict:
    """
    Analyze job-resume match using OpenAI.
    
    Returns:
        Dict with score, gaps, and suggested achievements
    """
    prompt = f"""You are an expert ATS resume optimizer. Analyze this job description against the candidate's resume.

JOB DESCRIPTION:
{job_desc[:4000]}

CANDIDATE RESUME/ACCOMPLISHMENTS:
{resume[:4000]}

IMPORTANT SCORING RULES:
- The candidate has ~5 years of experience and targets INDIVIDUAL CONTRIBUTOR roles only.
- Roles requiring 8+ years of experience should be penalized (cap at 50).

STRICT SENIORITY PENALTY (MANDATORY):
- If the job title contains ANY of: "Lead", "Principal", "Staff", "Senior Staff", "Manager", "Director", "Head of", "VP", "Chief":
  -> CAP the match_score at a MAXIMUM of 30, regardless of how perfect the skill overlap is.
  -> The ONLY exception: if the job description EXPLICITLY states "individual contributor" or "no direct reports" AND requires 5 years or fewer of experience, then score normally.
- If the title contains "Senior" (but NOT "Senior Staff", "Senior Lead", "Senior Principal"), score normally. Senior IC is the target level.
- Ideal title matches: "DevOps Engineer", "Senior DevOps Engineer", "SRE", "Cloud Engineer", "Platform Engineer", "Infrastructure Engineer"
- Be STRICT. A perfect skill match with a Principal/Lead/Director title MUST score at most 30.

IRRELEVANT ROLE PENALTY (MANDATORY):
- If the role is primarily Sales, Marketing, HR, Legal, Finance, Recruiting, or Customer Success: score 0.
- If the role is primarily frontend/UI/UX with no infrastructure component: cap at 20.

Provide a JSON response with:
1. "match_score": 0-100 score based on skills/experience overlap (APPLY SENIORITY PENALTY if applicable)
2. "matching_skills": List of skills the candidate has that match the job
3. "missing_skills": List of required skills the candidate is missing
4. "gap_analysis": 1-2 sentence summary of gaps (mention if role is too senior)
5. "suggested_achievements": List of 3-5 achievement bullet points that DIRECTLY ADDRESS the job requirements.

   CRITICAL INSTRUCTIONS for achievements:
   - Each achievement MUST target a specific requirement from the job description
   - Start with action verb (Led, Implemented, Designed, Automated, Reduced, etc.)
   - Include relevant technologies MENTIONED IN THE JOB (e.g., Azure, Kubernetes, Terraform, CI/CD)
   - Use placeholder metrics: **[XX]%** or **[XX]** for numbers the candidate will fill in
   - Focus on outcomes: cost savings, uptime, deployment frequency, incident reduction
   - If job mentions specific tools (Datadog, Splunk, Jenkins), include those in achievements

   Example format:
   "Implemented [TOOL_FROM_JOB] monitoring solution, reducing mean time to detection by **[XX]%**"
   "Automated deployment pipelines using [TECH_FROM_JOB], enabling **[XX]** releases per day"

Return ONLY valid JSON, no markdown code blocks."""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.3
        )
        
        content = response.content[0].text if hasattr(response, 'content') else response.choices[0].message.content
        # Clean up response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        
        return json.loads(content)
    except Exception as e:
        print(f"  ⚠️ OpenAI error: {e}")
        return {"match_score": 0, "error": str(e)}


def process_enterprise_jobs(dry_run: bool = False, limit: int = 10, gen_cover_letters: bool = False, company_filter: str = None) -> List[Dict]:
    """
    Process enterprise jobs from job_state.json.

    Args:
        dry_run: If True, don't make API calls
        limit: Max jobs to process
        gen_cover_letters: If True, generate cover letters for 70%+ matches
        company_filter: Optional - only analyze jobs from this company

    Returns:
        List of match results
    """
    config = load_config()

    # Load job state
    if not STATE_PATH.exists():
        print("❌ No job_state.json found. Run ats_sniper.py first.")
        return []

    with open(STATE_PATH, 'r', encoding='utf-8') as f:
        state = json.load(f)

    # Filter enterprise jobs
    enterprise_patterns = config.get("company_tiers", {}).get("enterprise", [])
    enterprise_jobs = []

    for url, job in state.get("jobs", {}).items():
        if any(pattern in url.lower() for pattern in enterprise_patterns):
            # Apply company filter if specified
            if company_filter:
                if company_filter.lower() not in url.lower() and company_filter.lower() not in job.get('company', '').lower():
                    continue
            enterprise_jobs.append({"url": url, **job})

    filter_msg = f" (filtered: {company_filter})" if company_filter else ""
    print(f"📊 Found {len(enterprise_jobs)} enterprise jobs{filter_msg}")
    
    if dry_run:
        print("⚠️ DRY RUN - would process these jobs:")
        for job in enterprise_jobs[:limit]:
            print(f"   • {job.get('company', 'Unknown')}: {job.get('title', 'Unknown')}")
        return []
    
    # Initialize OpenAI
    client = OpenAI(api_key=config.get("openai_key"))
    model = config.get("settings", {}).get("openai_model", "gpt-4o-mini")
    
    # Load resume
    resume = load_base_resume()
    if not resume:
        print("❌ No accomplishments.md found")
        return []
    
    results = []
    
    print(f"\n🤖 Analyzing {min(limit, len(enterprise_jobs))} jobs with {model}...")
    
    for job in enterprise_jobs[:limit]:
        print(f"\n📄 {job.get('company', 'Unknown')}: {job.get('title', 'Unknown')}")
        
        # Fetch full job description
        job_data = fetch_job_description(job["url"])
        if not job_data:
            print("   ⚠️ Could not fetch job description")
            continue
        
        # Analyze match
        analysis = analyze_job_match(client, job_data.get("description", ""), resume, model)

        result = {
            "url": job["url"],
            "company": job.get("company", "Unknown"),
            "title": job.get("title", "Unknown"),
            **analysis
        }

        score = analysis.get("match_score", 0)
        emoji = "✅" if score >= 80 else "⚠️" if score >= 60 else "❌"
        print(f"   {emoji} Match: {score}%")
        if analysis.get("gap_analysis"):
            print(f"   📝 Gaps: {analysis['gap_analysis']}")

        # Generate cover letter for high-scoring matches (70%+)
        if gen_cover_letters and score >= 70:
            print(f"   📝 Generating cover letter...")
            cover_letter = generate_cover_letter(
                client,
                job_data.get("description", ""),
                resume,
                job.get("company", "Unknown"),
                job.get("title", "Unknown"),
                model
            )
            if cover_letter:
                result["cover_letter"] = cover_letter
                # Save to file
                COVER_LETTERS_DIR.mkdir(exist_ok=True)
                safe_company = "".join(c for c in job.get("company", "Unknown") if c.isalnum() or c in " _-")
                safe_title = "".join(c for c in job.get("title", "Unknown")[:30] if c.isalnum() or c in " _-")
                cl_path = COVER_LETTERS_DIR / f"cover_letter_{safe_company}_{safe_title}.txt"
                with open(cl_path, 'w', encoding='utf-8') as f:
                    f.write(cover_letter)
                print(f"   💾 Saved: {cl_path.name}")

        results.append(result)
    
    # Save results
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"match_report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    print(f"\n📊 Report saved: {report_path}")

    return results


def inject_achievements_to_accomplishments(report_path: str, target_company: str = None) -> Path:
    """
    Inject AI-generated achievements from a match report into accomplishments.md.
    Creates a new section for AI-generated achievements that can be reviewed and edited.

    Args:
        report_path: Path to the match report JSON
        target_company: Optional - only inject for this company

    Returns:
        Path to the updated accomplishments.md
    """
    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Filter by company if specified
    if target_company:
        report = [r for r in report if target_company.lower() in r.get('company', '').lower()]

    if not report:
        print(f"❌ No matches found" + (f" for {target_company}" if target_company else ""))
        return None

    # Load existing accomplishments
    with open(ACCOMPLISHMENTS_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if AI section already exists
    ai_section_marker = "## AI-Generated Achievements (Review Required)"
    if ai_section_marker not in content:
        content += f"\n\n---\n\n{ai_section_marker}\n"
        content += "**Instructions:** Review each achievement below. Replace `[XX]` with real metrics.\n"
        content += "Copy approved achievements to the appropriate job section above.\n\n"

    # Add new achievements
    new_achievements = []
    for match in report:
        if match.get('match_score', 0) < 60:
            continue  # Skip low matches

        company = match.get('company', 'Unknown')
        title = match.get('title', 'Unknown')
        suggestions = match.get('suggested_achievements', [])

        if not suggestions:
            continue

        # Create unique ID based on company/title
        safe_company = ''.join(c for c in company if c.isalnum())[:10].upper()
        timestamp = datetime.now().strftime('%m%d')

        new_achievements.append(f"\n### {company} - {title} (Match: {match.get('match_score', 0)}%)\n")

        for i, achievement in enumerate(suggestions, 1):
            achievement_id = f"AI-{safe_company}-{timestamp}-{i:02d}"
            new_achievements.append(f"\n#### {achievement_id}\n")
            new_achievements.append(f"**AI-Generated Achievement**\n")
            new_achievements.append(f"- {achievement}\n")
            new_achievements.append(f"- **Technologies:** (add relevant tech)\n")
            new_achievements.append(f"- **Roles:** devops, sre, cloud\n")

    if not new_achievements:
        print("❌ No achievements to inject (all matches < 60%)")
        return None

    content += ''.join(new_achievements)

    # Write back
    with open(ACCOMPLISHMENTS_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✅ Injected {len(new_achievements) // 5} AI achievements into accomplishments.md")
    print(f"📝 Review and edit: {ACCOMPLISHMENTS_FILE}")

    return ACCOMPLISHMENTS_FILE


def generate_targeted_resume(report_path: str, job_index: int = 0) -> Path:
    """
    Generate a targeted resume for a specific job from a match report.

    Args:
        report_path: Path to the match report JSON
        job_index: Which job in the report to target (0 = best match)

    Returns:
        Path to the generated resume markdown
    """
    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Sort by match score and get target job
    report = sorted(report, key=lambda x: x.get('match_score', 0), reverse=True)

    if job_index >= len(report):
        print(f"❌ Job index {job_index} out of range (max: {len(report) - 1})")
        return None

    job = report[job_index]
    company = job.get('company', 'Unknown')
    title = job.get('title', 'Unknown')

    # Create a targeted resume source file
    safe_company = ''.join(c for c in company if c.isalnum() or c == ' ').replace(' ', '_')
    safe_title = ''.join(c for c in title if c.isalnum() or c == ' ')[:30].replace(' ', '_')

    resume_filename = f"resume_{safe_company}_{safe_title}.md"
    resume_path = Path(__file__).parent.parent / resume_filename

    # Copy from devops template and customize
    template_path = Path(__file__).parent.parent / "resume_devops.md"
    if not template_path.exists():
        print(f"❌ Template not found: {template_path}")
        return None

    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()

    # Update the role in frontmatter
    template = template.replace(
        'role: "DevOps Engineer"',
        f'role: "{title}"'
    )

    # Add a comment about the target job
    header_comment = f"""# Targeted Resume for: {company} - {title}
# Match Score: {job.get('match_score', 0)}%
# Missing Skills: {', '.join(job.get('missing_skills', [])[:3])}
#
# Review AI achievements in accomplishments.md and add relevant IDs below

"""
    template = header_comment + template

    with open(resume_path, 'w', encoding='utf-8') as f:
        f.write(template)

    print(f"✅ Created targeted resume: {resume_path}")
    print(f"   Company: {company}")
    print(f"   Role: {title}")
    print(f"   Match: {job.get('match_score', 0)}%")

    return resume_path


if __name__ == "__main__":
    import argparse
    import glob

    parser = argparse.ArgumentParser(description="Resume Tailor v3")
    parser.add_argument("--dry-run", action="store_true", help="Don't make API calls")
    parser.add_argument("--limit", type=int, default=5, help="Max jobs to process")
    parser.add_argument("--company", type=str, help="Filter analysis to specific company (e.g., --company Medpace)")
    parser.add_argument("--cover-letters", action="store_true", help="Generate cover letters for 70 percent or higher matches")
    parser.add_argument("--cost", action="store_true", help="Show cost analysis")
    parser.add_argument("--inject", action="store_true", help="Inject AI achievements into accomplishments.md")
    parser.add_argument("--inject-company", type=str, help="Only inject for specific company")
    parser.add_argument("--generate-resume", action="store_true", help="Generate targeted resume for best match")
    parser.add_argument("--report", type=str, help="Path to specific report file (default: latest)")
    args = parser.parse_args()

    if args.cost:
        print("=" * 60)
        print("💰 GPT-4o-mini COST ANALYSIS")
        print("=" * 60)
        print("""
        PRICING (as of March 2026):
        ┌────────────────────┬──────────────┬──────────────┐
        │ Model              │ Input/1M tok │ Output/1M tok│
        ├────────────────────┼──────────────┼──────────────┤
        │ GPT-4o-mini        │ $0.15        │ $0.60        │
        │ GPT-4o             │ $2.50        │ $10.00       │
        │ GPT-4-turbo        │ $10.00       │ $30.00       │
        └────────────────────┴──────────────┴──────────────┘

        ESTIMATED COST PER OPERATION (GPT-4o-mini):
        • Match analysis:     ~$0.003/job  (2K input + 500 output tokens)
        • Cover letter:       ~$0.002/job  (1.5K input + 400 output tokens)
        • TOTAL per job:      ~$0.005/job

        MONTHLY ESTIMATES:
        ┌─────────────┬───────────────┬────────────────────┐
        │ Jobs/month  │ Match only    │ Match + Cover Ltrs │
        ├─────────────┼───────────────┼────────────────────┤
        │ 50 jobs     │ $0.15         │ $0.25              │
        │ 100 jobs    │ $0.30         │ $0.50              │
        │ 200 jobs    │ $0.60         │ $1.00              │
        └─────────────┴───────────────┴────────────────────┘

        With your $120/month budget, you could process ~24,000 jobs!
        """)
        sys.exit(0)

    # Get report path (use latest if not specified)
    if args.report:
        report_path = args.report
    else:
        reports = sorted(glob.glob(str(REPORTS_DIR / "match_report_*.json")))
        report_path = reports[-1] if reports else None

    # Handle inject command
    if args.inject:
        print("=" * 60)
        print("📥 INJECTING AI ACHIEVEMENTS")
        print("=" * 60)
        if not report_path:
            print("❌ No reports found. Run analysis first: python resume_tailor.py --limit 10")
            sys.exit(1)
        print(f"📄 Using report: {report_path}")
        inject_achievements_to_accomplishments(report_path, args.inject_company)
        sys.exit(0)

    # Handle generate-resume command
    if args.generate_resume:
        print("=" * 60)
        print("📝 GENERATING TARGETED RESUME")
        print("=" * 60)
        if not report_path:
            print("❌ No reports found. Run analysis first: python resume_tailor.py --limit 10")
            sys.exit(1)
        print(f"📄 Using report: {report_path}")
        generate_targeted_resume(report_path)
        sys.exit(0)

    print("=" * 60)
    print("🎯 RESUME TAILOR v3 - AI-Powered Matching")
    print("=" * 60)

    if args.cover_letters:
        print("📝 Cover letter generation ENABLED (for 70%+ matches)")

    results = process_enterprise_jobs(
        dry_run=args.dry_run,
        limit=args.limit,
        gen_cover_letters=args.cover_letters,
        company_filter=args.company
    )

    if results:
        print("\n" + "=" * 60)
        print("📊 TOP MATCHES")
        print("=" * 60)
        for r in sorted(results, key=lambda x: x.get("match_score", 0), reverse=True)[:5]:
            cl = "📝" if r.get("cover_letter") else "  "
            print(f"   {cl} {r.get('match_score', 0):3}% | {r['company']}: {r['title']}")

        print("\n" + "=" * 60)
        print("📋 NEXT STEPS")
        print("=" * 60)
        print("  1. Inject achievements: python resume_tailor.py --inject")
        print("  2. Generate resume:     python resume_tailor.py --generate-resume")
        print("  3. Run generate_resumes.py to create DOCX/PDF")

