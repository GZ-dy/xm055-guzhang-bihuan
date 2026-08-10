# start_wecom_ai_bot_live.ps1
#
# Windows 本地值班版启动脚本 (PowerShell 5.1+)
# 2026-07-10 V12+: 正式模式启动器, 只检查 gate, **不自动替用户开启**.
#                   用户必须在启动前手动设 3 个生产 env.
#
# 用途: 在 Windows 本地以 LIVE 模式启动企业微信智能机器人
#       -> 真写飞书 + 真发邮件
#
# !!! 警告 !!!
# 本脚本启动后, bot 收到群消息会**真写飞书表 + 真发邮件**.
# 请确认:
#   1. 业务规则已确认
#   2. SMTP 授权码 (FAULT_SMTP_PASSWORD) 已设
#   3. 飞书配置 (config\private\feishu.json) 已就位
#   4. case_id 前缀不在 dry-run-only 白名单 (test/agent/demo/e2e/mock/smoke)
#
# 用法:
#   $env:FAULT_AUTO_SEND_ON_VALIDATED = "1"
#   $env:FAULT_CONFIRM_LIVE_WRITE = "LIVE-WRITE"
#   $env:FAULT_PROD_CASE_TOKEN = "<your-token>"
#   $env:WECHAT_BOT_ID = "<your-bot-id>"
#   $env:WECHAT_BOT_SECRET = "<your-bot-secret>"
#   .\start_wecom_ai_bot_live.ps1
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

# 1. 切到脚本所在目录 (用 PSScriptRoot)
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = $ScriptDir
Set-Location $ProjectRoot
$CredentialPath = Join-Path $ProjectRoot "config\private\wecom_bot.dpapi.json"

Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "  !!! LIVE 模式启动 !!! (真写飞书 + 真发邮件)" -ForegroundColor Magenta
Write-Host "  项目根: $ProjectRoot" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Magenta

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

# 3. 凭据校验；自动启动时读取当前 Windows 用户的 DPAPI 加密配置。
$temporaryBotId = $false
$temporaryBotSecret = $false
$temporarySmtpUser = $false
$temporarySmtpPassword = $false
$temporaryLiveGates = $false
if (-not $Mock) {
    $botId = $env:WECHAT_BOT_ID
    $botSecret = $env:WECHAT_BOT_SECRET
    if (Test-Path -LiteralPath $CredentialPath) {
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
            if ([string]::IsNullOrWhiteSpace($env:FAULT_SMTP_USER) -and -not [string]::IsNullOrWhiteSpace("$($savedCredential.smtp_user)")) {
                $env:FAULT_SMTP_USER = "$($savedCredential.smtp_user)".Trim()
                $temporarySmtpUser = $true
            }
            if ([string]::IsNullOrWhiteSpace($env:FAULT_SMTP_PASSWORD) -and -not [string]::IsNullOrWhiteSpace("$($savedCredential.smtp_password_dpapi)")) {
                $secureSmtpPassword = "$($savedCredential.smtp_password_dpapi)" | ConvertTo-SecureString
                $smtpPasswordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSmtpPassword)
                try {
                    $env:FAULT_SMTP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($smtpPasswordPtr)
                    $temporarySmtpPassword = $true
                } finally {
                    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($smtpPasswordPtr)
                }
            }
            if ($savedCredential.live_write_enabled -eq $true) {
                $secureToken = "$($savedCredential.prod_case_token_dpapi)" | ConvertTo-SecureString
                $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
                try {
                    $env:FAULT_PROD_CASE_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
                } finally {
                    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
                }
                $env:FAULT_AUTO_SEND_ON_VALIDATED = "1"
                $env:FAULT_CONFIRM_LIVE_WRITE = "LIVE-WRITE"
                $temporaryLiveGates = $true
            }
            Write-Host "[OK] 已读取 Windows 当前用户加密的 live 配置" -ForegroundColor Green
        } catch {
            Write-Host "[X] 无法读取 live 配置；请重新运行 .\install_wecom_bot_autostart.ps1" -ForegroundColor Red
            exit 1
        }
    }
    if ([string]::IsNullOrWhiteSpace($botId) -or [string]::IsNullOrWhiteSpace($botSecret)) {
        Write-Host "[X] 错误: WECHAT_BOT_ID / WECHAT_BOT_SECRET 未设置" -ForegroundColor Red
        Write-Host ""
        Write-Host "PowerShell 临时设 (本会话有效):" -ForegroundColor Yellow
        Write-Host "  `$env:WECHAT_BOT_ID='<your_bot_id>'"
        Write-Host "  `$env:WECHAT_BOT_SECRET='<your_bot_secret>'"
        exit 1
    }
    Write-Host "[OK] Bot ID + Secret 已设"
    if ([string]::IsNullOrWhiteSpace($env:FAULT_SMTP_USER) -or [string]::IsNullOrWhiteSpace($env:FAULT_SMTP_PASSWORD)) {
        Write-Host "[X] 错误: FAULT_SMTP_USER / FAULT_SMTP_PASSWORD 未设置" -ForegroundColor Red
        Write-Host "    自动启动请重新运行 .\install_wecom_bot_autostart.ps1" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[OK] SMTP 发件邮箱 + 授权码已设"
}

