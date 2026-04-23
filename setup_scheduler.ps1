# ATS Sniper v3 - Windows Task Scheduler Setup
# Run this script as Administrator to create scheduled tasks
# Schedule: On Login + fresh-watch bridge + lightweight freshness passes + 4:30 PM afternoon run
# Pipeline: Workday API + Custom Scraper + USAJobs API → Email → Resume Generation

$ErrorActionPreference = "Stop"

function Resolve-ScriptDirectory {
    $candidates = @()

    if ($PSScriptRoot) {
        $candidates += $PSScriptRoot
    }

    if ($PSCommandPath) {
        $candidates += (Split-Path -Parent $PSCommandPath)
    }

    if ($MyInvocation.MyCommand.Path) {
        $candidates += (Split-Path -Parent $MyInvocation.MyCommand.Path)
    }

    try {
        $currentLocation = (Get-Location).Path
        if ($currentLocation) {
            $candidates += $currentLocation
        }
    }
    catch {
    }

    $candidates = $candidates | Where-Object { $_ } | Select-Object -Unique

    foreach ($candidate in $candidates) {
        $entrypoint = Join-Path -Path $candidate -ChildPath "run_scheduled_task.ps1"
        if (Test-Path $entrypoint) {
            return $candidate
        }
    }

    throw "Could not resolve the ATS Sniper script directory. Run setup_scheduler.ps1 from the ats_sniper folder or start PowerShell in that folder before pasting the script contents."
}

$scriptDir = Resolve-ScriptDirectory
$scheduledTaskScript = Join-Path -Path $scriptDir -ChildPath "run_scheduled_task.ps1"
$hiddenLauncherScript = Join-Path -Path $scriptDir -ChildPath "run_scheduled_task_hidden.vbs"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellCommand = Get-Command pwsh.exe -ErrorAction SilentlyContinue
$shellExe = if ($powerShellCommand) { $powerShellCommand.Source } else { "powershell.exe" }
$wscriptCommand = Get-Command wscript.exe -ErrorAction SilentlyContinue
$launcherExe = if ($wscriptCommand) { $wscriptCommand.Source } else { (Join-Path $env:SystemRoot "System32\wscript.exe") }
$script:taskLogonMode = $null

Write-Host ""

# Task names
$taskNameOnLogin = "ATS_Sniper_OnLogin_CurrentUser"
$taskNameFreshWatch = "ATS_Sniper_FreshWatch_CurrentUser"
$taskNameLightweight = "ATS_Sniper_Lightweight_CurrentUser"
$taskNameAfternoon = "ATS_Sniper_Afternoon_CurrentUser"
$taskNameMonitor = "ATS_Sniper_RunMonitor_CurrentUser"
$legacyTaskNames = @(
    "ATS_Sniper_FreshWatch",
    "ATS_Sniper_OnLogin",
    "ATS_Sniper_Afternoon",
    "ATS_Sniper_Lightweight",
    "ATS_Sniper_Morning",
    "ATS_Sniper_Evening",
    "ATS_Sniper_Hourly",
    "ATS_Sniper_RunMonitor"
)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  ATS Sniper v3 - Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "  Schedule: On Login + 10:35 AM-4:15 PM fresh watch + 11:30 AM / 2:30 PM / 6:30 PM lightweight + 4:30 PM Daily" -ForegroundColor Cyan
Write-Host "  Pipeline: Scrapers → AI Resume → Email" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $scheduledTaskScript)) {
    throw "Scheduled task entrypoint not found: $scheduledTaskScript"
}

if (-not (Test-Path $hiddenLauncherScript)) {
    throw "Scheduled task hidden launcher not found: $hiddenLauncherScript"
}

function Register-AtsTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,

        [Parameter(Mandatory = $true)]
        [object]$Action,

        [Parameter(Mandatory = $true)]
        [object[]]$Trigger,

        [Parameter(Mandatory = $true)]
        [object]$Settings,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $logonTypes = @("S4U", "Interactive")
    $lastError = $null

    foreach ($logonType in $logonTypes) {
        try {
            $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType $logonType -RunLevel Limited
            Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $principal -Description $Description -Force -ErrorAction Stop | Out-Null
            if (-not $script:taskLogonMode) {
                $script:taskLogonMode = $logonType
            }
            return $logonType
        }
        catch {
            $lastError = $_
            if ($logonType -eq "S4U") {
                Write-Host "  [WARN] S4U registration failed; falling back to hidden interactive mode" -ForegroundColor Yellow
            }
        }
    }

    throw $lastError
}

