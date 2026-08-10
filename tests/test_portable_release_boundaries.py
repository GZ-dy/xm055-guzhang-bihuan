import unittest
from pathlib import Path


class PortableReleaseBoundaryTests(unittest.TestCase):
    def test_builder_excludes_hermes_webhook_helper(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('"scripts\\post_to_hermes_webhook.py"', script)
        self.assertIn("Remove-Item -LiteralPath $hermesWebhook -Force", script)

    def test_builder_excludes_backup_and_temporary_manual_indexes(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('$portableIndexPath = Join-Path $OutDir "data\\reference\\manuals_index\\manual_index.json"', script)
        for pattern in ("*.bak", "*.bak-*", "*.tmp", "*.temp", "*~"):
            self.assertIn(f'$_.Name -like "{pattern}"', script)
        self.assertIn("} | Remove-Item -Force", script)

    def test_builder_excludes_all_backups_and_historical_ledger_snapshots(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('Join-Path $OutDir "data\\reference\\ledger_snapshots"', script)
        for pattern in ("*.bak", "*.bak-*", "*.backup", "*.backup-*", "*.tmp", "*.temp", "*~"):
            self.assertIn(f'$_.Name -like "{pattern}"', script)

    def test_builder_ships_current_wecom_entrypoints_without_contact_backups(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")

        for name in (
            "start_wecom_ai_bot_dry_run.ps1",
            "start_wecom_ai_bot_dry_run.bat",
            "start_wecom_ai_bot_live.ps1",
            "install_wecom_bot_autostart.ps1",
            "setup_env.ps1",
            "verify_env.ps1",
            "diagnose_bot.ps1",
            "stop_bot.ps1",
        ):
            self.assertIn(f'"{name}"', script)
        self.assertIn('data\\reference\\contacts', script)
        self.assertIn('config/private/wecom_bot.env.template', script)
        self.assertIn('$_.Name -like "*.bak-*"', script)
        self.assertTrue((root / "data/reference/contacts/多域系统告警通报联系人-仅姓名版.xlsx").is_file())


    def test_builder_ships_one_click_new_pc_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")

        for name in (
            "INSTALL_DEPENDENCIES.ps1",
            "INSTALL_DEPENDENCIES.bat",
            "NEW_PC_SETUP.ps1",
            "NEW_PC_SETUP.bat",
            "ONE_CLICK_NEW_PC.bat",
            "RUN_KEEP_OPEN.cmd",
            "README_DEPENDENCIES.md",
        ):
            self.assertIn(f'"{name}"', script)
        self.assertNotIn('"MIGRATE_TO_NEW_PC.md"', script)


if __name__ == "__main__":
    unittest.main()
