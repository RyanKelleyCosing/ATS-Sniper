"""Quick test: regenerate W&S resume with larger fonts."""
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent
while not (ROOT_DIR / "generate_tailored_resume.py").exists() and ROOT_DIR != ROOT_DIR.parent:
    ROOT_DIR = ROOT_DIR.parent

sys.path.insert(0, str(ROOT_DIR))

from generate_tailored_resume import generate_tailored_resume_for_job

result = generate_tailored_resume_for_job(
    job_url="https://careers-westernsouthern.icims.com/jobs/20070/cloud-engineer-ii/job",
    job_description="""Cloud Engineer II - Western & Southern Financial Group (Cincinnati, OH)
Design and implement Azure cloud infrastructure using IaC (Bicep, ARM Templates).
Build and maintain CI/CD pipelines in Azure DevOps. Manage AKS clusters.
Monitoring: Azure Monitor, Application Insights, Log Analytics. Cost optimization.
Containerization (Docker, Kubernetes). Security: Azure Key Vault, RBAC, NSGs.
Requirements: 3+ years Azure, CI/CD, Azure DevOps, IaC, Kubernetes/AKS,
Python, PowerShell, Bash. Azure certs preferred.""",
    company="Western & Southern Financial Group",
    role="Cloud Engineer II",
    match_score=92,
)
print(f"Status: {result['status']}")
