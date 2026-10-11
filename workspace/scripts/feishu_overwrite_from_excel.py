from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feishu_ledger.client import (  # noqa: E402
    FeishuApiError,
    batch_create_records,
    batch_delete_records,
    batch_update_records,
    list_fields,
    list_records,
    load_feishu_config,
    prepare_fields_for_feishu,
)
from src.manuals_review.preview import build_manual_result  # noqa: E402


DEFAULT_EXCEL = Path("D:/MailMasterData/gaozhe@shsnc.com_1190/parts/66/【2026】多域系统故障记录表.xlsx")
DEFAULT_SHEET = "2026年度故障记录"
CONFIRM_TOKEN = "OVERWRITE-FEISHU"
UPDATE_CONFIRM_TOKEN = "UPDATE-FEISHU"
MANUAL_INDEX = ROOT / "data" / "reference" / "manuals_index" / "manual_index.json"

HEADER_MAP = {
    "序号": "序号",
    "所属域": "所属域",
    "系统名称": "系统名称",
    "是否已提故障工单/已提复制工单号": "是否已提故障工单号",
    "故障上报情况": "故障上报情况",
    "故障发生时间": "故障发生时间",
    "响应时间": "响应时间",
    "故障恢复时间": "故障恢复时间",
    "故障时长(分钟)": "故障时长(分钟)",
    "业务中断开始时间": "业务中断开始时间",
    "业务中断结束时间": "业务中断结束时间",
    "业务中断时长(分钟)": "业务中断时长(分钟)",
    "故障描述": "故障描述",
    "恢复手段": "恢复手段",
    "故障原因": "故障原因",
    "闭环信息": "闭环信息",
    "闭环时间": "闭环时间",
    "多域处理人员": "处理人员",
    "故障分类": "故障分类",
    "命中的告警信息": "命中的告警信息",
    "复盘状态": "复盘状态",
    "优化处理措施": "优化处理措施",
    "预计修复时间": "预计修复时间",
}

VALUE_ALIASES = {
    "系统名称": {
        "CLC业务": "CLC业务卡",
        "IT工单": "IT工单系统",
        "集中化工单": "集中化工单系统",
        "集中化工单系统(IWOS)": "集中化工单系统",
    },
    "故障上报情况": {
        "服务台": "服务台上报",
        "服务台报障。": "服务台报障",
        "省客服报障。": "省客服",
    },
}

