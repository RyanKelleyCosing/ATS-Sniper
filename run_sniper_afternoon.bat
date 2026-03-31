@echo off
REM ATS Sniper v3 - Full Pipeline (Workday + Custom + USAJobs + Email + Resumes)
REM Runs at 04:30 PM - Afternoon run
set PYTHONUTF8=1
cd /d "%~dp0"
echo =============================================
echo ATS Sniper v3 - Afternoon Pipeline
echo =============================================
python run_full_pipeline.py
exit /b %errorlevel%

