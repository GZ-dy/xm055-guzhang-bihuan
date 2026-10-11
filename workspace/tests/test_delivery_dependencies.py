import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeliveryDependencyTests(unittest.TestCase):
    def test_base_requirements_declares_docx_runtime(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8-sig")
        self.assertIn("python-docx", requirements)

    def test_portable_builder_includes_wecom_requirements(self):
        script = (ROOT / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")
        required = script.split("$required =", 1)[1].split("foreach ($item", 1)[0]
        self.assertIn('"requirements-wecom.txt"', required)
        self.assertIn('"scripts"', required)

    def test_installer_prefers_wheelhouse_then_falls_back_online(self):
        script = (ROOT / "install_env.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("--no-index --find-links", script)
        self.assertIn("trying online index", script)
        self.assertLess(script.index("--no-index --find-links"), script.index("trying online index"))
        self.assertIn("Dependency install failed", script)
        self.assertIn('Join-Path $ScriptDir "requirements.txt"', script)
        self.assertIn('Join-Path $ScriptDir "requirements-wecom.txt"', script)


if __name__ == "__main__":
    unittest.main()
