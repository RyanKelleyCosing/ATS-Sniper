@echo off
REM ATS Sniper v3 - Full Pipeline (Workday + Custom + USAJobs + Email + Resumes)
REM Runs at 09:30 AM - Enterprise focus
cd /d "%~dp0"
echo =============================================
echo ATS Sniper v3 - Morning Pipeline
echo =============================================
python run_full_pipeline.py
exit /b %errorlevel%

