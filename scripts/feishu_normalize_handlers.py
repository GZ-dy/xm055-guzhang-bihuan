from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.feishu_ledger.client import (  # noqa: E402
    FeishuApiError,
    batch_update_records,
    list_records,
    load_feishu_config,
)
from src.mail_sender.contacts import load_contacts, lookup_owner  # noqa: E402


CONFIRM_TOKEN = "NORMALIZE-HANDLERS"
OUTPUT = ROOT / "outputs" / "ledger" / "feishu_handler_normalization_result.json"
GROUP_NAMES = ("多域维护组", "多域组", "基础平台组")


def _names(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[、,，;/；\s]+", value) if part.strip()]


def _category(actual: str, expected: str) -> str:
    actual_names = _names(actual)
    expected_names = _names(expected)
    if any(group in actual for group in GROUP_NAMES):
        return "group_name"
    if len(actual_names) <= 1:
        return "single_or_empty"
    if set(actual_names) == set(expected_names):
        return "order_or_separator"
    return "member_mismatch"


def _plan(records: list[dict[str, Any]], contacts: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for record in records:
        fields = record.get("fields") or {}
        system_name = str(fields.get("系统名称") or "").strip()
        actual = str(fields.get("处理人员") or "").strip()
        expected, _, match_info = lookup_owner(contacts, system_name)
        if not expected:
            unmapped.append(
                {
                    "record_id": str(record.get("record_id") or ""),
                    "序号": fields.get("序号"),
                    "系统名称": system_name,
                    "当前处理人员": actual,
                    "match_info": match_info,
                }
            )
            continue
        if actual == expected:
            continue
        candidates.append(
            {
                "record_id": str(record.get("record_id") or ""),
                "序号": fields.get("序号"),
                "系统名称": system_name,
                "更新前": actual,
                "更新后": expected,
                "category": _category(actual, expected),
            }
        )
    return {
        "total_records": len(records),
        "candidate_count": len(candidates),
        "unmapped_count": len(unmapped),
        "category_counts": {
            category: sum(1 for item in candidates if item["category"] == category)
            for category in ("group_name", "single_or_empty", "order_or_separator", "member_mismatch")
        },
        "candidates": candidates,
        "unmapped": unmapped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize Feishu 处理人员 from the contacts contract")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-live-write", default="")
    args = parser.parse_args()

    config = load_feishu_config(ROOT)
    before_records = list_records(config)
    result = _plan(before_records, load_contacts(ROOT))
    result["status"] = "ready"
    result["field_scope"] = ["处理人员"]

    if args.apply:
        if args.confirm_live_write != CONFIRM_TOKEN:
            result["status"] = "blocked"
            result["blocked_reason"] = f"missing --confirm-live-write {CONFIRM_TOKEN}"
        else:
            updates = [
                {"record_id": item["record_id"], "fields": {"处理人员": item["更新后"]}}
                for item in result["candidates"]
            ]
            try:
                applied = batch_update_records(updates, config) if updates else {"updated_count": 0}
                after_by_id = {
                    str(record.get("record_id") or ""): record.get("fields") or {}
                    for record in list_records(config)
                }
                mismatches = [
                    {
                        "record_id": item["record_id"],
                        "序号": item["序号"],
                        "系统名称": item["系统名称"],
                        "expected": item["更新后"],
                        "readback": str(after_by_id.get(item["record_id"], {}).get("处理人员") or ""),
                    }
                    for item in result["candidates"]
                    if str(after_by_id.get(item["record_id"], {}).get("处理人员") or "") != item["更新后"]
                ]
                result["updated_count"] = int(applied.get("updated_count") or 0)
                result["readback_checked"] = len(result["candidates"])
                result["readback_mismatches"] = mismatches
                result["status"] = "updated" if not mismatches and result["updated_count"] == len(updates) else "failed"
            except FeishuApiError as exc:
                result["status"] = "failed"
                result["error"] = str(exc)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "candidate_count": result["candidate_count"],
                "category_counts": result["category_counts"],
                "updated_count": result.get("updated_count", 0),
                "readback_mismatches": len(result.get("readback_mismatches", [])),
                "output": str(OUTPUT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["status"] in ("ready", "updated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
