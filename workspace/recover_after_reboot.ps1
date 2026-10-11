[CmdletBinding()]
param(
    [string]$TaskName = "FaultClosedLoopWeComBot",
    [switch]$CheckOnly,
    [ValidateRange(10, 120)][int]$WaitSeconds = 45
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = (Get-Location).Path }
Set-Location $Root

function Stop-Recovery {
    param(
        [Parameter(Mandatory=$true)][string]$Message,
        [int]$Code = 1
    )
    Write-Host "[FAILED] $Message" -ForegroundColor Red
    exit $Code
}

function Format-TaskResult {
    param($Value)
    return "0x{0:X8}" -f (([int64]$Value) -band 4294967295L)
}

function Resolve-AccountSid {
    param([AllowEmptyString()][string]$Account)
    if ([string]::IsNullOrWhiteSpace($Account)) { return "" }
    if ($Account -match '^S-\d-') { return $Account }
    try {
        return (New-Object Security.Principal.NTAccount($Account)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        return ""
    }
}

function Get-AllBotProcesses {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.Name -match '^pythonw?\.exe$' -and
            "$($_.CommandLine)".IndexOf("wecom_fault_bot.py", [StringComparison]::OrdinalIgnoreCase) -ge 0
        })
    } catch {
        throw "Cannot verify local bot processes: $($_.Exception.Message)"
    }
}

function Get-LogicalBotInstances {
    param(
        [AllowEmptyCollection()][object[]]$Processes = @(),
        [Parameter(Mandatory=$true)][string]$ExpectedVenvPython,
        [Parameter(Mandatory=$true)][string]$ExpectedBasePython
    )

    $items = @($Processes)
    if ($items.Count -eq 0) { return }

    $invocationKey = {
        param([AllowEmptyString()][string]$CommandLine)
        $text = "$CommandLine".Trim()
        if ([string]::IsNullOrWhiteSpace($text)) { return "" }
        if ($text -match '^"[^"]+"\s+(?<tail>.+)$') {
            return [regex]::Replace($Matches['tail'].Trim(), '\s+', ' ')
        }
        if ($text -match '^\S+\s+(?<tail>.+)$') {
            return [regex]::Replace($Matches['tail'].Trim(), '\s+', ' ')
        }
        return ""
    }

    try {
        $venvPython = [IO.Path]::GetFullPath($ExpectedVenvPython)
        $basePython = [IO.Path]::GetFullPath($ExpectedBasePython)
    } catch {
        $venvPython = ""
        $basePython = ""
    }

    $byPid = @{}
    $childrenByParent = @{}
    $allowPairing = -not (
        [string]::IsNullOrWhiteSpace($venvPython) -or
        [string]::IsNullOrWhiteSpace($basePython)
    )
    foreach ($process in $items) {
        $pidKey = "$($process.ProcessId)"
        if ([string]::IsNullOrWhiteSpace($pidKey) -or $byPid.ContainsKey($pidKey)) {
            $allowPairing = $false
            continue
        }
        $byPid[$pidKey] = $process
        $parentKey = "$($process.ParentProcessId)"
        if (-not $childrenByParent.ContainsKey($parentKey)) {
            $childrenByParent[$parentKey] = @()
        }
        $childrenByParent[$parentKey] = @($childrenByParent[$parentKey]) + @($process)
    }

    $pairByParentId = @{}
    $pairedChildIds = @{}
    if ($allowPairing) {
        foreach ($child in $items) {
            $childKey = "$($child.ProcessId)"
            $parentKey = "$($child.ParentProcessId)"
            if (-not $byPid.ContainsKey($parentKey)) { continue }
            $parent = $byPid[$parentKey]

            # Only collapse the proven two-process Windows venv redirector shape.
            if (@($childrenByParent[$parentKey]).Count -ne 1) { continue }
            if ($childrenByParent.ContainsKey($childKey) -and @($childrenByParent[$childKey]).Count -gt 0) { continue }
            if ($byPid.ContainsKey("$($parent.ParentProcessId)")) { continue }
            if ([string]::IsNullOrWhiteSpace("$($parent.ExecutablePath)") -or
                [string]::IsNullOrWhiteSpace("$($child.ExecutablePath)") -or
                $null -eq $parent.CreationDate -or
                $null -eq $child.CreationDate) {
                continue
            }

            try {
                $parentExecutable = [IO.Path]::GetFullPath("$($parent.ExecutablePath)")
                $childExecutable = [IO.Path]::GetFullPath("$($child.ExecutablePath)")
                $parentCreated = [datetime]$parent.CreationDate
                $childCreated = [datetime]$child.CreationDate
            } catch {
                continue
            }
            $parentInvocation = & $invocationKey "$($parent.CommandLine)"
            $childInvocation = & $invocationKey "$($child.CommandLine)"
            $createdSeconds = ($childCreated - $parentCreated).TotalSeconds
            if (-not $parentExecutable.Equals($venvPython, [StringComparison]::OrdinalIgnoreCase) -or
                -not $childExecutable.Equals($basePython, [StringComparison]::OrdinalIgnoreCase) -or
                [string]::IsNullOrWhiteSpace($parentInvocation) -or
                -not $parentInvocation.Equals($childInvocation, [StringComparison]::OrdinalIgnoreCase) -or
                $createdSeconds -lt 0 -or
                $createdSeconds -gt 2) {
                continue
            }

            $pairByParentId[$parentKey] = $child
            $pairedChildIds[$childKey] = $true
        }
    }

    foreach ($process in $items) {
        $pidKey = "$($process.ProcessId)"
        if ($pairedChildIds.ContainsKey($pidKey)) { continue }
        $physicalProcesses = @($process)
        if ($pairByParentId.ContainsKey($pidKey)) {
            $physicalProcesses += $pairByParentId[$pidKey]
        }
        [pscustomobject]@{
            ProcessId = $process.ProcessId
            ParentProcessId = $process.ParentProcessId
            CreationDate = $process.CreationDate
            ExecutablePath = $process.ExecutablePath
            CommandLine = $process.CommandLine
            MemberProcessIds = @($physicalProcesses | Select-Object -ExpandProperty ProcessId)
            PhysicalProcesses = @($physicalProcesses)
            IsRedirectorPair = ($physicalProcesses.Count -eq 2)
        }
    }
}

