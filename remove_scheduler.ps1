# ATS Sniper - Remove Scheduled Tasks
# Run this as Administrator to remove the automated tasks

Write-Host "Removing ATS Sniper scheduled tasks..." -ForegroundColor Yellow

$tasks = @("ATS_Sniper_Morning", "ATS_Sniper_Evening", "ATS_Sniper_Startup", "ATS_Sniper_Hourly")

foreach ($task in $tasks) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "  [OK] Removed: $task" -ForegroundColor Green
}

Write-Host ""
Write-Host "All ATS Sniper tasks removed." -ForegroundColor Cyan

