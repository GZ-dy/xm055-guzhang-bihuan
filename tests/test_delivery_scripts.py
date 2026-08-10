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
        ):
            self.assertTrue((self.root / name).read_bytes().startswith(b"\xef\xbb\xbf"), name)


if __name__ == "__main__":
    unittest.main()
