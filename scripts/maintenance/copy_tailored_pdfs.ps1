param(
    [string]$OutputRoot = "",
    [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
if (-not (Test-Path (Join-Path $repoRoot "outputs"))) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repoRoot "outputs"
}

if (-not $TargetDir) {
    $TargetDir = Join-Path $repoRoot "outputs\pdfs"
}

function Test-IsRejectedRun {
    param(
        [string]$PdfPath
    )

    $directory = Split-Path -Parent $PdfPath
    $analysisReport = Get-ChildItem -Path $directory -Filter "*_Analysis.md" -File | Select-Object -First 1
    if (-not $analysisReport) {
        return $false
    }

    $reportText = Get-Content -Path $analysisReport.FullName -Raw
    return $reportText -match "\*\*Status:\*\*\s+REJECT"
}

if (-not (Test-Path -Path $OutputRoot)) {
    throw "Output root not found: $OutputRoot"
}

New-Item -Path $TargetDir -ItemType Directory -Force | Out-Null

$candidatePdfs = Get-ChildItem -Path $OutputRoot -Recurse -Filter "*_Resume.pdf" -File |
    Where-Object {
        $_.DirectoryName -ne $TargetDir -and -not (Test-IsRejectedRun -PdfPath $_.FullName)
    } |
    Sort-Object LastWriteTimeUtc -Descending

$latestByName = @{}
foreach ($pdf in $candidatePdfs) {
    if (-not $latestByName.ContainsKey($pdf.Name)) {
        $latestByName[$pdf.Name] = $pdf
    }
}

$desiredNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $latestByName.GetEnumerator()) {
    $sourcePdf = $entry.Value
    $destinationPdf = Join-Path $TargetDir $sourcePdf.Name
    Copy-Item -Path $sourcePdf.FullName -Destination $destinationPdf -Force
    $desiredNames.Add($sourcePdf.Name) | Out-Null
    Write-Host "Copied $($sourcePdf.Name) from $($sourcePdf.Directory.Name)"
}

Get-ChildItem -Path $TargetDir -Filter "*.pdf" -File |
    Where-Object { -not $desiredNames.Contains($_.Name) } |
    ForEach-Object {
        Remove-Item -Path $_.FullName -Force
        Write-Host "Removed stale PDF $($_.Name)"
    }

Write-Host "Ready PDFs in ${TargetDir}: $($desiredNames.Count)"