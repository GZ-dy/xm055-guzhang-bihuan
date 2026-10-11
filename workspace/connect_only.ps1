# connect_only.ps1
# 2026-07-10 V12+: 企业微信连接测试 (probe 模式)
# 用法:
#   cd D:\fault-closed-loop
#   .\connect_only.ps1
#   # 输 Bot ID + Secret (屏幕不显示)
#   # 连接成功后群里 @机器人 测 "测试连接"
#   # Ctrl+C 退出 probe
#   # 之后: .\start_full.ps1 走 dry-run
#
# 重要: 本脚本是 probe 模式, 收到消息只回探活话术, 不调 pipeline.
#       本脚本**不主动清空**用户已设的 3 个生产 env (避免误覆盖用户主动设置的 live 模式).

$ErrorActionPreference = "Stop"

# 自动 cd (用 PSScriptRoot)
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir
Write-Host "[auto-cd] cwd = $ScriptDir" -ForegroundColor Cyan
Write-Host ""

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  故障闭环 V1 - 企业微信连接测试 (probe 模式)" -ForegroundColor Cyan
Write-Host "  测完 Ctrl+C 退出; 当前窗口关闭凭据即失效" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# 检查 (不主动清空!) 3 个生产 env 状态
$gate1 = "$($env:FAULT_AUTO_SEND_ON_VALIDATED)".Trim()
$gate2 = "$($env:FAULT_CONFIRM_LIVE_WRITE)".Trim()
$gate3 = "$($env:FAULT_PROD_CASE_TOKEN)".Trim()
if ($gate1 -eq "1" -or $gate2 -eq "LIVE-WRITE" -or $gate3 -ne "") {
    Write-Host "  [!] 检测到部分/全部 3 个生产 env 已被设" -ForegroundColor Yellow
    Write-Host "      FAULT_AUTO_SEND_ON_VALIDATED = '$gate1'" -ForegroundColor Gray
    Write-Host "      FAULT_CONFIRM_LIVE_WRITE     = '$gate2'" -ForegroundColor Gray
    Write-Host "      FAULT_PROD_CASE_TOKEN        = '*** (length $($gate3.Length))'" -ForegroundColor Gray
    Write-Host "  [hint] probe 模式不影响这些 env, 但跑 .\start_full.ps1 时会被 gate 检查拦截" -ForegroundColor Yellow
    Write-Host ""
}

# Bot ID (明文输入)
$env:WECHAT_BOT_ID = (Read-Host "请输入 WECHAT_BOT_ID").Trim()

# Secret (SecureString, 屏幕不显示, 用完 ZeroFree)
$secure = Read-Host "请输入 WECHAT_BOT_SECRET" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:WECHAT_BOT_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
Write-Host "  [OK] Bot ID + Secret 已设 (Secret 已从内存清零)" -ForegroundColor Green
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

Write-Host "  [..] 启动 probe 模式 (Ctrl+C 停止)..." -ForegroundColor Yellow
Write-Host "  [..] 连接成功后, 在企业微信群 @机器人 发 '测试连接'" -ForegroundColor Yellow
Write-Host ""
& $py -B scripts\wecom_fault_bot.py --run-mode probe
