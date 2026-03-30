@echo off
REM ATS Sniper v3 - Full Pipeline (Workday + Custom + USAJobs + Email + Resumes)
cd /d "%~dp0"
echo =============================================
echo ATS Sniper v3 - Full Pipeline
echo =============================================
echo.
echo Step 1: Running scrapers and sending email...
python run_full_pipeline.py
echo.
echo Step 2: Generating resumes...
cd ..
python generate_resumes.py
echo.
echo =============================================
echo Pipeline complete!
echo =============================================
exit /b %errorlevel%

