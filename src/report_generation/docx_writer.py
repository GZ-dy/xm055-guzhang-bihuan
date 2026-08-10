from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"w": W_NS}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MC_IGNORABLE = f"{{{MC_NS}}}Ignorable"
ET.register_namespace("w", W_NS)

DATABASE_OWNER_GROUP = "亚信厂商"  # 2026-07-11 12:00 BUGFIX12: 用户确认亚信维护组就是亚信厂商

HEADING_MATCHERS = {
    "故障现象": (("故障", "现象"), ("故障", "描述"), ("事件", "描述")),
    "故障原因分析&结论": (("原因", "结论"), ("原因", "分析")),
    "后续完善措施": (("后续", "措施"), ("优化", "措施"), ("完善", "措施")),
}


def write_report_docx(context: dict[str, Any], path: Path, template_path: Path) -> Path:
    if not template_path.exists():
        raise FileNotFoundError(f"report template not found: {template_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, path)

    with zipfile.ZipFile(path, "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}

    document_xml = entries["word/document.xml"]
    template_namespaces = _register_template_namespaces(document_xml)
    root = ET.fromstring(document_xml)
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("template missing word/body")

    _fill_document(body, context)
    # BUGFIX44 (2026-07-13 16:10, 用户原话 "故障发现红框需要删除"):
    # 在 _fill_document 填完内容后, 真正删除 body 里的 Table ("处理时间/处理人")
    # 实际位置: 在 "故障处理过程&记录" 段后 (而非 "故障发现" 段后)
    _delete_table_after_heading(body, "故障处理过程&记录", required_headers=("处理时间", "处理人"))
    _sanitize_ignorable_namespaces(root, template_namespaces)
    entries["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, payload in entries.items():
            target.writestr(name, payload)
    return path


def _register_template_namespaces(document_xml: bytes) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for _event, (prefix, uri) in ET.iterparse(BytesIO(document_xml), events=("start-ns",)):
        prefix = prefix or ""
        namespaces[prefix] = uri
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            # ElementTree reserves generated prefixes such as ns0; they are not
            # meaningful to Word's mc:Ignorable contract.
            continue
    return namespaces


def _sanitize_ignorable_namespaces(root: ET.Element, namespaces: dict[str, str]) -> None:
    value = root.get(MC_IGNORABLE, "")
    if not value:
        return

    used_uris: set[str] = set()
    for element in root.iter():
        for qname in (element.tag, *element.attrib):
            if isinstance(qname, str) and qname.startswith("{") and "}" in qname:
                used_uris.add(qname[1:qname.index("}")])

    ignorable = [prefix for prefix in value.split() if namespaces.get(prefix) in used_uris]
    if ignorable:
        root.set(MC_IGNORABLE, " ".join(ignorable))
    else:
        root.attrib.pop(MC_IGNORABLE, None)


def _fill_document(body: ET.Element, context: dict[str, Any]) -> None:
    meta = context["meta"]
    incident = context["incident"]
    impact = context["impact"]
    diagnosis = context["diagnosis"]
    handling = context["handling"]

    _replace_first(body, "XXXX系统X月XX日故障报告", _report_title(incident))
    _replace_prefix(body, "创建日期:", _date_text(meta.get("created_at")))
    _fill_after_heading(body, "故障现象", _phenomenon_text(context))
    _fill_after_heading(body, "故障原因分析&结论", _cause_conclusion(incident, diagnosis, handling))
    _replace_tail_section(body, "后续完善措施", _report_followup_lines(context))
    _replace_between_table_and_heading(body, 3, "故障原因分析&结论", _process_report_lines(context))

    _fill_tables(_tables(body), incident, impact, diagnosis, handling)


def _report_title(incident: dict[str, Any]) -> str:
    system = _display_system(incident.get("system_name") or "XX系统")
    occurred = _parse_dt(incident.get("occurred_at", ""))
    if occurred:
        return f"{system}{occurred.month}月{occurred.day}日故障报告"
    return f"{system}故障报告"


def _phenomenon_text(context: dict[str, Any]) -> str:
    incident = context["incident"]
    diagnosis = context["diagnosis"]
    handling = context.get("handling", {})
    if handling.get("process_source") == "actual_timeline":
        occurred = _parse_dt(incident.get("occurred_at", ""))
        description = _strip_sentence_end(diagnosis.get("fault_description", ""))
        if occurred and description:
            return f"{occurred.year}年{occurred.month}月{occurred.day}日{occurred:%H:%M}分{description}；"
    # BUGFIX43 v3 (2026-07-13 16:10, 用户原话 "现象需要修改什么时间 通报xx系统故障 出现什么问题"):
    # 报告"故障现象"段 = "HH:MM 通报xx系统故障 出现什么问题" 句式
    # 用户的 OPENAPI 7/13 案例:
    #   "11点10分网格通保障接口信令异常, 提示: 接口错误[4001];"
    # 拆解:
    #   - "11点10分" = occurred_at 时间
    #   - "网格通保障接口" = system_name
    #   - "信令异常" = symptom
    #   - "提示: 接口错误[4001]" = 详细错误信息
    occurrence_time = incident.get("occurred_at", "")
    time_prefix = _cn_time_prefix(occurrence_time)
    discovery = incident.get("discovery_source") or "服务台"
    raw_system = incident.get("system_name") or "XX系统"
    system_label = raw_system if raw_system.endswith("系统") else f"{raw_system}系统"
    raw_description = _strip_sentence_end(diagnosis.get("fault_description", ""))
    if raw_description:
        # BUGFIX43 v2: 用户给了完整 fault_description 时, 用全文 (但不重复时间前缀)
        # BUGFIX43 v3: 用户给的 description 包含时间时, 去掉 description 中的时间, 只取后段
        clean_desc = raw_description
        if time_prefix and time_prefix in clean_desc:
            clean_desc = clean_desc.replace(time_prefix, "", 1)
        clean_desc = clean_desc.strip("，,。 ")
        # 完整句式: "HH:MM 通报xx系统故障 出现什么问题"
        if clean_desc:
            sentence = f"{time_prefix}{discovery}通报{system_label}故障, {clean_desc}；"
        else:
            sentence = f"{time_prefix}{discovery}通报{system_label}故障；"
        return sentence
    # 兼容老 case: fault_description 空时, 走原 BUGFIX13 拼接逻辑
    prefix = time_prefix
    symptom_text = _core_symptom(incident, diagnosis)
    if not symptom_text:
        sentence = f"{discovery}通报{system_label}故障；"
        return f"{prefix}{sentence}" if prefix else sentence
    if raw_system and raw_system in symptom_text:
        sentence = f"{discovery}通报{symptom_text}；"
    else:
        sentence = f"{discovery}通报{system_label}{symptom_text}；"
    return f"{prefix}{sentence}" if prefix else sentence


def _cn_time_prefix(occurred_at: str) -> str:
    """BUGFIX43 v3: 把 ISO 时间 "2026-07-13 11:10:00" 转成 "11点10分" 形式.
    已含 "点" "分" 直接返回; 空返回 ""."""
    if not occurred_at:
        return ""
    s = str(occurred_at).strip()
    if "点" in s or "分" in s:
        return s + " " if not s.endswith(" ") else s
    # 解析 HH:MM
    import re as _re
    m = _re.search(r"(\d{1,2})[:\uff1a](\d{2})", s)
    if not m:
        return ""
    h, mi = m.group(1), m.group(2)
    return f"{h}点{mi}分 "


def _cause_conclusion(incident: dict[str, Any], diagnosis: dict[str, Any], handling: dict[str, Any]) -> str:
    cause = _strip_sentence_end(diagnosis.get("fault_cause", ""))
    symptom = _core_symptom(incident, diagnosis)
    system = _display_system(incident.get("system_name") or "")
    solution = _strip_sentence_end(handling.get("solution", ""))
    group = handling.get("handling_group") or handling.get("owner") or "运维人员"
    if cause and symptom:
        cause_text = cause if "导致" in cause else f"{cause}导致{symptom}"
        target = system or "相关系统"
        if _is_database_scope(cause, solution):
            return (
                f"{cause_text}；经{group}排查{target}中间件和服务状态并拨测数据库连通性，"
                f"已通知{DATABASE_OWNER_GROUP}处理数据库侧问题，"
                f"{target}访问异常与{cause}相关，该异常影响{target}认证及访问链路，"
                f"导致用户无法正常访问{target}。"
            )
        return (
            f"{cause_text}；经{group}核查确认，{target}无法打开与{cause}相关，"
            f"该异常影响{target}认证及访问链路，导致用户无法正常访问{target}。"
        )
    if cause:
        return f"{cause}；"
    return ""


def _process_report_lines(context: dict[str, Any]) -> list[str]:
    incident = context["incident"]
    diagnosis = context["diagnosis"]
    handling = context["handling"]
    return _numbered_process_lines(_detailed_process_lines(incident, diagnosis, handling))


def _report_followup_lines(context: dict[str, Any]) -> list[str]:
    incident = context["incident"]
    diagnosis = context["diagnosis"]
    handling = context["handling"]
    return _operational_followup_lines(incident, diagnosis, handling)


def _impact_scope_text(incident: dict[str, Any], diagnosis: dict[str, Any], impact: dict[str, Any]) -> str:
    system = _display_system(incident.get("system_name") or "")
    description = diagnosis.get("fault_description", "")
    if system and ("无法打开" in description or "无法访问" in description or "不可用" in description):
        return f"造成{system}无法打开，用户无法正常访问服务"
    return impact.get("impact_scope", "")


def _affected_business_text(incident: dict[str, Any], diagnosis: dict[str, Any], impact: dict[str, Any]) -> str:
    system = _display_system(incident.get("system_name") or "")
    description = diagnosis.get("fault_description", "")
    if system and ("无法打开" in description or "无法访问" in description or "不可用" in description):
        return f"造成{system}无法打开，用户无法正常访问系统，相关业务支撑功能无法正常使用。"
    return impact.get("affected_business", "") or impact.get("impact_scope", "")


def _business_type_text(incident: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    system = _display_system(incident.get("system_name") or "")
    description = diagnosis.get("fault_description", "")
    if system and ("无法打开" in description or "无法访问" in description or "不可用" in description):
        return "所有业务"
    return f"{system}相关业务" if system else ""


def _fill_tables(
    tables: list[ET.Element],
    incident: dict[str, Any],
    impact: dict[str, Any],
    diagnosis: dict[str, Any],
    handling: dict[str, Any],
) -> None:
    table_writes = (
        (
            ("影响业务", "影响业务范围"),
            {
                "影响业务": _affected_business_text(incident, diagnosis, impact),
                "业务故障原因": diagnosis.get("fault_cause", ""),
                "解决方式": handling.get("solution", ""),
                "影响业务种类": _business_type_text(incident, diagnosis),
                "影响业务范围": _impact_scope_text(incident, diagnosis, impact),
            },
        ),
        (
            ("发生时间", "恢复时间"),
            {
                # BUGFIX42 (2026-07-13 15:30, 用户原话 "时间不采用推断更加好"):
                # 报告表 2 显示 5 个时间点 (发生 + 定位 + 处理完 + 恢复 + 时长)
                "发生时间": _cn_datetime(incident.get("occurred_at", "")) + _diagnosis_suffix(incident),
                "恢复时间": _cn_datetime(incident.get("recovered_at", "")) + _resolved_suffix(incident),
                "发现人": incident.get("discovery_source", ""),
            },
        ),
        (
            # BUGFIX44 (2026-07-13 16:10, 用户原话 "故障发现红框需要删除"):
            # 报告"故障发现"段下的 ("处理时间", "处理人") 表**整段删除**.
            # 原因: 已表 1 ("发生时间/恢复时间/发现人/定位/处理完成") 完整覆盖 5 个时间点 + 发现人, 不再重复.
            # 红框=表头灰色底纹 (BFBFBF) + 边框, 也一并删除.
            # 修法: 让 _set_row_by_headers 跳过 BUGFIX44 占位 header
            ("__BUGFIX44_DELETE__",),
            {},
        ),
        (
            ("关闭时间", "上报时间"),
            {
                "故障原因": diagnosis.get("fault_cause", ""),
                "关闭时间": _cn_datetime(incident.get("recovered_at", "")),
                "上报人": incident.get("discovery_source", ""),
                "上报时间": _cn_datetime(incident.get("occurred_at", "")),
            },
        ),
    )
    for required_headers, values_by_header in table_writes:
        for table in tables:
            if _set_row_by_headers(table, 1, values_by_header, required_headers):
                break


def _detailed_process_lines(
    incident: dict[str, Any],
    diagnosis: dict[str, Any],
    handling: dict[str, Any],
) -> list[str]:
    records = [str(item).strip() for item in handling.get("process_records") or [] if str(item).strip()]
    if handling.get("process_source") == "actual_timeline":
        group = handling.get("handling_group") or handling.get("owner") or "运维人员"
        return [
            _expand_process_record(record, incident, group, index, len(records))
            for index, record in enumerate(records)
        ]
    group = handling.get("handling_group") or handling.get("owner") or "运维人员"
    system = _display_system(incident.get("system_name") or "")
    symptom = _core_symptom(incident, diagnosis)
    cause = _strip_sentence_end(diagnosis.get("fault_cause", ""))
    solution = _strip_sentence_end(handling.get("solution", ""))
    target = system or "相关系统"
    service_scope = _service_scope(cause, solution)
    if len(records) <= 2:
        first_record = records[0] if records else ""
        discovery_name = incident.get("discovery_source") or "服务台"
        # 2026-07-11 13:35 BUGFIX15 修复: 第 1 行格式 "{time}分接到{谁}报障{症状}，{group}排查"
        # (用户原话"14:30分接到服务台报障页面无法打开, 多域维护组排查")
        stripped_first = _strip_sentence_end(_strip_leading_time(first_record))
        # 如果 first_record 是占位词"接到故障信息"/"报障"等, 替换成新句式
        placeholder_pattern = re.compile(r"^(?:接到故障信息|接到报障|收到报障|故障报障)$")
        if stripped_first and not placeholder_pattern.match(stripped_first):
            first_detail = stripped_first
        else:
            # BUGFIX43 (2026-07-13 15:50, 用户原话 "我给的字段是这样"):
            # discovery_name 已经含"报障"/"通知"/"反馈"等动词时, 不再重复拼"报障"
            # 避免"接到用户报障报障"+症状 这种重复
            discovery_verb = discovery_name
            if not any(kw in discovery_verb for kw in ("报障", "通知", "反馈", "告知", "发现", "巡检", "上报")):
                discovery_verb = f"{discovery_name}报障"
            first_detail = f"接到{discovery_verb}"
            # 把症状短描述拼到报障后, 让第 1 行更完整
            short_symptom = _short_symptom(symptom, process_target=incident.get("system_name") or "")
            if short_symptom and short_symptom not in first_detail:
                first_detail = f"{first_detail}{short_symptom}"
# BUGFIX43 (2026-07-13 15:50, 用户原话 "我给的字段是这样 不是两行故障过程记录"):
        # 报告"故障处理过程&记录"从 5 行硬模板**改成 4 行** (按 4 个时间点, 避免重复表 2 已有时间)
        # 第 1 行: 发生时间 + 接到报障 + 症状, 多域维护组排查
        # 第 2 行: 原因定位时间 + 原因定位 (用 fault_cause 短描述)
        # 第 3 行: 问题解决时间 + solution 执行
        # 第 4 行: 恢复时间 + 验证系统恢复
        step_times = _process_step_times_4(incident, records)
        cause_short = _strip_sentence_end(cause)[:12] if cause else ""
        return [
            f"{step_times[0]}{first_detail}，{group}排查；",
            f"{step_times[1]}{group}定位{cause_short or '故障原因'}；",
            f"{step_times[2]}{group}{_process_solution_text(solution) if solution else '处理完成'}；",
            f"{step_times[3]}{group}验证{target}访问和登录恢复正常。",
        ]
    # BUGFIX43 (2026-07-13 15:50, 用户原话 "前面出现时间后面怎么还出现时间"):
    # 如果 process_records > 2 (用户给了完整过程), 仍用 5 行模板 (保持 BUGFIX13-15 兼容)
    step_times = _process_step_times(incident, records)
    # 2026-07-11 13:00 BUGFIX13 修复: records > 2 时也必须展开成 5 段式
    # (原 line 250 直接 return records 会少 2 行, 用户原话"不是应该五行吗")
    # 五行固定结构: 1-接报+排查 → 2-核查 → 3-发现 → 4-处置 → 5-验证
    # 2026-07-11 13:30 BUGFIX14 修复: first_detail 句式改为"接到谁报障"
    step_times = _process_step_times(incident, records)
    process_target = incident.get("system_name") or target
    first_record = records[0] if records else ""
    discovery_name = incident.get("discovery_source") or "服务台"
    # 2026-07-11 13:35 BUGFIX15 修复: 拼 symptom 短描述让第 1 行更完整
    # (用户原话"14:30分接到服务台报障页面无法打开, 多域维护组排查")
    stripped_first = _strip_sentence_end(_strip_leading_time(first_record))
    if stripped_first:
        first_detail = stripped_first
    else:
        first_detail = f"接到{discovery_name}报障"
        short_symptom = _short_symptom(symptom, process_target=incident.get("system_name") or "")
        if short_symptom and short_symptom not in first_detail:
            first_detail = f"{first_detail}{short_symptom}"
    if cause:
        cause_line = f"{step_times[2]}{group}检查{service_scope}运行状态，发现{cause}；"
    else:
        cause_line = f"{step_times[2]}{group}检查{service_scope}运行状态；"
    if solution:
        solution_line = f"{step_times[3]}{group}{_process_solution_text(solution)}；"
    else:
        solution_line = f"{step_times[3]}{group}执行恢复操作；"
    return [
        f"{step_times[0]}{first_detail}，{group}排查；",
        f"{step_times[1]}{group}接报并核查{process_target}访问状态，确认{symptom or process_target + '访问异常'}；",
        cause_line,
        solution_line,
        f"{step_times[4]}{group}验证{process_target}访问和登录恢复正常。",
    ]


def _numbered_process_lines(lines: list[str]) -> list[str]:
    return [f"{index}、  {line}" for index, line in enumerate(lines, 1) if line.strip()]


def _record_time_prefix(record: str, incident: dict[str, Any]) -> str:
    text = record.strip()
    full = re.match(r"^\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2})[:：](\d{1,2})", text)
    if full:
        hour = int(full.group(4))
        minute = int(full.group(5))
        return f"{hour:02d}:{minute:02d}分"
    short = re.match(r"^\s*(\d{1,2})[:：](\d{1,2})", text)
    if short:
        hour, minute = (int(part) for part in short.groups())
        return f"{hour:02d}:{minute:02d}分"
    return ""


def _time_text(value: Any) -> str:
    parsed = _parse_dt(str(value or ""))
    return f"{parsed.hour:02d}:{parsed.minute:02d}分" if parsed else ""


def _process_step_times(incident: dict[str, Any], records: list[str]) -> list[str]:
    record_times = [_record_time_prefix(record, incident) for record in records]
    start = _time_text(incident.get("occurred_at", "")) or (record_times[0] if record_times else "")
    end = _time_text(incident.get("recovered_at", "")) or (record_times[-1] if record_times else "")
    times = [start, "", "", "", end]
    for index, value in enumerate(record_times[1:-1][:3], 1):
        times[index] = value
    times[2] = _time_text(incident.get("diagnosis_at", "")) or times[2]
    times[3] = _time_text(incident.get("resolved_at", "")) or times[3]
    return times


# BUGFIX43 (2026-07-13 15:50, 用户原话 "我给的字段是这样 不是两行故障过程记录 我不是给了4个时间点吗 肯定是按4个时间点写4行"):
# 报告"故障处理过程&记录" 4 行版本用 4 个时间点 (BUGFIX42 新增):
#   1. occurred_at       (发生时间)
#   2. diagnosis_at      (原因定位时间, BUGFIX42)
#   3. resolved_at       (问题处理完成时间, BUGFIX42)
#   4. recovered_at      (系统恢复时间)
# 优先级: 用户输入 (incident 字段) > process_records 推断 > fallback
def _process_step_times_4(
    incident: dict[str, Any], records: list[str]
) -> list[str]:
    """返回 4 个时间: 发生 / 定位 / 处理完成 / 恢复。"""
    # 1. 发生时间: incident.occurred_at 优先, process_records[0] fallback
    start = _time_text(incident.get("occurred_at", "")) or (
        _record_time_prefix(records[0], incident) if records else ""
    )
    # 2. 定位时间: 只采用用户明确提供的 incident.diagnosis_at
    mid1 = _time_text(incident.get("diagnosis_at", ""))
    # 3. 处理完成时间: 只采用用户明确提供的 incident.resolved_at
    mid2 = _time_text(incident.get("resolved_at", ""))
    # 4. 恢复时间: incident.recovered_at 优先, process_records[-1] fallback
    end = _time_text(incident.get("recovered_at", "")) or (
        _record_time_prefix(records[-1], incident) if records else ""
    )
    return [start, mid1, mid2, end]


def _format_time(value: datetime) -> str:
    return f"{value.hour:02d}:{value.minute:02d}分"


def _service_scope(cause: str, solution: str) -> str:
    source = f"{cause} {solution}".upper()
    terms = [term for term in ("WEB", "CAS", "REDIS", "ZOOKEEPER") if term in source]
    if terms:
        return "和".join(terms) + "相关服务"
    if "数据库" in cause or "数据库" in solution:
        return "中间件和服务"
    return "相关服务"


def _is_database_scope(*texts: Any) -> bool:
    source = " ".join(str(text or "") for text in texts)
    upper = source.upper()
    return any(term in source for term in ("数据库", "数据源", "慢SQL")) or any(
        term in upper for term in ("DB2", "MYSQL", "ORACLE", "POSTGRES", "SQL SERVER")
    )


def _is_host_scope(*texts: Any) -> bool:
    source = " ".join(str(text or "") for text in texts)
    upper = source.upper()
    if any(term in source for term in ("主机", "服务器", "操作系统", "虚拟机", "宿主机", "硬件")) or any(
        term in upper for term in ("LINUX", "WINDOWS", "VMWARE")
    ):
        return True
    if _is_middleware_scope(*texts):
        return False
    return any(term in source for term in ("磁盘", "内存")) or "CPU" in upper


def _is_middleware_scope(*texts: Any) -> bool:
    source = " ".join(str(text or "") for text in texts)
    upper = source.upper()
    return any(term in source for term in ("中间件", "缓存", "消息队列", "容器", "服务进程")) or any(
        term in upper
        for term in (
            "REDIS",
            "ZOOKEEPER",
            "TOMCAT",
            "NGINX",
            "WEBLOGIC",
            "KAFKA",
            "RABBITMQ",
            "ROCKETMQ",
            "ELASTICSEARCH",
            "K8S",
            "KUBERNETES",
            "DOCKER",
            "JVM",
            "CAS",
            "WEB",
        )
    )


def _process_solution_text(solution: str) -> str:
    stripped = _strip_sentence_end(solution)
    return stripped if stripped.startswith(("重启", "恢复", "切换", "回退", "调整")) else f"执行{stripped}"


def _operational_followup_lines(
    incident: dict[str, Any],
    diagnosis: dict[str, Any],
    handling: dict[str, Any],
) -> list[str]:
    system = _display_system(incident.get("system_name") or "相关系统")
    cause = _strip_sentence_end(diagnosis.get("fault_cause", "")) or "相关服务异常"
    solution = _strip_sentence_end(handling.get("solution", "")) or "故障恢复操作"
    group = handling.get("handling_group") or handling.get("owner") or "运维人员"
    symptom = _core_symptom(incident, diagnosis) or f"{system}访问异常"
    service_scope = _service_scope(cause, solution)
    seed = "|".join((system, str(incident.get("occurred_at") or ""), cause, solution))
    variant = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16) % 3
    if _is_database_scope(cause, solution):
        templates = (
            (
                f"1、{group}加强{system}中间件和服务运行状态巡检，重点关注系统访问、认证链路、服务进程及日志异常情况。",
                f"2、{group}完善数据库连通性拨测和通知{DATABASE_OWNER_GROUP}处理机制，发现数据库连接异常或数据库服务不可用时及时联动处理。",
                f"3、数据库侧由{DATABASE_OWNER_GROUP}负责处置和后续监控完善，处置完成后由{group}执行恢复验证，复核系统打开、登录认证及关键功能访问状态。",
                f"4、将“{solution}”纳入标准处置步骤，明确执行前检查、异常节点操作、回退条件和执行后状态确认。",
                f"5、建立{system}故障复盘和手册更新机制，将{cause}、{symptom}及处置结果纳入知识库和应急预案。",
            ),
            (
                f"1、{group}补充{system}接口服务、信令链路、连接池及关键中间件巡检项，形成班前检查和异常留痕。",
                f"2、为{system}数据库连通性、连接耗时和失败率设置分级告警，触发后由{group}完成拨测并通知{DATABASE_OWNER_GROUP}处置。",
                f"3、建立联合恢复验证清单，明确{group}负责系统侧复核、{DATABASE_OWNER_GROUP}负责数据库侧确认，双方共同验证核心业务。",
                f"4、针对“{solution}”补充执行前资源检查、操作审批、回退方案和操作后观察期，降低异常节点重复发生风险。",
                f"5、按月复盘{system}{cause}类故障，跟踪整改项、告警有效性和责任人完成情况，并同步更新运维手册。",
            ),
            (
                f"1、将{system}接口可用性、认证成功率、信令时延及数据库连接状态纳入统一监控看板，持续观察异常趋势。",
                f"2、{group}增加数据库网络抖动、连接超时和连接池耗尽告警，异常时按分级升级路径联动{DATABASE_OWNER_GROUP}。",
                f"3、组织数据库不可用场景联合演练，由{DATABASE_OWNER_GROUP}完成数据库侧处置，{group}按恢复验证清单确认系统和业务状态。",
                f"4、将“{solution}”整理为标准作业卡，补充适用条件、操作步骤、风险提示、回退方式和验证口径。",
                f"5、把本次{symptom}的现象、{cause}、排查证据和处置结果关联归档，便于后续同类告警快速定位。",
            ),
        )
    elif _is_host_scope(cause, solution):
        templates = (
            (
                f"1、{group}加强{system}主机巡检，重点检查CPU、内存、磁盘、文件系统、系统负载及网络连通性。",
                f"2、根据{cause}补充主机资源阈值和容量趋势告警，达到预警线时提前清理、扩容或迁移负载。",
                f"3、完善主机恢复验证清单，先确认操作系统和资源状态，再由{group}验证服务进程、端口及核心业务。",
                f"4、将“{solution}”纳入主机标准处置步骤，明确执行权限、影响评估、回退方案和操作后观察期。",
                f"5、建立{system}主机类故障复盘机制，将{symptom}、{cause}和整改结果更新到巡检手册及应急预案。",
            ),
            (
                f"1、将{system}主机CPU、内存、磁盘IO、网络丢包和关键进程纳入统一监控看板，按班次检查运行趋势。",
                f"2、{group}为主机资源突增、磁盘空间不足和系统负载异常设置分级告警，明确升级通知和超时催办要求。",
                f"3、建立主机侧与应用侧联合恢复验证流程，主机状态恢复后继续验证服务启动、接口调用和业务访问。",
                f"4、针对“{solution}”补充操作审批、数据备份、执行记录和异常回退要求，确保处置过程可追溯。",
                f"5、定期核查{system}主机容量基线、补丁版本和高可用状态，跟踪同类{cause}整改项完成情况。",
            ),
            (
                f"1、{group}完善{system}主机自动巡检，覆盖硬件告警、文件系统、系统日志、时间同步和网络连通状态。",
                f"2、结合本次{symptom}优化资源告警阈值和告警收敛规则，减少重复告警并提前识别容量风险。",
                f"3、组织主机不可用及资源耗尽场景演练，按恢复验证清单确认操作系统、服务进程和核心业务状态。",
                f"4、将“{solution}”整理为主机标准作业卡，补充适用条件、操作命令、风险提示、回退方式和验证口径。",
                f"5、归档本次{cause}的资源曲线、系统日志、排查过程和处置结果，为后续同类问题提供定位依据。",
            ),
        )
    elif _is_middleware_scope(cause, solution):
        templates = (
            (
                f"1、{group}加强{system}中间件巡检，重点检查服务进程、监听端口、集群节点、连接池和关键日志。",
                f"2、根据{cause}补充中间件可用性、响应时间、积压量和资源使用率告警，异常时及时升级处置。",
                f"3、完善中间件恢复验证清单，处置后依次确认进程、端口、集群状态、上下游连接及核心业务。",
                f"4、将“{solution}”纳入中间件标准处置步骤，明确依赖检查、配置备份、节点操作顺序和回退条件。",
                f"5、建立{system}中间件容量基线和故障复盘机制，将{symptom}及处置经验更新到运维手册。",
            ),
            (
                f"1、将{system}中间件进程健康度、端口状态、内存占用、连接数和响应耗时纳入统一监控看板。",
                f"2、{group}补充集群节点离线、队列积压、缓存命中率下降和连接池异常等分级告警及通知规则。",
                f"3、建立分层恢复验证流程，先确认中间件实例和集群状态，再验证接口调用、认证链路和业务访问。",
                f"4、针对“{solution}”补充变更审批、配置差异检查、回滚脚本和操作后观察时间，降低重复故障风险。",
                f"5、定期复核{system}中间件版本、容量和单点风险，跟踪{cause}整改项及应急联系人有效性。",
            ),
            (
                f"1、{group}完善{system}中间件自动健康检查，覆盖进程存活、端口连通、集群同步及依赖服务状态。",
                f"2、结合本次{symptom}优化异常日志关键字、性能阈值和告警收敛规则，提升首报信息完整性。",
                f"3、组织中间件节点异常和服务不可用场景演练，按恢复验证清单确认实例、集群、链路和业务状态。",
                f"4、将“{solution}”整理为中间件标准作业卡，补充执行条件、操作步骤、风险提示、回退方式和验证标准。",
                f"5、归档本次{cause}的监控曲线、日志证据、排查顺序和处置结果，形成同类问题快速定位指引。",
            ),
        )
    else:
        templates = (
            (
                f"1、{group}加强{system}{cause}运行状态巡检，重点关注系统访问、认证链路、服务进程及日志异常情况。",
                f"2、补充{system}{cause}监控告警，发现{symptom}、认证异常或服务不可用时，及时通知值班运维人员排查处置。",
                f"3、完善{solution}后的恢复验证清单，处置完成后由{group}复核系统打开、登录认证及关键功能访问状态。",
                f"4、将“{solution}”纳入标准处置步骤，明确执行前检查、操作步骤、回退条件和执行后状态确认。",
                f"5、定期复盘{system}故障处置情况，将{cause}、{symptom}及处置结果更新到运维手册和应急预案。",
            ),
            (
                f"1、{group}完善{system}{service_scope}日常健康检查，增加进程、端口、资源使用率和关键日志巡检项。",
                f"2、根据本次{symptom}补充可用性和性能告警阈值，明确告警确认、升级通知和超时催办要求。",
                f"3、将系统打开、登录认证、核心功能和上下游链路纳入恢复验证清单，处置后由{group}逐项确认并留痕。",
                f"4、针对“{solution}”形成标准操作卡，补充执行条件、影响评估、回退步骤和操作后观察时间。",
                f"5、建立{system}同类故障跟踪台账，定期复核整改项、告警有效性和应急联系人信息。",
            ),
            (
                f"1、将{system}{service_scope}可用率、响应时间和异常日志纳入监控看板，按班次检查运行趋势。",
                f"2、{group}补充{cause}自动探测和告警收敛规则，减少重复告警并提升首报信息完整性。",
                f"3、完善分层恢复验证机制，先确认服务进程和端口，再验证认证链路、核心功能及上下游调用。",
                f"4、复核“{solution}”涉及的配置、容量和变更风险，明确异常回退、观察期和再次触发时的升级路径。",
                f"5、结合本次{symptom}开展故障复盘和桌面演练，将排查顺序、关键命令和验证标准更新到应急预案。",
            ),
        )
    return ["后续运维完善措施：", *templates[variant]]


def _replace_first(body: ET.Element, old_text: str, new_text: str) -> None:
    for paragraph in _paragraphs(body):
        if _same_label(_text(paragraph), old_text):
            _set_text(paragraph, new_text)
            return
    for paragraph in _paragraphs(body):
        current = _text(paragraph)
        if "故障报告" in current and ("XX" in current.upper() or current.endswith("故障报告")):
            _set_text(paragraph, new_text)
            return


def _replace_prefix(body: ET.Element, prefix: str, suffix: str) -> None:
    for paragraph in _paragraphs(body):
        if _normalize_label(_text(paragraph)).startswith(_normalize_label(prefix)):
            _set_text_after_prefix(paragraph, prefix, suffix)
            return


def _fill_after_heading(body: ET.Element, heading: str, text: str) -> None:
    children = list(body)
    index = _paragraph_index(children, heading)
    if index is None:
        return
    target = _next_paragraph(children, index + 1)
    if target is not None:
        _set_text(target, text)
        if heading == "故障原因分析&结论":
            _clear_paragraph_indent_and_alignment(target)


def _replace_tail_section(body: ET.Element, heading: str, lines: list[str]) -> None:
    children = list(body)
    start = _paragraph_index(children, heading)
    if start is None:
        return
    end = next((idx for idx in range(start + 1, len(children)) if children[idx].tag.endswith("sectPr")), len(children))
    _fill_paragraph_slots(body, children, start + 1, end, lines)


def _replace_between_table_and_heading(body: ET.Element, table_number: int, heading: str, lines: list[str]) -> None:
    children = list(body)
    table_seen = 0
    table_index: int | None = None
    heading_index: int | None = None
    for index, child in enumerate(children):
        if child.tag.endswith("tbl"):
            table_seen += 1
            if table_seen == table_number:
                table_index = index
        if table_index is not None and _is_paragraph(child) and _matches_label(_text(child), heading):
            heading_index = index
            break
    if table_index is None or heading_index is None or table_index >= heading_index:
        return
    _fill_paragraph_slots(body, children, table_index + 1, heading_index, lines)


def _set_row_by_headers(
    table: ET.Element,
    row_index: int,
    values_by_header: dict[str, Any],
    required_headers: tuple[str, ...],
) -> bool:
    rows = table.findall("./w:tr", NS)
    if len(rows) <= row_index:
        return False
    headers = [_text(cell) for cell in rows[0].findall("./w:tc", NS)]
    if not all(_has_header(headers, header) for header in required_headers):
        return False
    cells = rows[row_index].findall("./w:tc", NS)
    for cell, header in zip(cells, headers):
        matched = _matching_header(header, values_by_header)
        if matched is not None:
            _set_cell_text(cell, values_by_header[matched])
    return True


# BUGFIX44 (2026-07-13 16:10, 用户原话 "故障发现红框需要删除"):
# 删除 body 里某个标题段落之后, 表头包含 required_headers 的 table.
# 例: heading="故障发现", required_headers=("处理时间", "处理人")
def _delete_table_after_heading(
    body: ET.Element,
    heading: str,
    required_headers: tuple[str, ...],
) -> None:
    children = list(body)
    heading_index: int | None = None
    for i, child in enumerate(children):
        if not _is_paragraph(child):
            continue
        text_runs = child.findall(".//w:t", NS)
        if text_runs and any(_text(child) == heading for _ in text_runs):
            heading_index = i
            break
    if heading_index is None:
        return
    # 在 heading 后找第一个 table, 表头匹配就删
    for j in range(heading_index + 1, len(children)):
        child = children[j]
        tag = child.tag.split("}")[-1]
        if tag != "tbl":
            continue
        rows = child.findall("./w:tr", NS)
        if not rows:
            continue
        headers = [_text(cell) for cell in rows[0].findall("./w:tc", NS)]
        if all(_has_header(headers, h) for h in required_headers):
            body.remove(child)
            return
        # 不匹配, 继续往下找 (只删第一个匹配的)
        return


def _fill_paragraph_slots(
    body: ET.Element,
    children: list[ET.Element],
    start: int,
    end: int,
    lines: list[str],
) -> None:
    slots = [child for child in children[start:end] if _is_paragraph(child)]
    style_source = _first_plain_slot(slots)
    style_properties = copy.deepcopy(style_source.find("./w:pPr", NS)) if style_source is not None else None
    template = copy.deepcopy(style_source) if style_source is not None else _paragraph_template(children, start)
    for paragraph, line in zip(slots, lines):
        _apply_paragraph_properties(paragraph, style_properties)
        _set_text(paragraph, line)
        _clear_paragraph_text_background(paragraph)
    for paragraph in slots[len(lines):]:
        _set_text(paragraph, "")
    if len(lines) <= len(slots):
        return
    current_children = list(body)
    insert_at = current_children.index(slots[-1]) + 1 if slots else min(start, len(current_children))
    for line in lines[len(slots):]:
        paragraph = copy.deepcopy(template)
        _apply_paragraph_properties(paragraph, style_properties)
        _set_text(paragraph, line)
        _clear_paragraph_text_background(paragraph)
        body.insert(insert_at, paragraph)
        insert_at += 1


def _first_plain_slot(slots: list[ET.Element]) -> ET.Element | None:
    if not slots:
        return None
    return next((slot for slot in slots if _paragraph_style_id(slot) != "45"), slots[0])


def _paragraph_style_id(paragraph: ET.Element) -> str:
    p_style = paragraph.find("./w:pPr/w:pStyle", NS)
    return p_style.get(f"{{{W_NS}}}val", "") if p_style is not None else ""


def _apply_paragraph_properties(paragraph: ET.Element, properties: ET.Element | None) -> None:
    current = paragraph.find("./w:pPr", NS)
    if current is not None:
        paragraph.remove(current)
    if properties is not None:
        paragraph.insert(0, copy.deepcopy(properties))


def _set_cell_text(cell: ET.Element, value: Any) -> None:
    paragraphs = cell.findall("./w:p", NS)
    if not paragraphs:
        paragraphs = [ET.SubElement(cell, f"{{{W_NS}}}p")]
    _set_text(paragraphs[0], value)
    for paragraph in paragraphs[1:]:
        _set_text(paragraph, "")


def _clear_paragraph_indent_and_alignment(paragraph: ET.Element) -> None:
    ppr = paragraph.find("./w:pPr", NS)
    if ppr is None:
        ppr = ET.Element(f"{{{W_NS}}}pPr")
        paragraph.insert(0, ppr)
    for tag in ("ind", "jc", "numPr"):
        child = ppr.find(f"./w:{tag}", NS)
        if child is not None:
            ppr.remove(child)
    ind = ET.Element(f"{{{W_NS}}}ind")
    ind.set(f"{{{W_NS}}}left", "0")
    ind.set(f"{{{W_NS}}}firstLine", "480")
    ind.set(f"{{{W_NS}}}firstLineChars", "200")
    ppr.append(ind)
    jc = ET.Element(f"{{{W_NS}}}jc")
    jc.set(f"{{{W_NS}}}val", "left")
    ppr.append(jc)


def _clear_paragraph_text_background(paragraph: ET.Element) -> None:
    ppr = paragraph.find("./w:pPr", NS)
    if ppr is not None:
        shd = ppr.find("./w:shd", NS)
        if shd is not None:
            ppr.remove(shd)
    for rpr in paragraph.findall(".//w:rPr", NS):
        for tag in ("highlight", "shd", "color"):
            child = rpr.find(f"./w:{tag}", NS)
            if child is not None:
                rpr.remove(child)


def _paragraph_template(children: list[ET.Element], start: int) -> ET.Element:
    for child in children[start:]:
        if _is_paragraph(child):
            return copy.deepcopy(child)
    paragraph = ET.Element(f"{{{W_NS}}}p")
    return paragraph


def _paragraph_index(children: list[ET.Element], text: str, start: int = 0) -> int | None:
    for index in range(start, len(children)):
        child = children[index]
        if _is_paragraph(child) and _matches_label(_text(child), text):
            return index
    return None


def _next_paragraph(children: list[ET.Element], start: int) -> ET.Element | None:
    for child in children[start:]:
        if _is_paragraph(child):
            return child
    return None


def _paragraphs(body: ET.Element) -> list[ET.Element]:
    return [child for child in list(body) if _is_paragraph(child)]


def _tables(body: ET.Element) -> list[ET.Element]:
    return [child for child in list(body) if child.tag.endswith("tbl")]


def _is_paragraph(element: ET.Element) -> bool:
    return element.tag.endswith("p")


def _text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.findall(".//w:t", NS)).strip()


def _set_text(paragraph: ET.Element, text: Any) -> None:
    value = "" if text is None else str(text)
    runs = paragraph.findall("./w:r", NS)
    if not runs:
        runs = [ET.SubElement(paragraph, f"{{{W_NS}}}r")]
    _set_run_text(runs[0], value)
    for run in runs[1:]:
        _set_run_text(run, "")


def _set_text_after_prefix(paragraph: ET.Element, prefix: str, suffix: Any) -> None:
    value = "" if suffix is None else str(suffix)
    nodes = paragraph.findall(".//w:t", NS)
    if not nodes:
        _set_text(paragraph, f"{prefix}{value}")
        return
    remaining = len(prefix)
    written = False
    for node in nodes:
        current = node.text or ""
        if remaining >= len(current):
            remaining -= len(current)
            continue
        if remaining > 0:
            _set_text_node(node, current[:remaining] + value)
            remaining = 0
            written = True
        elif not written:
            _set_text_node(node, value)
            written = True
        else:
            _set_text_node(node, "")
    if not written:
        _set_text(paragraph, f"{prefix}{value}")


def _set_run_text(run: ET.Element, value: str) -> None:
    rpr = run.find("./w:rPr", NS)
    for child in list(run):
        if child is not rpr:
            run.remove(child)
    parts = value.split("\n")
    if not parts:
        parts = [""]
    for index, part in enumerate(parts):
        if index:
            ET.SubElement(run, f"{{{W_NS}}}br")
        node = ET.SubElement(run, f"{{{W_NS}}}t")
        _set_text_node(node, part)


def _set_text_node(node: ET.Element, value: str) -> None:
    node.text = value
    if value.startswith(" ") or value.endswith(" "):
        node.set(XML_SPACE, "preserve")
    elif XML_SPACE in node.attrib:
        del node.attrib[XML_SPACE]


def _has_header(headers: list[str], expected: str) -> bool:
    return any(_same_label(header, expected) for header in headers)


def _matching_header(header: str, values_by_header: dict[str, Any]) -> str | None:
    header_norm = _normalize_label(header)
    for expected in values_by_header:
        if header_norm == _normalize_label(expected):
            return expected
    for expected in values_by_header:
        if _same_label(header, expected):
            return expected
    return None


def _matches_label(current: str, expected: str) -> bool:
    if _same_label(current, expected):
        return True
    matchers = HEADING_MATCHERS.get(expected, ())
    return any(all(keyword in current for keyword in keywords) for keywords in matchers)


def _same_label(current: str, expected: str) -> bool:
    current_norm = _normalize_label(current)
    expected_norm = _normalize_label(expected)
    return bool(current_norm and expected_norm and (current_norm == expected_norm or expected_norm in current_norm))


def _normalize_label(value: str) -> str:
    return re.sub(r"[\s\t:：&＆/\\\-—_（）()\[\]【】]+", "", str(value or "")).strip()


def _time_range(incident: dict[str, Any]) -> str:
    start = incident.get("occurred_at", "")
    end = incident.get("recovered_at", "")
    if start and end:
        return f"{start} 至 {end}"
    return start or end


def _display_system(system_name: str) -> str:
    if not system_name:
        return ""
    return system_name if system_name.endswith("系统") else f"{system_name}系统"


def _date_text(value: Any) -> str:
    parsed = _parse_dt(str(value or ""))
    if parsed:
        return f"{parsed.year}-{parsed.month}-{parsed.day}"
    return str(value or "")


def _cn_datetime(value: Any, *, minute_suffix: bool = False) -> str:
    parsed = _parse_dt(str(value or ""))
    if not parsed:
        return str(value or "")
    suffix = "分" if minute_suffix else ""
    return f"{parsed.year}年{parsed.month}月{parsed.day}日{parsed.hour:02d}:{parsed.minute:02d}{suffix}"


# BUGFIX42 (2026-07-13 15:30, 用户原话 "时间不采用推断更加好"):
# 加 2 个 helper, 在"发生时间/恢复时间"单元格后追加原因定位/处理完成时间
def _diagnosis_suffix(incident: dict[str, Any]) -> str:
    """返回"（原因定位 HH:mm）"或空字符串。"""
    diagnosis_at = str(incident.get("diagnosis_at") or "").strip()
    if not diagnosis_at:
        return ""
    cn = _cn_datetime(diagnosis_at)
    return f"\n（原因定位 {cn}）" if cn else ""


def _resolved_suffix(incident: dict[str, Any]) -> str:
    """返回"（处理完成 HH:mm）"或空字符串。"""
    resolved_at = str(incident.get("resolved_at") or "").strip()
    if not resolved_at:
        return ""
    cn = _cn_datetime(resolved_at)
    return f"\n（处理完成 {cn}）" if cn else ""


def _parse_dt(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def _ensure_sentence(text: Any) -> str:
    value = _strip_sentence_end(text)
    return f"{value}。" if value else ""


def _strip_sentence_end(text: Any) -> str:
    return str(text or "").strip().rstrip("。；;")


def _strip_leading_time(text: str) -> str:
    return re.sub(r"^\s*(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*)?\d{1,2}[:：]\d{1,2}\s*(?:分)?", "", text).strip(" ，,。；;")


def _short_symptom(symptom: str, process_target: str = "") -> str:
    """BUGFIX15: 提取症状短描述, 用于第 1 行"接到XX报障{症状}, YY排查"句式.
    用户原话"14:30分接到服务台报障页面无法打开, 多域维护组排查"
    优先级: 1) 页面/界面无法打开 2) 无法访问/无法登录 3) XX异常/XX失败
    """
    if not symptom:
        return ""
    text = symptom[:50]
    # 1. 最高优先: "页面无法打开" / "界面无法打开" / "页面打不开" / "界面打不开"
    for kw in ("页面无法打开", "界面无法打开", "页面无法访问", "界面无法访问",
               "页面打不开", "界面打不开", "页面无法使用", "界面无法使用"):
        if kw in text:
            return kw
    # 2. 次优先: "无法打开" / "无法访问" / "无法登录" / "无法使用"
    for kw in ("无法打开", "无法访问", "无法登录", "无法使用"):
        if kw in text:
            return kw
    # 3. "XX异常" / "XX失败"
    m = re.search(r"([一-龥]{2,8}(?:异常|失败))", text)
    if m:
        return m.group(1)
    # 4. "无法XX" 通配
    m = re.search(r"(无法[一-龥]{2,8})", text)
    if m:
        return m.group(1)
    # 5. 兜底: symptom 前 8 字
    return text[:8] if text else ""


def _expand_process_record(
    record: str,
    incident: dict[str, Any],
    group: str,
    index: int,
    total: int,
) -> str:
    cleaned = re.sub(r"^\s*\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*", "", record.strip())
    cleaned = _strip_sentence_end(cleaned)
    matched = re.match(r"^(\d{1,2}[:：]\d{2})\s*(.*)$", cleaned)
    time_text = matched.group(1).replace("：", ":") if matched else ""
    action = matched.group(2).strip() if matched else cleaned
    system = _display_system(incident.get("system_name") or "相关系统")

    if index == 0:
        action = re.sub(rf"[，,；;\s]*{re.escape(group)}(?:接报后)?排查(?:系统)?$", "", action).strip()
        if group not in action:
            action = (
                f"{action}；{group}接报后立即响应并开展故障排查，"
                f"重点核查{system}接口服务、信令链路及相关告警信息"
            )
    elif index == total - 1 and group not in action:
        action = (
            f"{action}；{group}同步复核系统运行状态及关键链路，"
            "确认系统运行稳定"
        )
    else:
        action = _polish_operational_action(action)
        if group not in action:
            action = f"{group}{action}"

    return _ensure_sentence(f"{time_text} {action}".strip())


def _polish_operational_action(action: str) -> str:
    text = action.replace("数据库以及信令是否正常", "数据库连接状态及信令链路运行情况").replace("以及", "及")
    if "信令数据延时超过正常值" in text and "定位异常节点" not in text:
        text = f"{text.rstrip('，,')}，并继续定位异常节点"
    restarted = re.match(r"^重启(.+?)[，,](.+?)恢复$", text)
    if restarted:
        target, recovered = restarted.groups()
        text = f"重启{target}并持续观察{recovered}状态，确认{recovered}恢复"
    return text


def _core_symptom(incident: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    text = _strip_sentence_end(diagnosis.get("fault_description", ""))
    # 2026-07-11 13:30 BUGFIX14 修复: 多去掉一些常见前缀
    # 避免"用户报障发现服务台通知OPENAPI无法访问"这类 discovery + 前缀 重复
    text = re.sub(r"^(?:服务台通知|服务台反馈|用户反馈|多域维护组巡检发现|巡检发现|服务台发现|用户报障|服务台报障|用户通知|服务台告知)", "", text).strip()
    if text:
        return text
    system = _display_system(incident.get("system_name") or "")
    return f"{system}使用异常" if system else ""
