# install_wecom_bot_autostart.ps1
# 当前 Windows 用户登录后自动运行企业微信故障机器人（正式 live）。

[CmdletBinding()]
param(
    [string]$TaskName = "FaultClosedLoopWeComBot"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$CredentialPath = Join-Path $ProjectRoot "config\private\wecom_bot.dpapi.json"
$LauncherPath = Join-Path $ProjectRoot "start_wecom_ai_bot_live.ps1"

if ($env:OS -ne "Windows_NT") { throw "该安装器只支持 Windows。" }
if (-not (Test-Path -LiteralPath $LauncherPath)) { throw "缺少启动器: $LauncherPath" }

$botId = (Read-Host "请输入已配置机器人的 Bot ID").Trim()
$secureSecret = Read-Host "请输入同一个机器人的 Bot Secret（只保存 Windows 加密密文）" -AsSecureString
$secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
try {
    if ([Runtime.InteropServices.Marshal]::ReadInt16($secretPtr) -eq 0) { throw "Bot Secret 不能为空。" }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
}
if ([string]::IsNullOrWhiteSpace($botId)) { throw "Bot ID 不能为空。" }

$smtpUser = (Read-Host "请输入 SMTP 发件邮箱（例如 your@163.com）").Trim()
$secureSmtpPassword = Read-Host "请输入 SMTP 授权码（只保存 Windows 加密密文）" -AsSecureString
$smtpPasswordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSmtpPassword)
try {
    if ([Runtime.InteropServices.Marshal]::ReadInt16($smtpPasswordPtr) -eq 0) { throw "SMTP 授权码不能为空。" }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($smtpPasswordPtr)
}
if ([string]::IsNullOrWhiteSpace($smtpUser)) { throw "SMTP 发件邮箱不能为空。" }

$credentialDir = Split-Path -Parent $CredentialPath
New-Item -ItemType Directory -Path $credentialDir -Force | Out-Null
$prodToken = ConvertTo-SecureString ([Guid]::NewGuid().ToString("N")) -AsPlainText -Force
@{
    schema_version = 2
    bot_id = $botId
    bot_secret_dpapi = ConvertFrom-SecureString $secureSecret
    smtp_user = $smtpUser
    smtp_password_dpapi = ConvertFrom-SecureString $secureSmtpPassword
    live_write_enabled = $true
    prod_case_token_dpapi = ConvertFrom-SecureString $prodToken
    created_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $CredentialPath -Encoding UTF8

$powerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -NoPrompt' -f $LauncherPath
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "故障闭环企业微信机器人（live：写飞书并发送邮件）"
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] 机器人和 SMTP 凭据已用 Windows DPAPI 加密保存。" -ForegroundColor Green
Write-Host "[OK] 自动启动任务已创建并启动: $TaskName" -ForegroundColor Green
Write-Host "[LIVE] 群内故障消息会直接写飞书并发送 SMTP 邮件。" -ForegroundColor Magenta
