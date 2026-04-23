@echo off
REM Weekly winget update - updates all installed apps silently
REM Designed to run as a scheduled task on user logon (once per week)

set LOCKFILE=%TEMP%\winget_weekly_update.lock
set LOG=%USERPROFILE%\winget_update.log

REM Check if we already ran this week
if exist "%LOCKFILE%" (
    for /f %%a in ('powershell -NoProfile -Command "(Get-Item '%LOCKFILE%').LastWriteTime.ToString('yyyy-MM-dd')"') do set LASTRUN=%%a
    for /f %%a in ('powershell -NoProfile -Command "(Get-Date).AddDays(-7).ToString('yyyy-MM-dd')"') do set WEEKAGO=%%a
    if "%LASTRUN%" GEQ "%WEEKAGO%" (
        exit /b 0
    )
)

echo [%date% %time%] Starting weekly winget update >> "%LOG%"
winget upgrade --all --silent --disable-interactivity --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
echo [%date% %time%] Update complete >> "%LOG%"

REM Update lock file timestamp
echo %date% > "%LOCKFILE%"
