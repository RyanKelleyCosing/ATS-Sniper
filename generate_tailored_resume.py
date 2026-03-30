#!/usr/bin/env python3
"""
AI Resume Tailoring Engine - Surgical Editor for Hot Jobs

Strategy: Use LLM as a surgical editor, NOT to write from scratch.
This avoids "AI-sounding" resumes while ensuring perfect job alignment.

Process:
1. Context Injection: Feed LLM the JD + Master Resume + Accomplishments Bank
2. Pivot Logic: LLM selects top 5 most relevant accomplishments
3. Summary Rewrite: LLM rewrites Professional Summary for company's tech stack
4. Render: Inject selections into Jinja2 template
5. Export: Generate PDF/DOCX via existing generate_resumes.py

Only runs for HOT jobs (match score >= 80%)
"""

import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

# Paths
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
SKILLS_PATH = SCRIPT_DIR / "master_skills.json"
ACCOMPLISHMENTS_PATH = ROOT_DIR / "accomplishments.md"
OUTPUTS_DIR = SCRIPT_DIR / "outputs"

# Import from parent directory
sys.path.insert(0, str(ROOT_DIR))


def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_skills_taxonomy() -> dict:
    with open(SKILLS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_accomplishments() -> str:
    """Load raw accomplishments markdown."""
    with open(ACCOMPLISHMENTS_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def load_resume_template(role: str) -> str:
    """Load the appropriate resume template based on role."""
    skills = load_skills_taxonomy()
    role_lower = role.lower().replace(" ", "_").replace("-", "_")
    
    # Map role to template
    template_file = None
    for role_key, role_config in skills.get("role_mappings", {}).items():
        if role_key in role_lower or role_lower in role_key:
            template_file = role_config.get("template")
            break
    
    if not template_file:
        template_file = "resume_devops.md"  # Default fallback
    
    template_path = ROOT_DIR / template_file
    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    return ""


def parse_accomplishments_to_dict(md_content: str) -> Dict[str, Dict]:
    """Parse accomplishments.md into a dict keyed by ID."""
    accomplishments = {}
    current_id = None
    current_data = {}
    
    lines = md_content.split('\n')
    for line in lines:
        # Match accomplishment ID headers like ### CICD-001
        if line.startswith('### ') and re.match(r'^### [A-Z0-9]+-\d+', line):
            if current_id and current_data:
                accomplishments[current_id] = current_data
            current_id = line[4:].strip()
            current_data = {"id": current_id, "title": "", "bullet": "", "technologies": "", "roles": []}
        elif current_id:
            if line.startswith('**') and line.endswith('**'):
                current_data["title"] = line.strip('*').strip()
            elif line.startswith('- ') and not line.startswith('- **'):
                current_data["bullet"] = line[2:].strip()
            elif line.startswith('- **Technologies:**'):
                current_data["technologies"] = line.split(':', 1)[1].strip()
            elif line.startswith('- **Roles:**'):
                roles_str = line.split(':', 1)[1].strip()
                current_data["roles"] = [r.strip() for r in roles_str.split(',')]
    
    if current_id and current_data:
        accomplishments[current_id] = current_data
    
    return accomplishments


def analyze_job_and_select_accomplishments(
    client: OpenAI,
    job_description: str,
    accomplishments: Dict[str, Dict],
    company: str,
    role: str,
    model: str = "gpt-4o-mini"
) -> Dict:
    """
    Use LLM to analyze job and select best accomplishments.
    
    Returns:
        Dict with:
        - selected_accomplishments: List of accomplishment IDs (top 5-8)
        - rewritten_summary: Tailored professional summary
        - match_reasoning: Why these were selected
        - detected_tech_stack: Key technologies mentioned in JD
    """
    # Format accomplishments for LLM
    acc_text = "\n".join([
        f"ID: {aid}\nTitle: {data['title']}\nBullet: {data['bullet']}\nTech: {data['technologies']}"
        for aid, data in accomplishments.items()
    ])
    
    prompt = f"""You are an expert resume tailoring specialist. Your job is to surgically select the BEST accomplishments AND infer additional ones where gaps exist.

COMPANY: {company}
ROLE: {role}

JOB DESCRIPTION:
{job_description[:4000]}

CANDIDATE'S ACCOMPLISHMENTS BANK:
{acc_text[:6000]}

INSTRUCTIONS:
1. Analyze the job description for key requirements, tech stack, and priorities
2. Select MINIMUM 6 accomplishments, ideally 8-10, that best match this specific role
3. IMPORTANT: If the JD requires a key technology/skill that has no matching accomplishment, INFER a realistic one. Use your knowledge of what someone in this role would typically accomplish. Include realistic metrics.
4. Write a 2-3 sentence professional summary. DO NOT use the exact job title - use a generic equivalent (e.g., "Infrastructure Engineer" instead of "Lead DevOps Infrastructure Engineer")
5. For EACH of the candidate's job positions, ensure there are at least 2-3 bullets (selecting from bank OR inferring new ones)
6. Identify the key technologies mentioned in the JD
7. INFER ADJACENT SKILLS: If the JD requires a skill the candidate likely has based on their experience, ADD it to tailored_skills. Examples:
   - Has Terraform → likely knows Ansible, CloudFormation, Pulumi
   - Has Kubernetes → likely knows Helm, container networking, service mesh
   - Has Azure DevOps → likely knows GitHub Actions, GitLab CI
   - Has Python → likely knows Bash, scripting, automation
   - Has SQL Server → likely knows PostgreSQL, database optimization

CRITICAL PAGE LENGTH CONSTRAINT (MUST BE 1 PAGE):
- Resume MUST fit on exactly 1 page - DO NOT add content that would push to page 2
- Select 6-10 accomplishments total (not more)
- Skills section: EXACTLY 4 categories with 4-5 skills each (16-20 skills MAX)
- Summary: 2-3 sentences only
- If in doubt, be conservative - fewer items is better than page overflow

The candidate has multiple job positions. Analyze the resume template to identify each position's title and context.
Match accomplishments to positions based on role context (e.g., database roles get database achievements, DevOps roles get infrastructure achievements).
Do NOT assign cloud/infrastructure achievements to database-focused positions.

For inferred accomplishments, create realistic, quantified achievements. Example formats:
- DevOps: "Implemented CI/CD pipeline automation, reducing deployment time by 80%"
- Database: "Optimized SQL stored procedures, reducing query execution time by 60% across 500+ daily reports"

Respond in this exact JSON format:
{{
    "selected_accomplishments": ["CICD-001", "K8S-001", ...],
    "inferred_accomplishments": {{
        "Company A": [
            "Implemented X achieving Y% improvement...",
            ...
        ],
        "Company B": [
            "Optimized performance achieving X% improvement...",
            ...
        ]
    }},
    "rewritten_summary": "Results-driven infrastructure professional with X+ years...",
    "tailored_skills": {{
        "CI/CD & AUTOMATION": ["Azure DevOps", "YAML Pipelines", "GitHub Actions", "Jenkins"],
        "CLOUD & INFRASTRUCTURE": ["Azure", "Kubernetes", "Terraform", "Docker"],
        "MONITORING & OBSERVABILITY": ["Prometheus", "Grafana", "Azure Monitor", "Log Analytics"],
        "SCRIPTING & DATABASES": ["Python", "PowerShell", "SQL Server", "T-SQL"]
    }},
    "match_reasoning": "Selected based on...",
    "detected_tech_stack": ["Azure", "Kubernetes", "Python", ...],
    "industry_focus": "finance|healthcare|tech|retail|other",
    "confidence_score": 85
}}

For tailored_skills:
- Reorder categories so the MOST JD-RELEVANT category is FIRST
- EXACTLY 4 categories, 4-5 skills each (16-20 total MAX for 1-page fit)
- Include skills from JD that candidate LIKELY has based on adjacent experience
- Mark inferred skills with * (e.g., "Ansible*") so human can verify
- Category names should match JD language when possible

CRITICAL:
- Only select existing accomplishments that are genuinely relevant
- Infer REALISTIC accomplishments for gaps - these should sound natural, not AI-generated
- DO NOT use the job title verbatim in the summary - keep it generic
- Database-focused positions MUST have database/data-focused achievements, NOT cloud/DevOps
- HARD LIMIT: Resume must fit 1 page. When in doubt, include LESS content.
"""
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.3
        )
        content = response.choices[0].message.content.strip()
        
        # Parse JSON from response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        return json.loads(content)
    except Exception as e:
        print(f"  ❌ LLM analysis error: {e}")
        return {
            "selected_accomplishments": list(accomplishments.keys())[:5],
            "rewritten_summary": "",
            "match_reasoning": "Fallback selection",
            "detected_tech_stack": [],
            "confidence_score": 50
        }


def create_tailored_resume_source(
    company: str,
    role: str,
    analysis: Dict,
    base_template: str,
    output_dir: Path
) -> Path:
    """
    Create a tailored resume source file with selected accomplishments.

    This creates a new resume_Company_Role.md file with:
    - Selected accomplishments injected
    - Rewritten summary
    """
    import yaml

    # Parse the base template
    if base_template.startswith('---'):
        parts = base_template.split('---', 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            body = parts[2]
        else:
            frontmatter = {}
            body = base_template
    else:
        frontmatter = {}
        body = base_template

    # Update frontmatter with tailored summary
    if analysis.get("rewritten_summary"):
        frontmatter["summary"] = analysis["rewritten_summary"]

    # Update role to match job
    frontmatter["role"] = role

    # Update skills section with tailored/reordered skills
    if analysis.get("tailored_skills"):
        frontmatter["skills"] = analysis["tailored_skills"]

    # Create new experience section with selected accomplishments
    selected_ids = analysis.get("selected_accomplishments", [])
    inferred = analysis.get("inferred_accomplishments", {})

    # Map company names to inferred accomplishments (normalize for matching)
    def normalize_company(name):
        return name.lower().replace(' ', '').replace('.', '').replace('-', '')

    inferred_map = {normalize_company(k): v for k, v in inferred.items()}

    # Find the experience section and rebuild with selected accomplishments
    exp_lines = []
    in_experience = False
    current_job_name = None
    current_job_line = None
    job_bullets_added = {}  # Track bullets per job

    for line in body.split('\n'):
        if line.strip() == "## Experience":
            in_experience = True
            exp_lines.append(line)
        elif in_experience:
            if line.startswith('### '):
                # Before moving to next job, inject inferred bullets for previous job if needed
                if current_job_name and current_job_name in inferred_map:
                    for inferred_bullet in inferred_map[current_job_name]:
                        # Add inferred bullet as direct text (not ID reference)
                        exp_lines.append(f"- {inferred_bullet}")
                        job_bullets_added[current_job_name] = job_bullets_added.get(current_job_name, 0) + 1

                # Parse job name from line like "### Company | Title | Dates"
                current_job_line = line
                parts = line[4:].split('|')
                if parts:
                    current_job_name = normalize_company(parts[0].strip())
                exp_lines.append(line)

            elif line.startswith('- ') and re.match(r'^- [A-Z0-9]+-\d+', line):
                # This is an accomplishment reference
                acc_id = line[2:].strip()
                if acc_id in selected_ids:
                    exp_lines.append(line)
                    job_bullets_added[current_job_name] = job_bullets_added.get(current_job_name, 0) + 1
            elif line.startswith('## '):
                # Before ending experience section, inject final job's inferred bullets
                if current_job_name and current_job_name in inferred_map:
                    for inferred_bullet in inferred_map[current_job_name]:
                        exp_lines.append(f"- {inferred_bullet}")
                        job_bullets_added[current_job_name] = job_bullets_added.get(current_job_name, 0) + 1

                # End of experience section
                in_experience = False
                exp_lines.append(line)
            else:
                exp_lines.append(line)
        else:
            exp_lines.append(line)

    # Handle case where experience section is at end of file (no ## after it)
    if in_experience and current_job_name and current_job_name in inferred_map:
        for inferred_bullet in inferred_map[current_job_name]:
            exp_lines.append(f"- {inferred_bullet}")

    # Build new content
    new_content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n"
    new_content += '\n'.join(exp_lines)

    # Create safe filename
    safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:30]
    safe_role = re.sub(r'[^\w\s-]', '', role).replace(' ', '_')[:30]

    output_path = output_dir / f"resume_{safe_company}_{safe_role}.md"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return output_path


def generate_analysis_report(
    company: str,
    role: str,
    job_url: str,
    match_score: int,
    analysis: Dict,
    output_dir: Path
) -> Path:
    """Generate a markdown analysis report for the job match."""

    report = f"""# Job Analysis Report

## Job Details
- **Company:** {company}
- **Role:** {role}
- **URL:** {job_url}
- **Match Score:** {match_score}%
- **Confidence:** {analysis.get('confidence_score', 'N/A')}%
- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Detected Tech Stack
{', '.join(analysis.get('detected_tech_stack', ['Not analyzed']))}

## Industry Focus
{analysis.get('industry_focus', 'General').title()}

## Selected Accomplishments
{chr(10).join(['- ' + aid for aid in analysis.get('selected_accomplishments', [])])}

## Match Reasoning
{analysis.get('match_reasoning', 'No reasoning provided')}

## Tailored Summary
{analysis.get('rewritten_summary', 'Using default summary')}

---
*Generated by ATS Sniper v3 - AI Resume Tailoring Engine*
"""

    safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:30]
    safe_role = re.sub(r'[^\w\s-]', '', role).replace(' ', '_')[:30]

    report_path = output_dir / f"{safe_company}_{safe_role}_Analysis.md"

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    return report_path