function New-DailyRecurringTriggers {
    param(
        [Parameter(Mandatory = $true)]
        [datetime]$StartTime,

        [Parameter(Mandatory = $true)]
        [datetime]$EndTime,

        [Parameter(Mandatory = $true)]
        [int]$IntervalMinutes
    )

    $triggers = @()
    $cursor = $StartTime
    while ($cursor -le $EndTime) {
        $triggers += New-ScheduledTaskTrigger -Daily -At $cursor
        $cursor = $cursor.AddMinutes($IntervalMinutes)
    }

    return $triggers
}

# Remove ALL existing ATS Sniper tasks
Write-Host "Removing existing tasks (if any)..." -ForegroundColor Yellow
foreach ($taskName in @($taskNameOnLogin, $taskNameFreshWatch, $taskNameLightweight, $taskNameAfternoon, $taskNameMonitor) + $legacyTaskNames) {
    Disable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Host "  [OK] Removed legacy tasks" -ForegroundColor Green

# Common settings
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

function New-AtsHiddenTaskAction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskArgument
    )

    $launcherArgs = "`"$hiddenLauncherScript`" `"$shellExe`" `"$scheduledTaskScript`" `"$scriptDir`" `"$TaskArgument`""
    return New-ScheduledTaskAction -Execute $launcherExe -Argument $launcherArgs -WorkingDirectory $scriptDir
}

# ============================================
# TASK 1: On Login (Any User)
# ============================================
Write-Host ""
Write-Host "Creating On Login task for $currentUser..." -ForegroundColor Green
$actionLogin = New-AtsHiddenTaskAction -TaskArgument "morning"
$triggerLogon = New-ScheduledTaskTrigger -AtLogon -User $currentUser

$loginMode = Register-AtsTask -TaskName $taskNameOnLogin -Action $actionLogin -Trigger @($triggerLogon) -Settings $settings -Description "ATS Sniper v3 - Morning pipeline on login"
Write-Host "  [OK] Created: $taskNameOnLogin ($loginMode)" -ForegroundColor Green

# ============================================
# TASK 2: Fresh Watch Bridge
# ============================================
Write-Host ""
Write-Host "Creating Fresh Watch bridge task (10:35 AM-4:15 PM every 10 minutes)..." -ForegroundColor Green
$actionFreshWatch = New-AtsHiddenTaskAction -TaskArgument "fresh_watch"
$freshWatchStart = (Get-Date).Date.AddHours(10).AddMinutes(35)
$freshWatchEnd = (Get-Date).Date.AddHours(16).AddMinutes(15)
$freshWatchTriggers = New-DailyRecurringTriggers -StartTime $freshWatchStart -EndTime $freshWatchEnd -IntervalMinutes 10
$freshWatchSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew

$freshWatchMode = Register-AtsTask -TaskName $taskNameFreshWatch -Action $actionFreshWatch -Trigger $freshWatchTriggers -Settings $freshWatchSettings -Description "ATS Sniper v3 - Web-only fresh-watch bridge between full runs"
Write-Host "  [OK] Created: $taskNameFreshWatch (10:35 AM-4:15 PM every 10 minutes, $freshWatchMode)" -ForegroundColor Green

# ============================================
# TASK 3: Lightweight Freshness Passes
# ============================================
Write-Host ""
Write-Host "Creating Lightweight freshness task (11:30 AM, 2:30 PM, 6:30 PM)..." -ForegroundColor Green
$actionLightweight = New-AtsHiddenTaskAction -TaskArgument "lightweight"
$triggerLightweightLateMorning = New-ScheduledTaskTrigger -Daily -At "11:30AM"
$triggerLightweightMidday = New-ScheduledTaskTrigger -Daily -At "2:30PM"
$triggerLightweightEvening = New-ScheduledTaskTrigger -Daily -At "6:30PM"
$lightweightSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -MultipleInstances IgnoreNew

