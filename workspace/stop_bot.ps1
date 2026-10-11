# stop_bot.ps1
# 2026-07-10 V12+: 停掉所有 wecom_fault_bot 进程
# 用法:
#   .\stop_bot.ps1

$ErrorActionPreference = "Continue"

$ScriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptDir)) { $ScriptDir = (Get-Location).Path }
Set-Location $ScriptDir

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  停掉所有 wecom_fault_bot 进程" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

$autoStartTask = Get-ScheduledTask -TaskName "FaultClosedLoopWeComBot" -ErrorAction SilentlyContinue
if ($autoStartTask -and $autoStartTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName "FaultClosedLoopWeComBot" -ErrorAction SilentlyContinue
    Write-Host "  [OK] 自动启动任务本次运行已停止（下次登录仍会启动）" -ForegroundColor Green
}

# 现代 Windows 已移除 wmic；直接读取 Win32_Process.CommandLine。
try {
    $botPids = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.Name -match '^pythonw?\.exe$' -and
                $_.CommandLine -like '*wecom_fault_bot.py*'
            } |
            Select-Object -ExpandProperty ProcessId
    )
} catch {
    Write-Host "  [X] 无法读取进程列表: $_" -ForegroundColor Red
    exit 1
}

if ($botPids.Count -eq 0) {
    Write-Host "  [OK] 没有 wecom_fault_bot 进程在跑" -ForegroundColor Green
    exit 0
}

Write-Host "  [找到] $($botPids.Count) 个 wecom_fault_bot 进程: $($botPids -join ', ')" -ForegroundColor Yellow
Write-Host "  [..] 停掉..." -ForegroundColor Yellow

foreach ($botPid in $botPids) {
    try {
        Stop-Process -Id $botPid -Force -ErrorAction Stop
        Write-Host "    [OK] PID $botPid 已停" -ForegroundColor Green
    } catch {
        Write-Host "    [X] PID $botPid 停失败: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "  [完成] 所有 wecom_fault_bot 进程已停" -ForegroundColor Green
Write-Host "  [下一步] 启新模式:" -ForegroundColor Yellow
Write-Host "    .\start_wecom_ai_bot_dry_run.ps1   (干跑, 不真写)" -ForegroundColor Cyan
Write-Host "    .\start_wecom_ai_bot_live.ps1      (真写, 需 3 gate 全开)" -ForegroundColor Cyan
