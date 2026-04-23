param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "afternoon", "lightweight", "fresh_watch", "monitor")]
    [string]$Task
)

$ErrorActionPreference = "Stop"

function Get-AtsPythonExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $preferredVenvNames = @()
    if ($env:ATS_SNIPER_VENV_NAME) {
        $preferredVenvNames += $env:ATS_SNIPER_VENV_NAME
    }
    $preferredVenvNames += @(".venv-jobspy", ".venv313", ".venv")

    foreach ($venvName in ($preferredVenvNames | Where-Object { $_ } | Select-Object -Unique)) {
        $candidate = Join-Path $RootPath "$venvName\Scripts\python.exe"
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "Virtual environment Python not found. Checked: $($preferredVenvNames -join ', ')"
}

function Write-TaskLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LogPath,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    Add-Content -Path $LogPath -Value $Message -Encoding utf8
}

$scriptDir = $PSScriptRoot
$pythonExe = Get-AtsPythonExecutable -RootPath $scriptDir
$logDir = Join-Path $scriptDir "outputs\scheduled"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$taskConfig = switch ($Task) {
    "morning" {
        @{
            Script = Join-Path $scriptDir "run_full_pipeline.py"
            Arguments = @("--run-type", "morning")
            LogPath = Join-Path $logDir "morning.log"
        }
    }
    "afternoon" {
        @{
            Script = Join-Path $scriptDir "run_full_pipeline.py"
            Arguments = @("--run-type", "afternoon")
            LogPath = Join-Path $logDir "afternoon.log"
        }
    }
    "lightweight" {
        @{
            Script = Join-Path $scriptDir "run_full_pipeline.py"
            Arguments = @("--run-type", "lightweight")
            LogPath = Join-Path $logDir "lightweight.log"
        }
    }
    "fresh_watch" {
        @{
            Script = Join-Path $scriptDir "run_fresh_watch.py"
            Arguments = @()
            LogPath = Join-Path $logDir "fresh_watch.log"
        }
    }
    "monitor" {
        @{
            Script = Join-Path $scriptDir "monitor_pipeline_runs.py"
            Arguments = @()
            LogPath = Join-Path $logDir "monitor.log"
        }
    }
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-TaskLog -LogPath $taskConfig.LogPath -Message "[$timestamp] Starting ATS Sniper task '$Task'"
Write-TaskLog -LogPath $taskConfig.LogPath -Message "[$timestamp] Using Python interpreter: $pythonExe"

$stdoutCapture = [System.IO.Path]::GetTempFileName()
$stderrCapture = [System.IO.Path]::GetTempFileName()
$exitCode = 1

Push-Location $scriptDir
try {
    $env:PYTHONUTF8 = "1"
    & $pythonExe $taskConfig.Script @($taskConfig.Arguments) 1> $stdoutCapture 2> $stderrCapture
    $exitCode = $LASTEXITCODE
}
catch {
    $errorTimestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-TaskLog -LogPath $taskConfig.LogPath -Message "[$errorTimestamp] Task launcher error: $($_.Exception.Message)"
}
finally {
    Pop-Location

    foreach ($capturePath in @($stdoutCapture, $stderrCapture)) {
        if (-not (Test-Path $capturePath)) {
            continue
        }

        $capturedText = [System.IO.File]::ReadAllText($capturePath, [System.Text.Encoding]::UTF8)
        if ($capturedText) {
            Add-Content -Path $taskConfig.LogPath -Value $capturedText -Encoding utf8
        }

        Remove-Item $capturePath -Force -ErrorAction SilentlyContinue
    }
}

$endTimestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-TaskLog -LogPath $taskConfig.LogPath -Message "[$endTimestamp] Completed ATS Sniper task '$Task' with exit code $exitCode"
exit $exitCode