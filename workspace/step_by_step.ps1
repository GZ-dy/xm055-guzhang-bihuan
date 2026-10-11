# step_by_step.ps1
# 2026-07-10 v2: 重写, 避免 PowerShell 5.1 解析坑 (用简单 if/else, 不用 foreach+array, 不用嵌套 .ps1 调用)
# 解决 "找不到 .ps1 文件" 问题 (用户在 System32 跑, 不是在解压目录)
# 用法: 把 V6 zip 解压到 D:\fault-closed-loop\, 然后:
#   cd D:\fault-closed-loop
#   .\step_by_step.ps1

# 关闭严格模式 (PowerShell 5.1 对未定义变量更宽松)
Set-StrictMode -Off
$ErrorActionPreference = "Continue"

# 自动 cd 到本脚本所在目录 (用 $PSScriptRoot 更稳, 不依赖 $MyInvocation)
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir
Write-Host "[auto-cd] cwd = $ScriptDir" -ForegroundColor Cyan
Write-Host ""

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Fault Closed-Loop V1 - step_by_step v2" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 4 个关键文件 (用 if/else if, 不用 foreach)
Write-Host "[1] check required files" -ForegroundColor Yellow
$allOk = $true

if (Test-Path "diagnose_bot.ps1") {
    Write-Host "  [OK] diagnose_bot.ps1" -ForegroundColor Green
} else {
    Write-Host "  [X]  diagnose_bot.ps1 NOT FOUND" -ForegroundColor Red
    $allOk = $false
}

if (Test-Path "start_wecom_ai_bot_dry_run.ps1") {
    Write-Host "  [OK] start_wecom_ai_bot_dry_run.ps1" -ForegroundColor Green
} else {
    Write-Host "  [X]  start_wecom_ai_bot_dry_run.ps1 NOT FOUND" -ForegroundColor Red
    $allOk = $false
}

if (Test-Path "scripts\wecom_fault_bot.py") {
    Write-Host "  [OK] scripts\wecom_fault_bot.py" -ForegroundColor Green
} else {
    Write-Host "  [X]  scripts\wecom_fault_bot.py NOT FOUND" -ForegroundColor Red
    $allOk = $false
}

if (Test-Path "config\private\feishu.json") {
    Write-Host "  [OK] config\private\feishu.json" -ForegroundColor Green
} else {
    Write-Host "  [X]  config\private\feishu.json NOT FOUND" -ForegroundColor Red
    $allOk = $false
}

if ($allOk -eq $false) {
    Write-Host ""
    Write-Host "  [FATAL] some files missing. Make sure V6 zip is fully extracted." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# 2. 检查 Python
Write-Host "[2] check Python" -ForegroundColor Yellow
$pyVer = "not found"
try {
    $pyVer = python -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>&1
} catch {
    $pyVer = "not found"
}
Write-Host "  Python: $pyVer" -ForegroundColor Gray
Write-Host ""

# 3. 检查 env 变量
Write-Host "[3] check env vars" -ForegroundColor Yellow
$envOk = $true
$id = $env:WECHAT_BOT_ID
if ($id) {
    $mask = $id.Substring(0, [Math]::Min(4, $id.Length)) + "***"
    Write-Host "  [OK] WECHAT_BOT_ID = $mask" -ForegroundColor Green
} else {
    Write-Host "  [X]  WECHAT_BOT_ID not set" -ForegroundColor Red
    $envOk = $false
}
$sec = $env:WECHAT_BOT_SECRET
if ($sec) {
    Write-Host "  [OK] WECHAT_BOT_SECRET = *** (length $($sec.Length))" -ForegroundColor Green
} else {
    Write-Host "  [X]  WECHAT_BOT_SECRET not set" -ForegroundColor Red
    $envOk = $false
}

if ($envOk -eq $false) {
    Write-Host ""
    Write-Host "  hint: run setup_env.ps1 first to set credentials" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  [B] Quick mock mode (skip enterprise wecom, just verify bot works)" -ForegroundColor Cyan
    Write-Host "  [E] Exit to set env first" -ForegroundColor Cyan
    $choice = Read-Host "  Choose (B/E)"
    if ($choice -eq "B" -or $choice -eq "b") {
        Write-Host ""
        Write-Host "  [mock mode] running bot with hardcoded test text..." -ForegroundColor Cyan
        & python -B "scripts\wecom_fault_bot.py" --run-mode dry-run --mock --mock-text "系统: BOMC`n发生时间: 2026年7月10日12:10`n恢复时间: 2026年7月10日12:20`n故障现象: BOMC无法打开`n故障原因: CAS服务异常`n解决方式: 重启WEB和CAS服务"
        Read-Host "Press Enter to exit"
        exit 0
    } else {
        Read-Host "Press Enter to exit"
        exit 1
    }
}
Write-Host ""

# 4. 跑 diagnose_bot
Write-Host "[4] run diagnose_bot.ps1" -ForegroundColor Yellow
Write-Host ""
& ".\diagnose_bot.ps1"
$diagCode = $LASTEXITCODE
Write-Host ""
Write-Host "  diagnose exit code: $diagCode" -ForegroundColor Gray
Write-Host ""

# 5. 问你是否启动 bot
Write-Host "[5] start dry-run bot?" -ForegroundColor Yellow
$ans = Read-Host "  Start bot now? (y/N)"
if ($ans -eq "y" -or $ans -eq "Y") {
    Write-Host ""
    Write-Host "  starting bot (Ctrl+C to stop)..." -ForegroundColor Cyan
    Write-Host "  then in WeCom group: @bot send fault info" -ForegroundColor Cyan
    Write-Host ""
    & ".\start_wecom_ai_bot_dry_run.ps1"
} else {
    Write-Host "  skipped. to start manually: .\start_wecom_ai_bot_dry_run.ps1" -ForegroundColor Yellow
}
