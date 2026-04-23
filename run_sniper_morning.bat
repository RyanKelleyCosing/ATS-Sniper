@echo off
REM ATS Sniper v3 - Full Pipeline (Workday + Custom + USAJobs + Email + Resumes)
REM Runs at 09:30 AM - Enterprise focus
setlocal
set PYTHONUTF8=1
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PYTHON_EXE="
for %%D in (".venv-jobspy" ".venv313" ".venv") do (
	if not defined PYTHON_EXE if exist "%SCRIPT_DIR%%%~D\Scripts\python.exe" (
		set "PYTHON_EXE=%SCRIPT_DIR%%%~D\Scripts\python.exe"
	)
)

if not defined PYTHON_EXE (
	echo ERROR: Virtual environment Python not found under .venv-jobspy, .venv313, or .venv
	endlocal & exit /b 1
)

echo =============================================
echo ATS Sniper v3 - Morning Pipeline
echo =============================================
echo Using Python: "%PYTHON_EXE%"
"%PYTHON_EXE%" "%SCRIPT_DIR%run_full_pipeline.py" --run-type morning
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

