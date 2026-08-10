from __future__ import annotations

from pathlib import Path
from typing import Any


SYSTEM_DOMAIN = {
    # 2026-07-11 09:50 修改: OPENAPI/openapi 的所属域从 "B域" 改为 "经分域"
    # 用户硬性要求: OPENAPI/openapi 应该是经分域, 不是 B 域
    "BOMC": "B域",
    "CLC业务卡": "B域",
    "IT工单系统": "B域",
    "PASS系统": "B域",
    "SmartBI": "经分域",
    "openapi": "经分域",
    "OPENAPI": "经分域",
    "smp": "B域",
    "下载中心": "B域",
    "乡村振兴": "B域",
    "大数据PaaS平台": "B域",
    "大数据K8s": "经分域",
    "大数据K8S": "经分域",
    "大数据平台K8s": "经分域",
    "大数据平台K8S": "经分域",
    "大数据平台K8S集群": "经分域",
    "实时营销": "M域",
    "数字员工": "B域",
    "标签库": "经分域",
    "省级业务集中稽核系统": "B域",
    "自助分析": "经分域",
    "自建空充": "B域",
    "集中化工单系统": "B域",
    "集中运营 IOP": "B域",
}


def build_record_payload(
    context: dict[str, Any],
    report_path: Any | None,
    *,
    mail_status: str = "未生成",
    manual_status: str = "待补充",
    manual_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = context["meta"]
    incident = context["incident"]
    impact = context["impact"]
    diagnosis = context["diagnosis"]
    handling = context["handling"]
    duration_minutes = incident.get("duration_minutes", "")
    process_text = _process_text(incident, handling)
    closure_info = _closure_info(
        handling.get("closure_info", ""),
        handling.get("solution", ""),
        diagnosis.get("fault_cause", ""),
    )
    system_name = incident.get("system_name", "")
    owner_match = meta.get("owner_match") or {}
    system_domain = (
        owner_match.get("domain_feishu")
        or SYSTEM_DOMAIN.get(system_name, "")
        or SYSTEM_DOMAIN.get(str(system_name).removesuffix("系统"), "")
    )
    followup_measures = "\n".join(handling.get("followup_measures") or [])
    report_name = _file_label(report_path)

    # 2026-07-11 08:20 修复: 返回的 record_payload 必须有顶层 "case_id" 键
    # 原因: offline_ledger.workbook.append_or_update_record() 读 record_payload["case_id"]
    # 之前 fields["序号"] = meta["case_id"] 是 case_id 占位, 不是 record_payload["case_id"]
    return {
        "api_called": False,
        "write_mode": "dry_run",
        "target": "fault_record_table",
        "case_id": meta["case_id"],  # 2026-07-11 08:20 顶 层 case_id (供 offline/feishu ledger 用)
        "fields": {
            "序号": meta["case_id"],  # case_id 占位; 写飞书时 _write_feishu_record() 会自动改成 max+1 数字 (create 路径) 或保留原值 (update/dedupe 路径)
            "所属域": system_domain,
            "系统名称": system_name,
            "是否已提故障工单号": "否",  # 默认值; 真提工单时手工录入/事故后修订
            "故障上报情况": incident.get("discovery_source", ""),
            "故障发生时间": incident.get("occurred_at", ""),
            "响应时间": incident.get("occurred_at", ""),
            "故障恢复时间": incident.get("recovered_at", ""),
            "故障时长(分钟)": duration_minutes,
            "业务中断开始时间": incident.get("occurred_at", ""),
            "业务中断结束时间": incident.get("recovered_at", ""),
            "业务中断时长(分钟)": duration_minutes,
            "影响范围": impact.get("impact_scope", ""),
            "影响业务": impact.get("affected_business", ""),
            "影响描述": impact.get("impact_description", ""),
            "故障描述": diagnosis.get("fault_description", ""),
            "恢复手段": handling.get("solution", ""),
            "故障原因": diagnosis.get("fault_cause", ""),
            "闭环信息": closure_info,
            "闭环时间": incident.get("recovered_at", ""),
            "处理人员": handling.get("owner", ""),
            "故障分类": "其他",
            "命中的告警信息": "",
            "故障报告": report_name,
            "资料状态": "已补报告" if report_name else "待补报告",
            "邮件闭环状态": mail_status,
            "故障报告附件": "",
            "资料完整度": _material_completeness(report_path, context),
            "看板优先级": _priority(duration_minutes),
            "手册状态": manual_status,
            "故障手册": (manual_result or {}).get("match_name") or (manual_result or {}).get("match_target", ""),
            "故障手册链接": "",
            "故障手册附件": "",
            "手册系统": (manual_result or {}).get("manual_system", ""),
            "手册分类": (manual_result or {}).get("classification", ""),
            "手册故障模式": (manual_result or {}).get("match_name", ""),
            "复盘状态": "",
            "优化处理措施": followup_measures,
            "预计修复时间": "",
            "处理过程": process_text,
        },
    }


def _material_completeness(report_path: Any | None, context: dict[str, Any]) -> str:
    has_report = bool(report_path and Path(str(report_path)).exists())
    has_evidence = any(context.get("input", {}).get(key) for key in ("screenshots", "logs", "attachments"))
    if has_report and has_evidence:
        return "基础资料已齐"
    if has_report:
        return "缺附件"
    if has_evidence:
        return "缺报告索引"
    return "缺报告和附件"


def _priority(duration_minutes: Any) -> str:
    try:
        minutes = float(duration_minutes)
    except (TypeError, ValueError):
        return "P3"
    if minutes >= 60:
        return "P1"
    if minutes >= 30:
        return "P2"
    return "P3"


def _process_text(incident: dict[str, Any], handling: dict[str, Any]) -> str:
    process_records = handling.get("process_records") or []
    if process_records:
        return "\n".join(process_records)
    records: list[str] = []
    if incident.get("occurred_at"):
        records.append(f"{incident['occurred_at']} 接收故障信息")
    if incident.get("recovered_at"):
        records.append(f"{incident['recovered_at']} {handling.get('solution') or '业务恢复'}")
    return "\n".join(records)


def _closure_info(raw: str, solution: str, fault_cause: str) -> str:
    if raw and raw.strip() and raw.strip() != "无":
        return raw.strip()
    parts = []
    if solution:
        parts.append(f"通过{solution}恢复")
    if fault_cause:
        parts.append(f"故障原因为{fault_cause}")
    return "，".join(parts) + "。" if parts else ""


def _file_label(path_value: Any | None) -> str:
    if not path_value:
        return ""
    path = Path(str(path_value)).resolve()
    if not path.exists():
        return ""
    return path.name
