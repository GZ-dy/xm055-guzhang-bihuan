"""测试 wecom_fault_bot.py 的本地两步式闭环与回执。"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import wecom_fault_bot as bot  # noqa: E402


class TestCompletionReply(unittest.TestCase):
    def test_only_confirms_mail_after_sent_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "mail_send_result.json"
            outcome = {"error": None, "result": {"outputs": {"mail_send_result": str(result_path)}}}

            result_path.write_text('{"status":"已发送"}', encoding="utf-8")
            self.assertEqual(bot._completion_reply(outcome), "邮件已发送")

            result_path.write_text('{"status":"失败"}', encoding="utf-8")
            self.assertTrue(bot._completion_reply(outcome).startswith("处理未完成："))

    def test_websocket_dry_run_reply_contains_generated_outputs(self):
        outcome = {
            "received": True,
            "case_id": "wecom_case_1",
            "error": None,
            "result": {
                "status": "validated",
                "outputs": {
                    "report_docx": "outputs/reports/report.docx",
                    "offline_record_workbook": "outputs/ledger/ledger.xlsx",
                    "mail_preview": "outputs/mail/mail_preview.json",
                },
            },
        }

        reply = bot._outcome_reply(outcome, run_mode="dry-run")

        self.assertIn("2) case_id：wecom_case_1", reply)
        self.assertIn("4) 报告路径：outputs/reports/report.docx", reply)
        self.assertIn("5) 台账路径：outputs/ledger/ledger.xlsx", reply)
        self.assertIn("6) 邮件预览：outputs/mail/mail_preview.json", reply)
        self.assertNotIn("7) 邮件状态", reply)

    def test_websocket_live_reply_adds_confirmed_mail_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "mail_send_result.json"
            result_path.write_text('{"status":"已发送"}', encoding="utf-8")
            outcome = {
                "received": True,
                "case_id": "wecom_case_live",
                "error": None,
                "result": {
                    "status": "validated",
                    "outputs": {"mail_send_result": str(result_path)},
                },
            }

            reply = bot._outcome_reply(outcome, run_mode="live")

            self.assertEqual(reply, "邮件已发送")


class TestWebSocketRecovery(unittest.TestCase):
    def test_reconnects_when_sdk_receive_loop_has_stopped(self):
        class DoneTask:
            @staticmethod
            def done():
                return True

            @staticmethod
            def exception():
                return None

        class Manager:
            _receive_task = DoneTask()
            _is_manual_close = False
            connect_count = 0

            async def connect(self):
                self.connect_count += 1

        manager = Manager()
        client = type("Client", (), {"_ws_manager": manager})()

        recovered = asyncio.run(bot._recover_stalled_websocket(client))

        self.assertTrue(recovered)
        self.assertEqual(1, manager.connect_count)


class TestReplyMessageHermesForward(unittest.TestCase):
    """V13+ Hermes 转发模式回执测试"""

    def test_hermes_forward_six_fields(self):
        """6 字段: 收到/case_id/提交 Hermes/run_id/delivery_id/autopilot_id"""
        fake_result = {
            "mode": "hermes_forward",
            "status": "submitted_to_hermes",
            "run_id": "dfcc3b2c-3d59-475e-ad8e-813e265ef414",
            "delivery_id": "9f590c5a-0200-47bd-89ff-c2cae78d7d29",
            "autopilot_id": "ce844cd4-1351-4f5b-8363-7413defac45a",
        }
        reply = bot._reply_message(
            received=True,
            case_id="wecom_20260710_xyz999",
            result=fake_result,
        )
        self.assertIn("1) 收到消息：是", reply)
        self.assertIn("2) case_id：wecom_20260710_xyz999", reply)
        self.assertIn("3) 提交 Hermes：成功", reply)
        self.assertIn("4) run_id：dfcc3b2c", reply)
        self.assertIn("5) delivery_id：9f590c5a", reply)
        self.assertIn("6) autopilot_id：ce844cd4", reply)
        self.assertIn("Hermes", reply)

    def test_hermes_forward_error(self):
        """Hermes 转发失败回执"""
        reply = bot._reply_message(
            received=True,
            case_id="wecom_test_err",
            result={"mode": "hermes_forward"},
            error="post_to_hermes_webhook exit=2: connection refused",
        )
        self.assertIn("1) 收到消息：是", reply)
        self.assertIn("3) 提交 Hermes：失败", reply)
        self.assertIn("connection refused", reply)

    def test_legacy_pipeline_two_step_six_fields(self):
        """V12 老两步式 6 字段保留兼容 (mode=pipeline_two_step)"""
        fake_result = {
            "mode": "pipeline_two_step",
            "status": "validated",
            "outputs": {
                "report_docx": "/tmp/test/outputs/reports/case1/report.docx",
                "offline_record_workbook": "/tmp/test/outputs/ledger/case1/ledger.xlsx",
                "mail_preview_path": "/tmp/test/outputs/mail/case1/mail_preview.txt",
            },
            "missing_fields": [],
        }
        reply = bot._reply_message(
            received=True,
            case_id="wecom_20260710_abcd1234",
            result=fake_result,
        )
        self.assertIn("1) 收到消息：是", reply)
        self.assertIn("2) case_id：wecom_20260710_abcd1234", reply)
        self.assertIn("3) 校验：", reply)
        self.assertIn("4) 报告路径：", reply)
        self.assertIn("5) 台账路径：", reply)
        self.assertIn("6) 邮件预览：", reply)
        self.assertIn("report.docx", reply)
        self.assertIn("ledger.xlsx", reply)
        self.assertIn("mail_preview.txt", reply)


class TestReplyMessageMissingFields(unittest.TestCase):
    """缺字段/错误/空文本场景"""

    def test_missing_fields_appears(self):
        fake_result = {
            "mode": "pipeline_two_step",
            "status": "need_review",
            "outputs": {},
            "missing_fields": ["incident.duration_minutes", "handling.solution"],
        }
        reply = bot._reply_message(
            received=True,
            case_id="wecom_20260710_miss",
            result=fake_result,
        )
        self.assertIn("缺字段", reply)
        self.assertIn("incident.duration_minutes", reply)

    def test_error_path(self):
        reply = bot._reply_message(
            received=True,
            case_id="",
            result=None,
            error="boom: pipeline crash",
        )
        self.assertIn("1) 收到消息：是", reply)
        self.assertIn("3) 校验：失败", reply)
        self.assertIn("boom", reply)
        self.assertNotIn("4) 报告路径", reply)

    def test_empty_received(self):
        reply = bot._reply_message(
            received=False,
            case_id="",
            result=None,
        )
        self.assertIn("1) 收到消息：否", reply)


class TestLocalPipelineEndToEnd(unittest.TestCase):
    """机器人必须在本地依次生成上下文和正式产物。"""

    @patch.object(bot, "run")
    def test_handle_text_runs_local_pipeline_two_steps(self, run_mock):
        run_mock.side_effect = [
            {
                "status": "validated",
                "outputs": {"incident_context": str(ROOT / "work" / "case.json")},
            },
            {
                "status": "validated",
                "outputs": {
                    "report_docx": str(ROOT / "outputs" / "report.docx"),
                    "offline_record_workbook": str(ROOT / "outputs" / "ledger.xlsx"),
                    "mail_preview_path": str(ROOT / "outputs" / "mail.txt"),
                },
                "missing_fields": [],
            },
        ]

        outcome = bot.handle_text(
            "系统：OPENAPI系统\n故障原因：数据库网络异常\n"
            "1. 2026年7月13日11:10：网格通通知OPENAPI接口故障 多域维护组排查系统\n"
            "2. 2026年7月13日11:45：网格通验证业务恢复，系统恢复正常",
            case_prefix="mock",
        )

        self.assertEqual(2, run_mock.call_count)
        first_options = run_mock.call_args_list[0].args[1]
        second_options = run_mock.call_args_list[1].args[1]
        self.assertTrue(first_options.incident_context_only)
        self.assertTrue(second_options.all_dry_run)
        self.assertFalse(second_options.apply)
        self.assertFalse(second_options.send_mail)
        self.assertEqual("pipeline_two_step", outcome["result"]["mode"])
        reply = bot._reply_message(
            received=outcome["received"],
            case_id=outcome["case_id"],
            result=outcome["result"],
            error=outcome["error"],
        )
        self.assertIn("3) 校验：通过", reply)
        self.assertIn("4) 报告路径：", reply)
        self.assertIn("5) 台账路径：", reply)

    def test_async_handle_text_keeps_event_loop_responsive(self):
        events = []

        def slow_handle_text(*_args, **_kwargs):
            time.sleep(0.05)
            events.append("handled")
            return {"status": "validated"}

        async def scenario():
            async def heartbeat():
                await asyncio.sleep(0.005)
                events.append("heartbeat")

            with patch.object(bot, "handle_text", side_effect=slow_handle_text):
                result, _ = await asyncio.gather(
                    bot._handle_text_async("故障信息", run_mode="live"),
                    heartbeat(),
                )
            return result

        result = asyncio.run(scenario())

        self.assertEqual({"status": "validated"}, result)
        self.assertEqual(["heartbeat", "handled"], events)

    def test_async_handle_text_serializes_pipeline_jobs(self):
        active = 0
        max_active = 0
        counter_lock = __import__("threading").Lock()

        def slow_handle_text(*_args, **_kwargs):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with counter_lock:
                active -= 1
            return {"status": "validated"}

        async def scenario():
            with patch.object(bot, "handle_text", side_effect=slow_handle_text):
                return await asyncio.gather(
                    bot._handle_text_async("故障一", run_mode="live"),
                    bot._handle_text_async("故障二", run_mode="live"),
                )

        results = asyncio.run(scenario())

        self.assertEqual([{"status": "validated"}, {"status": "validated"}], results)
        self.assertEqual(1, max_active)


class TestLiveWriteGate(unittest.TestCase):
    """V12+ 4 道 env gate 测"""

    def test_live_write_disabled_by_default(self):
        """默认 3 env gate 都关"""
        # 清除所有 gate
        for k in ("FAULT_AUTO_SEND_ON_VALIDATED", "FAULT_CONFIRM_LIVE_WRITE",
                  "FAULT_PROD_CASE_TOKEN"):
            os.environ.pop(k, None)
        live, reason = bot._live_write_enabled("wecom_test_20260710_x", run_mode="live")
        self.assertFalse(live)
        # 任意 gate 缺失都拒绝
        self.assertTrue(
            "FAULT_AUTO_SEND_ON_VALIDATED" in reason
            or "FAULT_CONFIRM_LIVE_WRITE" in reason
            or "FAULT_PROD_CASE_TOKEN" in reason,
            f"reason 应提到哪个 gate 缺: got {reason!r}",
        )

    def test_live_write_enabled_with_all_gates(self):
        """3 gate 全开 = live_write=True"""
        os.environ["FAULT_AUTO_SEND_ON_VALIDATED"] = "1"
        os.environ["FAULT_CONFIRM_LIVE_WRITE"] = "LIVE-WRITE"
        os.environ["FAULT_PROD_CASE_TOKEN"] = "***"
        try:
            live, reason = bot._live_write_enabled("wecom_prod_20260710_x", run_mode="live")
            self.assertTrue(live)
            self.assertIn("三 gate 全开", reason)
        finally:
            for k in ("FAULT_AUTO_SEND_ON_VALIDATED", "FAULT_CONFIRM_LIVE_WRITE",
                      "FAULT_PROD_CASE_TOKEN"):
                os.environ.pop(k, None)

    def test_live_write_disabled_for_test_case_id(self):
        """case_id 前缀 test/agent/demo 拒绝真发"""
        os.environ["FAULT_AUTO_SEND_ON_VALIDATED"] = "1"
        os.environ["FAULT_CONFIRM_LIVE_WRITE"] = "LIVE-WRITE"
        os.environ["FAULT_PROD_CASE_TOKEN"] = "***"
        try:
            for prefix in ("test_", "agent_", "demo_", "mock_", "smoke_"):
                live, reason = bot._live_write_enabled(f"{prefix}20260710_x", run_mode="live")
                self.assertFalse(live, f"{prefix} should not allow live_write")
                self.assertIn("dry-run-only", reason)
        finally:
            for k in ("FAULT_AUTO_SEND_ON_VALIDATED", "FAULT_CONFIRM_LIVE_WRITE",
                      "FAULT_PROD_CASE_TOKEN"):
                os.environ.pop(k, None)

    def test_dry_run_and_mock_stay_off_when_all_gates_are_open(self):
        """环境门不能把 dry-run 或 mock 升级为真实写入。"""
        os.environ["FAULT_AUTO_SEND_ON_VALIDATED"] = "1"
        os.environ["FAULT_CONFIRM_LIVE_WRITE"] = "LIVE-WRITE"
        os.environ["FAULT_PROD_CASE_TOKEN"] = "***"
        try:
            for mode in ("dry-run", "probe", "mock"):
                live, reason = bot._live_write_enabled("wecom_prod_20260710_x", run_mode=mode)
                self.assertFalse(live)
                self.assertIn("不是 live", reason)
        finally:
            for k in ("FAULT_AUTO_SEND_ON_VALIDATED", "FAULT_CONFIRM_LIVE_WRITE",
                      "FAULT_PROD_CASE_TOKEN"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
