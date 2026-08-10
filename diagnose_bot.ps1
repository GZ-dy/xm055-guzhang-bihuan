# diagnose_bot.ps1
# 2026-07-10 V12+: 诊断 bot 当前运行状态
#   - 不用 Get-Process.CommandLine；优先 CIM，旧系统才回退 wmic
#   - 不引用其他不存在脚本
#   - 不引用 diagnose_running_bot.ps1 (已删除, 本脚本是唯一诊断入口)
#
# 用法:
#   .\diagnose_bot.ps1

$ErrorActionPreference = "Continue"

# 自动 cd (用 PSScriptRoot)
$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  WeCom AI bot 诊断工具 (V12+)" -ForegroundColor Cyan
Write-Host "  脚本: $ScriptDir\diagnose_bot.ps1" -ForegroundColor Gray
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 找 python 进程 (用 PID + 进程名, 不依赖 CommandLine)
Write-Host "[1] 找所有 python 进程" -ForegroundColor Yellow
$pyProcs = Get-Process python -ErrorAction SilentlyContinue
if (-not $pyProcs) {
    Write-Host "  [X] 没有 python 进程在跑" -ForegroundColor Red
    Write-Host "      下一步: .\start_wecom_ai_bot_dry_run.ps1" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [找到] $($pyProcs.Count) 个 python 进程:" -ForegroundColor Green
foreach ($p in $pyProcs) {
    Write-Host "      PID=$($p.Id)  Started=$($p.StartTime)  Memory=$([math]::Round($p.WorkingSet64 / 1MB, 1))MB" -ForegroundColor Gray
}
Write-Host ""

# 2. 读取命令行。现代 Windows 已移除 wmic，因此优先 CIM。
Write-Host "[2] 看进程命令行 (CIM 优先, wmic 兼容回退)" -ForegroundColor Yellow
$botProcs = @()
$wmicCommand = Get-Command wmic -ErrorAction SilentlyContinue
foreach ($p in $pyProcs) {
    $cmdline = ""
    try {
        $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)" -ErrorAction Stop
        $cmdline = [string]$cimProcess.CommandLine
    } catch {}
    if ([string]::IsNullOrWhiteSpace($cmdline) -and $wmicCommand) {
        try {
            $wmicOut = & $wmicCommand.Source process where "ProcessId=$($p.Id)" get CommandLine 2>$null
            if ($wmicOut -and $wmicOut.Count -gt 1) {
                $cmdline = ($wmicOut | Select-Object -Skip 1) -join "`n"
            }
        } catch {
            $cmdline = ""
        }
    }

    $isBot = $cmdline -like "*wecom_fault_bot*"
    $botMarker = if ($isBot) { " [BOT]" } else { "" }
    Write-Host "      PID=$($p.Id)$botMarker  CmdLen=$($cmdline.Length)" -ForegroundColor Gray
    if ($isBot) {
        $botProcs += [pscustomobject]@{
            Pid       = $p.Id
            Cmdline   = $cmdline
            Started   = $p.StartTime
        }
    }
}
Write-Host ""