function Restore-OriginalTaskState {
    param(
        [Parameter(Mandatory=$true)][string]$TaskName,
        [Parameter(Mandatory=$true)][string]$OriginalState,
        [bool]$StartAttempted,
        [bool]$EnabledByScript
    )
    $rollbackErrors = @()
    if ($StartAttempted -or $EnabledByScript) {
        try {
            Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        } catch {
            $rollbackErrors += $_.Exception.Message
        }
    }

    if ($StartAttempted) {
        try {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        } catch {
            $rollbackErrors += $_.Exception.Message
        }
    }

    if ($StartAttempted -or $EnabledByScript) { Start-Sleep -Seconds 1 }
    if ($OriginalState -eq "Ready" -and $StartAttempted) {
        try {
            Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        } catch {
            $rollbackErrors += $_.Exception.Message
        }
    }
    if ($StartAttempted -or $EnabledByScript) { Start-Sleep -Seconds 2 }

    $actualState = "NotFound"
    $remainingBotProcesses = @()
    $processesVerified = $false
    try {
        $actualState = "$(Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Select-Object -ExpandProperty State)"
    } catch {
        $rollbackErrors += $_.Exception.Message
    }
    try {
        $remainingBotProcesses = @(Get-AllBotProcesses)
        $processesVerified = $true
    } catch {
        $rollbackErrors += $_.Exception.Message
    }
    return [pscustomobject]@{
        Succeeded = (
            $rollbackErrors.Count -eq 0 -and
            $actualState -eq $OriginalState -and
            $processesVerified -and
            $remainingBotProcesses.Count -eq 0
        )
        ActualState = $actualState
        BotProcessCount = $remainingBotProcesses.Count
        BotProcessIds = @($remainingBotProcesses | Select-Object -ExpandProperty ProcessId)
        Errors = @($rollbackErrors)
    }
}

