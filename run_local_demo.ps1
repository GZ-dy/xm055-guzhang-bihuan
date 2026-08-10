$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
if ([string]::IsNullOrEmpty($Root)) { $Root = (Get-Location).Path }
Set-Location $Root

# This process is always offline-only, regardless of the caller's environment.
Remove-Item Env:FAULT_AUTO_SEND_ON_VALIDATED -ErrorAction SilentlyContinue
Remove-Item Env:FAULT_CONFIRM_LIVE_WRITE -ErrorAction SilentlyContinue
Remove-Item Env:FAULT_PROD_CASE_TOKEN -ErrorAction SilentlyContinue

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project .venv is missing. Run .\install_env.ps1 first."
}

$CaseId = "portable_package_demo"
$Text = "【BOMC-cas服务异常】2026年7月8日10:13用户反馈BOMC系统无法访问。发生时间：2026年7月8日10:13。恢复时间：2026年7月8日10:25。故障原因：CAS服务异常。解决方式：重启CAS和WEB服务。影响范围：相关用户无法访问BOMC系统。负责人：高喆。处理组：多域维护组。处理过程：10:13接到用户报障|10:20重启CAS和WEB服务|10:25验证业务恢复。后续措施：补充CAS服务监控|固化恢复验证清单。"

Write-Host "[1/2] Building validated incident context..." -ForegroundColor Cyan
$FirstRaw = (& $Python -B run_closed_loop.py --text $Text --case-id $CaseId --incident-context-only | Out-String)
if ($LASTEXITCODE -ne 0) { throw "incident-context step failed with exit code $LASTEXITCODE" }
$First = $FirstRaw | ConvertFrom-Json
if ($First.status -ne "validated") { throw "incident context status is $($First.status), expected validated" }

$ContextPath = Join-Path $Root "work\cases\$CaseId\incident_context.json"
if (-not (Test-Path -LiteralPath $ContextPath -PathType Leaf)) { throw "incident context was not created: $ContextPath" }

Write-Host "[2/2] Generating offline artifacts..." -ForegroundColor Cyan
$SecondRaw = (& $Python -B run_closed_loop.py --incident-context $ContextPath --all-dry-run | Out-String)
if ($LASTEXITCODE -ne 0) { throw "offline artifact step failed with exit code $LASTEXITCODE" }
$Second = $SecondRaw | ConvertFrom-Json
if ($Second.status -ne "validated") { throw "offline demo status is $($Second.status), expected validated" }

$ArtifactDirs = @(
    (Join-Path $Root "outputs\reports\$CaseId"),
    (Join-Path $Root "outputs\ledger\$CaseId"),
    (Join-Path $Root "outputs\mail\$CaseId"),
    (Join-Path $Root "outputs\manuals\$CaseId"),
    (Join-Path $Root "outputs\validation\$CaseId")
)
foreach ($Directory in $ArtifactDirs) {
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { throw "missing artifact directory: $Directory" }
    if (-not (Get-ChildItem -LiteralPath $Directory -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        throw "artifact directory is empty: $Directory"
    }
}

Write-Host "[OK] offline demo validated with five artifact groups" -ForegroundColor Green
