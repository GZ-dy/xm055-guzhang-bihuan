import unittest
from pathlib import Path
from unittest.mock import patch

from src.feishu_ledger.client import (
    FeishuApiError,
    FeishuConfigError,
    find_record_by_case_id,
    find_record_by_system_time,
)
from src.orchestrator.pipeline import RunOptions, _write_feishu_record


class _Config:
    field_map = {"case_id": "闭环ID"}
    table_url = "https://example.invalid/table"


class FeishuDedupeFailClosedTests(unittest.TestCase):
    def test_lookup_errors_are_not_treated_as_misses(self):
        config = _Config()
        for error_type in (FeishuApiError, FeishuConfigError):
            with self.subTest(error_type=error_type.__name__), patch(
                "src.feishu_ledger.client.list_records", side_effect=error_type("read failed")
            ):
                with self.assertRaisesRegex(error_type, "read failed"):
                    find_record_by_system_time("BOMC", "2026-07-14 12:00:00", config)
                with self.assertRaisesRegex(error_type, "read failed"):
                    find_record_by_case_id("case-1", config)

    def test_pipeline_blocks_create_when_dedupe_read_fails(self):
        config = _Config()
        with (
            patch("src.orchestrator.pipeline.load_feishu_config", return_value=config),
            patch("src.feishu_ledger.client.list_records", side_effect=FeishuApiError("read failed")),
            patch("src.orchestrator.pipeline.create_record") as create_record,
        ):
            result = _write_feishu_record(
                Path.cwd(),
                RunOptions(),
                {"case_id": "case-1", "fields": {}},
            )

        self.assertEqual(result["status"], "失败")
        self.assertIn("read failed", result["blocked_reason"])
        create_record.assert_not_called()

    def test_normal_miss_and_case_id_ambiguity_are_unchanged(self):
        config = _Config()
        with patch("src.feishu_ledger.client.list_records", return_value=[]):
            self.assertEqual(find_record_by_system_time("BOMC", "2026-07-14 12:00:00", config), {})
            self.assertEqual(find_record_by_case_id("case-1", config), {})

        duplicate = {"record_id": "rec", "fields": {"闭环ID": "case-1"}}
        with patch("src.feishu_ledger.client.list_records", return_value=[duplicate, duplicate]):
            result = find_record_by_case_id("case-1", config)
        self.assertEqual(result["record_id"], "")
        self.assertIn("dedupe ambiguous", result["blocked_reason"])

    def test_system_time_ambiguity_is_blocked(self):
        config = _Config()
        duplicate = {
            "record_id": "rec",
            "fields": {"系统名称": "BOMC", "故障发生时间": 1784001600000},
        }
        with patch("src.feishu_ledger.client.list_records", return_value=[duplicate, duplicate]):
            result = find_record_by_system_time("BOMC", 1784001600000, config)
        self.assertEqual(result["record_id"], "")
        self.assertIn("dedupe ambiguous", result["blocked_reason"])


if __name__ == "__main__":
    unittest.main()