if ($env:OS -ne "Windows_NT") {
    Stop-Recovery "This recovery script only supports Windows."
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Fault Closed Loop - Recover After Reboot" -ForegroundColor Cyan
Write-Host "  Project: $Root" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$requiredFiles = @(
    "run_closed_loop.py",
    "start_wecom_ai_bot_live.ps1",
    "scripts\wecom_fault_bot.py",
    ".venv\Scripts\python.exe",
    ".venv\pyvenv.cfg",
    "config\private\feishu.json",
    "config\private\wecom_bot.dpapi.json"
)
$missingFiles = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $Root $_) -PathType Leaf)
})
if ($missingFiles.Count -gt 0) {
    Write-Host "Missing required deployment files:" -ForegroundColor Red
    $missingFiles | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Stop-Recovery "Do not start live. Run START_LOCAL_DEMO.cmd, then repair the deployment locally."
}
Write-Host "[OK] Required deployment files are present." -ForegroundColor Green

$tasks = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
if ($tasks.Count -eq 0) {
    Stop-Recovery "Scheduled task '$TaskName' was not found. Run install_wecom_bot_autostart.ps1 in this folder." 2
}
if ($tasks.Count -ne 1) {
    Stop-Recovery "Found $($tasks.Count) tasks named '$TaskName'. Refusing an ambiguous LIVE start." 2
}
$task = $tasks[0]

if ("$($task.TaskPath)" -ne "\") {
    Stop-Recovery "Task '$TaskName' is not registered at the expected root task path." 2
}

