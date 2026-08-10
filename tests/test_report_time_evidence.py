import unittest

from src.report_generation.docx_writer import _process_step_times, _process_step_times_4


class ReportTimeEvidenceTests(unittest.TestCase):
    def test_four_step_times_leave_missing_middle_times_blank(self):
        incident = {
            "occurred_at": "2026-07-08 12:10",
            "recovered_at": "2026-07-08 12:20",
            "diagnosis_at": "",
            "resolved_at": "",
        }
        records = ["12:10服务台报障", "12:20恢复正常"]

        self.assertEqual(_process_step_times_4(incident, records), ["12:10分", "", "", "12:20分"])

        incident.update(diagnosis_at="2026-07-08 12:14", resolved_at="2026-07-08 12:18")
        self.assertEqual(
            _process_step_times_4(incident, records),
            ["12:10分", "12:14分", "12:18分", "12:20分"],
        )

    def test_five_step_times_use_only_recorded_or_explicit_times(self):
        incident = {
            "occurred_at": "2026-07-08 12:10",
            "recovered_at": "2026-07-08 12:20",
            "diagnosis_at": "",
            "resolved_at": "",
        }
        records = ["12:10接报", "12:14核查", "12:20恢复"]

        self.assertEqual(
            _process_step_times(incident, records),
            ["12:10分", "12:14分", "", "", "12:20分"],
        )

        incident.update(diagnosis_at="2026-07-08 12:16", resolved_at="2026-07-08 12:18")
        self.assertEqual(
            _process_step_times(incident, records),
            ["12:10分", "12:14分", "12:16分", "12:18分", "12:20分"],
        )


if __name__ == "__main__":
    unittest.main()
