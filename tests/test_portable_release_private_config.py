import unittest
from pathlib import Path


class PortableReleasePrivateConfigTests(unittest.TestCase):
    def test_private_config_is_an_explicit_file_not_a_recursive_directory_copy(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools/build_portable_release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('Join-Path $Root "config\\private\\feishu.json"', script)
        self.assertIn('Join-Path $OutDir "config\\private\\feishu.json"', script)
        self.assertNotIn(
            'Copy-Existing (Join-Path $Root "config\\private") (Join-Path $OutDir "config\\private")',
            script,
        )


if __name__ == "__main__":
    unittest.main()
