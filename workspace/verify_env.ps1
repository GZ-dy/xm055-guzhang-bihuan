# verify_env.ps1
#
# 启动 bot 前检查 6 个 env + 飞书 config 是否齐全
# 不打印真值, 只打印长度/掩码
#
# 用法:
#   .\verify_env.ps1

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  bot 启动前 env 检查" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$ok = $true

# 1. WECHAT_BOT_ID
$val = $env:WECHAT_BOT_ID
if ($val) {
    $mask = $val.Substring(0,[Math]::Min(4,$val.Length)) + "***" + $val.Substring([Math]::Max(0,$val.Length-4))
    Write-Host "  [OK]   WECHAT_BOT_ID              = $mask (长度 $($val.Length))" -ForegroundColor Green
} else {
    Write-Host "  [X]    WECHAT_BOT_ID              = (未设)" -ForegroundColor Red
    $ok = $false
}

# 2. WECHAT_BOT_SECRET
$val = $env:WECHAT_BOT_SECRET
if ($val) {
    Write-Host "  [OK]   WECHAT_BOT_SECRET          = *** (长度 $($val.Length))" -ForegroundColor Green
} else {
    Write-Host "  [X]    WECHAT_BOT_SECRET          = (未设)" -ForegroundColor Red
    $ok = $false
}

# 3. FAULT_SMTP_USER (可选, 只在真发邮件时需要)
$val = $env:FAULT_SMTP_USER
if ($val) {
    Write-Host "  [OK]   FAULT_SMTP_USER            = $val" -ForegroundColor Green
} else {
    Write-Host "  [WARN] FAULT_SMTP_USER            = (未设, dry-run 不需要, 真发邮件需要)" -ForegroundColor Yellow
}

# 4. FAULT_SMTP_PASSWORD (可选)
$val = $env:FAULT_SMTP_PASSWORD
if ($val) {
    Write-Host "  [OK]   FAULT_SMTP_PASSWORD        = *** (长度 $($val.Length))" -ForegroundColor Green
} else {
    Write-Host "  [WARN] FAULT_SMTP_PASSWORD        = (未设, dry-run 不需要, 真发邮件需要)" -ForegroundColor Yellow
}

# 5. FAULT_AUTO_SEND_ON_VALIDATED (三 gate 1)
$val = $env:FAULT_AUTO_SEND_ON_VALIDATED
if ($val -eq "1") {
    Write-Host "  [OK]   FAULT_AUTO_SEND_ON_VALIDATED = '$val' (三 gate 1 已开)" -ForegroundColor Yellow
} else {
    Write-Host "  [INFO] FAULT_AUTO_SEND_ON_VALIDATED = '$val' (三 gate 1 未开 -> dry-run)" -ForegroundColor Gray
}

# 6. FAULT_CONFIRM_LIVE_WRITE (三 gate 2)
$val = $env:FAULT_CONFIRM_LIVE_WRITE
if ($val -eq "LIVE-WRITE") {
    Write-Host "  [OK]   FAULT_CONFIRM_LIVE_WRITE     = '$val' (三 gate 2 已开)" -ForegroundColor Yellow
} else {
    Write-Host "  [INFO] FAULT_CONFIRM_LIVE_WRITE     = '$val' (三 gate 2 未开 -> dry-run)" -ForegroundColor Gray
}

# 6.5 FAULT_PROD_CASE_TOKEN (三 gate 3, 2026-07-10 新增)
$val = $env:FAULT_PROD_CASE_TOKEN
if ($val) {
    Write-Host "  [OK]   FAULT_PROD_CASE_TOKEN        = '***' (长度 $($val.Length)) (三 gate 3 已开)" -ForegroundColor Yellow
} else {
    Write-Host "  [INFO] FAULT_PROD_CASE_TOKEN        = (未设) (三 gate 3 未开 -> dry-run)" -ForegroundColor Gray
}

# 7. 三 gate 组合判断 (2026-07-10 新增: 加重 gate 3)
$gate1 = ($env:FAULT_AUTO_SEND_ON_VALIDATED -eq "1")
$gate2 = ($env:FAULT_CONFIRM_LIVE_WRITE -eq "LIVE-WRITE")
$gate3 = [bool]$env:FAULT_PROD_CASE_TOKEN
Write-Host ""
if ($gate1 -and $gate2 -and $gate3) {
    Write-Host "  [!!] 三 gate 已开 -> LIVE-WRITE (真发飞书+邮件)" -ForegroundColor Yellow
    Write-Host "        发邮件还需 FAULT_SMTP_USER + FAULT_SMTP_PASSWORD" -ForegroundColor Yellow
    Write-Host "        注意: case_id 不能是 test/agent/demo/e2e/mock/smoke 前缀, 否则仍走 dry-run" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] 当前 dry-run 模式 (三 gate 至少一个未开)" -ForegroundColor Green
}

# 8. 飞书 config
$feishuJson = Join-Path $PSScriptRoot "config\private\feishu.json"
Write-Host ""
if (Test-Path $feishuJson) {
    Write-Host "  [OK]   config\private\feishu.json 存在" -ForegroundColor Green
    # 解析 JSON 看是否还有 REPLACE_ME
    try {
        $cfg = Get-Content $feishuJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $hasPlaceholder = $false
        foreach ($p in @("app_id", "app_secret", "bitable_app_token", "table_id")) {
            if ($cfg.$p -match "REPLACE_ME") {
                Write-Host "         [WARN] $p 仍是 REPLACE_ME, 需填真值才能真发飞书" -ForegroundColor Yellow
                $hasPlaceholder = $true
            }
        }
        if (-not $hasPlaceholder) {
            Write-Host "         4 个关键字段都已填值" -ForegroundColor Green
        }
    } catch {
        Write-Host "         [WARN] feishu.json 解析失败: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [X]    config\private\feishu.json 不存在" -ForegroundColor Red
    Write-Host "         copy config\private\feishu.template.json config\private\feishu.json" -ForegroundColor Yellow
    Write-Host "         然后用记事本填真值 (app_id / app_secret / bitable_app_token / table_id)" -ForegroundColor Yellow
    $ok = $false
}

# 9. Python 版本
Write-Host ""
try {
    $pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    if ($pyVer -match "^3\.(1[1-9]|[2-9]\d)") {
        Write-Host "  [OK] Python $pyVer" -ForegroundColor Green
    } elseif ($pyVer -match "^3\.\d+") {
        Write-Host "  [WARN] Python $pyVer (3.10 可能 OK, 建议 3.11+)" -ForegroundColor Yellow
    } else {
        Write-Host "  [X] Python 未找到或版本异常: $pyVer" -ForegroundColor Red
        $ok = $false
    }
} catch {
    Write-Host "  [X] Python 未找到" -ForegroundColor Red
    $ok = $false
}

# 10. wecom SDK
try {
    python -c "import aibot" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] aibot SDK 已装" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] aibot SDK 未装 (不影响 mock, 真发需要)" -ForegroundColor Yellow
    }
} catch {}

# 总结
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
if ($ok) {
    Write-Host "  [OK] 检查通过, 可以启动 bot" -ForegroundColor Green
    Write-Host "  下一步: .\start_wecom_ai_bot_dry_run.ps1" -ForegroundColor Green
    exit 0
} else {
    Write-Host "  [X] 有必填项缺失, 请补齐" -ForegroundColor Red
    Write-Host "  下一步: 跑 .\setup_env.ps1 设凭据" -ForegroundColor Yellow
    exit 1
}
