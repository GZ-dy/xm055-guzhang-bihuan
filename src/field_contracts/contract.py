from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


STATUS_VALUES = {"draft", "validated", "need_review"}

REQUIRED_FIELDS = (
    "meta.schema_version",
    "meta.case_id",
    "meta.created_at",
    "incident.system_name",
    "incident.fault_title",
    "incident.occurred_at",
    "incident.recovered_at",
    "incident.duration_minutes",
    "incident.discovery_source",
    # BUGFIX42 (2026-07-13 15:30, 用户原话 "时间不采用推断更加好"):
    # 加 2 个可选时间字段 (不进 REQUIRED_FIELDS, 缺值也 OK)
    # 报告"过程时间表" / 邮件"零、故障时间节点" 用
    # - "incident.diagnosis_at":   # 原因定位时间 (可选, e.g. "2026-07-13 11:20")
    # - "incident.resolved_at":    # 问题处理完成时间 (可选, e.g. "2026-07-13 11:40")
    "impact.impact_scope",
    "diagnosis.fault_description",
    "diagnosis.fault_cause",
    "diagnosis.boundary_tool",
    "handling.owner",
    "handling.handling_group",
    "handling.process_records",
    "handling.solution",
    "handling.closure_info",
    "handling.followup_measures",
)

MISSING_FIELD_PROMPTS = {
    "incident.system_name": "补充故障涉及的系统名称。",
    "incident.fault_title": "补充一句话故障标题。",
    "incident.occurred_at": "补充故障发生时间，格式 YYYY-MM-DD HH:mm。",
    "incident.recovered_at": "补充故障恢复时间，格式 YYYY-MM-DD HH:mm。",
    "incident.duration_minutes": "补充故障时长, 或提供发生/恢复时间自动计算 (BUGFIX42: 用户输入优先, 推断兜底)。",
    # BUGFIX42 (2026-07-13 15:30): 加 2 个新字段的 prompt
    "incident.diagnosis_at": "补充原因定位时间 (可选, 格式 YYYY-MM-DD HH:mm, e.g. 11:20)。",
    "incident.resolved_at": "补充问题处理完成时间 (可选, 格式 YYYY-MM-DD HH:mm, e.g. 11:40)。",
    "impact.impact_scope": "补充影响范围，例如哪些用户、业务或系统功能受影响。",
    "diagnosis.fault_description": "补充故障现象或事件描述。",
    "diagnosis.fault_cause": "补充故障原因；不确定时写待确认原因。",
    "handling.owner": "补充处理人员/负责人。",
    "handling.process_records": "补充处理过程时间线，每条以 HH:mm 开头。",
    "handling.solution": "补充恢复手段或解决方式。",
    "handling.closure_info": "补充闭环结论，例如系统已恢复并完成验证。",
    "handling.followup_measures": "补充后续完善措施；没有也请写明暂无。",
}


def validate_context(
    context: dict[str, Any],
    report_path: Path | None = None,
    *,
    require_report: bool = True,
) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if not _has_value(_get(context, field))]
    input_ok = any(
        _has_value(_get(context, field))
        for field in ("input.text", "input.screenshots", "input.logs", "input.attachments")
    )
    if not input_ok:
        missing.append("input")

    errors: list[str] = []
    duration = _get(context, "incident.duration_minutes")
    if _has_value(duration) and (not isinstance(duration, int) or duration < 0):
        errors.append("incident.duration_minutes must be an integer >= 0")
    if _has_value(_get(context, "incident.occurred_at")) and not _valid_minute_time(_get(context, "incident.occurred_at")):
        errors.append("incident.occurred_at must use YYYY-MM-DD HH:mm")
    if _has_value(_get(context, "incident.recovered_at")) and not _valid_minute_time(_get(context, "incident.recovered_at")):
        errors.append("incident.recovered_at must use YYYY-MM-DD HH:mm")
    if _has_value(_get(context, "meta.status")) and _get(context, "meta.status") not in STATUS_VALUES:
        errors.append("meta.status has unsupported value")
    _validate_process_records(_get(context, "handling.process_records"), errors)

    report_ok = bool(report_path and report_path.exists() and report_path.suffix.lower() == ".docx")
    missing_fields = sorted(set(missing))
    status = "validated" if not missing_fields and not errors and (report_ok or not require_report) else "need_review"
    return {
        "validated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "missing_fields": missing_fields,
        "missing_field_prompts": _missing_field_prompts(missing_fields),
        "errors": errors,
        "check_contract_map": {
            "input": "pass" if input_ok else "missing",
            "required_fields": "pass" if not missing_fields else "missing",
            "field_format": "pass" if not errors else "error",
            "report_docx": "pass" if report_ok else ("not_required" if not require_report else "not_generated"),
        },
        "contract_check_groups": {
            "input": {"source_material": "pass" if input_ok else "missing"},
            "incident_context": {
                "required_fields": "pass" if not missing_fields else "missing",
                "field_format": "pass" if not errors else "error",
            },
            "outputs": {"report_docx": "pass" if report_ok else ("not_required" if not require_report else "not_generated")},
        },
    }


def _get(payload: dict[str, Any], dotted: str) -> Any:
    value: Any = payload
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _missing_field_prompts(fields: list[str]) -> list[dict[str, str]]:
    return [{"field": field, "prompt": MISSING_FIELD_PROMPTS.get(field, f"补充字段 {field}。")} for field in fields]


def _valid_minute_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return True


def _validate_process_records(value: Any, errors: list[str]) -> None:
    if not _has_value(value):
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append("handling.process_records must be a string array")
        return
    minutes: list[int] = []
    for item in value:
        minute = _record_start_minute(item)
        if minute is None:
            errors.append("handling.process_records entries must start with HH:mm or YYYY-MM-DD HH:mm")
            return
        minutes.append(minute)
    if minutes != sorted(minutes):
        errors.append("handling.process_records must be in chronological order")


def _record_start_minute(value: str) -> int | None:
    text = value.strip()
    for fmt_len, fmt in ((16, "%Y-%m-%d %H:%M"),):
        try:
            parsed = datetime.strptime(text[:fmt_len], fmt)
        except ValueError:
            pass
        else:
            return parsed.toordinal() * 1440 + parsed.hour * 60 + parsed.minute
    if len(text) >= 5 and text[0:2].isdigit() and text[2] in ":：" and text[3:5].isdigit():
        hour = int(text[0:2])
        minute = int(text[3:5])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    return None
