import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.pipeline import RunOptions, _backfill_mail_status_after_send


class MailStatusBackfillGuardTests(unittest.TestCase):
    def test_smtp_failure_does_not_touch_feishu(self):
        with (
            patch("src.orchestrator.pipeline.load_feishu_config") as load_config,
            patch("src.orchestrator.pipeline.update_record") as update_record,
        ):
            result = _backfill_mail_status_after_send(
                Path.cwd(),
                RunOptions(send_mail=True),
                {"status": "已更新", "record_id": "existing-record"},
                {"smtp_called": True, "status": "失败"},
                True,
            )

        self.assertEqual(result, {"status": "not_required"})
        load_config.assert_not_called()
        update_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