def generate_tailored_resume_for_job(
    job_url: str,
    job_description: str,
    company: str,
    role: str,
    match_score: int = 80,
    dry_run: bool = False
) -> Optional[Dict]:
    """
    Main entry point: Generate a tailored resume for a hot job.

    Args:
        job_url: URL of the job posting
        job_description: Full text of the job description
        company: Company name
        role: Job title
        match_score: Pre-calculated match score
        dry_run: If True, don't generate files

    Returns:
        Dict with paths to generated files or None on failure
    """
    print(f"\n🎯 Generating tailored resume for: {role} @ {company}")
    print(f"   Match Score: {match_score}%")

    config = load_config()
    client = OpenAI(api_key=config.get("openai_key"))
    model = config.get("settings", {}).get("openai_model", "gpt-4o-mini")

    # Load accomplishments
    accomplishments_md = load_accomplishments()
    accomplishments = parse_accomplishments_to_dict(accomplishments_md)
    print(f"   Loaded {len(accomplishments)} accomplishments")

    # Load base template
    base_template = load_resume_template(role)

    if dry_run:
        print("   [DRY RUN] Would analyze and generate resume")
        return {"status": "dry_run"}

    # Create output directory
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_company = re.sub(r'[^\w\s-]', '', company).replace(' ', '_')[:20]
    safe_role = re.sub(r'[^\w\s-]', '', role).replace(' ', '_')[:20]
    output_dir = OUTPUTS_DIR / f"{date_str}_{safe_company}_{safe_role}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Analyze job and select accomplishments
    print("   📊 Analyzing job description...")
    analysis = analyze_job_and_select_accomplishments(
        client, job_description, accomplishments, company, role, model
    )

    selected_count = len(analysis.get("selected_accomplishments", []))
    print(f"   ✅ Selected {selected_count} accomplishments")

    # Step 2: Generate analysis report
    print("   📝 Generating analysis report...")
    report_path = generate_analysis_report(
        company, role, job_url, match_score, analysis, output_dir
    )

    # Step 3: Create tailored resume source
    print("   📄 Creating tailored resume source...")
    resume_source_path = create_tailored_resume_source(
        company, role, analysis, base_template, output_dir
    )

    # Step 4: Generate PDF and DOCX
    print("   🖨️ Generating PDF and DOCX...")
    pdf_path, docx_path = generate_resume_outputs(resume_source_path, output_dir)

    result = {
        "status": "success",
        "output_dir": str(output_dir),
        "analysis_report": str(report_path),
        "resume_source": str(resume_source_path),
        "resume_pdf": str(pdf_path) if pdf_path else None,
        "resume_docx": str(docx_path) if docx_path else None,
        "match_score": match_score,
        "confidence": analysis.get("confidence_score", 0),
        "selected_accomplishments": analysis.get("selected_accomplishments", [])
    }

    print(f"   ✅ Generated: {output_dir.name}/")
    return result


