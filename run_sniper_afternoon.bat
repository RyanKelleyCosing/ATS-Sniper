@echo off
REM ATS Sniper v3 - Full Pipeline (Workday + Custom + USAJobs + Email + Resumes)
REM Runs at 04:30 PM - Afternoon run
cd /d "%~dp0"
echo =============================================
echo ATS Sniper v3 - Afternoon Pipeline
echo =============================================
python run_full_pipeline.py
exit /b %errorlevel%

