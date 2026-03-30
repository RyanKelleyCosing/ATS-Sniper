#!/usr/bin/env python3
"""
Hot Job Processor - Automated Resume Tailoring for High-Match Jobs

The "Sniper" Logic:
- Score < 80%: Job goes to CSV for manual review
- Score >= 80%: Automatically generate tailored resume + attach to email notification

This is the bridge between job scoring and resume generation.
"""

import json
import csv
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Local imports
from job_scraper import fetch_job_description
from resume_tailor import analyze_job_match
from generate_tailored_resume import generate_tailored_resume_for_job

# Paths
SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "job_state.json"
OUTPUTS_DIR = SCRIPT_DIR / "outputs"
CSV_PATH = SCRIPT_DIR / "jobs_export.csv"

# Hot job threshold
HOT_JOB_THRESHOLD = 80


def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"seen_jobs": {}}


def load_master_resume() -> str:
    """Load master resume content for scoring."""
    resume_path = SCRIPT_DIR.parent / "resume_devops.md"
    if resume_path.exists():
        with open(resume_path, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


def categorize_jobs_by_score(jobs: List[Dict], resume: str, config: dict) -> Tuple[List[Dict], List[Dict]]:
    """
    Analyze jobs and split into hot (>=80%) and regular (<80%).
    
    Returns:
        Tuple of (hot_jobs, regular_jobs) with match analysis attached
    """
    from openai import OpenAI
    
    client = OpenAI(api_key=config.get("openai_key"))
    model = config.get("settings", {}).get("openai_model", "gpt-4o-mini")
    
    hot_jobs = []
    regular_jobs = []
    
    for job in jobs:
        print(f"\n📊 Analyzing: {job.get('title', 'Unknown')} @ {job.get('company', 'Unknown')}")
        
        # Fetch full job description
        job_data = fetch_job_description(job.get("url", ""))
        if not job_data:
            print("   ⚠️ Could not fetch job description, skipping")
            job["match_score"] = 0
            regular_jobs.append(job)
            continue
        
        job_desc = job_data.get("description", "")
        
        # Analyze match
        analysis = analyze_job_match(client, job_desc, resume, model)
        match_score = analysis.get("match_score", 0)
        
        # Attach analysis to job
        job["match_score"] = match_score
        job["gap_analysis"] = analysis.get("gap_analysis", "")
        job["suggested_achievements"] = analysis.get("suggested_achievements", [])
        job["job_description"] = job_desc
        
        emoji = "🔥" if match_score >= HOT_JOB_THRESHOLD else "📋"
        print(f"   {emoji} Match Score: {match_score}%")
        
        if match_score >= HOT_JOB_THRESHOLD:
            hot_jobs.append(job)
        else:
            regular_jobs.append(job)
    
    return hot_jobs, regular_jobs


def process_hot_jobs(hot_jobs: List[Dict], dry_run: bool = False) -> List[Dict]:
    """
    Process hot jobs: generate tailored resumes for each.
    
    Returns:
        List of job dicts with resume paths attached
    """
    results = []
    
    for job in hot_jobs:
        print(f"\n🎯 Processing HOT job: {job.get('title')} @ {job.get('company')}")
        
        result = generate_tailored_resume_for_job(
            job_url=job.get("url", ""),
            job_description=job.get("job_description", ""),
            company=job.get("company", "Unknown"),
            role=job.get("title", "Unknown"),
            match_score=job.get("match_score", 80),
            dry_run=dry_run
        )
        
        if result and result.get("status") == "success":
            job["resume_pdf"] = result.get("resume_pdf")
            job["resume_docx"] = result.get("resume_docx")
            job["analysis_report"] = result.get("analysis_report")
            job["output_dir"] = result.get("output_dir")
            results.append(job)
        else:
            print(f"   ⚠️ Failed to generate resume for {job.get('company')}")
    
    return results


def export_regular_jobs_to_csv(jobs: List[Dict]):
    """Export non-hot jobs to CSV for manual tracking."""
    if not jobs:
        return
    
    csv_data = []
    for job in jobs:
        csv_data.append({
            "Title": job.get("title", "Unknown"),
            "Company": job.get("company", "Unknown"),
            "URL": job.get("url", ""),
            "Match Score": job.get("match_score", 0),
            "Gap Analysis": job.get("gap_analysis", "")[:200],
            "Found Date": datetime.now().strftime("%Y-%m-%d"),
            "Source": job.get("source", "unknown"),
            "Applied": "",
            "Status": "",
            "Notes": ""
        })
    
    # Append to existing CSV or create new
    file_exists = CSV_PATH.exists()
    
    with open(CSV_PATH, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_data[0].keys())
        if not file_exists:
            writer.writeheader()
        writer.writerows(csv_data)
    
    print(f"📊 Exported {len(jobs)} regular jobs to {CSV_PATH.name}")


def run_hot_job_pipeline(jobs: List[Dict], dry_run: bool = False) -> Dict:
    """
    Main pipeline: Score jobs, generate resumes for hot ones, export rest to CSV.

    Args:
        jobs: List of job dicts from scrapers
        dry_run: If True, don't generate files

    Returns:
        Dict with hot_jobs (with resume paths) and regular_jobs
    """
    print("=" * 60)
    print("🎯 HOT JOB PROCESSOR - v3")
    print("=" * 60)
    print(f"Processing {len(jobs)} jobs...")
    print(f"Hot Job Threshold: {HOT_JOB_THRESHOLD}%")

    config = load_config()
    resume = load_master_resume()

    if not resume:
        print("⚠️ No master resume found, using basic scoring")

    # Step 1: Categorize by score
    print("\n📊 Phase 1: Scoring jobs...")
    hot_jobs, regular_jobs = categorize_jobs_by_score(jobs, resume, config)

    print(f"\n📈 Scoring Results:")
    print(f"   🔥 Hot Jobs (>={HOT_JOB_THRESHOLD}%): {len(hot_jobs)}")
    print(f"   📋 Regular Jobs (<{HOT_JOB_THRESHOLD}%): {len(regular_jobs)}")

    # Step 2: Process hot jobs
    processed_hot_jobs = []
    if hot_jobs:
        print("\n🎯 Phase 2: Generating tailored resumes for hot jobs...")
        processed_hot_jobs = process_hot_jobs(hot_jobs, dry_run=dry_run)

    # Step 3: Export regular jobs to CSV
    if regular_jobs:
        print("\n📊 Phase 3: Exporting regular jobs to CSV...")
        export_regular_jobs_to_csv(regular_jobs)

    return {
        "hot_jobs": processed_hot_jobs,
        "regular_jobs": regular_jobs,
        "stats": {
            "total_processed": len(jobs),
            "hot_count": len(processed_hot_jobs),
            "regular_count": len(regular_jobs),
            "resumes_generated": len([j for j in processed_hot_jobs if j.get("resume_pdf")])
        }
    }


def get_hot_job_attachments(hot_jobs: List[Dict]) -> List[Dict]:
    """
    Get list of resume attachments for email.

    Returns:
        List of dicts with 'path' and 'filename' for each attachment
    """
    attachments = []

    for job in hot_jobs:
        # Prefer PDF, fallback to DOCX
        if job.get("resume_pdf") and Path(job["resume_pdf"]).exists():
            attachments.append({
                "path": job["resume_pdf"],
                "filename": Path(job["resume_pdf"]).name,
                "company": job.get("company", "Unknown"),
                "role": job.get("title", "Unknown"),
                "match_score": job.get("match_score", 0)
            })
        elif job.get("resume_docx") and Path(job["resume_docx"]).exists():
            attachments.append({
                "path": job["resume_docx"],
                "filename": Path(job["resume_docx"]).name,
                "company": job.get("company", "Unknown"),
                "role": job.get("title", "Unknown"),
                "match_score": job.get("match_score", 0)
            })

    return attachments


if __name__ == "__main__":
    import sys

    # Test with sample jobs
    test_jobs = [
        {
            "title": "Senior DevOps Engineer",
            "company": "Test Company",
            "url": "https://example.com/job/1",
            "source": "test"
        }
    ]

    dry_run = "--dry-run" in sys.argv
    result = run_hot_job_pipeline(test_jobs, dry_run=dry_run)

    print("\n📊 Pipeline Results:")
    print(json.dumps(result["stats"], indent=2))

