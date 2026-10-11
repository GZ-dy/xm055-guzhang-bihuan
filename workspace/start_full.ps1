# start_full.ps1
# 2026-07-10 V12+: dry-run 一键启 (检查 gate 而非主动清空)
# 用法:
#   cd D:\fault-closed-loop
#   .\start_full.ps1
#
# 重要: 本脚本**只检查** 3 个生产 env 是否被清空, **不主动改用户已设的 env**.
#       用户若设了 3 个 gate, dry-run 拒绝启动 (避免误伤生产).
#       若要走 live 模式, 请用 .\start_wecom_ai_bot_live.ps1 (新)

$ErrorActionPreference = "Stop"

# 自动 cd (用 PSScriptRoot, 不依赖 $MyInvocation)
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir
Write-Host "[auto-cd] cwd = $ScriptDir" -ForegroundColor Cyan
Write-Host ""

# 检查 (不主动清空!) 3 个生产 env, 必须为"未开启"状态才走 dry-run
$gate1 = "$($env:FAULT_AUTO_SEND_ON_VALIDATED)".Trim()
$gate2 = "$($env:FAULT_CONFIRM_LIVE_WRITE)".Trim()
$gate3 = "$($env:FAULT_PROD_CASE_TOKEN)".Trim()

if ($gate1 -eq "1" -and $gate2 -eq "LIVE-WRITE" -and $gate3 -ne "") {
    Write-Host "  [!] 检测到 3 个生产 gate 已全开" -ForegroundColor Red
    Write-Host "      FAULT_AUTO_SEND_ON_VALIDATED = '$gate1'" -ForegroundColor Yellow
    Write-Host "      FAULT_CONFIRM_LIVE_WRITE     = '$gate2'" -ForegroundColor Yellow
    Write-Host "      FAULT_PROD_CASE_TOKEN        = '*** (length $($gate3.Length))'" -ForegroundColor Yellow
    Write-Host "  [X] start_full.ps1 是 dry-run 启动器, 不能在 gate 已开的情况下跑 (会真写飞书/发邮件)" -ForegroundColor Red
    Write-Host "  [hint] 若要正式启动, 跑 .\\start_wecom_ai_bot_live.ps1" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  [OK] 3 个生产 env 未开启 (符合 dry-run)" -ForegroundColor Green
Write-Host ""

# 检查 bot 凭据
if (-not $env:WECHAT_BOT_ID -or -not $env:WECHAT_BOT_SECRET) {
    Write-Host "  [X] WECHAT_BOT_ID 或 WECHAT_BOT_SECRET 未设" -ForegroundColor Red
    Write-Host "  [hint] 先跑 .\connect_only.ps1 设凭据" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  [OK] Bot ID = $($env:WECHAT_BOT_ID.Substring(0, [Math]::Min(4, $env:WECHAT_BOT_ID.Length)))***" -ForegroundColor Green
Write-Host "  [OK] Secret = *** (length $($env:WECHAT_BOT_SECRET.Length))" -ForegroundColor Green
Write-Host ""

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  故障闭环 V1 - dry-run 全闭环 (不真写飞书/邮件)" -ForegroundColor Cyan
Write-Host "  Ctrl+C 停止" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  现在可以在企业微信群里 @机器人 发故障" -ForegroundColor Yellow
Write-Host "  期望 6 字段回执 + 9 个产物 (落到 outputs\... 目录)" -ForegroundColor Yellow
Write-Host ""

# 优先使用项目 venv；便携目录没有 venv 时回退到已安装依赖的系统 Python。
$venvPy = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        Write-Host "  [X] 没找到项目 venv 或系统 Python" -ForegroundColor Red
        Write-Host "  [hint] 先跑 .\install_env.ps1" -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    }
    $py = $pythonCommand.Source
    Write-Host "  [OK] 未找到项目 venv，回退到系统 Python: $py" -ForegroundColor Yellow
}

& $py -B scripts\wecom_fault_bot.py --run-mode dry-run
