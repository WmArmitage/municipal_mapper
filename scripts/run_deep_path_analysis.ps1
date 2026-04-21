param(
    [string]$BatchId = "batch_4",
    [string]$Platform = "CivicPlus"
)

Write-Host "Running deep path pattern discovery..." -ForegroundColor Cyan
Write-Host "Batch: $BatchId | Platform: $Platform" -ForegroundColor Yellow

$scriptPath = "scripts/discover_deep_path_patterns.py"

if (!(Test-Path $scriptPath)) {
    Write-Host "ERROR: Script not found at $scriptPath" -ForegroundColor Red
    exit 1
}

python $scriptPath --batch-id $BatchId --platform $Platform

if ($LASTEXITCODE -ne 0) {
    Write-Host "Script failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Deep path discovery complete." -ForegroundColor Green