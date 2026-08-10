$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
if ([string]::IsNullOrEmpty($Root)) { $Root = (Get-Location).Path }
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project .venv is missing. Run .\install_env.ps1 first."
}

Write-Host "[1/2] Running local test suite..." -ForegroundColor Cyan
& $Python -B -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw "unit tests failed with exit code $LASTEXITCODE" }

Write-Host "[2/2] Running offline package demo..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "run_local_demo.ps1")
if ($LASTEXITCODE -ne 0) { throw "offline package demo failed with exit code $LASTEXITCODE" }

Write-Host "[OK] package validation passed" -ForegroundColor Green
