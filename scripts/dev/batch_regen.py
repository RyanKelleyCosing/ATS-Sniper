"""Batch regenerate all 7 resumes with larger fonts + spacing."""
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent
while not (ROOT_DIR / "generate_tailored_resume.py").exists() and ROOT_DIR != ROOT_DIR.parent:
    ROOT_DIR = ROOT_DIR.parent

sys.path.insert(0, str(ROOT_DIR))

from generate_tailored_resume import generate_tailored_resume_for_job

JOBS = [
    ("https://careers-westernsouthern.icims.com/jobs/20070/cloud-engineer-ii/job",
     "Western & Southern Financial Group", "Cloud Engineer II", 92,
     "Cloud Engineer II. Azure IaC (Bicep, ARM), CI/CD Azure DevOps, AKS, Monitoring, Docker, Kubernetes, Python, PowerShell, cost optimization, security (Key Vault, RBAC, NSGs). 3+ years Azure."),
    ("https://worldpay.wd5.myworkdayjobs.com/en-US/Worldpay_External_Careers_Site/job/CINCINNATI-OHIO/Software-Engineer-I---Mainframe_JR0609191-1",
     "Worldpay", "Software Engineer I - Mainframe", 50,
     "Software Engineer I Mainframe. Bridge legacy and modern infra. .NET migration, automation, scripting, payment processing. Modern dev practices, CI/CD, database skills, Python."),
    ("https://job-boards.greenhouse.io/defcon/jobs/5091753007",
     "DEFCON AI", "Cloud Infrastructure Engineer", 88,
     "Cloud Infrastructure Engineer. Multi-cloud Azure/AWS, CI/CD pipelines, Kubernetes, Docker, Terraform, Bicep, security controls, zero-trust, monitoring, self-healing infra, Python/Go scripting. 3+ years."),
    ("https://www.linkedin.com/jobs/cbts-devops", "CBTS", "DevOps Engineer", 82,
     "DevOps Engineer Middleware. Linux, Bash, Ansible, Jenkins, ArgoCD, K8S Dashboard, AWS (EKS, ECR, S3, CodeBuild, CodePipeline, CloudWatch, EC2, VPC, Autoscaling). CI/CD, Release Management. 3+ years AWS, 2+ years DevOps."),
    ("https://www.linkedin.com/jobs/tampa-devops", "Tampa Contract", "DevOps Engineer", 78,
     "DevOps Engineer Hybrid Tampa. CI/CD Linux Bash Ansible Jenkins, GitOps ArgoCD, K8S Dashboard, AWS EKS ECR S3 CodeBuild CodePipeline CloudWatch, scripting, containers, monitoring, automation."),
    ("https://jobs.lever.co/restaurant365/sre-ii", "Restaurant365", "Site Reliability Engineer II", 88,
     "SRE II. Kubernetes cluster management, CI/CD optimization, Prometheus Grafana Datadog monitoring, Terraform IaC, incident response, postmortems, automation, Python Bash scripting. 3+ years SRE/DevOps."),
    ("https://job-boards.greenhouse.io/gitlab/infrastructure-security", "GitLab", "Infrastructure Security Engineer", 82,
     "Infrastructure Security Engineer. Cloud security Azure/AWS/GCP, container/K8s hardening, CI/CD pipeline security, IaC security scanning, zero-trust, SOC2 FedRAMP compliance, security automation, Python scripting. 3+ years."),
]

for url, company, role, score, jd in JOBS:
    try:
        result = generate_tailored_resume_for_job(url, jd, company, role, score)
        print(f"  -> {result['status']}\n")
    except Exception as e:
        print(f"  -> ERROR: {e}\n")
