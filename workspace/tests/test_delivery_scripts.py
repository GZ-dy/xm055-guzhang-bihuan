import base64
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


class DeliveryScriptsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def test_batch_files_delegate_to_matching_powershell_scripts(self):
        for stem in (
            "install_env",
            "validate_package",
            "run_local_demo",
            "NEW_PC_SETUP",
            "INSTALL_DEPENDENCIES",
        ):
            batch = (self.root / f"{stem}.bat").read_text(encoding="utf-8-sig").lower()
            self.assertIn(f"%~dp0{stem.lower()}.ps1", batch)
            self.assertIn("exit /b", batch)

    def test_new_pc_setup_rebuilds_local_credentials_without_shipping_them(self):
        script = (self.root / "NEW_PC_SETUP.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("install_env.ps1", script)
        self.assertIn("feishu.template.json", script)
        self.assertIn("-AsSecureString", script)
        self.assertIn("ZeroFreeBSTR", script)
        self.assertIn("install_wecom_bot_autostart.ps1", script)
        self.assertIn("SkipDependencyInstall", script)
        self.assertNotIn("wecom_bot.dpapi.json", script)

    def test_one_click_new_pc_runs_dependencies_then_setup(self):
        batch_path = self.root / "ONE_CLICK_NEW_PC.bat"
        batch = batch_path.read_text(encoding="ascii").lower()
        self.assertIn("run_closed_loop.py", batch)
        self.assertIn("install_dependencies.ps1", batch)
        self.assertIn("new_pc_setup.ps1", batch)
        self.assertIn("-skipdependencyinstall", batch)
        self.assertIn("install_wecom_bot_autostart.ps1", batch)
        self.assertIn("pause", batch)
        self.assertTrue(batch_path.read_bytes().isascii())

    def test_keep_open_launcher_uses_cmd_k_and_ascii_only(self):
        launcher_path = self.root / "RUN_KEEP_OPEN.cmd"
        launcher = launcher_path.read_text(encoding="ascii").lower()
        self.assertIn("cmd.exe /d /k", launcher)
        self.assertIn("one_click_new_pc.bat", launcher)
        self.assertTrue(launcher_path.read_bytes().isascii())

    def test_recovery_launcher_delegates_and_keeps_result_visible(self):
        launcher_path = self.root / "RECOVER_AFTER_REBOOT.cmd"
        launcher = launcher_path.read_text(encoding="ascii").lower()
        self.assertIn("%~dp0recover_after_reboot.ps1", launcher)
        self.assertIn("-executionpolicy bypass", launcher)
        self.assertIn("%*", launcher)
        self.assertIn("errorlevel", launcher)
        self.assertIn("pause", launcher)
        self.assertTrue(launcher_path.read_bytes().isascii())

    def test_recovery_script_is_guarded_and_never_reinstalls(self):
        script_path = self.root / "recover_after_reboot.ps1"
        script = script_path.read_text(encoding="utf-8-sig")
        for required in (
            "[switch]$CheckOnly",
            "$PSScriptRoot",
            "Get-ScheduledTask",
            "Get-ScheduledTaskInfo",
            "Get-CimInstance Win32_Process",
            "Get-LogicalBotInstances",
            "ParentProcessId",
            "CreationDate",
            "MemberProcessIds",
            "PhysicalProcesses",
            ".venv\\pyvenv.cfg",
            "Enable-ScheduledTask",
            "Start-ScheduledTask",
            "Stop-ScheduledTask",
            "Disable-ScheduledTask",
            "WeCom bot websocket connected",
            "WeCom bot authenticated",
            "Received heartbeat ack",
            "baselineLineCount",
            'Read-Host "Confirm no other PC or local folder runs this Bot, then type RECOVER"',
        ):
            self.assertIn(required, script)
        check_only_index = script.index("if ($CheckOnly)")
        confirmation_index = script.index("then type RECOVER")
        post_confirmation_check_index = script.index(
            "$confirmedAllBotProcesses = @(Get-AllBotProcesses)"
        )
        live_mutation_index = script.index('if ($originalState -eq "Disabled")')
        self.assertLess(check_only_index, confirmation_index)
        self.assertLess(confirmation_index, post_confirmation_check_index)
        self.assertLess(post_confirmation_check_index, live_mutation_index)

        rollback_start = script.index("function Restore-OriginalTaskState")
        rollback_end = script.index('if ($env:OS -ne "Windows_NT")')
        rollback = script[rollback_start:rollback_end]
        self.assertLess(rollback.index("Disable-ScheduledTask"), rollback.index("Stop-ScheduledTask"))
        self.assertLess(rollback.index("Stop-ScheduledTask"), rollback.index("Enable-ScheduledTask"))
        self.assertIn("Get-AllBotProcesses", rollback)
        self.assertNotIn("Get-LogicalBotInstances", rollback)
        self.assertIn("$remainingBotProcesses.Count -eq 0", rollback)
        self.assertGreaterEqual(script.count("Get-LogicalBotInstances"), 4)
        self.assertNotIn("$allBotProcesses.Count -eq 1", script)
        for forbidden in (
            "Register-ScheduledTask",
            "Unregister-ScheduledTask",
            "Stop-Process",
            "ConvertFrom-SecureString",
            "FAULT_CONFIRM_LIVE_WRITE",
            "--apply",
            "--send-mail",
        ):
            self.assertNotIn(forbidden, script)

    def test_recovery_groups_only_proven_venv_redirector_pairs(self):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is unavailable")

        program = r'''
$ErrorActionPreference = "Stop"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:RECOVERY_SCRIPT_PATH,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) { throw ($errors -join "`n") }
$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq "Get-LogicalBotInstances"
}, $true)
if ($null -eq $functionAst) { throw "Get-LogicalBotInstances was not found" }
Invoke-Expression $functionAst.Extent.Text

function New-ProcessFixture {
    param(
        [int]$ProcessId,
        [int]$ParentProcessId,
        [datetime]$CreationDate,
        [string]$ExecutablePath,
        [string]$CommandLine
    )
    [pscustomobject]@{
        ProcessId = $ProcessId
        ParentProcessId = $ParentProcessId
        CreationDate = $CreationDate
        ExecutablePath = $ExecutablePath
        CommandLine = $CommandLine
    }
}

$venv = 'D:\app\.venv\Scripts\python.exe'
$base = 'C:\Python313\python.exe'
$otherBase = 'C:\OtherPython\python.exe'
$command = '"D:\app\.venv\Scripts\python.exe" -u -B scripts\wecom_fault_bot.py --case-prefix wecom --run-mode live'
$created = [datetime]'2026-08-11T16:06:44'

$parent1 = New-ProcessFixture 100 5000 $created $venv $command
$child1 = New-ProcessFixture 101 100 $created $base $command
$singleRaw = @($parent1, $child1)
$single = @(Get-LogicalBotInstances $singleRaw $venv $base)

$parent2 = New-ProcessFixture 200 6000 $created $venv $command
$child2 = New-ProcessFixture 201 200 $created.AddSeconds(1) $base $command
$twoRoots = @(Get-LogicalBotInstances @($parent1, $child1, $parent2, $child2) $venv $base)

$orphan1 = New-ProcessFixture 300 7000 $created $venv $command
$orphan2 = New-ProcessFixture 301 8000 $created $venv $command
$unrelated = @(Get-LogicalBotInstances @($orphan1, $orphan2) $venv $base)

$branchParent = New-ProcessFixture 400 9000 $created $venv $command
$branchChild1 = New-ProcessFixture 401 400 $created $base $command
$branchChild2 = New-ProcessFixture 402 400 $created $base $command
$branch = @(Get-LogicalBotInstances @($branchParent, $branchChild1, $branchChild2) $venv $base)

$wrongBaseChild = New-ProcessFixture 501 100 $created $otherBase $command
$wrongBase = @(Get-LogicalBotInstances @($parent1, $wrongBaseChild) $venv $base)

$lateChild = New-ProcessFixture 601 100 $created.AddSeconds(3) $base $command
$late = @(Get-LogicalBotInstances @($parent1, $lateChild) $venv $base)

$directOrphan = New-ProcessFixture 701 12345 $created $base $command
$pairAndOrphan = @(Get-LogicalBotInstances @($parent1, $child1, $directOrphan) $venv $base)

[ordered]@{
    single_physical = $singleRaw.Count
    single_logical = $single.Count
    single_member_pids = ($single[0].MemberProcessIds -join ',')
    two_roots_logical = $twoRoots.Count
    unrelated_same_command_logical = $unrelated.Count
    ambiguous_branch_logical = $branch.Count
    wrong_base_logical = $wrongBase.Count
    late_child_logical = $late.Count
    pair_and_orphan_logical = $pairAndOrphan.Count
} | ConvertTo-Json -Compress
'''
        encoded = base64.b64encode(program.encode("utf-16le")).decode("ascii")
        env = os.environ.copy()
        env["RECOVERY_SCRIPT_PATH"] = str(self.root / "recover_after_reboot.ps1")
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(evidence["single_physical"], 2)
        self.assertEqual(evidence["single_logical"], 1)
        self.assertEqual(evidence["single_member_pids"], "100,101")
        self.assertEqual(evidence["two_roots_logical"], 2)
        self.assertEqual(evidence["unrelated_same_command_logical"], 2)
        self.assertEqual(evidence["ambiguous_branch_logical"], 3)
        self.assertEqual(evidence["wrong_base_logical"], 2)
        self.assertEqual(evidence["late_child_logical"], 2)
        self.assertEqual(evidence["pair_and_orphan_logical"], 2)

    def test_safe_local_demo_launcher_never_enters_live_setup(self):
        launcher_path = self.root / "START_LOCAL_DEMO.cmd"
        launcher = launcher_path.read_text(encoding="ascii").lower()
        self.assertIn("install_dependencies.ps1", launcher)
        self.assertIn("validate_package.ps1", launcher)
        self.assertIn("pause", launcher)
        for forbidden in (
            "new_pc_setup",
            "install_wecom_bot_autostart",
            "start_wecom_ai_bot_live",
            "--apply",
            "--send-mail",
            "live-write",
        ):
            self.assertNotIn(forbidden, launcher)
        self.assertTrue(launcher_path.read_bytes().isascii())

    def test_dependency_installer_bootstraps_python_and_wecom_runtime_only(self):
        script = (self.root / "INSTALL_DEPENDENCIES.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Python.Python.3.13", script)
        self.assertIn("winget.exe", script)
        self.assertIn("python.org/ftp/python/3.13.14", script)
        self.assertIn("Invoke-WebRequest", script)
        self.assertIn("Start-BitsTransfer", script)
        self.assertIn("C54D9B9BBB8A36E6489363DDD01139707FD781D72F1F9E90C7EC65D0061368E0", script)
        self.assertIn('.venv', script)
        self.assertIn('requirements-wecom.txt', script)
        for module in ("openpyxl", "docx", "PIL", "pytesseract", "cryptography", "aibot"):
            self.assertIn(module, script)
        for forbidden in ("feishu.json", "wecom_bot.dpapi.json", "outputs", "workspace_migration"):
            self.assertNotIn(forbidden, script)
        self.assertIn("Start-Transcript", script)
        self.assertIn("dependency_install.log", script)
        self.assertIn("START_LOCAL_DEMO.cmd", script)
        self.assertIn("HANDOVER_OPERATIONS.md", script)

    def test_dependency_batch_keeps_errors_visible_and_checks_extraction(self):
        batch_path = self.root / "INSTALL_DEPENDENCIES.bat"
        batch = batch_path.read_text(encoding="ascii").lower()
        self.assertIn("run_closed_loop.py", batch)
        self.assertIn("install_dependencies.ps1", batch)
        self.assertIn("dependency_install.log", batch)
        self.assertIn("pause", batch)
        self.assertTrue(batch_path.read_bytes().isascii())

    def test_wecom_batch_delegates_without_leaving_project_root(self):
        batch = (self.root / "start_wecom_ai_bot_dry_run.bat").read_text(encoding="ascii").lower()
        self.assertIn('%~dp0start_wecom_ai_bot_dry_run.ps1', batch)
        self.assertIn("-executionpolicy bypass", batch)
        self.assertNotIn('%~dp0\\..', batch)
        self.assertNotIn("scripts\\wecom_fault_bot.py", batch)

    def test_dry_run_launcher_prompts_for_missing_bot_credentials_securely(self):
        script = (self.root / "start_wecom_ai_bot_dry_run.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Read-Host "请输入企业微信智能机器人的 Bot ID"', script)
        self.assertIn("-AsSecureString", script)
        self.assertIn("ZeroFreeBSTR", script)
        self.assertIn("Remove-Item Env:WECHAT_BOT_SECRET", script)

    def test_wecom_autostart_uses_dpapi_and_runs_live(self):
        launcher = (self.root / "start_wecom_ai_bot_live.ps1").read_text(encoding="utf-8-sig")
        installer = (self.root / "install_wecom_bot_autostart.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("ConvertTo-SecureString", launcher)
        self.assertIn("ConvertFrom-SecureString", installer)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", installer)
        self.assertIn("start_wecom_ai_bot_live.ps1", installer)
        self.assertIn("live_write_enabled = $true", installer)
        self.assertIn("smtp_password_dpapi", installer)
        self.assertIn("FAULT_SMTP_PASSWORD", launcher)
        self.assertIn("SMTP 发件邮箱 + 授权码已设", launcher)
        self.assertIn('Join-Path $ProjectRoot "work\\bot"', launcher)
        self.assertIn("wecom_bot_{0}.log", launcher)
        self.assertIn("*>> $logPath", launcher)
        self.assertIn('$ErrorActionPreference = "Continue"', launcher)

    def test_bot_diagnostics_support_modern_windows(self):
        script = (self.root / "diagnose_bot.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Get-CimInstance Win32_Process", script)
        self.assertIn("$ScriptDir\\outputs\\reports", script)
        self.assertNotIn("D:\\fault-closed-loop", script)

    def test_stop_bot_supports_modern_windows(self):
        script = (self.root / "stop_bot.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Get-CimInstance Win32_Process", script)
        self.assertNotIn("& wmic", script)
        self.assertIn("foreach ($botPid in $botPids)", script)

    def test_validate_runs_discovery_then_offline_demo(self):
        script = (self.root / "validate_package.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Join-Path $Root ".venv\\Scripts\\python.exe"', script)
        self.assertNotIn("Get-Command python", script)
        self.assertIn("-B -m unittest discover -s tests", script)
        self.assertIn('Join-Path $Root "run_local_demo.ps1"', script)

    def test_demo_is_two_step_and_cannot_enable_live_actions(self):
        script = (self.root / "run_local_demo.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Join-Path $Root ".venv\\Scripts\\python.exe"', script)
        self.assertNotIn("Get-Command python", script)
        self.assertIn("--incident-context-only", script)
        self.assertIn("--all-dry-run", script)
        self.assertNotIn("--apply", script)
        self.assertNotIn("--send-mail", script)
        for name in (
            "FAULT_AUTO_SEND_ON_VALIDATED",
            "FAULT_CONFIRM_LIVE_WRITE",
            "FAULT_PROD_CASE_TOKEN",
        ):
            self.assertIn(f"Remove-Item Env:{name}", script)
        for group in ("reports", "ledger", "mail", "manuals", "validation"):
            self.assertIn(f'outputs\\{group}\\$CaseId', script)

    def test_installer_targets_project_venv_directly(self):
        script = (self.root / "install_env.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Join-Path $ScriptDir ".venv\\Scripts\\python.exe"', script)
        self.assertIn("& $VenvPython -m pip install", script)
        self.assertNotIn("Activate.ps1", script)

    def test_powershell_delivery_scripts_have_utf8_bom(self):
        for name in (
            "validate_package.ps1",
            "run_local_demo.ps1",
            "NEW_PC_SETUP.ps1",
            "INSTALL_DEPENDENCIES.ps1",
            "recover_after_reboot.ps1",
        ):
            self.assertTrue((self.root / name).read_bytes().startswith(b"\xef\xbb\xbf"), name)


if __name__ == "__main__":
    unittest.main()
