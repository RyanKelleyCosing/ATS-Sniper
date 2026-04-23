@echo off
REM ATS Sniper v3 - Full Pipeline (Workday + Custom + USAJobs + Email + Resumes)
REM Runs at 04:30 PM - Afternoon run
setlocal
set PYTHONUTF8=1
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
	echo ERROR: Virtual environment Python not found at "%PYTHON_EXE%"
	endlocal & exit /b 1
)

echo =============================================
echo ATS Sniper v3 - Afternoon Pipeline
echo =============================================
"%PYTHON_EXE%" "%SCRIPT_DIR%run_full_pipeline.py" --run-type afternoon
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

