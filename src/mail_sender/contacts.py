"""
BUGFIX36 (2026-07-13): 联系人查询模块
从 data/reference/contacts/multi_domain_contacts.json 按 system_name 模糊查询 cc 收件人
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CONTACTS_PATH = "data/reference/contacts/multi_domain_contacts.json"


def load_contacts(root: Path) -> dict[str, Any]:
    """加载联系人 JSON, 不存在返回空 doc"""
    path = Path(root) / DEFAULT_CONTACTS_PATH
    if not path.exists():
        return {"systems": [], "domain_alias": {}, "groups": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"systems": [], "domain_alias": {}, "groups": {}}


def lookup_cc(contacts: dict[str, Any], system_name: str) -> tuple[list[str], dict[str, Any]]:
    """
    按 system_name 模糊查 cc 邮箱 (1-3 人)
    返回: (cc_email_list, match_info)
      match_info = {
        "matched": bool,
        "matched_name": str,
        "matched_alias": str,
        "match_level": "exact" | "include" | "alias" | "none",
        "domain_excel": str,
        "domain_feishu": str,
        "level": str,
        "roles": list[str],
        "contacts_count": int,
      }
    """
    if not system_name:
        return [], _none_match()

    system_name_norm = system_name.strip()
    system_name_lower = system_name_norm.lower()

    for s in contacts.get("systems", []):
        name = s.get("name", "")
        aliases = s.get("aliases", []) or [name]
        # L1: 精确匹配 (大小写敏感 + 大小写不敏感)
        if system_name_norm == name:
            return list(s.get("emails", [])), _ok_match(s, name, "exact", name)
        if system_name_lower == name.lower():
            return list(s.get("emails", [])), _ok_match(s, name, "exact", name)
        # L2: 包含匹配 (任一 alias 包含对方或被对方包含)
        # 注意区分: alias ⊇ system_name (alias 是 full name) vs system_name ⊇ alias (alias 是短名)
        for alias in aliases:
            if not alias:
                continue
            # 大小写敏感包含 (优先, 严格)
            if system_name_norm in alias or alias in system_name_norm:
                return list(s.get("emails", [])), _ok_match(s, name, "include", alias)
            # 大小写不敏感包含
            a_low = alias.lower()
            if system_name_lower in a_low or a_low in system_name_lower:
                return list(s.get("emails", [])), _ok_match(s, name, "include", alias)
        # L3: 别名相等 (大小写不敏感), 防 L1/L2 都没命中但 alias 完整匹配
        for alias in aliases:
            if alias.lower() == system_name_lower:
                return list(s.get("emails", [])), _ok_match(s, name, "alias", alias)

    return [], _none_match()


def _ok_match(s: dict[str, Any], name: str, level: str, alias: str = "") -> dict[str, Any]:
    return {
        "matched": True,
        "matched_name": name,
        "matched_alias": alias or name,
        "match_level": level,
        "domain_excel": s.get("domain_excel", ""),
        "domain_feishu": s.get("domain_feishu", ""),
        "level": s.get("level", ""),
        "roles": s.get("roles", []),
        "contacts_count": len(s.get("emails", [])),
    }


def _none_match() -> dict[str, Any]:
    return {
        "matched": False,
        "matched_name": "",
        "matched_alias": "",
        "match_level": "none",
        "domain_excel": "",
        "domain_feishu": "",
        "level": "",
        "roles": [],
        "contacts_count": 0,
    }


def _email_to_name(email: str) -> str:
    """从邮箱推邮箱前缀 (e.g. yangwengang@shsnc.com → yangwengang). 容错 None/空.
    BUGFIX38: 已废弃, 真名改从 zh_names['a'] 取."""
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[0].strip()


def lookup_owner(contacts: dict[str, Any], system_name: str) -> tuple[str, str, dict[str, Any]]:
    """
    BUGFIX38 (2026-07-13 13:00): 按 system_name 查处理人 (中文真名 + 小组名)
    BUGFIX39 (2026-07-13 13:45): 处理人 = 整个小组成员 (A+B+二线 3 人), 不只 A 角 1 人
    返回: (owner_zh_name, group_zh_name, match_info)
      - owner_zh_name: 一线 A+B+二线 真名, "、" 分隔 (e.g. "杨文刚、高喆、沈俊羽", 从 zh_names.a/b/second 取)
      - group_zh_name: 小组名 (e.g. "B 域 16 组", 用 system 自身域+序号)
      - match_info: 复用 _ok_match / _none_match
    """
    if not system_name:
        return "", "", _none_match()

    system_name_norm = system_name.strip()
    system_name_lower = system_name_norm.lower()

    for s in contacts.get("systems", []):
        name = s.get("name", "")
        aliases = s.get("aliases", []) or [name]
        matched = None
        matched_level = None
        matched_alias = name
        # L1: 精确匹配
        if system_name_norm == name:
            matched = s
            matched_level = "exact"
        elif system_name_lower == name.lower():
            matched = s
            matched_level = "exact"
        else:
            # L2: 包含匹配
            for alias in aliases:
                if not alias:
                    continue
                if system_name_norm in alias or alias in system_name_norm:
                    matched = s
                    matched_level = "include"
                    matched_alias = alias
                    break
                a_low = alias.lower()
                if system_name_lower in a_low or a_low in system_name_lower:
                    matched = s
                    matched_level = "include"
                    matched_alias = alias
                    break
            # L3: alias 大小写不敏感相等
            if not matched:
                for alias in aliases:
                    if alias.lower() == system_name_lower:
                        matched = s
                        matched_level = "alias"
                        matched_alias = alias
                        break

        if matched:
            zh_names = matched.get("zh_names", {}) or {}
            # BUGFIX39 v2: 处理人 = A+B+二线 3 人 (按 zh_names.a/b/second 顺序), "、" 分隔
            # 关键: emails 是 [a, b, second] 紧凑列表 (None 跳过), 不是 3 元素定长
            # 所以 email_idx 改用按 zh_names role 配对, 而不是 emails 索引
            owner_parts = []
            seen_emails = set()
            emails = matched.get("emails", []) or [""]
            for role_key in ("a", "b", "second"):
                zh = zh_names.get(role_key, "")
                if not zh and role_key == "a" and len(emails) > 0:
                    zh = _email_to_name(emails[0])
                # 重要: B 角和 second 角空, 绝不能用 emails 索引 fallback (越界)
                if zh and zh not in owner_parts:
                    owner_parts.append(zh)
            owner = "、".join(owner_parts) if owner_parts else ""
            # BUGFIX38 v2: group_zh 用系统自身的域+序号 (不用 group 共享)
            sys_domain = matched.get("domain_excel", "") or matched.get("domain_feishu", "")
            sys_seq = matched.get("seq", 0)
            group = f'{sys_domain} {sys_seq} 组' if sys_seq else sys_domain or matched.get("name", "")
            return owner, group, _ok_match(matched, name, matched_level or "exact", matched_alias)

    return "", "", _none_match()
