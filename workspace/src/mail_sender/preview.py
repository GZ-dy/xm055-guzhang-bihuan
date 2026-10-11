from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def build_mail_preview(
    context: dict[str, Any],
    report_path: Path | None,
    online_record_url: str = "",
    offline_record_path: Path | None = None,
    sender: str = "",
    recipients: tuple[str, ...] = (),
    cc: tuple[str, ...] = (),  # BUGFIX36 (2026-07-13): 项目小组成员抄送
) -> dict[str, Any]:
    incident = context["incident"]
    impact = context["impact"]
    diagnosis = context["diagnosis"]
    handling = context["handling"]
    system = _display_system(incident.get("system_name") or "")
    cause = diagnosis.get("fault_cause") or ""
    event_description = "，".join(
        part
        for part in (
            _event_time(incident.get("occurred_at")),
            str(diagnosis.get("fault_description") or "").strip(),
        )
        if part
    )
    body = [
        _headline(system, cause),
        "",
        f"一、事件描述： {_with_period(event_description)}",
        f"二、业务影响范围： {_with_semicolon(impact.get('impact_scope', ''))}",
        # BUGFIX41 (2026-07-13 14:30, 用户原话 "处理组要记得都是多域维护组"):
        # 邮件正文"三、处理组" = **硬编码"多域维护组"** (跟具体系统的小组解耦)
        # 飞书表"处理人员" / 离线表"多域处理人员" 仍然写 3 人真名
        f"三、处理组： 多域维护组",
        f"四、故障原因： {_with_semicolon(cause)}",
        f"五、解决方案： {_with_semicolon(handling.get('solution', ''))}",
        f"六、故障定界工具： {_with_semicolon(diagnosis.get('boundary_tool') or '无')}",
        f"七、故障发现来源： {_with_semicolon(incident.get('discovery_source', ''))}",
        f"八、业务恢复时长： {_with_semicolon(_duration_text(incident.get('duration_minutes')))}",
        "",
        _online_record_line(online_record_url),
        "故障报告：见附件。",
        "离线故障记录表：见附件。",
    ]
    has_report = bool(report_path and report_path.exists() and report_path.suffix.lower() == ".docx")
    has_offline_record = bool(
        offline_record_path and offline_record_path.exists() and offline_record_path.suffix.lower() == ".xlsx"
    )
    attachments = []
    if has_report:
        attachments.append(str(report_path))
    if has_offline_record:
        attachments.append(str(offline_record_path))
    has_record_reference = bool(online_record_url or has_offline_record)
    # 2026-07-10 23:35 修改: 邮件标题格式 = "{系统}故障报告" → "{故障月}日{系统}故障报告"
    # 2026-07-10 23:50 修改: 用 incident.occurred_at (故障发生时间), 不是当前时间
    from datetime import datetime as _dt
    occurred_at_raw = str(incident.get("occurred_at") or "").strip()
    date_obj: _dt | None = None
    if occurred_at_raw:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
            try:
                date_obj = _dt.strptime(occurred_at_raw, fmt)
                break
            except ValueError:
                continue
    if date_obj is None:
        date_obj = _dt.now()  # 兜底: 用当前时间
    # 2026-07-10 23:42 修复: _display_system 返回的是 "BOMC系统" (含"系统"), 不再附加 "系统"
    # 7月10日BOMC系统故障报告
    display_system = system.rstrip("系统") if system else ""
    subject = f"{date_obj.month}月{date_obj.day}日{display_system}系统故障报告" if display_system else f"{date_obj.month}月{date_obj.day}日故障报告"
    return {
        "smtp_called": False,
        "status": "待发送" if has_report and has_record_reference else "失败",
        "subject": subject,
        "from": sender,
        "to": list(recipients),
        "cc": list(cc),  # BUGFIX36 (2026-07-13): 项目小组成员抄送
        "body": "\n".join(body),
        "attachment": str(report_path) if has_report else "",
        "attachments": attachments,
        "blocked_reason": "" if has_report and has_record_reference else _blocked_reason(has_report, has_offline_record),
    }


def _duration_text(value: Any) -> str:
    if isinstance(value, int):
        return f"{value}分钟"
    return ""


def _event_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return text
    return f"{parsed.month}月{parsed.day}日{parsed.hour}点{parsed.minute:02d}分"


def _headline(system: str, cause: str) -> str:
    text = f"{system}{cause}".strip() or "故障"
    return f"闭环【{_with_period(text)}】"


def _with_period(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text[-1] in "。！？；;" else f"{text}。"


def _with_semicolon(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text[-1] in "；;" else f"{text}；"


def _online_record_line(online_record_url: str) -> str:
    if online_record_url:
        return f"在线故障记录表：{online_record_url}"
    return "在线故障记录表：在线回填失败或未取得链接，离线故障记录表见附件。"


def _blocked_reason(has_report: bool, has_offline_record: bool) -> str:
    reasons = []
    if not has_report:
        reasons.append("missing DOCX attachment")
    if not has_offline_record:
        reasons.append("missing offline XLSX attachment")
    return "; ".join(reasons)


def _display_system(system_name: str) -> str:
    if not system_name:
        return ""
    return system_name if system_name.endswith("系统") else f"{system_name}系统"
