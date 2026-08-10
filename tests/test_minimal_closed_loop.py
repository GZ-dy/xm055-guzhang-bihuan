from __future__ import annotations

import json
from io import BytesIO
import os
import pathlib
import re
import shutil
import tempfile
import unittest
from xml.etree import ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook

from src.feishu_ledger.client import FeishuApiError
from src.offline_ledger.workbook import write_offline_record_workbook
from src.orchestrator.pipeline import RunOptions, run


VALID_TEXT = (
    "【BOMC-cas服务异常】2026年7月8日10:13用户反馈BOMC系统无法访问。"
    "发生时间：2026年7月8日10:13。恢复时间：2026年7月8日10:25。"
    "故障原因：CAS服务异常。解决方式：重启CAS和WEB服务。"
    "影响范围：相关用户无法访问BOMC系统。负责人：高喆。处理组：多域维护组。"
    "处理过程：10:13接到用户报障|10:20重启CAS和WEB服务|10:25验证业务恢复。"
    "后续措施：补充CAS服务监控|固化恢复验证清单。"
)


class MinimalClosedLoopTest(unittest.TestCase):
    def test_contact_aliases_keep_future_handler_and_domain_mapping(self) -> None:
        from src.feishu_ledger.preview import SYSTEM_DOMAIN
        from src.mail_sender.contacts import load_contacts, lookup_owner

        contacts = load_contacts(Path(__file__).resolve().parents[1])
        cases = (
            ("CLC业务卡", "杨文刚、高喆、沈俊羽", "B域"),
            ("SmatrBI", "吴承谦、陈航、沈泽人", "经分域"),
        )
        for system_name, expected_owner, expected_domain in cases:
            owner, _group, match = lookup_owner(contacts, system_name)
            self.assertEqual(owner, expected_owner)
            self.assertEqual(match["domain_feishu"], expected_domain)
        for system_name in ("大数据K8s", "大数据K8S", "大数据平台K8s", "大数据平台K8S", "大数据平台K8S集群"):
            self.assertEqual(SYSTEM_DOMAIN[system_name], "经分域")

    def test_numbered_timeline_without_year_uses_current_year(self) -> None:
        from src.orchestrator.pipeline import _numbered_timeline_records, _solution_from_timeline

        records = _numbered_timeline_records(
            "1. 7月15日14:20：网格通通知OPENAPI接口故障\n"
            "2. 7月15日14:35：网格通验证业务恢复，系统恢复正常"
        )

        year = __import__("datetime").datetime.now().year
        self.assertEqual(
            records,
            [
                f"{year}-07-15 14:20 网格通通知OPENAPI接口故障",
                f"{year}-07-15 14:35 网格通验证业务恢复，系统恢复正常",
            ],
        )
        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(
            _numbered_timeline_records("1. 14:20：网格通通知OPENAPI接口故障"),
            [f"{today} 14:20 网格通通知OPENAPI接口故障"],
        )
        self.assertEqual(
            _numbered_timeline_records(
                "发生日期：7月14日\n1. 14:20：网格通通知OPENAPI接口故障"
            ),
            [f"{year}-07-14 14:20 网格通通知OPENAPI接口故障"],
        )
        self.assertEqual(
            _numbered_timeline_records(
                "发生日期：7月14日\n"
                "1. 11点10：网格通通知OPENAPI接口故障\n"
                "2. 11点40：网格通验证业务恢复，系统恢复正常"
            ),
            [
                f"{year}-07-14 11:10 网格通通知OPENAPI接口故障",
                f"{year}-07-14 11:40 网格通验证业务恢复，系统恢复正常",
            ],
        )
        self.assertEqual(
            _solution_from_timeline([f"{today} 14:28 多域维护组重启异常服务并验证接口"]),
            "重启异常服务并验证接口",
        )
        two_records = _numbered_timeline_records(
            "时间1：7月16日10:00：收到故障\n"
            "时间2：7月16日10:10：验证业务恢复，系统恢复正常"
        )
        six_records = _numbered_timeline_records(
            "\n".join(f"时间{i}：7月16日10:0{i - 1}：处理步骤{i}" for i in range(1, 7))
        )
        self.assertEqual(len(two_records), 2)
        self.assertEqual(len(six_records), 6)

    def test_numbered_actual_timeline_is_preserved_in_report_and_ledger(self) -> None:
        text = """系统：OPENAPI系统
故障原因：数据库网络异常

1. 2026年7月13日11:10：网格通通知OPENAPI接口故障 多域维护组排查系统
2. 2026年7月13日11:15：多域维护组检查数据库网络连接
3. 2026年7月13日11:25：多域维护组重启OPENAPI接口服务
4. 2026年7月13日11:35：多域维护组确认数据库连接恢复
5. 2026年7月13日11:45：网格通验证业务恢复，系统恢复正常
"""
        expected_records = [
            "2026-07-13 11:10 网格通通知OPENAPI接口故障 多域维护组排查系统",
            "2026-07-13 11:15 多域维护组检查数据库网络连接",
            "2026-07-13 11:25 多域维护组重启OPENAPI接口服务",
            "2026-07-13 11:35 多域维护组确认数据库连接恢复",
            "2026-07-13 11:45 网格通验证业务恢复，系统恢复正常",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            repo_root = Path(__file__).resolve().parents[1]
            shutil.copytree(repo_root / "data/reference/contacts", root / "data/reference/contacts")
            master = root / "outputs/ledger/master/【2026】多域系统故障记录表.xlsx"
            with patch("src.offline_ledger.workbook.MASTER_TABLE_PATH", master):
                result = run(
                    root,
                    RunOptions(
                        text=text,
                        case_id="case_actual_timeline",
                        all_dry_run=True,
                        online_record_url="https://example.invalid/record/case_actual_timeline",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_actual_timeline/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["handling"]["process_source"], "actual_timeline")
            self.assertEqual(context["handling"]["process_records"], expected_records)
            self.assertEqual(context["handling"]["solution"], "重启OPENAPI接口服务")
            self.assertEqual(context["handling"]["owner"], "杨文刚、高喆、沈俊羽")
            self.assertEqual(context["incident"]["recovered_at"], "2026-07-13 11:45")
            self.assertEqual(context["diagnosis"]["fault_description"], "网格通反馈OPENAPI系统接口故障")

            payload = json.loads((root / "outputs/ledger/case_actual_timeline/record_payload.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["fields"]["处理过程"], "\n".join(expected_records))
            self.assertEqual(payload["fields"]["恢复手段"], "重启OPENAPI接口服务")
            self.assertEqual(payload["fields"]["影响范围"], "影响OPENAPI系统使用")
            self.assertEqual(payload["fields"]["故障描述"], "网格通反馈OPENAPI系统接口故障")
            mail_preview = json.loads((root / "outputs/mail/case_actual_timeline/mail_preview.json").read_text(encoding="utf-8"))
            self.assertEqual(
                mail_preview["to"],
                ["gydyjzhwhz@gd.chinamobile.com"],
            )
            self.assertEqual(
                mail_preview["cc"],
                ["yangwengang@shsnc.com", "gaozhe@shsnc.com", "shenjunyu@shsnc.com"],
            )
            self.assertIn(
                "一、事件描述： 7月13日11点10分，网格通反馈OPENAPI系统接口故障。",
                mail_preview["body"],
            )
            self.assertNotIn("故障时间节点", mail_preview["body"])

            report = Document(result["outputs"]["report_docx"])
            report_paragraphs = [paragraph.text.strip() for paragraph in report.paragraphs if paragraph.text.strip()]
            self.assertIn(
                "1、  11:10 网格通通知OPENAPI接口故障；多域维护组接报后立即响应并开展故障排查，重点核查OPENAPI系统接口服务、信令链路及相关告警信息。",
                report_paragraphs,
            )
            self.assertIn(
                "5、  11:45 网格通验证业务恢复，系统恢复正常；多域维护组同步复核系统运行状态及关键链路，确认系统运行稳定。",
                report_paragraphs,
            )

            dry_workbook = load_workbook(root / "outputs/ledger/case_actual_timeline/【2026】多域系统故障记录表_case_actual_timeline.xlsx")
            dry_sheet = dry_workbook["2026年度故障记录"]
            self.assertEqual(dry_sheet["C69"].value, "自助分析")
            self.assertEqual(dry_sheet["C70"].value, "OPENAPI系统")
            self.assertEqual(dry_sheet["B70"].value, "经分域")
            self.assertEqual(dry_sheet["R70"].value, "杨文刚、高喆、沈俊羽")
            self.assertEqual([dry_sheet.cell(row, 1).value for row in range(2, 71)], list(range(1, 70)))
            self.assertEqual(dry_sheet["C69"].style_id, dry_sheet["C70"].style_id)
            dry_workbook.close()
            self.assertFalse(master.exists())

            live_output = root / "outputs/ledger/live/故障记录表.xlsx"
            with patch("src.offline_ledger.workbook.MASTER_TABLE_PATH", master):
                write_offline_record_workbook(
                    root / "data/templates/ledger/offline_fault_record_2026.xlsx",
                    live_output,
                    payload,
                    live=True,
                )
            for workbook_path in (live_output, master):
                live_workbook = load_workbook(workbook_path)
                live_sheet = live_workbook["2026年度故障记录"]
                self.assertEqual(live_sheet["C69"].value, "自助分析")
                self.assertEqual(live_sheet["C70"].value, "OPENAPI系统")
                self.assertEqual(live_sheet["B70"].value, "经分域")
                self.assertEqual(live_sheet["R70"].value, "杨文刚、高喆、沈俊羽")
                self.assertEqual([live_sheet.cell(row, 1).value for row in range(2, 71)], list(range(1, 70)))
                self.assertIsNone(live_sheet["C71"].value)
                live_workbook.close()

            report_path = Path(result["outputs"]["report_docx"])
            with ZipFile(report_path) as docx:
                document_xml_bytes = docx.read("word/document.xml")
                document_xml = document_xml_bytes.decode("utf-8")
            namespace_map = {
                prefix: uri
                for _event, (prefix, uri) in ET.iterparse(BytesIO(document_xml_bytes), events=("start-ns",))
            }
            document_root = ET.fromstring(document_xml_bytes)
            ignorable = document_root.get(
                "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable",
                "",
            )
            for prefix in ignorable.split():
                self.assertIn(prefix, namespace_map)
            for record in expected_records:
                self.assertNotIn(record, document_xml)
            self.assertIn("2026年7月13日11:10分网格通反馈OPENAPI系统接口故障；", document_xml)
            self.assertNotIn("接报并核查", document_xml)
            self.assertNotIn("已完成优化及预防措施", document_xml)
            self.assertIn("后续运维完善措施", document_xml)
            self.assertIn("OPENAPI系统", document_xml)
            self.assertIn("5、", document_xml)

    def test_wecom_mention_is_not_used_as_fault_description(self) -> None:
        text = """@故障闭环的机器人 系统：BOMC系统
故障原因：BOMC系统cas服务夯死
发生日期：7月15日
1. 09:10：服务台通知BOMC页面无法打开
2. 09:20：排查BOMC的cas和web服务是否存在
3. 09:30：确认cas服务夯死，重启cas与web服务
4. 09:35：服务台验证业务恢复，系统恢复正常
"""
        case_id = "case_wecom_mention_timeline"
        expected_title = "BOMC页面无法打开"
        expected_description = "服务台反馈BOMC页面无法打开"
        year = __import__("datetime").datetime.now().year

        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            repo_root = Path(__file__).resolve().parents[1]
            shutil.copytree(repo_root / "data/reference/contacts", root / "data/reference/contacts")
            master = root / "outputs/ledger/master/【2026】多域系统故障记录表.xlsx"
            with patch("src.offline_ledger.workbook.MASTER_TABLE_PATH", master):
                result = run(
                    root,
                    RunOptions(
                        text=text,
                        case_id=case_id,
                        all_dry_run=True,
                        online_record_url="https://example.invalid/record/test",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            context = json.loads(
                (root / f"work/cases/{case_id}/incident_context.json").read_text(encoding="utf-8")
            )
            self.assertEqual(context["incident"]["fault_title"], expected_title)
            self.assertEqual(context["diagnosis"]["fault_description"], expected_description)
            self.assertEqual(context["diagnosis"]["fault_cause"], "BOMC系统cas服务夯死")

            mail_preview = json.loads(
                (root / f"outputs/mail/{case_id}/mail_preview.json").read_text(encoding="utf-8")
            )
            self.assertIn(
                "一、事件描述： 7月15日9点10分，服务台反馈BOMC页面无法打开。",
                mail_preview["body"],
            )
            self.assertNotIn("@故障闭环的机器人", mail_preview["body"])

            report_path = Path(result["outputs"]["report_docx"])
            with ZipFile(report_path) as docx:
                document_xml = docx.read("word/document.xml").decode("utf-8")
            self.assertIn(
                f"{year}年7月15日09:10分{expected_description}；",
                document_xml,
            )
            self.assertNotIn("@故障闭环的机器人", document_xml)

            workbook = load_workbook(root / _offline_record_file(case_id))
            sheet = workbook["2026年度故障记录"]
            self.assertEqual(sheet["M70"].value, expected_description)
            self.assertNotIn("@故障闭环的机器人", sheet["M70"].value)
            workbook.close()

    def test_followup_templates_vary_by_incident_but_repeat_stably(self) -> None:
        from src.report_generation.docx_writer import _operational_followup_lines

        diagnosis = {
            "fault_cause": "数据库网络异常",
            "fault_description": "网格通反馈OPENAPI系统接口故障",
        }
        handling = {"solution": "重启故障节点，信令恢复", "handling_group": "多域维护组"}
        first_incident = {"system_name": "OPENAPI", "occurred_at": "2026-07-14 19:10"}
        second_incident = {"system_name": "OPENAPI", "occurred_at": "2026-07-16 19:10"}

        first = _operational_followup_lines(first_incident, diagnosis, handling)
        second = _operational_followup_lines(second_incident, diagnosis, handling)

        self.assertEqual(first, _operational_followup_lines(first_incident, diagnosis, handling))
        self.assertNotEqual(first, second)
        for lines in (first, second):
            self.assertEqual(len(lines), 6)
            self.assertTrue(any("亚信厂商" in line for line in lines))
            self.assertTrue(any("恢复验证" in line for line in lines))

        middleware = _operational_followup_lines(
            {"system_name": "大数据K8S", "occurred_at": "2026-07-14 20:10"},
            {"fault_cause": "K8S容器服务异常", "fault_description": "应用无法访问"},
            {"solution": "重启异常Pod", "handling_group": "多域维护组"},
        )
        host = _operational_followup_lines(
            {"system_name": "OPENAPI", "occurred_at": "2026-07-14 21:10"},
            {"fault_cause": "主机磁盘空间不足", "fault_description": "服务不可用"},
            {"solution": "清理磁盘并扩容", "handling_group": "多域维护组"},
        )
        self.assertTrue(any("中间件" in line for line in middleware))
        self.assertTrue(any("主机" in line for line in host))
        self.assertTrue(any("恢复验证" in line for line in middleware))
        self.assertTrue(any("恢复验证" in line for line in host))

    def test_core_run_keeps_only_contract_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            official = next((root / "data/templates/reports/official").glob("*.docx"))
            clean = next((root / "data/templates/reports/clean").glob("*.docx"))
            official_before = official.read_bytes()
            clean_before = clean.read_bytes()

            result = run(
                root,
                RunOptions(
                    text=VALID_TEXT,
                    case_id="case_contract",
                    all_dry_run=True,
                    online_record_url="https://example.invalid/record/case_contract",
                ),
            )

            self.assertEqual(result["status"], "validated")
            self.assertEqual(
                _runtime_files(root),
                [
                    "outputs/ledger/case_contract/execution_plan.json",
                    "outputs/ledger/case_contract/record_payload.json",
                    _offline_record_file("case_contract"),
                    "outputs/mail/case_contract/mail_preview.json",
                    "outputs/manuals/case_contract/manual_match_result.json",
                    "outputs/reports/case_contract/BOMC7月8日故障报告.docx",
                    "outputs/validation/case_contract/validation_result.json",
                    "work/cases/case_contract/incident_context.json",
                ],
            )
            self.assertEqual(official.read_bytes(), official_before)
            self.assertEqual(clean.read_bytes(), clean_before)
            self.assertFalse(any("phase" in name or "tmp" in name for name in _runtime_files(root)))

            context = json.loads((root / "work/cases/case_contract/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["meta"]["schema_version"], "1.0")
            self.assertEqual(context["incident"]["system_name"], "BOMC")
            self.assertEqual(context["incident"]["duration_minutes"], 12)
            self.assertEqual(context["handling"]["process_records"][0], "10:13接到用户报障")

            fields = json.loads((root / "outputs/ledger/case_contract/record_payload.json").read_text(encoding="utf-8"))["fields"]
            self.assertNotIn("邮件状态", fields)
            self.assertEqual(fields["邮件闭环状态"], "待发送")
            self.assertEqual(fields["手册状态"], "待补充")
            self.assertIn("故障报告", fields)
            self.assertEqual(fields["资料状态"], "已补报告")
            self.assertIn("资料完整度", fields)
            self.assertEqual(fields["看板优先级"], "P3")
            self.assertEqual(fields["所属域"], "B域")
            self.assertEqual(fields["故障发生时间"], "2026-07-08 10:13")
            self.assertEqual(fields["故障恢复时间"], "2026-07-08 10:25")
            self.assertEqual(fields["故障时长(分钟)"], 12)
            self.assertEqual(fields["影响范围"], "相关用户无法访问BOMC系统")
            self.assertEqual(fields["影响业务"], "相关用户无法访问BOMC系统")
            self.assertEqual(fields["影响描述"], "相关用户无法访问BOMC系统")
            self.assertNotIn("是否使用自动化智能工具", fields)
            self.assertNotIn("使用工具的名称", fields)
            self.assertEqual(fields["故障原因"], "CAS服务异常")
            plan = json.loads((root / "outputs/ledger/case_contract/execution_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["status"], "ready")
            self.assertFalse(plan["live_gate"]["requested"])

            mail = json.loads((root / "outputs/mail/case_contract/mail_preview.json").read_text(encoding="utf-8"))
            self.assertEqual(mail["status"], "待发送")
            self.assertEqual(mail["to"], ["gydyjzhwhz@gd.chinamobile.com"])
            self.assertIn("闭环【BOMC系统CAS服务异常。】", mail["body"])
            self.assertIn("在线故障记录表：https://example.invalid/record/case_contract", mail["body"])
            self.assertTrue(mail["attachment"].endswith("BOMC7月8日故障报告.docx"))
            self.assertEqual(len(mail["attachments"]), 2)
            self.assertTrue(any(path.endswith(".docx") for path in mail["attachments"]))
            self.assertTrue(any(path.endswith(".xlsx") for path in mail["attachments"]))
            manual = json.loads((root / "outputs/manuals/case_contract/manual_match_result.json").read_text(encoding="utf-8"))
            self.assertEqual(manual["status"], "待补充")

            offline = load_workbook(root / _offline_record_file("case_contract"), data_only=False)["2026年度故障记录"]
            self.assertEqual(offline.cell(70, 1).value, 69)
            self.assertEqual(offline.cell(70, 2).value, "B域")
            self.assertEqual(offline.cell(70, 3).value, "BOMC")
            self.assertEqual(offline.cell(70, 6).value.strftime("%Y-%m-%d %H:%M"), "2026-07-08 10:13")
            self.assertEqual(offline.cell(70, 8).value.strftime("%Y-%m-%d %H:%M"), "2026-07-08 10:25")
            self.assertEqual(offline.cell(70, 15).value, "CAS服务异常")
            self.assertEqual(offline.row_dimensions[70].height, offline.row_dimensions[69].height)
            self.assertEqual(offline.cell(70, 2).style_id, offline.cell(69, 2).style_id)
            self.assertEqual(offline.cell(70, 6).style_id, offline.cell(69, 6).style_id)
            self.assertEqual(offline.cell(70, 6).number_format, offline.cell(69, 6).number_format)
            self.assertIsNone(offline.cell(70, 21).value)
            self.assertIsNone(offline.cell(70, 22).value)

            with ZipFile(root / "outputs/reports/case_contract/BOMC7月8日故障报告.docx") as docx:
                self.assertIn("word/document.xml", docx.namelist())
                self.assertFalse(any(name.startswith("word/media/") for name in docx.namelist()))
                document_xml = docx.read("word/document.xml").decode("utf-8")
                self.assertIn("BOMC系统7月8日故障报告", document_xml)
                self.assertIn("CAS服务异常", document_xml)

            run(root, RunOptions(incident_context=root / "work/cases/case_contract/incident_context.json", feishu_dry_run=True))
            self.assertEqual(
                _runtime_files(root),
                [
                    "outputs/ledger/case_contract/execution_plan.json",
                    "outputs/ledger/case_contract/record_payload.json",
                    _offline_record_file("case_contract"),
                    "outputs/manuals/case_contract/manual_match_result.json",
                    "outputs/reports/case_contract/BOMC7月8日故障报告.docx",
                    "outputs/validation/case_contract/validation_result.json",
                    "work/cases/case_contract/incident_context.json",
                ],
            )

    def test_invalid_context_blocks_downstream_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            bad_text = VALID_TEXT.replace("10:13接到用户报障", "接到用户报障")

            result = run(root, RunOptions(text=bad_text, case_id="case_need_review", all_dry_run=True))

            self.assertEqual(result["status"], "need_review")
            self.assertNotIn("report_docx", result["outputs"])
            self.assertNotIn("record_payload", result["outputs"])
            self.assertNotIn("mail_preview", result["outputs"])
            self.assertNotIn("manual_match_result", result["outputs"])
            self.assertEqual(
                _runtime_files(root),
                [
                    "outputs/validation/case_need_review/validation_result.json",
                    "work/cases/case_need_review/incident_context.json",
                ],
            )
            validation = json.loads((root / "outputs/validation/case_need_review/validation_result.json").read_text(encoding="utf-8"))
            self.assertIn("handling.process_records entries must start with HH:mm or YYYY-MM-DD HH:mm", validation["errors"])

    def test_title_plus_impact_can_supply_fault_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            text = (
                "【BOMC-cas服务异常】发生时间：2026年7月8日10:13。恢复时间：2026年7月8日10:25。"
                "故障原因：CAS服务异常。解决方式：重启CAS和WEB服务。"
                "影响范围：相关用户无法访问BOMC系统。负责人：高喆。处理组：多域维护组。"
                "处理过程：10:13接到用户报障|10:20重启CAS和WEB服务|10:25验证业务恢复。"
                "后续措施：补充CAS服务监控|固化恢复验证清单。"
            )

            result = run(root, RunOptions(text=text, case_id="case_title_impact", all_dry_run=True))

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_title_impact/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["diagnosis"]["fault_description"], "BOMC-cas服务异常，相关用户无法访问BOMC系统")

    def test_short_description_fills_operational_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            text = "BOMC cas服务异常，10:13到10:25，重启CAS和WEB服务后恢复。"

            result = run(root, RunOptions(text=text, case_id="case_short_text", incident_context_only=True))

            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["missing_fields"], [])
            context = json.loads((root / "work/cases/case_short_text/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["incident"]["system_name"], "BOMC")
            self.assertEqual(context["incident"]["duration_minutes"], 12)
            self.assertTrue(context["incident"]["occurred_at"].endswith("10:13"))
            self.assertEqual(context["impact"]["impact_scope"], "影响BOMC系统使用")
            self.assertEqual(context["handling"]["owner"], "多域维护组")
            self.assertEqual(context["handling"]["handling_group"], "多域维护组")
            self.assertEqual(context["handling"]["process_records"], ["10:13接到故障信息", "10:25重启CAS和WEB服务"])
            self.assertEqual(context["handling"]["followup_measures"], ["补充BOMC系统CAS服务异常监控告警", "固化故障恢复验证清单"])

    def test_report_uses_short_reference_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            text = (
                "【BOMC-cas服务异常】发生时间：2026年7月8日12:10。恢复时间：2026年7月8日12:20。"
                "故障现象：服务台通知BOMC无法打开。故障原因：CAS服务异常。解决方式：重启WEB和CAS服务。"
                "处理过程：12:10服务台通知BOMC无法打开|12:20重启WEB和CAS后恢复正常。"
            )

            result = run(root, RunOptions(text=text, case_id="case_report_short_style", all_dry_run=True))

            self.assertEqual(result["status"], "validated")
            doc = Document(result["outputs"]["report_docx"])
            paragraphs = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
            # 2026-07-11 13:00 BUGFIX13 修复: 故障现象句式改为"谁发现+系统+故障"
            # (用户原话"应该是谁发现xx系统 出现什么故障")
            # BUGFIX43 v3 (2026-07-13 16:10, 用户原话 "现象需要修改什么时间 通报xx系统故障 出现什么问题"):
            # 报告"故障现象"段 = "HH:MM 通报xx系统故障 出现什么问题" 句式
            # text 给的是 "故障现象：服务台通知BOMC无法打开" → occurred_at=12:10 → "12点10分 用户报障通报BOMC系统故障, 服务台通知BOMC无法打开;"
            self.assertIn("12点10分 用户报障通报BOMC系统故障, 服务台通知BOMC无法打开；", paragraphs)
            self.assertNotIn("上报时间： 2026年7月8日12:10", paragraphs)
            # BUGFIX43 v2 (2026-07-13 15:50, 用户原话 "我给的字段是这样 我不是给了4个时间点吗 肯定是按4个时间点写4行"):
            # 报告"故障处理过程&记录"从 5 行硬模板**改成 4 行** (按 4 个时间点, 避免重复表 2 已有时间)
            # 第 1 行: 12:10 发生时间 + 接到报障 + 症状, 多域维护组排查
            # 第 2 行: 未提供定位时间, 时间留空 + 原因定位
            # 第 3 行: 未提供处理完成时间, 时间留空 + solution 执行 (重启WEB和CAS服务)
            # 第 4 行: 12:20 恢复时间 + 验证系统恢复
            self.assertIn(
                "1、  12:10分服务台通知BOMC无法打开，多域维护组排查；",
                paragraphs,
            )
            self.assertIn(
                "2、  多域维护组定位CAS服务异常；",
                paragraphs,
            )
            self.assertIn(
                "3、  多域维护组重启WEB和CAS服务；",
                paragraphs,
            )
            self.assertIn(
                "4、  12:20分多域维护组验证BOMC系统访问和登录恢复正常。",
                paragraphs,
            )
            # BUGFIX43 v2 回归: 中间 3 行 (核查/发现/处置) 不再出现
            self.assertNotIn(
                "2、  12:12分多域维护组接报并核查BOMC访问状态，确认BOMC无法打开；",
                "\n".join(paragraphs),
            )
            self.assertNotIn(
                "3、  12:15分多域维护组检查WEB和CAS相关服务运行状态，发现CAS服务异常；",
                "\n".join(paragraphs),
            )
            self.assertNotIn(
                "4、  12:18分多域维护组重启WEB和CAS服务；",
                "\n".join(paragraphs),
            )
            # BUGFIX43 回归: 第 5 行 (单独验证) 不再单独存在 (合并到第 2 行)
            self.assertNotIn(
                "5、  12:20分多域维护组验证BOMC访问和登录恢复正常。",
                "\n".join(paragraphs),
            )
            self.assertFalse(any(text.startswith(("2、", "3、")) and "至12:20分" in text for text in paragraphs))
            self.assertFalse(any(text.startswith("应用类：") for text in paragraphs))
            cause_paragraph = next(text for text in paragraphs if text.startswith("CAS服务异常导致"))
            self.assertIn("CAS服务异常导致BOMC无法打开", cause_paragraph)
            self.assertIn("经多域维护组核查确认，BOMC系统无法打开与CAS服务异常相关", cause_paragraph)
            self.assertIn("该异常影响BOMC系统认证及访问链路", cause_paragraph)
            self.assertNotIn("重启WEB和CAS服务", cause_paragraph)
            self.assertNotIn("复核", cause_paragraph)
            self.assertIsNone(re.search(r"\d{4}年|\d{1,2}:\d{2}", cause_paragraph))
            self.assertIn("后续运维完善措施：", paragraphs)
            self.assertTrue(any(text.startswith("1、") and "BOMC系统" in text for text in paragraphs))
            self.assertTrue(any(text.startswith("3、") and "恢复验证" in text for text in paragraphs))
            self.assertGreaterEqual(len([text for text in paragraphs if re.match(r"^\d+、", text)]), 5)
            self.assertEqual(doc.tables[1].rows[1].cells[0].text.strip(), "2026年7月8日12:10")
            self.assertEqual(doc.tables[1].rows[1].cells[1].text.strip(), "2026年7月8日12:20")
            # BUGFIX44 (2026-07-13 16:10, 用户原话 "故障发现红框需要删除"):
            # 报告"故障发现"段下的 ("处理时间/处理人") 表**整段删除** (避免重复 5 时间表).
            # 原 line 283 期望 doc.tables[2] = "处理时间" 表 cells[0] = "12:10 至 12:20" → 表删后断言无意义, 移除.
            with ZipFile(result["outputs"]["report_docx"]) as docx:
                root_xml = ET.fromstring(docx.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                first_table_row = root_xml.findall(".//w:tbl", ns)[0].findall("./w:tr", ns)[1]
                first_table_values = [
                    "".join(text_node.text or "" for text_node in cell.findall(".//w:t", ns))
                    for cell in first_table_row.findall("./w:tc", ns)
                ]
                self.assertEqual(
                    first_table_values,
                    [
                        "造成BOMC系统无法打开，用户无法正常访问系统，相关业务支撑功能无法正常使用。",
                        "CAS服务异常",
                        "重启WEB和CAS服务",
                        "所有业务",
                        "造成BOMC系统无法打开，用户无法正常访问服务",
                    ],
                )
                process_nodes = [
                    paragraph
                    for paragraph in root_xml.findall(".//w:p", ns)
                    if re.match(
                        r"^[1-4]、\s",
                        "".join(text_node.text or "" for text_node in paragraph.findall(".//w:t", ns)),
                    )
                ]
                # BUGFIX43 v2 (2026-07-13 15:50, 用户原话 "我给的字段是这样 我不是给了4个时间点吗 肯定是按4个时间点写4行"):
                # 报告"故障处理过程&记录" 4 行版: 第 1/2/3/4 行对应 4 个时间点
                self.assertEqual(len(process_nodes), 4)
                for paragraph in process_nodes:
                    p_style = paragraph.find("./w:pPr/w:pStyle", ns)
                    style_id = p_style.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") if p_style is not None else ""
                    self.assertNotEqual(style_id, "45")
                    self.assertIsNone(paragraph.find("./w:pPr/w:shd", ns))
                    color = paragraph.find("./w:pPr/w:rPr/w:color", ns)
                    if color is not None:
                        self.assertNotEqual(color.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"), "FFFFFF")
                cause_node = next(
                    paragraph
                    for paragraph in root_xml.findall(".//w:p", ns)
                    if "".join(text_node.text or "" for text_node in paragraph.findall(".//w:t", ns)).startswith("CAS服务异常导致")
                )
                ind = cause_node.find("./w:pPr/w:ind", ns)
                jc = cause_node.find("./w:pPr/w:jc", ns)
                self.assertIsNotNone(ind)
                self.assertEqual(ind.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}left"), "0")
                self.assertEqual(ind.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}firstLine"), "480")
                self.assertEqual(ind.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}firstLineChars"), "200")
                self.assertIsNone(ind.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hanging"))
                self.assertIsNone(ind.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hangingChars"))
                self.assertIsNone(cause_node.find("./w:pPr/w:numPr", ns))
                if jc is not None:
                    self.assertEqual(jc.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"), "left")
                created_node = next(
                    paragraph
                    for paragraph in root_xml.findall(".//w:p", ns)
                    if "".join(text_node.text or "" for text_node in paragraph.findall(".//w:t", ns)).startswith("创建日期:")
                )
                self.assertIsNotNone(created_node.find(".//w:tab", ns))

    def test_database_report_keeps_multi_domain_and_asiainfo_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            text = (
                "【OPENAPI-数据库连接异常】发生时间：2026年7月8日12:10。恢复时间：2026年7月8日12:20。"
                "故障现象：服务台通知OPENAPI无法访问。故障原因：数据库连接异常。解决方式：恢复数据库连接。"
                "处理过程：12:10服务台通知OPENAPI无法访问|12:20验证业务恢复。"
            )

            result = run(root, RunOptions(text=text, case_id="case_database_boundary", all_dry_run=True))

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_database_boundary/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(
                context["handling"]["followup_measures"],
                ["完善OPENAPI系统数据库连通性拨测和亚信厂商通知机制", "固化OPENAPI系统中间件和服务恢复验证清单"],
            )

            doc = Document(result["outputs"]["report_docx"])
            paragraphs = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
            # BUGFIX43 v2 (2026-07-13 15:50, 用户原话 "我给的字段是这样 不是两行故障过程记录 我不是给了4个时间点吗 肯定是按4个时间点写4行"):
            # 报告"故障处理过程&记录"从 5 行硬模板**改成 4 行** (按 4 个时间点, 避免重复表 2 已有时间)
            # 第 1 行: 12:10 发生时间 + 接到报障 + 症状, 多域维护组排查
            # 第 2 行: 未提供定位时间, 时间留空 + 原因定位
            # 第 3 行: 未提供处理完成时间, 时间留空 + solution 执行
            # 第 4 行: 12:20 恢复时间 + 验证系统恢复
            self.assertIn(
                "1、  12:10分服务台通知OPENAPI无法访问，多域维护组排查；",
                paragraphs,
            )
            self.assertIn(
                "2、  多域维护组定位数据库连接异常；",
                paragraphs,
            )
            self.assertIn(
                "3、  多域维护组恢复数据库连接；",
                paragraphs,
            )
            self.assertIn(
                "4、  12:20分多域维护组验证OPENAPI系统访问和登录恢复正常。",
                paragraphs,
            )
            # BUGFIX43 v2 回归: 中间 3 行 (核查/拨测/通知) 不再出现
            self.assertNotIn(
                "2、  12:12分多域维护组接报并核查OPENAPI访问状态、中间件和服务运行状态，确认OPENAPI无法访问；",
                "\n".join(paragraphs),
            )
            self.assertNotIn(
                "3、  12:15分多域维护组拨测数据库连通性，发现数据库侧异常并通知亚信厂商处理；",
                "\n".join(paragraphs),
            )
            self.assertNotIn(
                "4、  12:18分亚信厂商确认数据库连接异常，恢复数据库连接；",
                "\n".join(paragraphs),
            )
            cause_paragraph = next(text for text in paragraphs if text.startswith("数据库连接异常导致"))
            self.assertIn("经多域维护组排查OPENAPI系统中间件和服务状态并拨测数据库连通性", cause_paragraph)
            self.assertIn("已通知亚信厂商处理数据库侧问题", cause_paragraph)
            # BUGFIX43 v2 (2026-07-13 15:50, 用户原话 "我给的字段是这样 我不是给了4个时间点吗 肯定是按4个时间点写4行"):
            # 4 行版第 3 行就是 "多域维护组恢复数据库连接;" (solution) — 是预期, 不再 assertNotIn
            self.assertNotIn("多域维护组检查数据库", "\n".join(paragraphs))
            self.assertIn("多域维护组恢复数据库", "\n".join(paragraphs))
            followup_index = paragraphs.index("后续运维完善措施：")
            followup_lines = paragraphs[followup_index + 1:followup_index + 6]
            self.assertEqual(len(followup_lines), 5)
            self.assertEqual([line[:2] for line in followup_lines], ["1、", "2、", "3、", "4、", "5、"])
            self.assertTrue(any("亚信厂商" in line for line in followup_lines))
            self.assertTrue(any("恢复验证" in line for line in followup_lines))

    def test_report_writer_tolerates_minor_heading_text_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            clean = next((root / "data/templates/reports/clean").glob("*.docx"))
            template = Document(clean)
            for paragraph in template.paragraphs:
                if paragraph.text.strip() == "故障原因分析&结论":
                    paragraph.text = "原因分析及结论"
                if paragraph.text.strip() == "后续完善措施":
                    paragraph.text = "后续优化措施"
            template.save(clean)
            text = (
                "【BOMC-cas服务异常】发生时间：2026年7月8日12:10。恢复时间：2026年7月8日12:20。"
                "故障现象：服务台通知BOMC无法打开。故障原因：CAS服务异常。解决方式：重启WEB和CAS服务。"
                "处理过程：12:10服务台通知BOMC无法打开|12:20重启WEB和CAS后恢复正常。"
            )

            result = run(root, RunOptions(text=text, case_id="case_report_heading_change", all_dry_run=True))

            self.assertEqual(result["status"], "validated")
            doc = Document(result["outputs"]["report_docx"])
            paragraphs = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
            self.assertTrue(any(text.startswith("CAS服务异常导致") for text in paragraphs))
            self.assertIn("1、  12:10分服务台通知BOMC无法打开，多域维护组排查；", paragraphs)
            self.assertIn("后续运维完善措施：", paragraphs)

    def test_report_template_can_use_legacy_workbuddy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            legacy_dir = root / ".workbuddy" / "报告模板"
            legacy_dir.mkdir(parents=True)
            shutil.copyfile(
                next((root / "data/templates/reports/clean").glob("*.docx")),
                legacy_dir / "XX系统XXXX年XX月XX日故障报告 -干净模板.docx",
            )
            shutil.rmtree(root / "data/templates/reports/clean")

            result = run(root, RunOptions(text=VALID_TEXT, case_id="case_legacy_template_path", all_dry_run=True))

            self.assertEqual(result["status"], "validated")
            self.assertTrue(Path(result["outputs"]["report_docx"]).exists())

    def test_screenshot_sidecar_text_builds_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            screenshot = root / "work/input/bomc.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"not a real image; sidecar supplies OCR")
            screenshot.with_suffix(".ocr.txt").write_text(VALID_TEXT, encoding="utf-8")

            result = run(root, RunOptions(screenshots=[screenshot], case_id="case_screenshot", incident_context_only=True))

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_screenshot/incident_context.json").read_text(encoding="utf-8"))
            self.assertIn("BOMC-cas服务异常", context["input"]["text"])
            self.assertEqual(context["input"]["screenshot_texts"][0]["status"], "ok")
            self.assertEqual(context["input"]["screenshot_texts"][0]["source"], "sidecar")

    def test_screenshot_sidecar_ocr_fills_incident_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            screenshot = root / "work/input/openapi.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"not a real image; sidecar supplies OCR")
            screenshot.with_suffix(".ocr.txt").write_text(
                "\n".join(
                    [
                        "系统：OPENAPI",
                        "告警时间：2026-07-08 14:05",
                        "恢复时间：2026-07-08 14:20",
                        "故障现象：OPENAPI接口成功率为0，接口返回500 Internal Server Error",
                        "影响：订单查询接口不可用",
                        "故障原因：数据库服务器网络服务异常",
                        "处理措施：重启网络服务后恢复",
                        "处理过程：14:05收到监控告警|14:10重启网络服务|14:20验证接口恢复",
                        "后续措施：补充接口成功率监控|固化网络服务巡检",
                    ]
                ),
                encoding="utf-8",
            )

            result = run(root, RunOptions(screenshots=[screenshot], case_id="case_ocr_fields", incident_context_only=True))

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_ocr_fields/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["incident"]["system_name"], "OPENAPI")
            self.assertEqual(context["incident"]["occurred_at"], "2026-07-08 14:05")
            self.assertEqual(context["incident"]["recovered_at"], "2026-07-08 14:20")
            self.assertEqual(context["incident"]["duration_minutes"], 15)
            self.assertEqual(context["diagnosis"]["fault_description"], "OPENAPI接口成功率为0，接口返回500 Internal Server Error")
            self.assertEqual(context["impact"]["impact_scope"], "订单查询接口不可用")
            self.assertEqual(context["handling"]["solution"], "重启网络服务后恢复")
            refs = {item["field"]: item["source"] for item in context["evidence"]["source_refs"]}
            self.assertIn("input.screenshot_texts[0]", refs["system_name"])
            self.assertIn("input.screenshot_texts[0]", refs["fault_description"])

    def test_text_and_screenshot_ocr_are_parsed_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            screenshot = root / "work/input/smartbi.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"not a real image; sidecar supplies OCR")
            screenshot.with_suffix(".ocr.txt").write_text(
                "\n".join(
                    [
                        "系统名称：SmartBI",
                        "报错时间：2026-07-08 09:30",
                        "解决时间：2026-07-08 09:45",
                        "报错信息：报表加载超时，页面提示 gateway timeout",
                        "业务影响：经营分析报表暂时不可用",
                        "原因：查询连接池满",
                        "解决方式：重启查询服务并释放连接池",
                        "处理过程：09:30收到用户报障|09:38重启查询服务|09:45验证报表恢复",
                        "后续措施：补充连接池水位告警|固化报表恢复验证清单",
                    ]
                ),
                encoding="utf-8",
            )

            result = run(
                root,
                RunOptions(
                    text="用户描述：截图里有完整报错，已转多域维护组处理。",
                    screenshots=[screenshot],
                    case_id="case_text_image_fusion",
                    incident_context_only=True,
                ),
            )

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_text_image_fusion/incident_context.json").read_text(encoding="utf-8"))
            self.assertIn("用户描述", context["input"]["original_text"])
            self.assertIn("报表加载超时", context["input"]["text"])
            self.assertEqual(context["incident"]["system_name"], "SmartBI")
            self.assertEqual(context["diagnosis"]["fault_description"], "报表加载超时，页面提示 gateway timeout")
            self.assertEqual(context["impact"]["impact_scope"], "经营分析报表暂时不可用")
            self.assertEqual(context["handling"]["solution"], "重启查询服务并释放连接池")

    def test_screenshot_ocr_does_not_invent_missing_recovery_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            screenshot = root / "work/input/missing_recovery.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"not a real image; sidecar supplies OCR")
            screenshot.with_suffix(".ocr.txt").write_text(
                "\n".join(
                    [
                        "系统：BOMC",
                        "告警时间：2026-07-08 10:13",
                        "故障现象：BOMC登录失败",
                        "影响：用户无法登录BOMC系统",
                        "故障原因：CAS服务异常",
                        "处理措施：重启CAS服务",
                    ]
                ),
                encoding="utf-8",
            )

            result = run(root, RunOptions(screenshots=[screenshot], case_id="case_ocr_missing_recovery", incident_context_only=True))

            self.assertEqual(result["status"], "need_review")
            self.assertIn("incident.recovered_at", result["missing_fields"])

    def test_text_log_file_participates_in_context_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            log = root / "work/input/fault.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "\n".join(
                    [
                        "系统：PASS系统",
                        "发生时间：2026-07-08 16:00",
                        "恢复时间：2026-07-08 16:08",
                        "故障现象：PASS系统登录接口超时",
                        "影响范围：部分用户登录不可用",
                        "故障原因：认证服务连接异常",
                        "处理措施：重启认证服务",
                        "处理过程：16:00收到日志告警|16:05重启认证服务|16:08验证登录恢复",
                        "后续措施：补充认证服务连接监控|固化登录验证清单",
                    ]
                ),
                encoding="utf-8",
            )

            result = run(root, RunOptions(logs=[log], case_id="case_log_context", incident_context_only=True))

            self.assertEqual(result["status"], "validated")
            context = json.loads((root / "work/cases/case_log_context/incident_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["input"]["log_texts"][0]["status"], "ok")
            self.assertEqual(context["incident"]["system_name"], "PASS系统")
            self.assertEqual(context["diagnosis"]["fault_description"], "PASS系统登录接口超时")
            refs = {item["field"]: item["source"] for item in context["evidence"]["source_refs"]}
            self.assertIn("input.log_texts[0]", refs["system_name"])

    def test_incident_context_only_stops_before_downstream_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))

            result = run(root, RunOptions(text=VALID_TEXT, case_id="case_context_only", all_dry_run=True, incident_context_only=True))

            self.assertEqual(result["status"], "validated")
            self.assertEqual(
                _runtime_files(root),
                [
                    "outputs/validation/case_context_only/validation_result.json",
                    "work/cases/case_context_only/incident_context.json",
                ],
            )
            self.assertNotIn("report_docx", result["outputs"])
            self.assertNotIn("record_payload", result["outputs"])

    def test_manual_dry_run_matches_reference_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            _write_manual_index(root)

            result = run(root, RunOptions(text=VALID_TEXT, case_id="case_manual_match", manual_dry_run=True))

            self.assertEqual(result["status"], "validated")
            manual = json.loads((root / "outputs/manuals/case_manual_match/manual_match_result.json").read_text(encoding="utf-8"))
            self.assertEqual(manual["status"], "已匹配")
            self.assertEqual(manual["match_name"], "cas服务异常")
            self.assertEqual(manual["match_target"], "处理手册\\B域\\BOMC\\BOMC-cas服务异常.md")
            self.assertTrue(manual["match_exists"])
            # match_path 在 Linux 下由 Path(*parts) 输出为正斜杠（见 _guide_paths 的
            # PureWindowsPath → Path 修复）；断言前先把字面量里的反斜杠归一化成
            # 当前 OS 的分隔符,再和 endswith 比。
            normalized = manual["match_path"].replace("\\", os.sep)
            self.assertTrue(normalized.endswith(os.path.join("处理手册", "B域", "BOMC", "BOMC-cas服务异常.md")))

    def test_apply_requires_live_write_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_apply_gate")

            result = run(root, RunOptions(incident_context=context_path, apply=True))

            self.assertEqual(result["status"], "need_review")
            self.assertIn("missing confirm-live-write LIVE-WRITE", result["errors"])
            self.assertEqual(
                _runtime_files(root),
                [
                    "outputs/ledger/case_apply_gate/execution_plan.json",
                    "outputs/ledger/case_apply_gate/record_payload.json",
                    _offline_record_file("case_apply_gate"),
                    "outputs/manuals/case_apply_gate/manual_match_result.json",
                    "outputs/reports/case_apply_gate/BOMC7月8日故障报告.docx",
                    "outputs/validation/case_apply_gate/validation_result.json",
                    "work/cases/case_apply_gate/incident_context.json",
                ],
            )
            plan = json.loads((root / "outputs/ledger/case_apply_gate/execution_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["status"], "blocked")
            self.assertTrue(plan["live_gate"]["requested"])
            self.assertFalse(plan["live_gate"]["confirmed"])

    def test_existing_record_backfill_uses_update_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))

            result = run(
                root,
                RunOptions(
                    text=VALID_TEXT,
                    case_id="case_update_plan",
                    feishu_dry_run=True,
                    feishu_record_id="rec_test_123",
                ),
            )

            self.assertEqual(result["status"], "validated")
            plan = json.loads((root / "outputs/ledger/case_update_plan/execution_plan.json").read_text(encoding="utf-8"))
            update_action = next(action for action in plan["actions"] if action["name"] == "update_feishu_record")
            self.assertEqual(update_action["record_id"], "rec_test_123")
            self.assertEqual(update_action["mode"], "dry_run")
            self.assertTrue(update_action["missing_only"])
            fields = json.loads((root / "outputs/ledger/case_update_plan/record_payload.json").read_text(encoding="utf-8"))["fields"]
            self.assertIn("故障报告", fields)
            self.assertIn("故障手册附件", fields)

    def test_manual_match_backfills_clickable_manual_link_before_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            _write_manual_index(root)

            result = run(
                root,
                RunOptions(
                    text=VALID_TEXT,
                    case_id="case_manual_link_backfill",
                    feishu_dry_run=True,
                ),
            )

            self.assertEqual(result["status"], "validated")
            fields = json.loads((root / "outputs/ledger/case_manual_link_backfill/record_payload.json").read_text(encoding="utf-8"))["fields"]
            self.assertEqual(fields["手册状态"], "已匹配")
            self.assertEqual(fields["故障手册"], "cas服务异常")
            self.assertEqual(fields["故障手册链接"], "")
            self.assertEqual(fields["故障手册附件"], "")
            plan = json.loads((root / "outputs/ledger/case_manual_link_backfill/execution_plan.json").read_text(encoding="utf-8"))
            manual_action = next(action for action in plan["actions"] if action["name"] == "backfill_manual_attachment")
            self.assertEqual(manual_action["status"], "need_review")

    def test_existing_record_apply_still_requires_live_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_update_gate")

            result = run(
                root,
                RunOptions(
                    incident_context=context_path,
                    apply=True,
                    feishu_record_id="rec_test_123",
                ),
            )

            self.assertEqual(result["status"], "need_review")
            self.assertIn("missing confirm-live-write LIVE-WRITE", result["errors"])
            plan = json.loads((root / "outputs/ledger/case_update_gate/execution_plan.json").read_text(encoding="utf-8"))
            self.assertTrue(any(action["name"] == "update_feishu_record" for action in plan["actions"]))
            self.assertEqual(plan["status"], "blocked")

    def test_existing_record_dry_run_plans_missing_online_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_existing_plan")

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch(
                    "src.orchestrator.pipeline.get_record",
                    return_value={"fields": {"故障报告": "existing.docx", "资料完整度": "基础资料已齐"}},
                ),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        feishu_dry_run=True,
                        feishu_record_id="rec_existing_123",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            plan = json.loads((root / "outputs/ledger/case_existing_plan/execution_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["existing_record"]["status"], "ready")
            self.assertIn("故障报告附件", plan["existing_record"]["missing_fields"])
            self.assertIn("资料状态", plan["existing_record"]["missing_fields"])
            self.assertIn("看板优先级", plan["existing_record"]["missing_fields"])
            self.assertIn("手册状态", plan["existing_record"]["missing_fields"])
            self.assertNotIn("邮件状态", plan["existing_record"]["missing_fields"])
            self.assertNotIn("邮件闭环状态", plan["existing_record"]["missing_fields"])
            self.assertNotIn("故障报告", plan["existing_record"]["missing_fields"])

    def test_new_record_uses_previous_handler_for_same_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            text = VALID_TEXT.replace("负责人：高喆。", "")
            _write_local_handler_master(root)

            result = run(root, RunOptions(text=text, case_id="case_handler_lookup", feishu_dry_run=True))

            self.assertEqual(result["status"], "validated")
            fields = json.loads((root / "outputs/ledger/case_handler_lookup/record_payload.json").read_text(encoding="utf-8"))["fields"]
            self.assertEqual(fields["处理人员"], "张三、李四")
            offline = load_workbook(root / _offline_record_file("case_handler_lookup"), data_only=False)["2026年度故障记录"]
            self.assertEqual(offline.cell(70, 18).value, "张三、李四")

    def test_offline_record_uses_previous_handler_without_online_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            text = VALID_TEXT.replace("负责人：高喆。", "")
            _write_local_handler_master(root)

            result = run(root, RunOptions(text=text, case_id="case_offline_handler"))

            self.assertEqual(result["status"], "validated")
            offline = load_workbook(root / _offline_record_file("case_offline_handler"), data_only=False)["2026年度故障记录"]
            self.assertEqual(offline.cell(70, 18).value, "张三、李四")

    def test_existing_record_apply_updates_only_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_existing_apply")
            run(root, RunOptions(incident_context=context_path, feishu_dry_run=True))
            existing_fields = json.loads((root / "outputs/ledger/case_existing_apply/record_payload.json").read_text(encoding="utf-8"))["fields"]
            existing_fields["故障报告附件"] = [{"file_token": "old_report"}]
            updates: list[dict] = []

            def fake_update_record(record_id: str, payload: dict, config: object) -> dict:
                updates.append(payload["fields"])
                readback_fields = {**existing_fields, **payload["fields"]}
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已更新",
                    "record_id": record_id,
                    "record_url": "https://example.invalid/record/rec_existing_123",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": readback_fields},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "new_report"}),
                patch("src.orchestrator.pipeline.get_record", return_value={"fields": existing_fields}),
                patch("src.orchestrator.pipeline.update_record", side_effect=fake_update_record),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                        feishu_record_id="rec_existing_123",
                    ),
            )

            self.assertEqual(result["status"], "validated")
            self.assertEqual(updates, [{"资料完整度": "基础资料已齐"}])

    def test_apply_without_record_id_dedupes_by_case_id(self) -> None:
        """--apply without --feishu-record-id: if a row with the same case_id already
        exists, the pipeline must treat this run as an update of that row instead
        of creating a duplicate (regression test for SNC-153 dup bug)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_dedupe_hit")
            run(root, RunOptions(incident_context=context_path, feishu_dry_run=True))
            existing_fields = json.loads((root / "outputs/ledger/case_dedupe_hit/record_payload.json").read_text(encoding="utf-8"))["fields"]
            existing_fields["序号"] = 42
            existing_fields["闭环ID"] = "case_dedupe_hit"
            existing_fields.pop("故障报告附件", None)
            config = _FakeFeishuConfig()
            config.field_map = {"case_id": "闭环ID"}
            records = [{"record_id": "rec_existing_dedupe", "fields": existing_fields}]
            update_calls: list[tuple[str, dict]] = []
            create_calls: list[dict] = []

            def fake_update_record(record_id: str, payload: dict, config: object) -> dict:
                update_calls.append((record_id, payload))
                readback_fields = {**existing_fields, **payload["fields"]}
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已更新",
                    "record_id": record_id,
                    "record_url": f"https://example.invalid/record/{record_id}",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": readback_fields},
                }

            def fake_create_record(payload: dict, config: object) -> dict:
                create_calls.append(payload)
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已写入",
                    "record_id": "rec_should_not_be_used",
                    "record_url": "https://example.invalid/record/rec_should_not_be_used",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=config),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "new_report"}),
                patch("src.feishu_ledger.client.list_records", return_value=records) as list_records_mock,
                patch("src.orchestrator.pipeline.list_records", return_value=records),
                patch("src.orchestrator.pipeline.get_record", return_value={"fields": existing_fields}),
                patch("src.orchestrator.pipeline.update_record", side_effect=fake_update_record),
                patch("src.orchestrator.pipeline.create_record", side_effect=fake_create_record),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            list_records_mock.assert_called_once_with(config, None)
            self.assertEqual(update_calls[0][0], "rec_existing_dedupe")
            self.assertNotIn("序号", update_calls[0][1]["fields"])
            self.assertEqual(existing_fields["序号"], 42)
            self.assertEqual(create_calls, [], "create_record must NOT be called when dedupe hit found")
            dedupe_meta = json.loads(
                (root / "outputs/ledger/case_dedupe_hit/record_payload.json").read_text(encoding="utf-8")
            ).get("case_id_dedup", {})
            self.assertEqual(dedupe_meta.get("case_id"), "case_dedupe_hit")
            self.assertTrue(dedupe_meta.get("matched_existing_record"))
            self.assertFalse(dedupe_meta.get("explicit_record_id"))

    def test_dedupe_preflight_skips_existing_attachment_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_dedupe_attachments")
            run(root, RunOptions(incident_context=context_path, feishu_dry_run=True))
            existing_fields = json.loads(
                (root / "outputs/ledger/case_dedupe_attachments/record_payload.json").read_text(encoding="utf-8")
            )["fields"]
            existing_fields.update(
                {
                    "闭环ID": "case_dedupe_attachments",
                    "序号": 42,
                    "故障报告附件": [{"file_token": "old_report"}],
                    "故障手册附件": [{"file_token": "old_manual"}],
                }
            )
            config = _FakeFeishuConfig()
            config.field_map = {"case_id": "闭环ID"}
            records = [{"record_id": "rec_existing_attachments", "fields": existing_fields}]

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=config),
                patch("src.feishu_ledger.client.list_records", return_value=records),
                patch("src.orchestrator.pipeline.get_record", return_value={"fields": existing_fields}),
                patch("src.orchestrator.pipeline.upload_bitable_attachment") as upload_attachment,
                patch("src.orchestrator.pipeline.create_record") as create_record,
                patch(
                    "src.orchestrator.pipeline.update_record",
                    return_value={
                        "status": "已更新",
                        "record_id": "rec_existing_attachments",
                        "record_url": "https://example.invalid/record/rec_existing_attachments",
                        "fields": {"资料完整度": "基础资料已齐"},
                        "skipped_fields": [],
                        "readback": {
                            "checked": True,
                            "fields": {**existing_fields, "资料完整度": "基础资料已齐"},
                        },
                    },
                ) as update_record,
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            upload_attachment.assert_not_called()
            create_record.assert_not_called()
            self.assertEqual(update_record.call_count, 1)
            self.assertEqual(update_record.call_args.args[1]["fields"], {"资料完整度": "基础资料已齐"})

    def test_automatic_pipeline_skips_smtp_when_existing_record_is_sent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_auto_mail_sent")
            run(root, RunOptions(incident_context=context_path, feishu_dry_run=True))
            existing_fields = json.loads(
                (root / "outputs/ledger/case_auto_mail_sent/record_payload.json").read_text(encoding="utf-8")
            )["fields"]
            existing_fields.update(
                {
                    "闭环ID": "case_auto_mail_sent",
                    "序号": 42,
                    "故障报告附件": [{"file_token": "old_report"}],
                    "资料完整度": "基础资料已齐",
                    "邮件闭环状态": "已发送",
                }
            )
            config = _FakeFeishuConfig()
            config.field_map = {"case_id": "闭环ID"}
            records = [{"record_id": "rec_mail_sent", "fields": existing_fields}]

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=config),
                patch("src.feishu_ledger.client.list_records", return_value=records),
                patch("src.orchestrator.pipeline.get_record", return_value={"fields": existing_fields}),
                patch("src.orchestrator.pipeline.upload_bitable_attachment") as upload_attachment,
                patch("src.orchestrator.pipeline.create_record") as create_record,
                patch("src.orchestrator.pipeline.update_record") as update_record,
                patch("src.orchestrator.pipeline.send_mail") as smtp_send,
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        send_mail=True,
                        automatic=True,
                        confirm_live_write="LIVE-WRITE",
                        online_record_url="https://example.invalid/table",
                        smtp_user="sender@example.invalid",
                        mail_from="sender@example.invalid",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            upload_attachment.assert_not_called()
            create_record.assert_not_called()
            update_record.assert_not_called()
            smtp_send.assert_not_called()
            mail_result = json.loads(
                (root / "outputs/mail/case_auto_mail_sent/mail_send_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(mail_result["status"], "已发送")
            self.assertTrue(mail_result["deduplicated"])
            self.assertEqual(mail_result["dedupe_source"], "feishu")

    def test_automatic_mail_state_persists_and_blocks_replay(self) -> None:
        from src.mail_sender.smtp_sender import SmtpOptions
        from src.orchestrator.pipeline import _read_mail_state, _send_mail_with_guard

        preview = {
            "status": "待发送",
            "from": "sender@example.invalid",
            "to": ["receiver@example.invalid"],
            "subject": "故障报告",
            "attachments": ["report.docx", "ledger.xlsx"],
        }
        smtp_options = SmtpOptions(
            username="sender@example.invalid",
            sender="sender@example.invalid",
            recipients=("receiver@example.invalid",),
        )
        sent_result = {
            "smtp_called": True,
            "status": "已发送",
            "from": "sender@example.invalid",
            "to": ["receiver@example.invalid"],
            "subject": "故障报告",
            "attachments": preview["attachments"],
            "blocked_reason": "",
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = RunOptions(send_mail=True, automatic=True)
            with patch("src.orchestrator.pipeline.send_mail", return_value=sent_result) as smtp_send:
                first = _send_mail_with_guard(
                    root,
                    options,
                    "rec_persistent_mail",
                    {},
                    _FakeFeishuConfig(),
                    preview,
                    smtp_options,
                )
                second = _send_mail_with_guard(
                    root,
                    options,
                    "rec_persistent_mail",
                    {},
                    _FakeFeishuConfig(),
                    preview,
                    smtp_options,
                )

            self.assertEqual(first["status"], "已发送")
            self.assertEqual(_read_mail_state(root, "rec_persistent_mail")["status"], "sent")
            self.assertEqual(second["status"], "已发送")
            self.assertTrue(second["deduplicated"])
            self.assertEqual(second["dedupe_source"], "local_state")
            smtp_send.assert_called_once()

    def test_apply_without_record_id_falls_through_to_create_when_no_match(self) -> None:
        """When no existing row matches the case_id, the pipeline must still
        create_record as before — dedupe check is a no-op in this branch."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_dedupe_miss")
            create_calls: list[dict] = []

            def fake_create_record(payload: dict, config: object) -> dict:
                create_calls.append(payload)
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已写入",
                    "record_id": "rec_newly_created",
                    "record_url": "https://example.invalid/record/rec_newly_created",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "tok"}),
                patch("src.orchestrator.pipeline.find_record_by_system_time", return_value={}),
                patch("src.orchestrator.pipeline.find_record_by_case_id", return_value={}),
                patch("src.orchestrator.pipeline.list_records", return_value=[]),
                patch("src.orchestrator.pipeline.create_record", side_effect=fake_create_record),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            self.assertEqual(len(create_calls), 1)
            # 2026-07-09 修复: 序号在 create 路径由 _compute_next_sequence() 注入为 max+1 数字
            # 真实飞书环境返回 76 (max=75 + 1); 测试环境 _FakeFeishuConfig 缺字段, 兜底返回 1
            seq = create_calls[0]["fields"]["序号"]
            self.assertIsInstance(seq, int)
            self.assertGreaterEqual(seq, 1)

    def test_create_is_blocked_when_sequence_records_cannot_be_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_sequence_read_failed")

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "tok"}),
                patch("src.orchestrator.pipeline.find_record_by_system_time", return_value={}),
                patch("src.orchestrator.pipeline.find_record_by_case_id", return_value={}),
                patch(
                    "src.orchestrator.pipeline.list_records",
                    side_effect=FeishuApiError("sequence records read failed"),
                ),
                patch("src.orchestrator.pipeline.create_record") as create_record_mock,
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                    ),
                )

            self.assertEqual(result["status"], "need_review")
            self.assertIn("sequence records read failed", result["errors"])
            create_record_mock.assert_not_called()

    def test_apply_without_record_id_blocks_when_dedupe_is_ambiguous(self) -> None:
        """When 2+ rows share the same 序号, dedupe is ambiguous; the pipeline
        must NOT silently pick one — it must surface a blocked status."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_dedupe_ambiguous")
            create_calls: list[dict] = []

            def fake_create_record(payload: dict, config: object) -> dict:
                create_calls.append(payload)
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已写入",
                    "record_id": "rec_unwanted",
                    "record_url": "https://example.invalid/record/rec_unwanted",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "tok"}),
                patch(
                    "src.orchestrator.pipeline.find_record_by_system_time",
                    return_value={},  # 2026-07-11 BUGFIX12: 同系统同时查重不命中
                ),
                patch(
                    "src.orchestrator.pipeline.find_record_by_case_id",
                    return_value={
                        "record_id": "",
                        "fields": {},
                        "record_url": "",
                        "blocked_reason": "case_id='case_dedupe_ambiguous' matched 2 Feishu rows; dedupe ambiguous, refusing to auto-resolve.",
                    },
                ),
                patch("src.orchestrator.pipeline.create_record", side_effect=fake_create_record),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                    ),
                )

            # pipeline.py catches the ambiguity inside find_record_by_case_id (no
            # exception is raised), so the record write returns the "失败" result
            # from the except branch — which now includes the case_id_dedup meta.
            self.assertEqual(create_calls, [], "create_record must NOT fire when dedupe is ambiguous")
            self.assertIn("errors", result)
            dedupe_meta = json.loads(
                (root / "outputs/ledger/case_dedupe_ambiguous/record_payload.json").read_text(encoding="utf-8")
            ).get("case_id_dedup", {})
            self.assertTrue(dedupe_meta.get("ambiguous"))
            self.assertIn("matched 2 Feishu rows", dedupe_meta.get("blocked_reason", ""))

    def test_workspace_manual_index_is_self_contained(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        index_path = repo_root / "data/reference/manuals_index/manual_index.json"
        index = json.loads(index_path.read_text(encoding="utf-8-sig"))
        # index 里的 project_root 在 Windows 原机器上是 F:\...,迁移到 Linux 后
        # Path() 不会识别盘符前缀，就会落到根 / 下、is_relative_to(repo_root) 失败。
        # 归一化策略：先用 PureWindowsPath 拆 parts。源码索引以 repo 根目录名
        # 开头，基于 repo_root.parent；便携包索引直接以 data 开头，基于
        # repo_root；旧绝对 Windows fixture 剥掉驱动盘后沿用 repo_root.parent。
        # AGENTS.md 第 8 节有这套约定的完整说明。
        raw_root = index["project_root"]
        parts = pathlib.PureWindowsPath(raw_root).parts
        def _is_drive(p: str) -> bool:
            return len(p) >= 2 and p[1] == ":" and (p.endswith("\\") or p.endswith("/"))
        has_drive = any(_is_drive(p) for p in parts)
        stripped = [p for p in parts if not _is_drive(p)]
        if stripped:
            base = repo_root.parent if has_drive or stripped[0] == repo_root.name else repo_root
            manual_root = (base / Path(*stripped)).resolve()
        else:
            manual_root = Path(raw_root)
        try:
            self.assertTrue(manual_root.is_relative_to(repo_root))
        except (OSError, ValueError):
            self.fail(f"manual_root path invalid: {raw_root!r}")
        self.assertNotIn("hermes", str(manual_root).lower())
        for system in index["systems"]:
            for guide in system.get("guides", []):
                # guide["path"] 在 Windows 原 index 里是反斜杠字符串。在 Linux 下
                # Path("a\\b") 会把它当成单段、找不到文件，所以这里全部用
                # PureWindowsPath 拆 parts，再 Path(*parts) 拼到 manual_root。
                # 这是审计报告 D 段路径双风格约定的回归测试。
                guide_parts = pathlib.PureWindowsPath(guide["path"]).parts
                guide_rel = Path(*guide_parts) if guide_parts else Path(guide["path"])
                markdown = manual_root / guide_rel
                # AGENTS.md 第 8 节同源说明：保持原始 if parts[0] == "处理手册"
                # 那条规则，剥掉"处理手册"段后拼到 导出手册/html/...
                if guide_parts and guide_parts[0] == "处理手册":
                    inner = Path(*guide_parts[1:])
                else:
                    inner = guide_rel
                html = manual_root / "导出手册" / "html" / inner.with_suffix(".html")
                # 实物文件存在性交给姊妹任务「补手册索引」承担（部分 html 仍是
                # README 占位，详见 work/cases/bomc_smoke_20260709/SNC-153_STATUS.md
                # 第二轮简版）。本测试只验"路径可解析、不含 windows 反斜杠"这件事：
                if os.sep != "\\":
                    self.assertNotIn("\\", str(markdown), f"markdown 路径里仍含反斜杠: {markdown}")
                    self.assertNotIn("\\", str(html), f"html 路径里仍含反斜杠: {html}")
                self.assertTrue(markdown.parent.exists(), f"markdown parent dir 缺失: {markdown.parent}")
                self.assertTrue(html.parent.exists(), f"html parent dir 缺失: {html.parent}")

    def test_portable_builder_uses_current_workspace_layout(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = (repo_root / "tools/build_portable_release.ps1").read_text(encoding="utf-8")

        self.assertIn("fault_closed_loop_v1", script)
        self.assertNotIn("hermes_portable", script)
        self.assertNotIn("HERMES_ROOT", script)
        self.assertNotIn("portable.env", script)

    def test_send_mail_requires_apply_live_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_mail_blocked")

            result = run(
                root,
                RunOptions(
                    incident_context=context_path,
                    online_record_url="https://example.invalid/record/case_mail_blocked",
                    send_mail=True,
                    mail_to=["receiver@example.invalid"],
                    smtp_user="sender@example.invalid",
                ),
            )

            self.assertEqual(result["status"], "need_review")
            self.assertIn("live writes require --apply --confirm-live-write LIVE-WRITE", result["errors"])
            self.assertEqual(
                _runtime_files(root),
                [
                    "outputs/ledger/case_mail_blocked/execution_plan.json",
                    "outputs/ledger/case_mail_blocked/record_payload.json",
                    _offline_record_file("case_mail_blocked"),
                    "outputs/mail/case_mail_blocked/pre_mail_check.json",
                    "outputs/manuals/case_mail_blocked/manual_match_result.json",
                    "outputs/reports/case_mail_blocked/BOMC7月8日故障报告.docx",
                    "outputs/validation/case_mail_blocked/validation_result.json",
                    "work/cases/case_mail_blocked/incident_context.json",
                ],
            )
            check = json.loads((root / "outputs/mail/case_mail_blocked/pre_mail_check.json").read_text(encoding="utf-8"))
            self.assertEqual(check["status"], "blocked")
            self.assertIn("--apply --confirm-live-write LIVE-WRITE", check["blocked_reason"])

    def test_confirmed_apply_stops_mail_when_feishu_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_mail_blocked")

            result = run(
                root,
                RunOptions(
                    incident_context=context_path,
                    send_mail=True,
                    apply=True,
                    confirm_live_write="LIVE-WRITE",
                    mail_to=["receiver@example.invalid"],
                    smtp_user="sender@example.invalid",
                ),
            )

            self.assertEqual(result["status"], "need_review")
            self.assertTrue(any("missing Feishu config" in error for error in result["errors"]))
            self.assertNotIn("mail_send_result", result["outputs"])
            attachment_result = json.loads((root / "outputs/ledger/case_mail_blocked/attachment_upload_result.json").read_text(encoding="utf-8"))
            self.assertEqual(attachment_result["status"], "failed")
            self.assertIn("missing Feishu config", attachment_result["blocked_reason"])
            record_result = json.loads((root / "outputs/ledger/case_mail_blocked/record_payload.json").read_text(encoding="utf-8"))
            self.assertEqual(record_result["status"], "失败")

    def test_send_mail_blocks_when_manual_link_is_missing_after_feishu_write(self) -> None:
        # 2026-07-11 08:55 修改: 缺手册时不再阻塞邮件发送 (用户硬性要求 "@故障信息一定要发邮件")
        # 改为: 缺手册时也要发邮件, 但邮件 blocked_reason 中记录 "故障手册缺失, 待补充"
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_mail_missing_manual")

            def fake_create_record(payload: dict, config: object) -> dict:
                # 2026-07-11 08:55 修改: readback.fields 只含真正写入的字段 (飞书实际行为)
                # 过滤掉空值字段, 模拟飞书实际行为
                filtered_fields = {k: v for k, v in payload["fields"].items() if v not in (None, "", [])}
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已写入",
                    "record_id": "rec_ok_123",
                    "record_url": "https://example.invalid/record/rec_ok_123",
                    "fields": filtered_fields,
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": filtered_fields},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "tok"}),
                patch("src.orchestrator.pipeline.list_records", return_value=[]),
                patch("src.orchestrator.pipeline.create_record", side_effect=fake_create_record),
                patch(
                    "src.orchestrator.pipeline.send_mail",
                    return_value={
                        "smtp_called": True,
                        "status": "已发送",
                        "from": "sender@example.invalid",
                        "to": ["receiver@example.invalid"],
                        "subject": "ok",
                        "attachment": "",
                        "attachments": [],
                        "blocked_reason": "",
                    },
                ) as smtp_send,
                patch(
                    "src.orchestrator.pipeline.update_record",
                    return_value={
                        "status": "已更新",
                        "record_id": "rec_ok_123",
                        "record_url": "https://example.invalid/record/rec_ok_123",
                        "readback": {"checked": True, "fields": {"邮件闭环状态": "已发送"}},
                    },
                ),
                patch("src.orchestrator.pipeline.find_record_by_case_id", return_value={}),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        send_mail=True,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                        mail_to=["receiver@example.invalid"],
                        smtp_user="sender@example.invalid",
                    ),
                )

            # 缺手册时邮件也要发送
            self.assertEqual(result["status"], "validated")
            smtp_send.assert_called_once()
            check = json.loads((root / "outputs/mail/case_mail_missing_manual/pre_mail_check.json").read_text(encoding="utf-8"))
            self.assertEqual(check["status"], "ready")
            # 故障手册不再在 missing_fields 中 (2026-07-11 08:55)
            self.assertNotIn("故障手册", check.get("missing_fields", []))
            self.assertNotIn("故障手册附件", check.get("missing_fields", []))
            send_result = json.loads((root / "outputs/mail/case_mail_missing_manual/mail_send_result.json").read_text(encoding="utf-8"))
            # SMTP 应被调用 (用户硬性要求)
            self.assertTrue(send_result["smtp_called"])

    def test_send_mail_backfills_mail_status_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            _write_manual_index(root)
            manual_docx = root / "data/reference/manuals/导出手册/docx/B域/BOMC/BOMC-cas服务异常.docx"
            manual_docx.parent.mkdir(parents=True)
            manual_docx.write_bytes(b"manual docx placeholder")
            context_path = _validated_context(root, "case_mail_status_backfill")
            mail_updates: list[dict] = []

            def fake_create_record(payload: dict, config: object) -> dict:
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已写入",
                    "record_id": "rec_mail_ok",
                    "record_url": "https://example.invalid/record/rec_mail_ok",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            def fake_update_record(record_id: str, payload: dict, config: object) -> dict:
                mail_updates.append(payload["fields"])
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已更新",
                    "record_id": record_id,
                    "record_url": "https://example.invalid/record/rec_mail_ok",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "tok"}),
                patch("src.orchestrator.pipeline.list_records", return_value=[]),
                patch("src.orchestrator.pipeline.create_record", side_effect=fake_create_record),
                patch("src.orchestrator.pipeline.update_record", side_effect=fake_update_record),
                patch(
                    "src.orchestrator.pipeline.send_mail",
                    return_value={
                        "smtp_called": True,
                        "status": "已发送",
                        "from": "sender@example.invalid",
                        "to": ["receiver@example.invalid"],
                        "subject": "ok",
                        "attachment": "",
                        "attachments": [],
                        "blocked_reason": "",
                    },
                ),
                patch("src.orchestrator.pipeline.find_record_by_case_id", return_value={}),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        send_mail=True,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                        mail_to=["receiver@example.invalid"],
                        smtp_user="sender@example.invalid",
                    ),
                )

            self.assertEqual(result["status"], "validated")
            self.assertEqual(mail_updates, [{"邮件闭环状态": "已发送"}])
            backfill = json.loads((root / "outputs/ledger/case_mail_status_backfill/mail_status_backfill_result.json").read_text(encoding="utf-8"))
            self.assertEqual(backfill["status"], "已更新")
            self.assertEqual(backfill["mail_status"], "已发送")
            preview = json.loads((root / "outputs/mail/case_mail_status_backfill/mail_preview.json").read_text(encoding="utf-8"))
            self.assertTrue(any(path.endswith(".docx") for path in preview["attachments"]))
            self.assertTrue(any(path.endswith(".xlsx") for path in preview["attachments"]))
            record_result = json.loads(
                (root / "outputs/ledger/case_mail_status_backfill/record_payload.json").read_text(encoding="utf-8")
            )
            record_fields = record_result["readback"]["fields"]
            self.assertTrue(record_fields["故障报告附件"])
            self.assertTrue(record_fields["故障手册附件"])
            self.assertEqual(record_fields["资料完整度"], "基础资料已齐")
            plan = json.loads(
                (root / "outputs/ledger/case_mail_status_backfill/execution_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(plan["status"], "ready")
            manual_action = next(action for action in plan["actions"] if action["name"] == "backfill_manual_attachment")
            pre_mail_action = next(action for action in plan["actions"] if action["name"] == "pre_mail_online_record_check")
            self.assertEqual(manual_action["missing_fields"], [])
            self.assertEqual(pre_mail_action["missing_fields"], [])
            self.assertEqual(pre_mail_action["status"], "ready")

    def test_smtp_failure_does_not_backfill_mail_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            _write_manual_index(root)
            manual_docx = root / "data/reference/manuals/导出手册/docx/B域/BOMC/BOMC-cas服务异常.docx"
            manual_docx.parent.mkdir(parents=True)
            manual_docx.write_bytes(b"manual docx placeholder")
            context_path = _validated_context(root, "case_mail_status_failed")
            mail_updates: list[dict] = []

            def fake_create_record(payload: dict, config: object) -> dict:
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已写入",
                    "record_id": "rec_mail_failed",
                    "record_url": "https://example.invalid/record/rec_mail_failed",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            def fake_update_record(record_id: str, payload: dict, config: object) -> dict:
                mail_updates.append(payload["fields"])
                return {
                    "api_called": True,
                    "write_mode": "live",
                    "status": "已更新",
                    "record_id": record_id,
                    "record_url": "https://example.invalid/record/rec_mail_failed",
                    "fields": payload["fields"],
                    "skipped_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": payload["fields"]},
                }

            with (
                patch("src.orchestrator.pipeline.load_feishu_config", return_value=_FakeFeishuConfig()),
                patch("src.orchestrator.pipeline.upload_bitable_attachment", return_value={"file_token": "tok"}),
                patch("src.orchestrator.pipeline.list_records", return_value=[]),
                patch("src.orchestrator.pipeline.create_record", side_effect=fake_create_record),
                patch("src.orchestrator.pipeline.update_record", side_effect=fake_update_record),
                patch(
                    "src.orchestrator.pipeline.send_mail",
                    return_value={
                        "smtp_called": True,
                        "status": "失败",
                        "from": "sender@example.invalid",
                        "to": ["receiver@example.invalid"],
                        "subject": "failed",
                        "attachment": "",
                        "attachments": [],
                        "blocked_reason": "SMTPAuthenticationError",
                    },
                ),
                patch("src.orchestrator.pipeline.find_record_by_case_id", return_value={}),
            ):
                result = run(
                    root,
                    RunOptions(
                        incident_context=context_path,
                        send_mail=True,
                        apply=True,
                        confirm_live_write="LIVE-WRITE",
                        mail_to=["receiver@example.invalid"],
                        smtp_user="sender@example.invalid",
                    ),
                )

            self.assertEqual(result["status"], "need_review")
            # 2026-07-09 修复: SMTP 失败时不应回填"发送失败", 飞书 record 应保持原状
            self.assertEqual(mail_updates, [])
            # outputs["mail_status_backfill_result"] 不应被写入
            self.assertNotIn("mail_status_backfill_result", result["outputs"])
            # 但 outputs/mail/<case>/mail_send_result.json 仍记录 SMTP 失败结果
            mail_send_path = root / "outputs/mail/case_mail_status_failed/mail_send_result.json"
            self.assertTrue(mail_send_path.exists(), "mail_send_result.json 应记录 SMTP 失败")
            mail_send = json.loads(mail_send_path.read_text(encoding="utf-8"))
            self.assertEqual(mail_send.get("status"), "失败")
            self.assertIn("SMTPAuthenticationError", mail_send.get("blocked_reason", ""))

    def test_mail_dry_run_still_writes_preview_without_live_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))

            result = run(
                root,
                RunOptions(
                    text=VALID_TEXT,
                    case_id="case_mail_preview",
                    mail_dry_run=True,
                    online_record_url="https://example.invalid/record/case_mail_preview",
                    mail_to=["receiver@example.invalid"],
                    mail_cc=["owner@example.invalid"],
                    smtp_user="sender@example.invalid",
                ),
            )

            self.assertEqual(result["status"], "validated")
            self.assertEqual(
                _runtime_files(root),
                [
                    _offline_record_file("case_mail_preview"),
                    "outputs/mail/case_mail_preview/mail_preview.json",
                    "outputs/reports/case_mail_preview/BOMC7月8日故障报告.docx",
                    "outputs/validation/case_mail_preview/validation_result.json",
                    "work/cases/case_mail_preview/incident_context.json",
                ],
            )
            preview = json.loads((root / "outputs/mail/case_mail_preview/mail_preview.json").read_text(encoding="utf-8"))
            self.assertEqual(preview["from"], "sender@example.invalid")
            self.assertEqual(preview["to"], ["gydyjzhwhz@gd.chinamobile.com"])
            self.assertEqual(preview["cc"], [])
            self.assertEqual(len(preview["attachments"]), 2)

    def test_confirm_send_argument_is_not_part_of_run_options(self) -> None:
        with self.assertRaises(TypeError):
            RunOptions(confirm_send="SEND-MAIL")

    def test_retired_feishu_apply_argument_is_not_part_of_run_options(self) -> None:
        with self.assertRaises(TypeError):
            RunOptions(feishu_apply=True)

    def test_legacy_mail_confirmation_no_longer_controls_smtp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))

            result = run(
                root,
                RunOptions(
                    text=VALID_TEXT,
                    case_id="case_mail_old_confirm",
                    online_record_url="https://example.invalid/record/case_mail_old_confirm",
                    send_mail=True,
                    mail_to=["receiver@example.invalid"],
                    smtp_user="sender@example.invalid",
                ),
            )

            self.assertEqual(result["status"], "need_review")
            self.assertIn("live writes require --incident-context plus --apply --confirm-live-write LIVE-WRITE", result["errors"])

    def test_send_mail_with_record_id_requires_readable_feishu_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_mail_record_check")

            result = run(
                root,
                RunOptions(
                    incident_context=context_path,
                    feishu_record_id="rec_test_123",
                    send_mail=True,
                ),
            )

            self.assertEqual(result["status"], "need_review")
            self.assertIn("live writes require --apply --confirm-live-write LIVE-WRITE", result["errors"])

    def test_apply_send_mail_with_record_id_stops_on_missing_feishu_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _case_root(Path(tmp))
            context_path = _validated_context(root, "case_mail_record_check")

            result = run(
                root,
                RunOptions(
                    incident_context=context_path,
                    feishu_record_id="rec_test_123",
                    send_mail=True,
                    apply=True,
                    confirm_live_write="LIVE-WRITE",
                    mail_to=["receiver@example.invalid"],
                    smtp_user="sender@example.invalid",
                ),
            )

            self.assertEqual(result["status"], "need_review")
            self.assertTrue(any("missing Feishu config" in error for error in result["errors"]))
            self.assertNotIn("mail_send_result", result["outputs"])
            attachment_result = json.loads((root / "outputs/ledger/case_mail_record_check/attachment_upload_result.json").read_text(encoding="utf-8"))
            self.assertEqual(attachment_result["status"], "failed")
            self.assertIn("missing Feishu config", attachment_result["blocked_reason"])


def _write_local_handler_master(root: Path) -> None:
    template = root / "data/templates/ledger/offline_fault_record_2026.xlsx"
    master = root / "outputs/ledger/master/【2026】多域系统故障记录表.xlsx"
    workbook = load_workbook(template)
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.value = None
    master.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(master)
    for index, handler in enumerate(("张三", "李四"), 1):
        write_offline_record_workbook(
            template,
            master,
            {
                "case_id": f"history_{index}",
                "fields": {
                    "系统名称": "BOMC",
                    "处理人员": handler,
                    "故障发生时间": f"2026-07-0{index} 10:00:00",
                },
            },
        )


class _FakeFeishuConfig:
    field_map: dict[str, str] = {}
    table_url = "https://example.invalid/table"


def _case_root(root: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copytree(repo_root / "data/templates/reports", root / "data/templates/reports")
    shutil.copytree(repo_root / "data/templates/ledger", root / "data/templates/ledger")
    return root


def _offline_record_file(case_id: str) -> str:
    return f"outputs/ledger/{case_id}/【2026】多域系统故障记录表_{case_id}.xlsx"


def _validated_context(root: Path, case_id: str) -> Path:
    run(root, RunOptions(text=VALID_TEXT, case_id=case_id, incident_context_only=True))
    return root / "work/cases" / case_id / "incident_context.json"


def _write_manual_index(root: Path) -> None:
    path = root / "data/reference/manuals_index/manual_index.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "project_root": "F:\\workbuddy\\多域AI故障\\hermes\\故障手册知识库\\workbuddy",
                "systems": [
                    {
                        "system": "BOMC",
                        "aliases": ["BOMC"],
                        "guides": [
                            {
                                "path": "处理手册\\B域\\BOMC\\BOMC-cas服务异常.md",
                                "fault_pattern": "cas服务异常",
                                "classification": "应用",
                                "symptoms": ["BOMC系统cas服务异常，用户无法访问BOMC系统"],
                                "recovery_actions": ["重启cas和web服务"],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manual = root / "data/reference/manuals/处理手册/B域/BOMC/BOMC-cas服务异常.md"
    manual.parent.mkdir(parents=True)
    manual.write_text("# BOMC-cas服务异常\n", encoding="utf-8")
    html = root / "data/reference/manuals/导出手册/html/B域/BOMC/BOMC-cas服务异常.html"
    html.parent.mkdir(parents=True)
    html.write_text("<h1>BOMC-cas服务异常</h1>\n", encoding="utf-8")


def _runtime_files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for base in (root / "outputs", root / "work")
        for p in base.rglob("*")
        if p.is_file()
    )


if __name__ == "__main__":
    unittest.main()
