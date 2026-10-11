from __future__ import annotations

import json
import pathlib
from pathlib import Path
from typing import Any


def build_manual_result(context: dict[str, Any], manual_index_path: Path | None = None) -> dict[str, Any]:
    incident = context["incident"]
    diagnosis = context["diagnosis"]
    handling = context["handling"]
    source_values = [
        incident.get("system_name", ""),
        incident.get("fault_title", ""),
        diagnosis.get("fault_description", ""),
        diagnosis.get("fault_cause", ""),
        handling.get("solution", ""),
    ]
    keywords = _keywords(*source_values)
    result = {
        "status": "待补充",
        "system_name": incident.get("system_name", ""),
        "fault_title": incident.get("fault_title", ""),
        "keywords": keywords,
        "match_name": "",
        "match_target": "",
        "match_reason": "",
    }
    if not manual_index_path or not manual_index_path.exists():
        return result
    try:
        index = json.loads(manual_index_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        result["status"] = "需人工确认"
        result["match_reason"] = f"manual index read failed: {type(exc).__name__}"
        return result

    match = _best_match(index, incident.get("system_name", ""), keywords, source_values)
    if not match:
        return result
    guide, score, matched_terms = match
    paths = _guide_paths(index, manual_index_path, guide)
    if not paths["match_exists"]:
        result.update(
            {
                "status": "需人工确认",
                "match_name": guide.get("fault_pattern", ""),
                "match_target": guide.get("path", ""),
                "manual_system": guide.get("system", ""),
                "manual_domain": guide.get("domain", ""),
                "classification": guide.get("classification", ""),
                "match_path": paths["match_path"],
                "match_html": paths["match_html"],
                "match_reason": "matched index but manual file is missing",
            }
        )
        return result
    result.update(
        {
            "status": "已匹配",
            "match_name": guide.get("fault_pattern", ""),
            "match_target": guide.get("path", ""),
            "manual_system": guide.get("system", ""),
            "manual_domain": guide.get("domain", ""),
            "classification": guide.get("classification", ""),
            "match_path": paths["match_path"],
            "match_html": paths["match_html"],
            "match_exists": paths["match_exists"],
            "match_html_exists": paths["match_html_exists"],
            "match_reason": f"score={score}; terms={', '.join(matched_terms)}",
        }
    )
    return result


def _keywords(*values: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for token in value.replace("【", " ").replace("】", " ").replace("-", " ").split():
            token = token.strip(" ，。；;:：/\\()（）")
            if 2 <= len(token) <= 40 and token not in seen:
                seen.add(token)
                result.append(token)
    return result


def _best_match(
    index: dict[str, Any],
    system_name: str,
    keywords: list[str],
    source_values: list[Any] | None = None,
) -> tuple[dict[str, Any], int, list[str]] | None:
    """
    BUGFIX31 新匹配算法 (2026-07-11 22:50):
    1. 系统先确定 (外层 continue 跳过其他系统) - 已保留
    2. fault_pattern 匹配: 用 manual.keywords 字段 (用户表达关键词表), 不用 symptoms 历史段落
    3. 命中数 + match_priority 加权评分
    4. 完全去掉 _business_hint 的散弹枪

    评分公式:
      score = 命中关键词数 × 5 + match_priority
    命中数 ≥ 1 且 score ≥ 阈值才返回
    """
    query = _normal(" ".join([*keywords, *(str(value) for value in source_values or [])]))
    system_query = _normal(system_name)
    best: tuple[dict[str, Any], int, list[str]] | None = None
    for system in index.get("systems", []):
        system_names = [_normal(system.get("system", "")), *[_normal(alias) for alias in system.get("aliases", [])]]
        system_match = bool(
            system_query
            and (system_query in system_names or system_query.removesuffix("系统") in system_names)
        )
        # 系统隔离: user 指定了 system, 只能匹配该系统的 manuals
        if system_query and not system_match:
            continue
        for guide in system.get("guides", []):
            score = 5 if system_match else 0
            matched_terms: list[str] = []
            # BUGFIX31: 用 guide.keywords (用户表达关键词表), 反向子串匹配
            # 即: 每个 guide_keyword 在用户 query (含 keywords + source_values) 中是否出现
            guide_keywords = guide.get("keywords", [])
            if guide_keywords:
                # 新算法: keywords 字段存在, 只用 keywords 匹配
                for gk in guide_keywords:
                    gk_term = _normal(gk)
                    if len(gk_term) >= 2 and gk_term in query and gk not in matched_terms:
                        score += 5  # 关键词命中 +5
                        matched_terms.append(gk)
            else:
                # BUGFIX31 兼容: 老 fixture 无 keywords, fallback 到 symptoms + recovery_actions 子串匹配
                guide_text = _normal(
                    " ".join(
                        [
                            str(guide.get("fault_pattern", "")),
                            str(guide.get("classification", "")),
                            " ".join(str(item) for item in guide.get("symptoms", [])),
                            " ".join(str(item) for item in guide.get("recovery_actions", [])),
                        ]
                    )
                )
                pattern = _normal(guide.get("fault_pattern", ""))
                if pattern and pattern in query:
                    score += 8
                    matched_terms.append(str(guide.get("fault_pattern", "")))
                for keyword in keywords:
                    term = _normal(keyword)
                    if len(term) >= 2 and term in guide_text and keyword not in matched_terms:
                        score += 3
                        matched_terms.append(keyword)
            # BUGFIX31: match_priority 加权 (同分时优先高 priority)
            priority = guide.get("match_priority", 0)
            score += priority
            # 阈值: 必须有 ≥1 个关键词命中 + system_match
            if system_match and matched_terms and score >= 10 and (best is None or score > best[1]):
                best = (guide, score, matched_terms)
    return best


def _business_hint(system_name: str, query: str) -> str:
    system = _normal(system_name)
    if system == "bomc":
        if any(term in query for term in ("连接池", "redis连接池", "连接池满", "连接池耗尽")):
            return "线程池满"
        if any(term in query for term in ("cas", "无法监控", "当前告警", "监控告警", "夯死")):
            return "cas服务异常"
        if any(term in query for term in ("30443", "端口冲突", "公共模块无法连接", "公共模块")):
            return "30443端口冲突与公共模块无法连接"
    if system == "openapi":
        # 2026-07-11 08:35 修改: 增加"数据库网络异常"等关键词
        if any(term in query for term in (
            "数据库网络异常", "数据库网络", "db网络", "网路异常", "网路中断",  # 新增
            "数据库服务宕机", "数据库宕机", "数据库服务异常",
            "接口成功率为0", "成功率为0", "接口率为0",
        )):
            return "数据库服务器网络服务异常"
        if any(term in query for term in ("主机软死锁", "软死锁", "主机宕机")):
            return "主机软死锁"
    if system == "it工单系统":
        if any(term in query for term in ("数据库服务宕机", "数据库宕机", "数据库服务异常", "数据库网络异常")):
            return "数据库服务器网络服务异常"
        if any(term in query for term in ("itsm", "工单页面无法打开", "itsm服务", "工单系统无法访问")):
            return "itsm服务异常"
    if system == "smartbi":
        if any(term in query for term in ("smartbi卡顿", "smartbi无法登录", "smartbi页面无法打开", "smartbi异常")):
            return "线程池满"
    if system == "下载中心":
        if any(term in query for term in ("文件扫描卡死", "扫描程序未完成运行", "扫描卡死")):
            return "文件扫描程序卡死"
        if any(term in query for term in ("用户同步程序卡死", "用户同步卡死", "同步卡死")):
            return "用户同步程序卡死"
    if system == "实时营销":
        if any(term in query for term in ("tomcat服务异常", "tomcat掉线", "tomcat报错", "后台日志大量报错")):
            return "Tomcat服务异常"
        if any(term in query for term in ("端口服务异常", "端口连接数异常", "端口连接数", "端口异常")):
            return "端口服务异常"
    if system == "数字员工":
        if any(term in query for term in ("redis内存溢出", "redis溢出", "智能小7响应异常null")):
            return "redis内存溢出"
        if any(term in query for term in ("智能小7打开异常", "智能小7提报障单异常", "接口服务异常", "接口调用异常")):
            return "接口服务异常"
    if system == "自助分析":
        if any(term in query for term in ("后台任务进程卡住", "跑数任务夯死", "任务夯死", "后台跑数卡住")):
            return "后台任务进程卡住"
    if system == "集中化工单系统":
        if any(term in query for term in ("flow服务假死", "flow假死", "用户无法登录集中化工单系统", "工单系统无法登录")):
            return "flow服务假死"
    return ""


def _normal(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _guide_paths(index: dict[str, Any], manual_index_path: Path, guide: dict[str, Any]) -> dict[str, Any]:
    local_root = manual_index_path.parent.parent / "manuals"
    root = local_root if local_root.exists() else Path(str(index.get("project_root") or local_root))
    raw = str(guide.get("path", ""))
    # 索引里的 path 用 windows 反斜杠，Linux/macOS 下 Path 不识别为分隔符，
    # 这里统一用 PureWindowsPath 拆 parts，再用 Path 拼到 root，保证跨平台。
    if "\\" in raw:
        parts = pathlib.PureWindowsPath(raw).parts
    else:
        parts = tuple(Path(raw).parts)
    guide_rel = Path(*parts) if parts else Path(raw)
    markdown = guide_rel if guide_rel.is_absolute() else root / guide_rel
    html = _html_path(root, guide_rel)
    return {
        "match_path": str(markdown),
        "match_html": str(html) if html else "",
        "match_exists": markdown.exists(),
        "match_html_exists": bool(html and html.exists()),
    }


def _html_path(root: Path, guide_rel: Path) -> Path | None:
    parts = guide_rel.parts
    if not parts:
        return None
    if parts[0] == "处理手册":
        return root / "导出手册" / "html" / Path(*parts[1:]).with_suffix(".html")
    return root / "导出手册" / "html" / guide_rel.with_suffix(".html")
