import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.pipeline import RunOptions, _paths, run


class CaseIdPathGuardTests(unittest.TestCase):
    def test_rejects_unsafe_case_ids(self):
        root = Path(tempfile.gettempdir()) / "fault-case-id-guard"
        unsafe = ("", "   ", ".", "..", "../src", r"..\src", "/tmp/src", r"C:\src")

        for case_id in unsafe:
            with self.subTest(case_id=case_id), self.assertRaises(ValueError):
                _paths(root, case_id)

    def test_valid_case_id_keeps_existing_layout(self):
        root = Path(tempfile.gettempdir()) / "fault-case-id-guard"

        paths = _paths(root, "bomc_smoke_20260709")

        self.assertEqual(paths["case"], root / "work" / "cases" / "bomc_smoke_20260709")
        self.assertEqual(paths["report"], root / "outputs" / "reports" / "bomc_smoke_20260709")

    def test_run_rejects_context_case_id_before_output_reset(self):
        context = {"meta": {"case_id": "../../src"}}
        with (
            patch("src.orchestrator.pipeline._load_or_build_context", return_value=context),
            patch("src.orchestrator.pipeline._ensure_runtime_meta"),
            patch("src.orchestrator.pipeline._reset_outputs") as reset_outputs,
            self.assertRaises(ValueError),
        ):
            run(Path(tempfile.gettempdir()), RunOptions())

        reset_outputs.assert_not_called()

    def test_run_rejects_cli_case_id_before_output_reset(self):
        with (
            patch("src.orchestrator.pipeline._reset_outputs") as reset_outputs,
            self.assertRaises(ValueError),
        ):
            run(Path(tempfile.gettempdir()), RunOptions(text="BOMC故障", case_id="../src"))

        reset_outputs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
