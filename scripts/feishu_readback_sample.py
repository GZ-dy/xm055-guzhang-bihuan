from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.feishu_ledger.client import list_records, load_feishu_config  # noqa: E402


def main() -> int:
    records = list_records(load_feishu_config(ROOT))
    rows = []
    for record in records:
        fields = record.get("fields", {})
        seq = str(fields.get("序号", ""))
        if seq in {str(value) for value in range(62, 69)}:
            rows.append(
                {
                    "序号": fields.get("序号", ""),
                    "所属域": fields.get("所属域", ""),
                    "系统名称": fields.get("系统名称", ""),
                    "故障上报情况": fields.get("故障上报情况", ""),
                    "故障发生时间": fields.get("故障发生时间", ""),
                }
            )
    rows.sort(key=lambda item: int(item["序号"] or 0))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