# 4. 三 gate 校验 (只检查, 不自动设; 三 gate 任一缺失 → 拒绝启动 LIVE 模式)
$gate1 = "$($env:FAULT_AUTO_SEND_ON_VALIDATED)".Trim()
$gate2 = "$($env:FAULT_CONFIRM_LIVE_WRITE)".Trim()
$gate3 = "$($env:FAULT_PROD_CASE_TOKEN)".Trim()

Write-Host ""
Write-Host "[3 gate 校验]" -ForegroundColor Yellow
Write-Host "  FAULT_AUTO_SEND_ON_VALIDATED = '$gate1' (需 '1')" -ForegroundColor $(if($gate1 -eq "1"){"Green"}else{"Red"})
Write-Host "  FAULT_CONFIRM_LIVE_WRITE     = '$gate2' (需 'LIVE-WRITE')" -ForegroundColor $(if($gate2 -eq "LIVE-WRITE"){"Green"}else{"Red"})
Write-Host "  FAULT_PROD_CASE_TOKEN        = '*** (length $($gate3.Length))' (需非空)" -ForegroundColor $(if($gate3 -ne ""){"Green"}else{"Red"})

$gateOk = ($gate1 -eq "1") -and ($gate2 -eq "LIVE-WRITE") -and ($gate3 -ne "")
if (-not $gateOk) {
    Write-Host ""
    Write-Host "[X] LIVE 模式启动被拒绝: 三 gate 未全开 (本脚本**不自动替用户开启**)" -ForegroundColor Red
    Write-Host ""
    Write-Host "  请在启动前手动设 (PowerShell 范例):" -ForegroundColor Yellow
    Write-Host "    `$env:FAULT_AUTO_SEND_ON_VALIDATED = '1'"
    Write-Host "    `$env:FAULT_CONFIRM_LIVE_WRITE = 'LIVE-WRITE'"
    Write-Host "    `$env:FAULT_PROD_CASE_TOKEN = '<your-token>'"
    Write-Host "    .\start_wecom_ai_bot_live.ps1"
    Write-Host ""
    Write-Host "  或跑 dry-run 验证流程 (不真写):" -ForegroundColor Yellow
    Write-Host "    .\start_wecom_ai_bot_dry_run.ps1"
    exit 1
}

Write-Host ""
Write-Host "[OK] 三 gate 全开 + case_id 不在 deny list (在 bot 内白名单兜底) -> LIVE 模式启动" -ForegroundColor Green
Write-Host ""

# 5. 启动 Python；隐藏计划任务必须落日志，否则断线原因无法追溯。
$logDir = Join-Path $ProjectRoot "work\bot"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir ("wecom_bot_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
Write-Host "[日志] $logPath" -ForegroundColor Cyan
$botArgs = @("-B", "scripts\wecom_fault_bot.py", "--case-prefix", $CasePrefix)
if (-not [string]::IsNullOrWhiteSpace($OnlineRecordUrl)) {
    $botArgs += @("--online-record-url", $OnlineRecordUrl)
}
if ($Mock) {
    Write-Host "[Mock 模式] 不连 WeCom WebSocket" -ForegroundColor Magenta
    $botArgs += "--mock"
    if ($MockText) {
        $botArgs += @("--mock-text", $MockText)
    }
} else {
    Write-Host "[启动] wecom_fault_bot.py --run-mode live (真写飞书 + 真发邮件)" -ForegroundColor Green
    $botArgs += @("--run-mode", "live")
}
$exitCode = 1
$launcherErrorActionPreference = $ErrorActionPreference
try {
    # SDK 把 INFO/DEBUG 也写 stderr；这里仅负责落盘，不能让 PowerShell 当成终止错误。
    $ErrorActionPreference = "Continue"
    & $py -u @botArgs *>> $logPath
    $exitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $launcherErrorActionPreference
    if ($temporaryBotId) { Remove-Item Env:WECHAT_BOT_ID -ErrorAction SilentlyContinue }
    if ($temporaryBotSecret) { Remove-Item Env:WECHAT_BOT_SECRET -ErrorAction SilentlyContinue }
    if ($temporarySmtpUser) { Remove-Item Env:FAULT_SMTP_USER -ErrorAction SilentlyContinue }
    if ($temporarySmtpPassword) { Remove-Item Env:FAULT_SMTP_PASSWORD -ErrorAction SilentlyContinue }
    if ($temporaryLiveGates) {
        Remove-Item Env:FAULT_AUTO_SEND_ON_VALIDATED -ErrorAction SilentlyContinue
        Remove-Item Env:FAULT_CONFIRM_LIVE_WRITE -ErrorAction SilentlyContinue
        Remove-Item Env:FAULT_PROD_CASE_TOKEN -ErrorAction SilentlyContinue
    }
}

exit $exitCode
