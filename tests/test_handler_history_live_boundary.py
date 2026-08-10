import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.pipeline import RunOptions, _fill_handler_from_previous_system, run
from tests.test_minimal_closed_loop import _case_root, _validated_context


class HandlerHistoryLiveBoundaryTests(unittest.TestCase):
    def test_all_dry_run_does_not_read_feishu(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_dry_handler_boundary")
            with (
                patch("src.orchestrator.pipeline.load_feishu_config") as load_config,
                patch("src.orchestrator.pipeline.list_records") as list_records,
            ):
                result = run(root, RunOptions(incident_context=context_path, all_dry_run=True))

        self.assertEqual(result["status"], "validated")
        load_config.assert_not_called()
        list_records.assert_not_called()

    def test_confirmed_live_apply_reads_feishu_history(self):
        records = [
            {
                "fields": {
                    "系统名称": "BOMC",
                    "处理人员": "李四",
                    "故障发生时间": 1,
                }
            }
        ]
        payload = {"fields": {"系统名称": "BOMC", "处理人员": "多域维护组"}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.orchestrator.pipeline.load_feishu_config", return_value=object()) as load_config,
            patch("src.orchestrator.pipeline.list_records", return_value=records) as list_records,
        ):
            _fill_handler_from_previous_system(
                Path(tmp),
                RunOptions(apply=True, confirm_live_write="LIVE-WRITE"),
                {"handling": {"handling_group": "多域维护组"}},
                payload,
            )

        load_config.assert_called_once()
        list_records.assert_called_once()
        self.assertEqual(payload["fields"]["处理人员"], "李四")


if __name__ == "__main__":
    unittest.main()
