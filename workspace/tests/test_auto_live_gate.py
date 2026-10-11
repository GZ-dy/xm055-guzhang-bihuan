import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import run_closed_loop


class AutoLiveGateTests(unittest.TestCase):
    def _invoke(self, args, env, records=None, run_result=None, expected_exit=0):
        stderr = io.StringIO()
        with (
            patch.dict("os.environ", env, clear=True),
            patch("sys.argv", ["run_closed_loop.py", *args]),
            patch("run_closed_loop.run", return_value=run_result or {"status": "validated"}) as mocked_run,
            patch(
                "src.feishu_ledger.client.load_feishu_config",
                return_value=SimpleNamespace(field_map={"case_id": "序号"}, table_url="https://example.invalid/table"),
            ),
            patch("src.feishu_ledger.client.list_records", return_value=records or []),
            redirect_stdout(io.StringIO()),
            redirect_stderr(stderr),
        ):
            self.assertEqual(run_closed_loop.main(), expected_exit)
        return mocked_run.call_args.args[1], stderr.getvalue()

    def test_auto_send_flag_alone_does_not_inject_live(self):
        options, stderr = self._invoke(
            ["--incident-context", "work/cases/case_guard/incident_context.json", "--feishu-record-id", "rec1"],
            {"FAULT_AUTO_SEND_ON_VALIDATED": "1"},
        )

        self.assertFalse(options.apply)
        self.assertFalse(options.send_mail)
        self.assertEqual(options.confirm_live_write, "")
        self.assertIn("阻断自动 live", stderr)

    def test_all_three_environment_gates_inject_live(self):
        options, _ = self._invoke(
            ["--incident-context", "work/cases/case_guard/incident_context.json", "--feishu-record-id", "rec1"],
            {
                "FAULT_AUTO_SEND_ON_VALIDATED": "1",
                "FAULT_CONFIRM_LIVE_WRITE": "LIVE-WRITE",
                "FAULT_PROD_CASE_TOKEN": "REAL-2026",
            },
        )

        self.assertTrue(options.apply)
        self.assertTrue(options.send_mail)
        self.assertEqual(options.confirm_live_write, "LIVE-WRITE")

    def test_auto_send_skips_mail_when_dedup_record_is_already_sent(self):
        options, stderr = self._invoke(
            ["--incident-context", "work/cases/case_sent/incident_context.json"],
            {
                "FAULT_AUTO_SEND_ON_VALIDATED": "1",
                "FAULT_CONFIRM_LIVE_WRITE": "LIVE-WRITE",
                "FAULT_PROD_CASE_TOKEN": "REAL-2026",
            },
            [{"record_id": "rec1", "fields": {"序号": "case_sent", "邮件闭环状态": "已发送"}}],
        )

        self.assertTrue(options.apply)
        self.assertFalse(options.send_mail)
        self.assertEqual(options.feishu_record_id, "rec1")
        self.assertIn("跳过重复发信", stderr)

    def test_auto_send_keeps_mail_when_dedup_record_is_not_sent(self):
        options, _ = self._invoke(
            ["--incident-context", "work/cases/case_pending/incident_context.json"],
            {
                "FAULT_AUTO_SEND_ON_VALIDATED": "1",
                "FAULT_CONFIRM_LIVE_WRITE": "LIVE-WRITE",
                "FAULT_PROD_CASE_TOKEN": "REAL-2026",
            },
            [{"record_id": "rec2", "fields": {"序号": "case_pending", "邮件闭环状态": "待发送"}}],
        )

        self.assertTrue(options.apply)
        self.assertTrue(options.send_mail)
        self.assertEqual(options.feishu_record_id, "rec2")

    def test_explicit_cli_keeps_mail_when_dedup_record_is_already_sent(self):
        options, _ = self._invoke(
            [
                "--incident-context", "work/cases/case_manual/incident_context.json",
                "--apply", "--send-mail", "--confirm-live-write", "LIVE-WRITE",
            ],
            {},
            [{"record_id": "rec3", "fields": {"序号": "case_manual", "邮件闭环状态": "已发送"}}],
        )

        self.assertTrue(options.apply)
        self.assertTrue(options.send_mail)
        self.assertEqual(options.feishu_record_id, "rec3")

    def test_cli_dedupe_does_not_choose_between_ambiguous_records(self):
        options, stderr = self._invoke(
            [
                "--incident-context", "work/cases/case_ambiguous/incident_context.json",
                "--apply", "--confirm-live-write", "LIVE-WRITE",
            ],
            {},
            [
                {"record_id": "rec1", "fields": {"序号": "case_ambiguous"}},
                {"record_id": "rec2", "fields": {"序号": "case_ambiguous"}},
            ],
        )

        self.assertEqual(options.feishu_record_id, "")
        self.assertIn("dedupe ambiguous", stderr)

    def test_exit_code_reflects_run_status(self):
        self._invoke(["--text", "BOMC故障"], {}, run_result={"status": "validated"}, expected_exit=0)
        self._invoke(["--text", "BOMC故障"], {}, run_result={"status": "need_review"}, expected_exit=1)

    def test_explicit_cli_live_flags_do_not_require_environment_gates(self):
        options, _ = self._invoke(
            [
                "--incident-context", "work/cases/case_guard/incident_context.json",
                "--apply", "--send-mail", "--confirm-live-write", "LIVE-WRITE",
                "--feishu-record-id", "rec1",
            ],
            {},
        )

        self.assertTrue(options.apply)
        self.assertTrue(options.send_mail)
        self.assertEqual(options.confirm_live_write, "LIVE-WRITE")


if __name__ == "__main__":
    unittest.main()