if ($botProcs.Count -eq 0) {
    Write-Host "  [X] 没找到 wecom_fault_bot 进程" -ForegroundColor Red
    Write-Host "      下一步: .\start_wecom_ai_bot_dry_run.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "[3] 查 bot 启动模式" -ForegroundColor Yellow
$probeFound = $false
$dryrunFound = $false
$liveFound = $false
foreach ($bp in $botProcs) {
    $cl = $bp.Cmdline
    if ($cl -like "*--run-mode probe*") {
        Write-Host "  [!] PID=$($bp.Pid) 是 PROBE 模式 (只回探活话术, 不处理故障)" -ForegroundColor Red
        $probeFound = $true
    } elseif ($cl -like "*--run-mode dry-run*") {
        Write-Host "  [OK] PID=$($bp.Pid) 是 DRY-RUN 模式 (跑 pipeline 生成产物, 不真写飞书/邮件)" -ForegroundColor Green
        $dryrunFound = $true
    } elseif ($cl -like "*--run-mode live*") {
        Write-Host "  [!!] PID=$($bp.Pid) 是 LIVE 模式 (真写飞书 + 真发邮件)" -ForegroundColor Magenta
        $liveFound = $true
    } else {
        Write-Host "  [?] PID=$($bp.Pid) 启动参数无 --run-mode (默认 dry-run, OK)" -ForegroundColor Yellow
        $dryrunFound = $true
    }
}
Write-Host ""

# 3. 检查 gate 状态 (纯 env 检查, 不依赖进程)
Write-Host "[4] 检查 3 个生产 gate" -ForegroundColor Yellow
$g1 = "$($env:FAULT_AUTO_SEND_ON_VALIDATED)".Trim()
$g2 = "$($env:FAULT_CONFIRM_LIVE_WRITE)".Trim()
$g3 = "$($env:FAULT_PROD_CASE_TOKEN)".Trim()
$g1Ok = ($g1 -eq "1")
$g2Ok = ($g2 -eq "LIVE-WRITE")
$g3Ok = ($g3 -ne "")
Write-Host "  FAULT_AUTO_SEND_ON_VALIDATED = '$g1'  $(if($g1Ok){'[OK]'}else{'[缺]'})" -ForegroundColor $(if($g1Ok){'Green'}else{'Red'})
Write-Host "  FAULT_CONFIRM_LIVE_WRITE     = '$g2'  $(if($g2Ok){'[OK]'}else{'[缺]'})" -ForegroundColor $(if($g2Ok){'Green'}else{'Red'})
Write-Host "  FAULT_PROD_CASE_TOKEN        = '*** (length $($g3.Length))'  $(if($g3Ok){'[OK]'}else{'[缺]'})" -ForegroundColor $(if($g3Ok){'Green'}else{'Red'})
Write-Host ""

# 4. 给建议
Write-Host "[5] 建议" -ForegroundColor Yellow
if ($probeFound -and -not ($dryrunFound -or $liveFound)) {
    Write-Host "  ⚠️ 你只有 PROBE 模式进程, 不会处理故障" -ForegroundColor Red
    Write-Host ""
    Write-Host "  修复步骤:" -ForegroundColor Yellow
    Write-Host "    1. 停掉 probe 进程:" -ForegroundColor White
    Write-Host "       .\stop_bot.ps1" -ForegroundColor Cyan
    Write-Host "    2. 启动 dry-run:" -ForegroundColor White
    Write-Host "       .\start_wecom_ai_bot_dry_run.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 2
} elseif ($liveFound -and -not ($g1Ok -and $g2Ok -and $g3Ok)) {
    Write-Host "  ⚠️ 启了 LIVE 模式但 3 gate 没全开 (bot 内会拒绝并降级到 dry-run)" -ForegroundColor Red
    Write-Host ""
    Write-Host "  修复步骤:" -ForegroundColor Yellow
    Write-Host "    1. 停 LIVE:" -ForegroundColor White
    Write-Host "       .\stop_bot.ps1" -ForegroundColor Cyan
    Write-Host "    2. 手动设 3 gate:" -ForegroundColor White
    Write-Host "       `$env:FAULT_AUTO_SEND_ON_VALIDATED = '1'" -ForegroundColor Cyan
    Write-Host "       `$env:FAULT_CONFIRM_LIVE_WRITE = 'LIVE-WRITE'" -ForegroundColor Cyan
    Write-Host "       `$env:FAULT_PROD_CASE_TOKEN = '<your-token>'" -ForegroundColor Cyan
    Write-Host "    3. 重启 LIVE:" -ForegroundColor White
    Write-Host "       .\start_wecom_ai_bot_live.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 3
} elseif ($dryrunFound -or $liveFound) {
    Write-Host "  [OK] bot 状态正常, 可以发故障信息测试" -ForegroundColor Green
    Write-Host "        群里 @机器人 发送 6 字段故障 → 看 6 字段回执" -ForegroundColor Green
    Write-Host "        期望产物落 $ScriptDir\outputs\reports\<case_id>\" -ForegroundColor Green
    exit 0
}

Write-Host ""
exit 0