$lightweightMode = Register-AtsTask -TaskName $taskNameLightweight -Action $actionLightweight -Trigger @($triggerLightweightLateMorning, $triggerLightweightMidday, $triggerLightweightEvening) -Settings $lightweightSettings -Description "ATS Sniper v3 - Lightweight freshness-first discovery passes"
Write-Host "  [OK] Created: $taskNameLightweight (11:30 AM, 2:30 PM, 6:30 PM, $lightweightMode)" -ForegroundColor Green

# ============================================
# TASK 4: Afternoon (4:30 PM Daily)
# ============================================
Write-Host ""
Write-Host "Creating Afternoon task (4:30 PM daily)..." -ForegroundColor Green
$actionAfternoon = New-AtsHiddenTaskAction -TaskArgument "afternoon"
$triggerAfternoon = New-ScheduledTaskTrigger -Daily -At "4:30PM"

$afternoonMode = Register-AtsTask -TaskName $taskNameAfternoon -Action $actionAfternoon -Trigger @($triggerAfternoon) -Settings $settings -Description "ATS Sniper v3 - Afternoon sweep (4:30 PM daily)"
Write-Host "  [OK] Created: $taskNameAfternoon (4:30 PM daily, $afternoonMode)" -ForegroundColor Green

# ============================================
# TASK 5: Run Monitor
# ============================================
Write-Host ""
Write-Host "Creating Run Monitor task (11:15 AM + 5:45 PM)..." -ForegroundColor Green
$actionMonitor = New-AtsHiddenTaskAction -TaskArgument "monitor"
$triggerMonitorMorning = New-ScheduledTaskTrigger -Daily -At "11:15AM"
$triggerMonitorAfternoon = New-ScheduledTaskTrigger -Daily -At "5:45PM"
$monitorSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew

$monitorMode = Register-AtsTask -TaskName $taskNameMonitor -Action $actionMonitor -Trigger @($triggerMonitorMorning, $triggerMonitorAfternoon) -Settings $monitorSettings -Description "ATS Sniper v3 - Monitor missed or failed scheduled runs"
Write-Host "  [OK] Created: $taskNameMonitor ($monitorMode)" -ForegroundColor Green

# ============================================
# Summary
# ============================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Tasks Created:" -ForegroundColor White
Write-Host "  1. $taskNameOnLogin - Runs on your login" -ForegroundColor White
Write-Host "  2. $taskNameFreshWatch - Runs every 10 minutes from 10:35 AM through 4:15 PM" -ForegroundColor White
Write-Host "  3. $taskNameLightweight - Runs daily at 11:30 AM, 2:30 PM, and 6:30 PM" -ForegroundColor White
Write-Host "  4. $taskNameAfternoon - Runs daily at 4:30 PM" -ForegroundColor White
Write-Host "  5. $taskNameMonitor - Checks for missed/failed runs at 11:15 AM and 5:45 PM" -ForegroundColor White
Write-Host ""
if ($script:taskLogonMode -eq "S4U") {
    Write-Host "Scheduled tasks run headlessly through Windows Script Host under: $currentUser" -ForegroundColor Gray
}
else {
    Write-Host "Scheduled tasks run silently through Windows Script Host under: $currentUser" -ForegroundColor Gray
    Write-Host "Full non-login headless mode was blocked by local Task Scheduler permissions, so tasks stay interactive but should no longer flash visible console windows." -ForegroundColor Gray
}
Write-Host "Task host: $launcherExe -> $shellExe" -ForegroundColor Gray
Write-Host "Scheduled output is appended under outputs\scheduled\*.log" -ForegroundColor Gray
Write-Host ""
Write-Host "Fresh watch and lightweight passes increase API usage; review SerpApi quota after the first week." -ForegroundColor Gray
Write-Host ""
Write-Host "To view: Task Scheduler" -ForegroundColor Gray
Write-Host "To test: .\run_sniper_morning.bat" -ForegroundColor Gray
Write-Host ""

