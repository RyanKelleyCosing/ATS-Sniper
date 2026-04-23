# Register weekly winget update as a scheduled task
# Runs at user logon, but only once per week (controlled by the .bat lock file)

$taskName = "Weekly Winget Update"
$batPath = Join-Path $PSScriptRoot "weekly_winget_update.bat"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $batPath
$trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Updates all apps via winget once per week at logon"

Write-Host "Scheduled task '$taskName' registered successfully."
Write-Host "It will run at each logon but only update once per week."
