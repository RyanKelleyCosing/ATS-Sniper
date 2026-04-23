param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,
    [Parameter(Mandatory = $true)]
    [string]$FunctionAppName,
    [ValidateSet("morning", "afternoon", "full")]
    [string]$RunType = "full",
    [switch]$DryRun,
    [switch]$SkipTailor,
    [switch]$V2
)

$ErrorActionPreference = "Stop"

$functionName = "RunAtsSniper"
$functionKey = az functionapp function keys list `
    --name $FunctionAppName `
    --resource-group $ResourceGroup `
    --function-name $functionName `
    --query default `
    -o tsv

if (-not $functionKey) {
    throw "Could not retrieve function key for $functionName"
}

$uri = "https://$FunctionAppName.azurewebsites.net/api/run-sniper?code=$functionKey"
$payload = @{
    run_type = $RunType
    dry_run = [bool]$DryRun
    skip_tailor = [bool]$SkipTailor
    v2 = [bool]$V2
} | ConvertTo-Json -Compress

Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/json" -Body $payload