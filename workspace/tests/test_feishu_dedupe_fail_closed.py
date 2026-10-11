import unittest
from pathlib import Path
from unittest.mock import patch

from src.feishu_ledger.client import (
    FeishuApiError,
    FeishuConfigError,
    create_record,
    find_record_by_case_id,
    find_record_by_system_time,
)
from src.orchestrator.pipeline import (
    RunOptions,
    _compute_next_sequence,
    _reorder_feishu_sequence_by_time,
    _resolve_feishu_target,
    _validate_feishu_case_id_field,
    _write_feishu_record,
)


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
            patch(
                "src.orchestrator.pipeline.list_fields",
                return_value=[{"field_name": "闭环ID", "type": 1}],
            ),
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

    def test_case_id_field_is_required_before_live_write(self):
        config = _Config()
        with patch(
            "src.orchestrator.pipeline.list_fields",
            return_value=[{"field_name": "系统名称", "type": 1}],
        ):
            with self.assertRaisesRegex(FeishuConfigError, "required case_id field"):
                _validate_feishu_case_id_field(config, "case-1")

    def test_create_record_never_posts_without_case_id_field(self):
        config = _Config()
        with (
            patch("src.feishu_ledger.client._tenant_access_token", return_value="local-test-token"),
            patch(
                "src.feishu_ledger.client.list_fields",
                return_value=[{"field_name": "系统名称", "type": 1}],
            ),
            patch("src.feishu_ledger.client._request_json") as request_json,
        ):
            result = create_record(
                {"case_id": "case-1", "fields": {"case_id": "case-1", "系统名称": "BOMC"}},
                config,
            )

        self.assertEqual(result["status"], "失败")
        self.assertFalse(result["api_called"])
        self.assertIn("required case_id field", result["blocked_reason"])
        request_json.assert_not_called()

    def test_explicit_record_id_cannot_target_another_case(self):
        config = _Config()
        with (
            patch("src.orchestrator.pipeline.load_feishu_config", return_value=config),
            patch(
                "src.orchestrator.pipeline.list_fields",
                return_value=[{"field_name": "闭环ID", "type": 1}],
            ),
            patch(
                "src.orchestrator.pipeline.get_record",
                return_value={"record_id": "rec-other", "fields": {"闭环ID": "case-other"}},
            ),
        ):
            result = _resolve_feishu_target(
                Path.cwd(),
                RunOptions(feishu_record_id="rec-other"),
                {"case_id": "case-current", "fields": {"case_id": "case-current"}},
            )

        self.assertEqual(result["status"], "failed")
        self.assertIn("belongs to case_id='case-other'", result["blocked_reason"])

    def test_explicit_record_id_can_backfill_empty_case_id(self):
        config = _Config()
        with (
            patch("src.orchestrator.pipeline.load_feishu_config", return_value=config),
            patch(
                "src.orchestrator.pipeline.list_fields",
                return_value=[{"field_name": "闭环ID", "type": 1}],
            ),
            patch(
                "src.orchestrator.pipeline.get_record",
                return_value={"record_id": "rec-legacy", "fields": {"闭环ID": ""}},
            ),
        ):
            result = _resolve_feishu_target(
                Path.cwd(),
                RunOptions(feishu_record_id="rec-legacy"),
                {"case_id": "case-current", "fields": {"case_id": "case-current"}},
            )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["record_id"], "rec-legacy")

    def test_sequence_uses_mapped_case_id_field(self):
        config = _Config()
        same_time = "2026-08-12 10:00"
        records = [
            {"record_id": "rec-c", "fields": {"故障发生时间": same_time, "闭环ID": "case-c"}},
            {"record_id": "rec-a", "fields": {"故障发生时间": same_time, "闭环ID": "case-a"}},
        ]
        with patch("src.orchestrator.pipeline.list_records", return_value=records):
            position = _compute_next_sequence(
                config,
                {"case_id": "case-b", "fields": {"故障发生时间": same_time}},
            )

        self.assertEqual(position, 2)

    def test_reorder_uses_mapped_case_id_field(self):
        config = _Config()
        same_time = "2026-08-12 10:00"
        state = {
            "rec-b": {"record_id": "rec-b", "fields": {"故障发生时间": same_time, "闭环ID": "case-b", "序号": 1}},
            "rec-a": {"record_id": "rec-a", "fields": {"故障发生时间": same_time, "闭环ID": "case-a", "序号": 2}},
        }

        def get_record(_config, record_id):
            return {"record_id": record_id, "fields": dict(state[record_id]["fields"])}

        def update_record(record_id, payload, _config):
            state[record_id]["fields"].update(payload["fields"])
            return {"status": "已更新", "record_id": record_id}

        with (
            patch("src.orchestrator.pipeline.list_records", return_value=list(state.values())),
            patch("src.orchestrator.pipeline.get_record", side_effect=get_record),
            patch("src.orchestrator.pipeline.update_record", side_effect=update_record),
        ):
            result = _reorder_feishu_sequence_by_time(config)

        self.assertEqual(result["updates_planned"], 2)
        self.assertEqual(state["rec-a"]["fields"]["序号"], 1)
        self.assertEqual(state["rec-b"]["fields"]["序号"], 2)

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