def generate_resume_outputs(resume_source_path: Path, output_dir: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """Generate PDF and DOCX from resume source using existing generator."""
    try:
        # Import the resume generator
        from generate_resumes import parse_accomplishments, parse_resume_source, create_pdf_resume, create_docx_resume

        accomplishments = parse_accomplishments(ACCOMPLISHMENTS_PATH)
        data = parse_resume_source(resume_source_path, accomplishments)

        # Generate outputs
        role_key = resume_source_path.stem.replace("resume_", "")

        pdf_path = output_dir / f"{role_key}_Resume.pdf"
        docx_path = output_dir / f"{role_key}_Resume.docx"

        create_pdf_resume(role_key, data, str(pdf_path))
        create_docx_resume(role_key, data, str(docx_path))

        return pdf_path, docx_path
    except Exception as e:
        print(f"   ⚠️ Resume generation error: {e}")
        return None, None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate tailored resume for a job")
    parser.add_argument("--url", help="Job URL")
    parser.add_argument("--company", help="Company name")
    parser.add_argument("--role", help="Job title")
    parser.add_argument("--score", type=int, default=80, help="Match score")
    parser.add_argument("--dry-run", action="store_true", help="Don't generate files")
    parser.add_argument("--test", action="store_true", help="Run with test data")

    args = parser.parse_args()

    if args.test:
        # Test with sample data
        result = generate_tailored_resume_for_job(
            job_url="https://example.com/job/123",
            job_description="""
            We are looking for a Senior DevOps Engineer to join our cloud infrastructure team.
            Requirements:
            - 3+ years of experience with Azure or AWS
            - Strong knowledge of Kubernetes and Docker
            - Experience with CI/CD pipelines (Azure DevOps, GitHub Actions)
            - Infrastructure as Code (Terraform, Bicep)
            - Python or PowerShell scripting
            """,
            company="Test Company",
            role="Senior DevOps Engineer",
            match_score=85,
            dry_run=args.dry_run
        )
        print(f"\nResult: {json.dumps(result, indent=2)}")
    elif args.url and args.company and args.role:
        # Fetch job description (would need job_scraper integration)
        print("Full integration requires job_scraper.fetch_job_description()")
    else:
        parser.print_help()

