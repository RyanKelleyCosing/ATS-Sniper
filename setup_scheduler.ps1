# ATS Sniper v3 - Windows Task Scheduler Setup
# Run this script as Administrator to create scheduled tasks
# Schedule: On Login (any user) + 4:30 PM Afternoon run
# Pipeline: Workday API + Custom Scraper + USAJobs API → Email → Resume Generation

$scriptDir = $PSScriptRoot
$batPathMorning = Join-Path $scriptDir "run_sniper_morning.bat"
$batPathAfternoon = Join-Path $scriptDir "run_sniper_afternoon.bat"

Write-Host ""

# Task names
$taskNameOnLogin = "ATS_Sniper_OnLogin"
$taskNameAfternoon = "ATS_Sniper_Afternoon"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  ATS Sniper v3 - Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "  Schedule: On Login + 4:30 PM Daily" -ForegroundColor Cyan
Write-Host "  Pipeline: Scrapers → AI Resume → Email" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Must run as Administrator!" -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as Administrator" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Remove ALL existing ATS Sniper tasks
Write-Host "Removing existing tasks (if any)..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName "ATS_Sniper_Morning" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskNameAfternoon -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskNameOnLogin -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "ATS_Sniper_Evening" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "ATS_Sniper_Hourly" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "  [OK] Removed legacy tasks" -ForegroundColor Green

# Common settings
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# ============================================
# TASK 1: On Login (Any User)
# ============================================
Write-Host ""
Write-Host "Creating On Login task (Any User)..." -ForegroundColor Green
$actionLogin = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPathMorning`"" -WorkingDirectory $scriptDir
$triggerLogon = New-ScheduledTaskTrigger -AtLogon
$principalLogin = New-ScheduledTaskPrincipal -GroupId "BUILTIN\Users" -RunLevel Limited

Register-ScheduledTask -TaskName $taskNameOnLogin -Action $actionLogin -Trigger $triggerLogon -Settings $settings -Principal $principalLogin -Description "ATS Sniper v3 - Full pipeline on login (any user)" | Out-Null
Write-Host "  [OK] Created: $taskNameOnLogin" -ForegroundColor Green

# ============================================
# TASK 2: Afternoon (4:30 PM Daily)
# ============================================
Write-Host ""
Write-Host "Creating Afternoon task (4:30 PM daily)..." -ForegroundColor Green
$actionAfternoon = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPathAfternoon`"" -WorkingDirectory $scriptDir
$triggerAfternoon = New-ScheduledTaskTrigger -Daily -At "4:30PM"

Register-ScheduledTask -TaskName $taskNameAfternoon -Action $actionAfternoon -Trigger $triggerAfternoon -Settings $settings -Description "ATS Sniper v3 - Afternoon sweep (4:30 PM daily)" | Out-Null
Write-Host "  [OK] Created: $taskNameAfternoon (4:30 PM daily)" -ForegroundColor Green

# ============================================
# Summary
# ============================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Tasks Created:" -ForegroundColor White
Write-Host "  1. $taskNameOnLogin - Runs on ANY user login" -ForegroundColor White
Write-Host "  2. $taskNameAfternoon - Runs daily at 4:30 PM" -ForegroundColor White
Write-Host ""
Write-Host "Both run: run_full_pipeline.py (scrapers + AI resume + email)" -ForegroundColor Gray
Write-Host ""
Write-Host "API Usage: ~16 queries/day x 30 days = 480/month" -ForegroundColor Gray
Write-Host "Your plan: 1000/month (plenty of headroom)" -ForegroundColor Gray
Write-Host ""
Write-Host "To view: Task Scheduler" -ForegroundColor Gray
Write-Host "To test: python run_full_pipeline.py --dry-run" -ForegroundColor Gray
Write-Host ""

