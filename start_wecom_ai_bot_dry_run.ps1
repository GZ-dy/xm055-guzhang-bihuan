# start_wecom_ai_bot_dry_run.ps1
#
# Windows 本地值班版启动脚本 (PowerShell 5.1+)
# V12+ (2026-07-10): 用 PSScriptRoot 作为项目根, 固定调用 .venv\Scripts\python.exe
#
# 用途: 在 Windows 本地以 dry-run 模式启动企业微信智能机器人
# 副作用: 不连飞书/邮件, 只生成报告+台账+邮件预览+incident_context
#
# 用法 (PowerShell):
#   $env:WECHAT_BOT_ID='...'
#   $env:WECHAT_BOT_SECRET='...'
#   .\start_wecom_ai_bot_dry_run.ps1
#
# 或传 mock 模式 (无真凭据端到端测试):
#   .\start_wecom_ai_bot_dry_run.ps1 -Mock
#
# 正式 live 模式: 用 .\start_wecom_ai_bot_live.ps1 (新), 不在这里搞
#
# 凭据规则 (本项目铁律, 不动):
#   - bot_id / bot_secret 只从 env 读, 不写代码/文档/日志
#   - 不入仓, 不入便携包, 不进 issue 评论

[CmdletBinding()]
param(
    [switch]$Mock,
    [switch]$NoPrompt,
    [string]$MockText,
    [string]$CasePrefix = "wecom",
    [string]$OnlineRecordUrl = ""
)

$ErrorActionPreference = "Stop"

# 1. 切到脚本所在目录 (用 PSScriptRoot, 不依赖 $MyInvocation, PowerShell 5.1 兼容)
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = $ScriptDir
Set-Location $ProjectRoot
$CredentialPath = Join-Path $ProjectRoot "config\private\wecom_bot.dpapi.json"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  企业微信智能机器人 (Windows 本地值班版 - dry-run)" -ForegroundColor Cyan
Write-Host "  项目根: $ProjectRoot" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 2. 优先使用项目 venv；便携目录没有 venv 时回退到已安装依赖的系统 Python。
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        Write-Host "[X] 错误: 没找到项目 venv 或系统 Python" -ForegroundColor Red
        Write-Host "    先跑 .\install_env.ps1" -ForegroundColor Yellow
        exit 1
    }
    $py = $pythonCommand.Source
    Write-Host "  [OK] 未找到项目 venv，回退到系统 Python: $py" -ForegroundColor Yellow
}
Write-Host "  [OK] Python: $py" -ForegroundColor Green

# 3. 凭据校验 (Mock 模式跳过)
$temporaryBotId = $false
$temporaryBotSecret = $false
if (-not $Mock) {
    $botId = $env:WECHAT_BOT_ID
    $botSecret = $env:WECHAT_BOT_SECRET

    if (([string]::IsNullOrWhiteSpace($botId) -or [string]::IsNullOrWhiteSpace($botSecret)) -and (Test-Path -LiteralPath $CredentialPath)) {
        try {
            $savedCredential = Get-Content -LiteralPath $CredentialPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]::IsNullOrWhiteSpace($botId)) {
                $botId = "$($savedCredential.bot_id)".Trim()
                $env:WECHAT_BOT_ID = $botId
                $temporaryBotId = $true
            }
            if ([string]::IsNullOrWhiteSpace($botSecret)) {
                $secureSecret = "$($savedCredential.bot_secret_dpapi)" | ConvertTo-SecureString
                $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
                try {
                    $botSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
                    $env:WECHAT_BOT_SECRET = $botSecret
                    $temporaryBotSecret = $true
                } finally {
                    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
                }
            }
            Write-Host "  [OK] 已读取 Windows 当前用户加密的机器人凭据" -ForegroundColor Green
        } catch {
            Write-Host "[X] 无法读取加密凭据；请重新运行 .\install_wecom_bot_autostart.ps1" -ForegroundColor Red
            exit 1
        }
    }

    if ($NoPrompt -and ([string]::IsNullOrWhiteSpace($botId) -or [string]::IsNullOrWhiteSpace($botSecret))) {
        Write-Host "[X] 自动启动缺少加密凭据；请先运行 .\install_wecom_bot_autostart.ps1" -ForegroundColor Red
        exit 1
    }
    if ([string]::IsNullOrWhiteSpace($botId)) {
        $botId = (Read-Host "请输入企业微信智能机器人的 Bot ID").Trim()
        $env:WECHAT_BOT_ID = $botId
        $temporaryBotId = $true
    }
    if ([string]::IsNullOrWhiteSpace($botSecret)) {
        $secureSecret = Read-Host "请输入同一个机器人的 Bot Secret" -AsSecureString
        $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
        try {
            $botSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
            $env:WECHAT_BOT_SECRET = $botSecret
            $temporaryBotSecret = $true
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
        }
    }
    if ([string]::IsNullOrWhiteSpace($botId) -or [string]::IsNullOrWhiteSpace($botSecret)) {
        Write-Host "[X] 错误: Bot ID / Secret 不能为空" -ForegroundColor Red
        exit 1
    }
    # 脱敏打印
    $bidLen = $botId.Length
    $bscLen = $botSecret.Length
    $bidMask = if ($bidLen -ge 8) { $botId.Substring(0,4) + "***" + $botId.Substring($bidLen-4) } else { "***" }
    Write-Host "[OK] 凭据完整:"
    Write-Host "     WECHAT_BOT_ID     = $bidMask (长度 $bidLen)"
    Write-Host "     WECHAT_BOT_SECRET = *** (长度 $bscLen)"
}

