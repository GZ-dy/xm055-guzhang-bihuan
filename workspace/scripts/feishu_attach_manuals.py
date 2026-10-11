from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feishu_ledger.client import (  # noqa: E402
    FeishuApiError,
    batch_update_records,
    list_records,
    load_feishu_config,
    upload_bitable_attachment,
)


CONFIRM_TOKEN = "ATTACH-MANUALS"
MANUAL_DOCX_ROOT = ROOT / "data" / "reference" / "manuals" / "导出手册" / "docx"


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach matched fault manuals to Feishu Bitable records")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-attach", default="")
    args = parser.parse_args()

    config = load_feishu_config(ROOT)
    records = list_records(config)
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for record in records:
        fields = record.get("fields") or {}
        if fields.get("手册状态") != "已匹配":
            continue
        manual_path = _manual_docx(fields)
        if not manual_path:
            skipped.append(
                {
                    "序号": str(fields.get("序号", "")),
                    "系统名称": str(fields.get("系统名称", "")),
                    "故障手册": str(fields.get("故障手册", "")),
                    "reason": "manual docx not found",
                }
            )
            continue
        candidates.append({"record_id": record["record_id"], "fields": fields, "path": manual_path})

    plan: dict[str, Any] = {
        "status": "ready",
        "records": len(records),
        "manual_candidates": len(candidates),
        "skipped": skipped,
    }
    if args.apply:
        if args.confirm_attach != CONFIRM_TOKEN:
            plan["status"] = "blocked"
            plan["blocked_reason"] = f"missing --confirm-attach {CONFIRM_TOKEN}"
        else:
            try:
                uploads: dict[Path, dict[str, Any]] = {}
                updates: list[dict[str, Any]] = []
                for item in candidates:
                    path = item["path"]
                    if path not in uploads:
                        uploads[path] = upload_bitable_attachment(path, config)
                    token = uploads[path]["file_token"]
                    updates.append(
                        {
                            "record_id": item["record_id"],
                            "fields": {"故障手册附件": [{"file_token": token}]},
                        }
                    )
                updated = batch_update_records(updates, config) if updates else {"updated_count": 0}
                plan.update(
                    {
                        "status": "attached",
                        "uploaded_files": len(uploads),
                        "updated_count": updated["updated_count"],
                    }
                )
            except FeishuApiError as exc:
                plan["status"] = "failed"
                plan["error"] = str(exc)

    output = ROOT / "outputs" / "ledger" / "feishu_attach_manuals_result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_jsonable(plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": plan["status"], "manual_candidates": len(candidates)}, ensure_ascii=False, indent=2))
    return 0


def _manual_docx(fields: dict[str, Any]) -> Path | None:
    domain = str(fields.get("所属域") or "").strip()
    system = str(fields.get("手册系统") or fields.get("系统名称") or "").strip()
    manual = str(fields.get("故障手册") or fields.get("手册故障模式") or "").strip()
    if not (domain and system and manual):
        return None
    direct = MANUAL_DOCX_ROOT / domain / system / f"{system}-{manual}.docx"
    if direct.exists():
        return direct
    matches = sorted(path for path in MANUAL_DOCX_ROOT.rglob("*.docx") if system.lower() in path.name.lower() and manual.lower() in path.name.lower())
    return matches[0] if matches else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