$actions = @($task.Actions)
if ($actions.Count -ne 1) {
    Stop-Recovery "Expected one task action; found $($actions.Count)." 2
}
$action = $actions[0]
$expectedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$expectedLauncher = Join-Path $expectedRoot "start_wecom_ai_bot_live.ps1"
$expectedPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$expectedVenvPython = Join-Path $expectedRoot ".venv\Scripts\python.exe"
$venvConfigPath = Join-Path $expectedRoot ".venv\pyvenv.cfg"
$expectedBasePython = ""
foreach ($line in @(Get-Content -LiteralPath $venvConfigPath -ErrorAction Stop)) {
    if ($line -match '^\s*executable\s*=\s*(.+?)\s*$') {
        $expectedBasePython = $Matches[1].Trim()
        break
    }
}
if ([string]::IsNullOrWhiteSpace($expectedBasePython)) {
    foreach ($line in @(Get-Content -LiteralPath $venvConfigPath -ErrorAction Stop)) {
        if ($line -match '^\s*home\s*=\s*(.+?)\s*$') {
            $expectedBasePython = Join-Path ($Matches[1].Trim()) "python.exe"
            break
        }
    }
}
try {
    $expectedBasePython = [IO.Path]::GetFullPath($expectedBasePython)
} catch {
    Stop-Recovery "Cannot resolve the base Python interpreter from .venv\pyvenv.cfg." 2
}
if (-not (Test-Path -LiteralPath $expectedBasePython -PathType Leaf)) {
    Stop-Recovery "The base Python interpreter recorded by .venv\pyvenv.cfg was not found." 2
}
$expectedArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -NoPrompt' -f $expectedLauncher
$actualRoot = ""
$actualPowerShell = ""
if (-not [string]::IsNullOrWhiteSpace("$($action.WorkingDirectory)")) {
    try { $actualRoot = [IO.Path]::GetFullPath("$($action.WorkingDirectory)").TrimEnd('\') } catch {}
}
if (-not [string]::IsNullOrWhiteSpace("$($action.Execute)")) {
    try { $actualPowerShell = [IO.Path]::GetFullPath("$($action.Execute)") } catch {}
}

if (-not $actualRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not $actualPowerShell.Equals($expectedPowerShell, [StringComparison]::OrdinalIgnoreCase) -or
    -not "$($action.Arguments)".Trim().Equals($expectedArguments, [StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "Task working directory: $($action.WorkingDirectory)" -ForegroundColor Yellow
    Write-Host "Expected directory:     $expectedRoot" -ForegroundColor Yellow
    Stop-Recovery "The task action does not match this deployment. Rebuild it with install_wecom_bot_autostart.ps1 here." 2
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentUser = $identity.Name
$currentSid = $identity.User.Value
$taskUser = "$($task.Principal.UserId)"
$taskSid = Resolve-AccountSid $taskUser
if ([string]::IsNullOrWhiteSpace($taskSid) -or $taskSid -ne $currentSid) {
    Write-Host "Task user:    $taskUser" -ForegroundColor Yellow
    Write-Host "Current user: $currentUser" -ForegroundColor Yellow
    Stop-Recovery "Log in as the task owner, or rebuild the task and DPAPI credentials for this Windows user." 2
}
if ("$($task.Principal.LogonType)" -ne "Interactive" -or "$($task.Principal.RunLevel)" -ne "Limited") {
    Stop-Recovery "The task principal is not the expected Interactive/Limited user task." 2
}

$triggers = @($task.Triggers)
if ($triggers.Count -ne 1) {
    Stop-Recovery "Expected one task trigger; found $($triggers.Count)." 2
}
$logonTriggers = @($triggers | Where-Object {
    $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" -and $_.Enabled -eq $true
})
if ($logonTriggers.Count -ne 1) {
    Stop-Recovery "Expected one enabled AtLogOn trigger; found $($logonTriggers.Count)." 2
}
$triggerSid = Resolve-AccountSid "$($logonTriggers[0].UserId)"
if ([string]::IsNullOrWhiteSpace($triggerSid) -or $triggerSid -ne $currentSid) {
    Stop-Recovery "The AtLogOn trigger belongs to another Windows user." 2
}
if ("$($task.Settings.MultipleInstances)" -ne "IgnoreNew") {
    Stop-Recovery "The task does not have the expected IgnoreNew instance guard." 2
}

$originalState = "$($task.State)"
if ($originalState -notin @("Running", "Ready", "Disabled")) {
    Stop-Recovery "Task state '$originalState' is not safe for automatic recovery." 2
}

try {
    $allBotProcesses = @(Get-AllBotProcesses)
    $allBotInstances = @(Get-LogicalBotInstances -Processes $allBotProcesses -ExpectedVenvPython $expectedVenvPython -ExpectedBasePython $expectedBasePython)
} catch {
    Stop-Recovery $_.Exception.Message 2
}
$existingBotInstances = @($allBotInstances | Where-Object {
    "$($_.CommandLine)".IndexOf($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
})
$otherBotInstances = @($allBotInstances | Where-Object {
    "$($_.CommandLine)".IndexOf($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0
})
$existingLiveInstances = @($existingBotInstances | Where-Object {
    "$($_.CommandLine)".IndexOf("--run-mode live", [StringComparison]::OrdinalIgnoreCase) -ge 0
})
if ($otherBotInstances.Count -gt 0) {
    Write-Host "Other bot instances are already running on this PC:" -ForegroundColor Red
    $otherBotInstances | ForEach-Object {
        Write-Host "  PIDs=$($_.MemberProcessIds -join ' -> ')  Executable=$($_.ExecutablePath)" -ForegroundColor Red
    }
    Stop-Recovery "Stop or identify the other local bot before recovery; its Bot ID cannot be verified safely." 2
}
if ($existingBotInstances.Count -gt 1) {
    Stop-Recovery "Found $($existingBotInstances.Count) logical bot instances for this deployment. Refusing a possible duplicate." 2
}
if ($originalState -ne "Running" -and $existingBotInstances.Count -gt 0) {
    Stop-Recovery "A bot process already exists while the scheduled task is '$originalState'. Refusing to start a second instance." 2
}
if ($originalState -eq "Running" -and $existingBotInstances.Count -eq 1 -and $existingLiveInstances.Count -ne 1) {
    Stop-Recovery "The running bot process is not in LIVE mode." 2
}

$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "[OK] Task structure, path, and Windows user match this deployment." -ForegroundColor Green
Write-Host "     State:          $originalState"
Write-Host "     Bot instances:  $($existingBotInstances.Count)"
Write-Host "     Python processes: $($allBotProcesses.Count)"
if ($existingBotInstances.Count -eq 1) {
    Write-Host "     Member PIDs:    $($existingBotInstances[0].MemberProcessIds -join ' -> ')"
}
Write-Host "     Last run:       $($taskInfo.LastRunTime)"
Write-Host "     Last result:    $(Format-TaskResult $taskInfo.LastTaskResult)"

if ($task.Settings.DisallowStartIfOnBatteries -or $task.Settings.StopIfGoingOnBatteries) {
    Write-Host "[WARN] Connect AC power; this task may not run on battery." -ForegroundColor Yellow
}

if ($CheckOnly) {
    Write-Host "[CHECK ONLY] No task was enabled or started." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "WARNING: continuing starts or verifies the LIVE bot." -ForegroundColor Magenta
Write-Host "It may write Feishu records and send production email." -ForegroundColor Magenta
$answer = (Read-Host "Confirm no other PC or local folder runs this Bot, then type RECOVER").Trim()
if (-not $answer.Equals("RECOVER", [StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "[CANCELLED] Nothing was changed." -ForegroundColor Yellow
    exit 3
}

# Confirmation may remain open for a long time. Re-check immediately before any LIVE mutation.
$confirmedTasks = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
if ($confirmedTasks.Count -ne 1) {
    Stop-Recovery "The scheduled task changed while waiting for confirmation. Nothing was started." 2
}
$confirmedState = "$($confirmedTasks[0].State)"
if ($confirmedState -ne $originalState) {
    Stop-Recovery "Task state changed from '$originalState' to '$confirmedState' while waiting. Nothing was started." 2
}
try {
    $confirmedAllBotProcesses = @(Get-AllBotProcesses)
    $confirmedAllBotInstances = @(Get-LogicalBotInstances -Processes $confirmedAllBotProcesses -ExpectedVenvPython $expectedVenvPython -ExpectedBasePython $expectedBasePython)
} catch {
    Stop-Recovery $_.Exception.Message 2
}
$confirmedExistingBotInstances = @($confirmedAllBotInstances | Where-Object {
    "$($_.CommandLine)".IndexOf($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
})
$confirmedOtherBotInstances = @($confirmedAllBotInstances | Where-Object {
    "$($_.CommandLine)".IndexOf($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0
})
$confirmedLiveBotInstances = @($confirmedExistingBotInstances | Where-Object {
    "$($_.CommandLine)".IndexOf("--run-mode live", [StringComparison]::OrdinalIgnoreCase) -ge 0
})
if ($confirmedOtherBotInstances.Count -gt 0) {
    $confirmedOtherBotInstances | ForEach-Object {
        Write-Host "  PIDs=$($_.MemberProcessIds -join ' -> ')  Executable=$($_.ExecutablePath)" -ForegroundColor Red
    }
    Stop-Recovery "Another local bot appeared while waiting for confirmation. Nothing was started." 2
}
if ($confirmedExistingBotInstances.Count -gt 1) {
    Stop-Recovery "Multiple logical bot instances appeared while waiting for confirmation. Nothing was started." 2
}
if ($originalState -ne "Running" -and $confirmedExistingBotInstances.Count -gt 0) {
    Stop-Recovery "A bot process appeared while waiting for confirmation. Nothing was started." 2
}
if ($originalState -eq "Running" -and $confirmedExistingBotInstances.Count -eq 1 -and $confirmedLiveBotInstances.Count -ne 1) {
    Stop-Recovery "The running bot changed out of LIVE mode while waiting. Nothing was started." 2
}
$task = $confirmedTasks[0]

$logPath = Join-Path $Root ("work\bot\wecom_bot_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$baselineLineCount = 0
if (Test-Path -LiteralPath $logPath -PathType Leaf) {
    $baselineLineCount = @(Get-Content -LiteralPath $logPath -ErrorAction SilentlyContinue).Count
}

$wasRunning = ($originalState -eq "Running")
$enabledByScript = $false
$startAttempted = $false
$startedAt = Get-Date
try {
    if ($originalState -eq "Disabled") {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        $enabledByScript = $true
        Write-Host "[OK] Scheduled task enabled." -ForegroundColor Green
    }
    if (-not $wasRunning) {
        $startAttempted = $true
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "[OK] Scheduled task start requested." -ForegroundColor Green
    } else {
        Write-Host "[OK] Scheduled task was already running; it was not restarted." -ForegroundColor Green
    }
} catch {
    $startError = $_.Exception.Message
    $rollback = Restore-OriginalTaskState $TaskName $originalState $startAttempted $enabledByScript
    if ($rollback.Succeeded) {
        Write-Host "[ROLLBACK] Verified task state '$originalState' and no bot process remains." -ForegroundColor Yellow
    } else {
        Write-Host "[UNVERIFIED] Rollback did not prove the original state. Current state: $($rollback.ActualState)." -ForegroundColor Red
        Write-Host "             Remaining bot processes: $($rollback.BotProcessCount)." -ForegroundColor Red
        $rollback.Errors | ForEach-Object { Write-Host "             $_" -ForegroundColor Red }
    }
    $rollbackSummary = if ($rollback.Succeeded) { "The attempted start was rolled back." } else { "Rollback could not be verified." }
    Stop-Recovery "Could not start the task. $rollbackSummary $startError" 4
}

$deadline = $startedAt.AddSeconds($WaitSeconds)
$healthy = $false
$latestLog = $null
$interestingLines = @()
$botInstances = @()
$liveBotInstances = @()
$processCheckError = ""

try {
    do {
        Start-Sleep -Seconds 2
        $task = Get-ScheduledTask -TaskName $TaskName
        $latestLog = Get-Item -LiteralPath $logPath -ErrorAction SilentlyContinue

        if ($latestLog -and $latestLog.LastWriteTime -ge $startedAt.AddSeconds(-2)) {
            $allLines = @(Get-Content -LiteralPath $latestLog.FullName -ErrorAction SilentlyContinue)
            $freshLines = @($allLines | Select-Object -Skip $baselineLineCount | Select-Object -Last 400)
            $connected = [bool]($freshLines -match "WeCom bot websocket connected")
            $authenticated = [bool]($freshLines -match "WeCom bot authenticated")
            $heartbeat = [bool]($freshLines -match "Received heartbeat ack")
            $interestingLines = @($freshLines | Select-String -Pattern "WeCom bot websocket connected", "WeCom bot authenticated", "Received heartbeat ack", "WeCom bot error", "websocket_receive_loop_stopped", "getaddrinfo failed", "Traceback", "Missing dependency" | Select-Object -Last 20)
            $freshHealth = if ($wasRunning) { $heartbeat } else { $connected -and $authenticated -and $heartbeat }
            if ("$($task.State)" -eq "Running" -and $freshHealth) {
                try {
                    $allBotProcesses = @(Get-AllBotProcesses)
                    $allBotInstances = @(Get-LogicalBotInstances -Processes $allBotProcesses -ExpectedVenvPython $expectedVenvPython -ExpectedBasePython $expectedBasePython)
                    $botInstances = @($allBotInstances | Where-Object {
                        "$($_.CommandLine)".IndexOf($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
                    })
                    $otherBotInstances = @($allBotInstances | Where-Object {
                        "$($_.CommandLine)".IndexOf($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0
                    })
                    $liveBotInstances = @($botInstances | Where-Object {
                        "$($_.CommandLine)".IndexOf("--run-mode live", [StringComparison]::OrdinalIgnoreCase) -ge 0
                    })
                    $processCheckError = ""
                } catch {
                    $botInstances = @()
                    $liveBotInstances = @()
                    $processCheckError = $_.Exception.Message
                }
                if ($allBotInstances.Count -eq 1 -and $botInstances.Count -eq 1 -and $liveBotInstances.Count -eq 1 -and $otherBotInstances.Count -eq 0) {
                    $healthy = $true
                    break
                }
            }
        }
    } while ((Get-Date) -lt $deadline)
} catch {
    $processCheckError = "Health check failed: $($_.Exception.Message)"
}

if ($healthy) {
    Write-Host "[DONE] Bot is running, authenticated, and receiving heartbeat acknowledgements." -ForegroundColor Green
    Write-Host "       Member PIDs: $($liveBotInstances[0].MemberProcessIds -join ' -> ')" -ForegroundColor Green
    Write-Host "       Log: $($latestLog.FullName)" -ForegroundColor Green
    exit 0
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$finalState = if ($task) { "$($task.State)" } else { "NotFound" }
$finalResult = "Unavailable"
try {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    $finalResult = Format-TaskResult $taskInfo.LastTaskResult
} catch {}
Write-Host "[FAILED] Health markers were not confirmed within $WaitSeconds seconds." -ForegroundColor Red
Write-Host "         Task state:   $finalState"
Write-Host "         Last result: $finalResult"
if ($latestLog) {
    Write-Host "         Latest log:  $($latestLog.FullName)"
    $interestingLines | ForEach-Object { Write-Host "         $($_.Line)" }
} else {
    Write-Host "         No current-day bot log was created." -ForegroundColor Red
}
if ($processCheckError) {
    Write-Host "         Process check failed: $processCheckError" -ForegroundColor Red
} elseif ($botInstances.Count -ne 1 -or $liveBotInstances.Count -ne 1) {
    Write-Host "         Expected one matching LIVE bot instance; found $($liveBotInstances.Count)." -ForegroundColor Red
}

$rollback = $null
if ($startAttempted -or $enabledByScript) {
    Write-Host "[ROLLBACK] Restoring the original task state: $originalState." -ForegroundColor Yellow
    $rollback = Restore-OriginalTaskState $TaskName $originalState $startAttempted $enabledByScript
    if ($rollback.Succeeded) {
        Write-Host "[ROLLBACK] Verified task state '$($rollback.ActualState)' and no bot process remains." -ForegroundColor Yellow
    } else {
        Write-Host "[UNVERIFIED] Rollback did not prove the original state. Current state: $($rollback.ActualState)." -ForegroundColor Red
        Write-Host "             Remaining bot processes: $($rollback.BotProcessCount)." -ForegroundColor Red
        $rollback.Errors | ForEach-Object { Write-Host "             $_" -ForegroundColor Red }
        Write-Host "             The task may restart LIVE or start at the next Windows logon." -ForegroundColor Red
    }
}
if ($wasRunning) {
    Write-Host "[UNVERIFIED] The task was already running and was not stopped by this script." -ForegroundColor Yellow
}
Write-Host "Review the current task state and today's bot log. Recreate local DPAPI credentials only if the launcher reports it cannot read live configuration." -ForegroundColor Yellow
exit 4
