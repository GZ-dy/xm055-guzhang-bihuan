# install_env.ps1
# 2026-07-10: 建 .venv 隔离环境 (避免污染系统 Python)
# 用法:
#   cd D:\fault-closed-loop
#   .\install_env.ps1

$ErrorActionPreference = "Stop"

# 自动 cd
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir
Write-Host "[auto-cd] cwd = $ScriptDir" -ForegroundColor Cyan
Write-Host ""

# 检查用于创建项目 .venv 的系统 Python 3.10+
$BootstrapPython = Get-Command python -ErrorAction SilentlyContinue
$BootstrapPrefix = @()
if (-not $BootstrapPython) {
    $BootstrapPython = Get-Command py -ErrorAction SilentlyContinue
    $BootstrapPrefix = @("-3")
}
if (-not $BootstrapPython) { throw "Python 3 was not found (tried python, then py -3)" }

$pyVer = & $BootstrapPython.Source @BootstrapPrefix -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>&1
Write-Host "  Python: $pyVer" -ForegroundColor Gray
$major = 0
$minor = 0
try {
    $parts = $pyVer.Split('.')
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
} catch {}
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    Write-Host "  [FATAL] need Python 3.10+, have $pyVer" -ForegroundColor Red
    exit 1
}

# 建 .venv
if (Test-Path ".\.venv") {
    Write-Host "  [OK] .venv already exists" -ForegroundColor Green
} else {
    Write-Host "  [..] creating .venv..." -ForegroundColor Yellow
    & $BootstrapPython.Source @BootstrapPrefix -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FATAL] venv create failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] .venv created" -ForegroundColor Green
}
Write-Host ""

# 直接使用项目环境，不依赖当前 PowerShell 的激活状态。
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "Project Python was not created: $VenvPython"
}
Write-Host "  [..] installing deps into project .venv..." -ForegroundColor Yellow

function Install-Requirements {
    param([Parameter(Mandatory=$true)][string]$RequirementsPath)

    $wheelhouse = Join-Path $ScriptDir "wheelhouse"
    if (Test-Path -LiteralPath $wheelhouse -PathType Container) {
        Write-Host "  [..] trying wheelhouse for $RequirementsPath" -ForegroundColor Yellow
        & $VenvPython -m pip install --no-index --find-links $wheelhouse -r $RequirementsPath --quiet
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warning "No compatible complete dependency set in wheelhouse for $RequirementsPath; trying online index."
    }

    & $VenvPython -m pip install -r $RequirementsPath --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency install failed for $RequirementsPath via wheelhouse (if present) and online index."
    }
}

$baseRequirements = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path -LiteralPath $baseRequirements -PathType Leaf)) {
    throw "Required dependency file missing: $baseRequirements"
}
Install-Requirements $baseRequirements

$wecomRequirements = Join-Path $ScriptDir "requirements-wecom.txt"
if (Test-Path -LiteralPath $wecomRequirements -PathType Leaf) {
    Install-Requirements $wecomRequirements
}
Write-Host "  [OK] deps installed" -ForegroundColor Green
Write-Host ""

# 验收
Write-Host "  [..] verifying..." -ForegroundColor Yellow
& $VenvPython -B -c "import openpyxl; import docx; print('  [OK] openpyxl ' + openpyxl.__version__); print('  [OK] python-docx ' + docx.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) { throw "Dependency verification failed: openpyxl/docx import error." }
Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  [DONE] .venv ready" -ForegroundColor Cyan
Write-Host ""
Write-Host "  usage:" -ForegroundColor Yellow
Write-Host "    .\validate_package.ps1" -ForegroundColor White
Write-Host "    .\start_wecom_ai_bot_dry_run.ps1" -ForegroundColor White
Write-Host "=================================================" -ForegroundColor Cyan
