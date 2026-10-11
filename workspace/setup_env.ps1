# setup_env.ps1
#
# 一次性设 7 个 env (本 PowerShell 窗口有效) (2026-07-10 加 FAULT_PROD_CASE_TOKEN)
#
# 用法:
#   1. 复制本文件到 C:\fault-closed-loop\setup_env.ps1
#   2. 用记事本编辑 7 个值
#   3. PowerShell 跑: .\setup_env.ps1
#   4. 跑 .\verify_env.ps1 确认
#   5. 跑 .\start_wecom_ai_bot_dry_run.ps1 启动
#
# 关 PowerShell 窗口后 env 失效 (这是特性, 不是 bug):
#   - 不污染系统环境
#   - 凭据不会泄到注册表
#   - 改密码不需重启电脑
#
# 想长期生效 (不推荐, 不入项目):
#   [System.Environment]::SetEnvironmentVariable("WECHAT_BOT_ID", "...", "User")

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  故障闭环 V1 + WeCom bot 凭据设置向导" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "请按提示填入 7 个 env, * 输入跳过 (保留原值)" -ForegroundColor Yellow
Write-Host ""

# 1. WECHAT_BOT_ID
$cur = $env:WECHAT_BOT_ID
$mask = if ($cur) { $cur.Substring(0,[Math]::Min(4,$cur.Length)) + "***" + $cur.Substring([Math]::Max(0,$cur.Length-4)) } else { "(未设)" }
$val = Read-Host "WECHAT_BOT_ID (当前: $mask)"
if ($val -and $val -ne "*") { $env:WECHAT_BOT_ID = $val.Trim() }

# 2. WECHAT_BOT_SECRET
$cur = $env:WECHAT_BOT_SECRET
$mask = if ($cur) { "*** (长度 $($cur.Length))" } else { "(未设)" }
$val = Read-Host "WECHAT_BOT_SECRET (当前: $mask)"
if ($val -and $val -ne "*") { $env:WECHAT_BOT_SECRET = $val.Trim() }

# 3. FAULT_SMTP_USER
$cur = $env:FAULT_SMTP_USER
$mask = if ($cur) { $cur } else { "(未设)" }
$val = Read-Host "FAULT_SMTP_USER (当前: $mask) - 例: your@163.com"
if ($val -and $val -ne "*") { $env:FAULT_SMTP_USER = $val.Trim() }

# 4. FAULT_SMTP_PASSWORD (163 邮箱授权码, 不是登录密码)
$cur = $env:FAULT_SMTP_PASSWORD
$mask = if ($cur) { "*** (长度 $($cur.Length))" } else { "(未设)" }
$val = Read-Host "FAULT_SMTP_PASSWORD (当前: $mask) - 163 邮箱授权码"
if ($val -and $val -ne "*") { $env:FAULT_SMTP_PASSWORD = $val.Trim() }

# 5. FAULT_AUTO_SEND_ON_VALIDATED (三 gate 1)
$cur = $env:FAULT_AUTO_SEND_ON_VALIDATED
$val = Read-Host "FAULT_AUTO_SEND_ON_VALIDATED (当前: '$cur') - 真发需 '1', dry-run 留空"
if ($val -and $val -ne "*") { $env:FAULT_AUTO_SEND_ON_VALIDATED = $val.Trim() }

# 6. FAULT_CONFIRM_LIVE_WRITE (三 gate 2)
$cur = $env:FAULT_CONFIRM_LIVE_WRITE
$val = Read-Host "FAULT_CONFIRM_LIVE_WRITE (当前: '$cur') - 真发需 'LIVE-WRITE', dry-run 留空"
if ($val -and $val -ne "*") { $env:FAULT_CONFIRM_LIVE_WRITE = $val.Trim() }

# 7. FAULT_PROD_CASE_TOKEN (三 gate 3, 2026-07-10 新增: 防 agent 误伤生产表)
$cur = $env:FAULT_PROD_CASE_TOKEN
$mask = if ($cur) { "*** (长度 $($cur.Length))" } else { "(未设)" }
$val = Read-Host "FAULT_PROD_CASE_TOKEN (当前: $mask) - 真生产要设, 任意非空 (例: REAL-2026), dry-run 留空"
if ($val -and $val -ne "*") { $env:FAULT_PROD_CASE_TOKEN = $val.Trim() }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  已设 (脱敏显示):" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

function Show-Masked($name, $val) {
    if (-not $val) {
        Write-Host "  $name = (未设)" -ForegroundColor Red
    } else {
        $len = $val.Length
        $masked = $val.Substring(0,[Math]::Min(4,$val.Length)) + "***" + $val.Substring([Math]::Max(0,$val.Length-4))
        Write-Host "  $name = $masked (长度 $len)" -ForegroundColor Green
    }
}

Show-Masked "WECHAT_BOT_ID              " $env:WECHAT_BOT_ID
Show-Masked "WECHAT_BOT_SECRET          " $env:WECHAT_BOT_SECRET
Show-Masked "FAULT_SMTP_USER            " $env:FAULT_SMTP_USER
$len = if ($env:FAULT_SMTP_PASSWORD) { $env:FAULT_SMTP_PASSWORD.Length } else { 0 }
Write-Host "  FAULT_SMTP_PASSWORD        = *** (长度 $len)" -ForegroundColor $(if($len){"Green"}else{"Red"})
Write-Host "  FAULT_AUTO_SEND_ON_VALIDATED = '$env:FAULT_AUTO_SEND_ON_VALIDATED'" -ForegroundColor $(if($env:FAULT_AUTO_SEND_ON_VALIDATED -eq "1"){"Yellow"}else{"Gray"})
Write-Host "  FAULT_CONFIRM_LIVE_WRITE     = '$env:FAULT_CONFIRM_LIVE_WRITE'" -ForegroundColor $(if($env:FAULT_CONFIRM_LIVE_WRITE -eq "LIVE-WRITE"){"Yellow"}else{"Gray"})
Write-Host "  FAULT_PROD_CASE_TOKEN        = $(if($env:FAULT_PROD_CASE_TOKEN){'*** (长度 '+$env:FAULT_PROD_CASE_TOKEN.Length+')'}else{'(未设)'})" -ForegroundColor $(if($env:FAULT_PROD_CASE_TOKEN){"Yellow"}else{"Gray"})

# 检查飞书 config
$feishuJson = Join-Path $PSScriptRoot "config\private\feishu.json"
Write-Host ""
if (Test-Path $feishuJson) {
    Write-Host "  [OK] config\private\feishu.json 存在" -ForegroundColor Green
} else {
    Write-Host "  [X] config\private\feishu.json 不存在" -ForegroundColor Red
    Write-Host "      复制 feishu.template.json 改名: copy config\private\feishu.template.json config\private\feishu.json" -ForegroundColor Yellow
    Write-Host "      然后用记事本填真值" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "下一步: 跑 .\verify_env.ps1 验证" -ForegroundColor Cyan
