param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,
    [Parameter(Mandatory = $true)]
    [string]$Location,
    [Parameter(Mandatory = $true)]
    [string]$FunctionAppName,
    [Parameter(Mandatory = $true)]
    [string]$StorageAccountName
)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
if (-not (Test-Path (Join-Path $repoRoot "config.json"))) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$configPath = Join-Path $repoRoot "config.json"

if (-not (Test-Path $configPath)) {
    throw "config.json not found at $configPath"
}

az account show | Out-Null

$configJson = Get-Content $configPath -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 30 -Compress

$stagingRoot = Join-Path $env:TEMP ("ats-sniper-func-" + [guid]::NewGuid().ToString())
$packageRoot = Join-Path $stagingRoot "package"
$zipPath = Join-Path $stagingRoot "ats-sniper-function.zip"
$appSettingsPath = Join-Path $stagingRoot "appsettings.json"

New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null

$excludeNames = @(
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "outputs",
    "reports",
    "cover_letters",
    "config.json",
    ".env",
    "job_state.json",
    "job_state_backup.json",
    "local.settings.json"
)

Get-ChildItem -LiteralPath $repoRoot -Force | Where-Object {
    $excludeNames -notcontains $_.Name
} | Copy-Item -Destination $packageRoot -Recurse -Force

Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal

az group create --name $ResourceGroup --location $Location | Out-Null

$storageExists = az storage account show --name $StorageAccountName --resource-group $ResourceGroup --query name -o tsv 2>$null
if (-not $storageExists) {
    az storage account create `
        --name $StorageAccountName `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        | Out-Null
}

$functionExists = az functionapp show --name $FunctionAppName --resource-group $ResourceGroup --query name -o tsv 2>$null
if (-not $functionExists) {
    az functionapp create `
        --name $FunctionAppName `
        --resource-group $ResourceGroup `
        --storage-account $StorageAccountName `
        --consumption-plan-location $Location `
        --runtime python `
        --runtime-version 3.11 `
        --functions-version 4 `
        --os-type Linux `
        | Out-Null
}

$appSettings = [ordered]@{
     ATS_SNIPER_CONFIG_JSON = $configJson
    ATS_SNIPER_RUNTIME_DIR = "/home/site/ats-sniper-data"
    ATS_SNIPER_STATE_PATH = "/home/site/ats-sniper-data/job_state.json"
    ATS_SNIPER_STATE_BACKUP_DIR = "/home/site/ats-sniper-data/state-backups"
    AzureWebJobsFeatureFlags = "EnableWorkerIndexing"
     ATS_SNIPER_SKIP_TASK_SCHEDULER_CHECKS = "true"
    ATS_SNIPER_SKIP_TAILOR = "true"
     ATS_SNIPER_MORNING_CRON = "0 30 13 * * *"
     ATS_SNIPER_AFTERNOON_CRON = "0 30 20 * * *"
     ATS_SNIPER_MONITOR_MORNING_CRON = "0 15 15 * * *"
     ATS_SNIPER_MONITOR_AFTERNOON_CRON = "0 45 21 * * *"
     WEBSITE_TIME_ZONE = "UTC"
     PYTHONUTF8 = "1"
     ATS_SNIPER_DISABLE_CUSTOM_SCRAPER = "true"
     SCM_DO_BUILD_DURING_DEPLOYMENT = "true"
     ENABLE_ORYX_BUILD = "true"
}

$appSettings | ConvertTo-Json -Depth 5 -Compress | Set-Content -Path $appSettingsPath -Encoding UTF8

az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroup `
    --settings ("@" + $appSettingsPath) `
    | Out-Null

az functionapp deployment source config-zip `
    --name $FunctionAppName `
    --resource-group $ResourceGroup `
    --build-remote true `
    --timeout 1800 `
    --src $zipPath `
    | Out-Null

Write-Host "Deployment complete." -ForegroundColor Green
Write-Host "Health: https://$FunctionAppName.azurewebsites.net/api/health" -ForegroundColor Cyan
Write-Host "Trigger: https://$FunctionAppName.azurewebsites.net/api/run-sniper" -ForegroundColor Cyan

Remove-Item $stagingRoot -Recurse -Force