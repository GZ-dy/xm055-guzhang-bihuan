import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from src.offline_ledger.workbook import MASTER_TABLE_PATH, write_offline_record_workbook
from src.orchestrator.pipeline import RunOptions, _write_offline_record_if_needed


class OfflineMasterRootTests(unittest.TestCase):
    @staticmethod
    def _template(path: Path) -> None:
        path.parent.mkdir(parents=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "2026年度故障记录"
        sheet.append(["序号", "系统名称", "故障发生时间"])
        workbook.save(path)
        workbook.close()

    def test_explicit_master_paths_stay_in_each_project_root_not_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cwd = base / "process-cwd"
            cwd.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                for index in (1, 2):
                    root = base / f"portable-{index}"
                    template = root / "data/templates/ledger/template.xlsx"
                    output = root / f"outputs/ledger/case-{index}/snapshot.xlsx"
                    master = root / MASTER_TABLE_PATH
                    self._template(template)

                    write_offline_record_workbook(
                        template,
                        output,
                        {
                            "case_id": f"case-{index}",
                            "fields": {
                                "系统名称": "BOMC",
                                "故障发生时间": "2026-07-14 10:00",
                            },
                        },
                        live=True,
                        master_path=master,
                    )

                    self.assertTrue(output.exists())
                    self.assertTrue(master.exists())
                self.assertFalse((cwd / MASTER_TABLE_PATH).exists())
            finally:
                os.chdir(old_cwd)

    def test_live_snapshot_contains_all_appended_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template" / "template.xlsx"
            master = root / "master.xlsx"
            first_snapshot = root / "case-1.xlsx"
            second_snapshot = root / "case-2.xlsx"
            self._template(template)

            for case_id, snapshot in (("case-1", first_snapshot), ("case-2", second_snapshot)):
                write_offline_record_workbook(
                    template,
                    snapshot,
                    {
                        "case_id": case_id,
                        "fields": {
                            "系统名称": "BOMC",
                            "故障发生时间": "2026-07-14 10:00",
                        },
                    },
                    live=True,
                    master_path=master,
                )

            workbook = load_workbook(second_snapshot)
            sheet = workbook["2026年度故障记录"]
            self.assertEqual([sheet.cell(row, 1).value for row in (2, 3)], [1, 2])
            self.assertEqual([sheet.cell(row, 4).value for row in (2, 3)], ["case-1", "case-2"])
            workbook.close()

    def test_pipeline_passes_project_scoped_master_path(self):
        root = Path(tempfile.gettempdir()) / "portable-project"
        snapshot = root / "outputs/ledger/case-portable/snapshot.xlsx"
        context = {
            "meta": {"case_id": "case-portable"},
            "incident": {"occurred_at": "2026-07-14 10:00"},
        }
        paths = {"ledger": snapshot.parent}

        with (
            patch("src.orchestrator.pipeline._fill_handler_from_previous_system"),
            patch("src.orchestrator.pipeline.write_offline_record_workbook", return_value=snapshot) as writer,
        ):
            _write_offline_record_if_needed(
                root,
                paths,
                context,
                {"case_id": "case-portable", "fields": {}},
                RunOptions(
                    incident_context=root / "work/cases/case-portable/incident_context.json",
                    apply=True,
                    confirm_live_write="LIVE-WRITE",
                ),
            )

        self.assertEqual(writer.call_args.kwargs["master_path"], root / MASTER_TABLE_PATH)
        self.assertTrue(writer.call_args.kwargs["live"])

    def test_pipeline_does_not_write_master_when_live_gate_is_incomplete(self):
        root = Path(tempfile.gettempdir()) / "portable-project"
        snapshot = root / "outputs/ledger/case-blocked/snapshot.xlsx"
        context = {
            "meta": {"case_id": "case-blocked"},
            "incident": {"occurred_at": "2026-07-14 10:00"},
        }

        with patch(
            "src.orchestrator.pipeline.write_offline_record_workbook",
            return_value=snapshot,
        ) as writer:
            _write_offline_record_if_needed(
                root,
                {"ledger": snapshot.parent},
                context,
                {"case_id": "case-blocked", "fields": {}},
                RunOptions(apply=True, confirm_live_write="LIVE-WRITE"),
            )

        self.assertFalse(writer.call_args.kwargs["live"])


if __name__ == "__main__":
    unittest.main()