# 4. 双 gate 校验 (只打印, 不替用户改; dry-run 模式下设了也不真发)
$gate1 = "$($env:FAULT_AUTO_SEND_ON_VALIDATED)".Trim()
$gate2 = "$($env:FAULT_CONFIRM_LIVE_WRITE)".Trim()
$gate3 = "$($env:FAULT_PROD_CASE_TOKEN)".Trim()
$gate1On = ($gate1 -eq "1")
$gate2On = ($gate2 -eq "LIVE-WRITE")
$gate3On = ($gate3 -ne "")
if ($gate1On -and $gate2On -and $gate3On) {
    Write-Host "[!] 三 gate 全开 -> 仍走 dry-run (本脚本是 dry-run 启动器, 不真写)" -ForegroundColor Yellow
    Write-Host "     走 live 模式请用 .\start_wecom_ai_bot_live.ps1" -ForegroundColor Yellow
} else {
    Write-Host "[OK] dry-run 模式 (3 gate 未全开):"
    Write-Host "     FAULT_AUTO_SEND_ON_VALIDATED = '$gate1' (需 '1')"
    Write-Host "     FAULT_CONFIRM_LIVE_WRITE    = '$gate2' (需 'LIVE-WRITE')"
    Write-Host "     FAULT_PROD_CASE_TOKEN        = '*** (length $($gate3.Length))' (需非空)"
}

# 5. 启动 Python
$botArgs = @("-B", "scripts\wecom_fault_bot.py", "--case-prefix", $CasePrefix)
if (-not [string]::IsNullOrWhiteSpace($OnlineRecordUrl)) {
    $botArgs += @("--online-record-url", $OnlineRecordUrl)
}
if ($Mock) {
    Write-Host ""
    Write-Host "[Mock 模式] 不连 WeCom WebSocket, 跑内置 BOMC 例子" -ForegroundColor Magenta
    $botArgs += "--mock"
    if ($MockText) {
        $botArgs += @("--mock-text", $MockText)
    }
} else {
    Write-Host ""
    Write-Host "[启动] wecom_fault_bot.py --run-mode dry-run" -ForegroundColor Green
    $botArgs += @("--run-mode", "dry-run")
}
$exitCode = 1
try {
    & $py @botArgs
    $exitCode = $LASTEXITCODE
} finally {
    if ($temporaryBotId) { Remove-Item Env:WECHAT_BOT_ID -ErrorAction SilentlyContinue }
    if ($temporaryBotSecret) { Remove-Item Env:WECHAT_BOT_SECRET -ErrorAction SilentlyContinue }
}

exit $exitCode