CLASSIFICATION_KEYWORDS = (
    ("数据库", ("数据库", "redis", "mysql", "oracle", "sql", "库")),
    ("主机", ("主机", "服务器", "磁盘", "内存", "cpu", "宕机", "虚拟机", "节点")),
    ("网络", ("网络", "专线", "链路", "端口", "防火墙", "dns", "ip")),
    ("中间件", ("tomcat", "nginx", "redis", "kafka", "mq", "cas", "web服务")),
    ("应用代码", ("接口", "程序", "应用", "代码", "服务异常", "任务", "进程")),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Overwrite Feishu Bitable records from the 2026 fault-record Excel")
    parser.add_argument("--excel", type=Path, default=DEFAULT_EXCEL)
    parser.add_argument("--sheet", default=DEFAULT_SHEET)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--confirm-overwrite", default="")
    parser.add_argument("--confirm-update", default="")
    args = parser.parse_args()

    config = load_feishu_config(ROOT)
    field_items = list_fields(config)
    current_records = list_records(config)
    excel_payloads, skipped_headers = _read_excel_rows(args.excel, args.sheet)

    prepared_records: list[dict[str, Any]] = []
    skipped_values: Counter[str] = Counter()
    source_skipped: list[dict[str, str]] = []
    for payload in excel_payloads:
        fields, skipped = prepare_fields_for_feishu(payload, field_items)
        if fields:
            prepared_records.append({"fields": fields})
        for item in skipped:
            skipped_values[f"{item['target']}|{item['reason']}"] += 1
            raw = payload.get(item["field"], "")
            if raw not in (None, ""):
                source_skipped.append(
                    {
                        "序号": str(payload.get("序号", "")),
                        "字段": item["target"],
                        "Excel原值": str(raw),
                        "原因": item["reason"],
                    }
                )

    plan = {
        "mode": "update" if args.update_existing else "apply" if args.apply else "plan",
        "excel": str(args.excel),
        "sheet": args.sheet,
        "excel_rows": len(excel_payloads),
        "current_feishu_records": len(current_records),
        "records_to_delete": len(current_records),
        "records_to_create": len(prepared_records),
        "skipped_headers": sorted(skipped_headers),
        "skipped_values": [{"field": key.split("|", 1)[0], "reason": key.split("|", 1)[1], "count": count} for key, count in skipped_values.items()],
        "source_nonempty_skipped_count": len(source_skipped),
        "source_nonempty_skipped_sample": source_skipped[:50],
        "sample_create_fields": [record["fields"] for record in prepared_records[:3]],
    }

    if args.update_existing:
        if args.confirm_update != UPDATE_CONFIRM_TOKEN:
            plan["status"] = "blocked"
            plan["blocked_reason"] = f"missing --confirm-update {UPDATE_CONFIRM_TOKEN}"
        else:
            try:
                update_records, missing_sequences = _existing_updates(prepared_records, current_records)
                updated = batch_update_records(update_records, config) if update_records else {"updated_count": 0}
                readback_records = list_records(config)
                verification = _verify_readback(excel_payloads, readback_records, field_items)
                plan.update(
                    {
                        "status": "updated",
                        "records_to_update": len(update_records),
                        "missing_sequences": missing_sequences,
                        "updated_count": updated["updated_count"],
                        "readback_records": len(readback_records),
                        "verification": verification,
                    }
                )
            except FeishuApiError as exc:
                plan["status"] = "failed"
                plan["error"] = str(exc)
    elif args.apply:
        if args.confirm_overwrite != CONFIRM_TOKEN:
            plan["status"] = "blocked"
            plan["blocked_reason"] = f"missing --confirm-overwrite {CONFIRM_TOKEN}"
        else:
            try:
                created = batch_create_records(prepared_records, config) if prepared_records else {"created_count": 0}
                deleted = batch_delete_records([record["record_id"] for record in current_records], config) if current_records else {"deleted_count": 0}
                readback_records = list_records(config)
                verification = _verify_readback(excel_payloads, readback_records, field_items)
                plan.update(
                    {
                        "status": "applied",
                        "deleted_count": deleted["deleted_count"],
                        "created_count": created["created_count"],
                        "readback_records": len(readback_records),
                        "verification": verification,
                    }
                )
            except FeishuApiError as exc:
                plan["status"] = "failed"
                plan["error"] = str(exc)
    else:
        plan["status"] = "ready"

    output_name = "feishu_update_result.json" if args.update_existing else "feishu_overwrite_result.json" if args.apply else "feishu_overwrite_plan.json"
    output = ROOT / "outputs" / "ledger" / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_jsonable(plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": plan["status"], "records_to_delete": plan["records_to_delete"], "records_to_create": plan["records_to_create"]}, ensure_ascii=False, indent=2))
    return 0


def _existing_updates(
    prepared_records: list[dict[str, Any]],
    current_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_seq = {str((record.get("fields") or {}).get("序号", "")): record.get("record_id", "") for record in current_records}
    updates: list[dict[str, Any]] = []
    missing: list[str] = []
    for record in prepared_records:
        seq = str(record.get("fields", {}).get("序号", ""))
        record_id = by_seq.get(seq)
        if not record_id:
            missing.append(seq)
            continue
        updates.append({"record_id": record_id, "fields": record["fields"]})
    return updates, missing


def _read_excel_rows(excel_path: Path, sheet_name: str) -> tuple[list[dict[str, Any]], set[str]]:
    workbook = load_workbook(excel_path, data_only=True, read_only=True)
    if sheet_name not in workbook.sheetnames:
        raise SystemExit(f"missing sheet: {sheet_name}")
    sheet = workbook[sheet_name]
    headers = [_normalize_header(sheet.cell(1, column).value) for column in range(1, sheet.max_column + 1)]
    skipped_headers = {header for header in headers if header and header not in HEADER_MAP}
    rows: list[dict[str, Any]] = []
    for row_number in range(2, sheet.max_row + 1):
        payload: dict[str, Any] = {}
        has_business_value = False
        for column, header in enumerate(headers, start=1):
            value = sheet.cell(row_number, column).value
            target = HEADER_MAP.get(header)
            if target:
                if target != "序号" and value not in (None, ""):
                    has_business_value = True
                payload[target] = _normalize_value(target, value)
        payload["业务中断开始时间"] = payload.get("响应时间", "")
        payload["业务中断结束时间"] = payload.get("故障恢复时间", "")
        payload["业务中断时长(分钟)"] = payload.get("故障时长(分钟)", "")
        payload["是否已提故障工单号"] = payload.get("是否已提故障工单号") or "否"
        _enrich_payload(payload)
        if has_business_value:
            rows.append(payload)
    return rows, skipped_headers


def _enrich_payload(payload: dict[str, Any]) -> None:
    payload["故障原因"] = payload.get("故障原因") or _fault_cause(payload)
    payload["闭环信息"] = _closure_info(payload)
    payload["闭环时间"] = payload.get("闭环时间") or payload.get("故障恢复时间", "")
    payload["处理过程"] = payload.get("处理过程") or _process_text(payload)
    payload["故障分类"] = payload.get("故障分类") or _fault_classification(payload)
    payload["资料状态"] = "已补报告" if payload.get("故障报告") else "待补报告"
    payload["资料完整度"] = "基础资料已齐" if payload.get("故障报告") else "缺报告和附件"
    payload["看板优先级"] = _priority(payload.get("故障时长(分钟)"))
    payload["复盘总结"] = payload.get("复盘总结") or _review_summary(payload)
    _apply_manual_match(payload)


def _fault_cause(payload: dict[str, Any]) -> str:
    text = " ".join(str(payload.get(key, "")) for key in ("系统名称", "故障描述", "恢复手段")).lower()
    if "openapi" in text and ("成功率为0" in text or "接口率为0" in text):
        return "数据库网络异常"
    if "bomc" in text and "无法监控" in text:
        return "CAS服务异常或连接池满"
    return "原因待补充"


def _closure_info(payload: dict[str, Any]) -> str:
    raw = str(payload.get("闭环信息") or "").strip()
    if raw and raw != "无":
        return raw
    solution = str(payload.get("恢复手段") or "").strip()
    reason = str(payload.get("故障原因") or "").strip()
    parts = []
    if solution:
        parts.append(f"通过{solution}恢复")
    if reason:
        parts.append(f"故障原因为{reason}")
    return "，".join(parts) + "。" if parts else "待补充"


def _process_text(payload: dict[str, Any]) -> str:
    start = payload.get("响应时间") or payload.get("故障发生时间")
    end = payload.get("故障恢复时间")
    solution = payload.get("恢复手段") or "业务恢复"
    records = []
    if start:
        records.append(f"{_format_time(start)} 接收故障信息")
    if end:
        records.append(f"{_format_time(end)} {solution}")
    return "\n".join(records)


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _fault_classification(payload: dict[str, Any]) -> str:
    text = " ".join(str(payload.get(key, "")) for key in ("故障描述", "故障原因", "恢复手段")).lower()
    for classification, keywords in CLASSIFICATION_KEYWORDS:
        if any(keyword.lower() in text for keyword in keywords):
            return classification
    return "其他"


def _priority(duration: Any) -> str:
    try:
        minutes = float(duration)
    except (TypeError, ValueError):
        return "P3"
    if minutes >= 60:
        return "P1"
    if minutes >= 30:
        return "P2"
    return "P3"


def _review_summary(payload: dict[str, Any]) -> str:
    system = payload.get("系统名称") or "相关系统"
    reason = payload.get("故障原因") or "原因待补充"
    recovery = payload.get("恢复手段") or "按既定流程恢复"
    duration = payload.get("故障时长(分钟)")
    duration_text = f"，故障时长{duration}分钟" if duration not in (None, "") else ""
    return f"{system}故障原因为{reason}，通过{recovery}恢复{duration_text}。"


def _apply_manual_match(payload: dict[str, Any]) -> None:
    manual = build_manual_result(_context_from_payload(payload), MANUAL_INDEX)
    payload["手册状态"] = manual.get("status") or "待补充"
    if manual.get("status") != "已匹配":
        return
    payload["故障手册"] = manual.get("match_name") or manual.get("match_target", "")
    payload["手册系统"] = manual.get("manual_system") or payload.get("系统名称", "")
    payload["手册分类"] = manual.get("classification") or payload.get("故障分类", "")
    payload["手册故障模式"] = manual.get("match_name", "")


def _context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident": {
            "system_name": payload.get("系统名称", ""),
            "fault_title": f"{payload.get('系统名称', '')}-{payload.get('故障描述', '')}",
        },
        "diagnosis": {
            "fault_description": payload.get("故障描述", ""),
            "fault_cause": payload.get("故障原因", ""),
        },
        "handling": {
            "solution": payload.get("恢复手段", ""),
        },
    }


def _verify_readback(
    excel_payloads: list[dict[str, Any]],
    readback_records: list[dict[str, Any]],
    field_items: list[dict[str, Any]],
) -> dict[str, Any]:
    by_seq = {str((record.get("fields") or {}).get("序号", "")): record.get("fields") or {} for record in readback_records}
    missing_records: list[str] = []
    missing_fields: list[dict[str, str]] = []
    incompatible_source_values: list[dict[str, str]] = []
    for payload in excel_payloads:
        seq = str(payload.get("序号", ""))
        fields, skipped = prepare_fields_for_feishu(payload, field_items)
        readback = by_seq.get(seq)
        if readback is None:
            missing_records.append(seq)
            continue
        for name, value in fields.items():
            if value not in (None, "") and readback.get(name) in (None, ""):
                missing_fields.append({"序号": seq, "字段": name, "写入值": str(value)})
        for item in skipped:
            raw = payload.get(item["field"], "")
            if raw not in (None, ""):
                incompatible_source_values.append(
                    {
                        "序号": seq,
                        "字段": item["target"],
                        "Excel原值": str(raw),
                        "原因": item["reason"],
                    }
                )
    status = "passed"
    if missing_records or missing_fields or incompatible_source_values:
        status = "warnings"
    return {
        "status": status,
        "expected_records": len(excel_payloads),
        "actual_records": len(readback_records),
        "missing_records": missing_records[:50],
        "missing_fields": missing_fields[:100],
        "incompatible_source_values_count": len(incompatible_source_values),
        "incompatible_source_values_sample": incompatible_source_values[:50],
    }


def _normalize_header(value: Any) -> str:
    return (
        str(value or "")
        .replace("\n", "")
        .replace(" ", "")
        .replace("（", "(")
        .replace("）", ")")
        .replace("*", "")
        .strip()
    )


def _normalize_value(field_name: str, value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        value = value.strip()
    return VALUE_ALIASES.get(field_name, {}).get(value, value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
