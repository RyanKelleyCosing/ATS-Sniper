# ATS Sniper - Remove Scheduled Tasks
# Run this as Administrator to remove the automated tasks

Write-Host "Removing ATS Sniper scheduled tasks..." -ForegroundColor Yellow

$tasks = @(
    # Active tasks (created by setup_scheduler.ps1)
    "ATS_Sniper_OnLogin",
    "ATS_Sniper_Afternoon",
    # Legacy task names (from previous versions)
    "ATS_Sniper_Morning",
    "ATS_Sniper_Evening",
    "ATS_Sniper_Startup",
    "ATS_Sniper_Hourly"
)

foreach ($task in $tasks) {
    $exists = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($exists) {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false
        Write-Host "  [OK] Removed: $task" -ForegroundColor Green
    } else {
        Write-Host "  [--] Not found: $task" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "All ATS Sniper tasks removed." -ForegroundColor Cyan

