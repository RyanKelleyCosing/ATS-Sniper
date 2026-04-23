#!/usr/bin/env python3
"""
Test script for ATS Sniper v3 - AI Resume Tailoring Pipeline

Tests the core flow:
1. Analyze job description
2. Select top accomplishments
3. Generate tailored resume
"""

import sys
from pathlib import Path

# Add repo root to path.
ROOT_DIR = Path(__file__).resolve().parent
while not (ROOT_DIR / "generate_tailored_resume.py").exists() and ROOT_DIR != ROOT_DIR.parent:
    ROOT_DIR = ROOT_DIR.parent

sys.path.insert(0, str(ROOT_DIR))

from generate_tailored_resume import generate_tailored_resume_for_job

# Sample job descriptions for testing
SAMPLE_JOBS = [
    {
        "company": "Kroger",
        "role": "Lead DevOps Infrastructure Engineer",
        "url": "https://jobs.kroger.com/kroger/job/167545",
        "match_score": 88,
        "description": """
Lead DevOps Infrastructure Engineer
Location: Blue Ash, OH (Cincinnati area)
Company: Kroger Technology

About the Role:
We are seeking an experienced Lead DevOps Infrastructure Engineer to join our Cloud Platform team.
You will lead the design, implementation, and maintenance of our cloud infrastructure and CI/CD pipelines.

Responsibilities:
- Lead the design and implementation of Azure cloud infrastructure
- Build and maintain CI/CD pipelines using Azure DevOps and GitHub Actions
- Implement Infrastructure as Code using Terraform and Bicep
- Manage Kubernetes clusters (AKS) and containerized workloads
- Mentor junior engineers and establish DevOps best practices
- Automate infrastructure provisioning and configuration
- Collaborate with development teams to improve deployment processes
- Monitor and optimize cloud costs and performance

Requirements:
- 5+ years of experience in DevOps/Infrastructure roles
- Strong experience with Azure cloud services (AKS, App Services, Functions, etc.)
- Expertise in Kubernetes and container orchestration
- Proficiency with Infrastructure as Code (Terraform, Bicep, ARM)
- Experience with CI/CD tools (Azure DevOps, GitHub Actions, Jenkins)
- Strong scripting skills (Python, PowerShell, Bash)
- Experience with monitoring and observability tools (Prometheus, Grafana, Azure Monitor)
- Excellent communication and leadership skills

Nice to Have:
- Azure certifications (AZ-104, AZ-400)
- Experience in retail/enterprise environments
- Knowledge of security best practices and compliance
"""
    },
    {
        "company": "Fidelity Investments",
        "role": "Principal Site Reliability Engineer",
        "url": "https://fmr.wd1.myworkdayjobs.com/job/Principal-Site-Reliability-Engineer_2126264",
        "match_score": 92,
        "description": """
Principal Site Reliability Engineer
Location: Durham, NC / Remote
Company: Fidelity Investments

The Role:
Join Fidelity's Enterprise Infrastructure team as a Principal SRE, driving reliability and 
performance for our mission-critical financial services platforms.

What You'll Do:
- Design and implement highly available, scalable infrastructure
- Lead SRE practices including SLOs, error budgets, and incident management
- Build automation for infrastructure provisioning and deployment
- Develop monitoring, alerting, and observability solutions
- Drive adoption of cloud-native technologies on Azure
- Mentor team members and establish engineering standards
- Participate in on-call rotation and incident response

Required Skills:
- 7+ years in SRE, DevOps, or Infrastructure Engineering
- Deep expertise in Kubernetes, Docker, and container orchestration
- Strong experience with Azure cloud platform
- Proficiency in Python, Go, or similar languages
- Experience with Terraform, Ansible, or similar IaC tools
- Knowledge of CI/CD practices and tools
- Understanding of distributed systems and microservices
- Strong problem-solving and communication skills

Preferred:
- Financial services industry experience
- Experience with service mesh (Istio, Linkerd)
- Familiarity with chaos engineering principles
"""
    }
]


def run_test(dry_run: bool = True):
    """Run test with sample job data."""
    print("=" * 70)
    print("🧪 ATS SNIPER V3 - PIPELINE TEST")
    print("=" * 70)
    
    for i, job in enumerate(SAMPLE_JOBS, 1):
        print(f"\n{'='*70}")
        print(f"📋 Test {i}/{len(SAMPLE_JOBS)}: {job['role']} @ {job['company']}")
        print("=" * 70)
        
        result = generate_tailored_resume_for_job(
            job_url=job["url"],
            job_description=job["description"],
            company=job["company"],
            role=job["role"],
            match_score=job["match_score"],
            dry_run=dry_run
        )
        
        if result:
            print(f"\n✅ Result: {result.get('status')}")
            if result.get("output_dir"):
                print(f"   📁 Output: {result.get('output_dir')}")
            if result.get("selected_accomplishments"):
                print(f"   🎯 Selected {len(result['selected_accomplishments'])} accomplishments")
        else:
            print("❌ No result returned")
    
    print("\n" + "=" * 70)
    print("🏁 TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-d" in sys.argv
    run_test(dry_run=dry_run)

