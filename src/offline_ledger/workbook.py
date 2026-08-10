from __future__ import annotations

import os
import re
import shutil
import tempfile
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


DEFAULT_TEMPLATE = Path("data/templates/ledger/offline_fault_record_2026.xlsx")
DEFAULT_SHEET = "2026年度故障记录"
MASTER_TABLE_PATH = Path("outputs/ledger/master/【2026】多域系统故障记录表.xlsx")

HEADER_FIELD_MAP = {
    "是否已提故障工单/已提复制工单号": "是否已提故障工单号",
    "多域处理人员": "处理人员",
    # 2026-07-11 09:30 新增: 离线表模板表头 → fields 字段名 映射
    # 2026-07-11 14:30 BUGFIX12 修正: 系统名称 与 record_payload["fields"]["系统名称"] 同名, 不再二次映射
    "所属域": "所属域",
}
DATETIME_FIELDS = {
    "故障发生时间",
    "响应时间",
    "故障恢复时间",
    "业务中断开始时间",
    "业务中断结束时间",
    "闭环时间",
    "预计修复时间",
}


def append_or_update_record(
    record_payload: dict[str, Any],
    template_path: Path = DEFAULT_TEMPLATE,
    master_path: Path = MASTER_TABLE_PATH,
    snapshot_path: Path | None = None,
) -> tuple[Path, int]:
    """离线表规则 (2026-07-10 22:32 + 2026-07-11 09:30 用户硬性要求):
    1. 模板永远只读, 运行时禁止修改
    2. 第一次运行: 从模板创建累计主表
    3. 后续 validated: 追加到累计主表
    4. 序号 = max(已有数字序号) + 1 (固定整数, 不是 =ROW()-1)
    5. case_id 唯一键; 同 case_id 重跑更新原行，不同 case_id 永远追加新行
    6. 保存累计主表后, 复制到 outputs/ledger/<case_id>/ 做快照 + 邮件附件
    7. 文件锁 + 临时文件原子替换 (避免并发 Excel 损坏)
    """
    if not template_path.exists():
        raise FileNotFoundError(f"missing offline record template: {template_path}")

    fields = dict(record_payload.get("fields", {}))
    case_id = str(record_payload.get("case_id", "")).strip()
    if not case_id:
        raise ValueError("record_payload.case_id is required")

    # 8. 文件锁
    master_path.parent.mkdir(parents=True, exist_ok=True)
    lock_dir = Path(tempfile.gettempdir()) / "fault_closed_loop_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{master_path.name}.lock"
    with open(lock_path, "w") as lock_fh:
        _lock_file(lock_fh)
        try:
            # 2. 第一次: 从模板创建
            if not master_path.exists():
                shutil.copy2(template_path, master_path)

            workbook = load_workbook(master_path)
            sheet = workbook[_sheet_name(workbook.sheetnames, fields)]
            case_id_col = _ensure_case_id_column(sheet)

            # 5. 只有同 case_id 重跑才更新；新的 case_id 必须追加到末尾。
            existing_row = _find_row_by_case_id(sheet, case_id_col, case_id)

            if existing_row:
                # 更新原行
                target_row = existing_row
            else:
                # 3. 紧接最后一条真实业务记录追加；忽略只有格式的空白行。
                target_row = _next_append_row(sheet)
                _copy_row_style(sheet, max(2, target_row - 1), target_row)

            # 4. 序号 = max(已有数字序号) + 1
            serial_no = _next_serial_no(sheet)
            source_row = max(2, target_row - 1) if existing_row else max(2, target_row - 1)

            # 写入 case_id (隐藏列)
            sheet.cell(target_row, case_id_col).value = case_id

            # 写入 fields
            headers = {
                column: _normalize_header(sheet.cell(1, column).value)
                for column in range(1, sheet.max_column + 1)
            }
            for column, header in headers.items():
                if not header or header == "序号" or header == "case_id":
                    continue
                field_name = HEADER_FIELD_MAP.get(header, header)
                if field_name not in fields:
                    continue
                sheet.cell(target_row, column).value = _cell_value(field_name, fields[field_name])

            # 写序号 (固定整数)
            sheet.cell(target_row, 1).value = serial_no

            _repair_written_cell_styles(sheet, source_row, target_row)
            _materialize_serial_numbers(sheet)

            # 7. 原子替换: 临时文件 + rename
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(master_path.parent), prefix=".master.", suffix=".xlsx.tmp"
            )
            os.close(tmp_fd)
            try:
                workbook.save(tmp_path)
                workbook.close()
                os.replace(tmp_path, master_path)  # 原子替换
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            # 7. 复制到 snapshot
            if snapshot_path:
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(master_path, snapshot_path)

            return master_path, serial_no
        finally:
            _unlock_file(lock_fh)


def _lock_file(lock_fh: Any) -> None:
    if os.name == "nt":
        import msvcrt

        lock_fh.write("\0")
        lock_fh.flush()
        lock_fh.seek(0)
        msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)


def _unlock_file(lock_fh: Any) -> None:
    if os.name == "nt":
        import msvcrt

        lock_fh.seek(0)
        msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


# === 保留旧接口 (兼容现有调用方) ===
def write_offline_record_workbook(
    template_path: Path,
    output_path: Path,
    record_payload: dict[str, Any],
    live: bool = False,
    master_path: Path | None = None,
) -> Path:
    """从原模板保留历史记录和格式，追加当前案例；仅 live 同步累计主表。"""
    if live:
        try:
            append_or_update_record(
                record_payload,
                template_path,
                master_path or MASTER_TABLE_PATH,
                snapshot_path=output_path,
            )
            return output_path
        except Exception as e:
            import logging
            logging.warning("master 离线表同步失败 (不影响 output): %s", e)
    append_or_update_record(record_payload, template_path, output_path)
    return output_path


def _sheet_name(sheet_names: list[str], fields: dict[str, Any]) -> str:
    occurred_at = str(fields.get("故障发生时间") or "")
    year = occurred_at[:4] if len(occurred_at) >= 4 and occurred_at[:4].isdigit() else ""
    yearly = f"{year}年度故障记录" if year else ""
    if yearly in sheet_names:
        return yearly
    if DEFAULT_SHEET in sheet_names:
        return DEFAULT_SHEET
    return sheet_names[0]


def _ensure_case_id_column(sheet: Any) -> int:
    """确保 sheet 有一个 case_id 隐藏列(追加到最右)."""
    headers = {
        column: _normalize_header(sheet.cell(1, column).value)
        for column in range(1, sheet.max_column + 1)
    }
    for column, header in headers.items():
        if header == "case_id":
            return column
    # 追加 case_id 列
    new_col = sheet.max_column + 1
    cell = sheet.cell(1, new_col)
    cell.value = "case_id"
    sheet.column_dimensions[cell.column_letter].hidden = True
    return new_col


def _find_row_by_case_id(sheet: Any, case_id_col: int, case_id: str) -> int | None:
    for row in range(2, sheet.max_row + 1):
        if str(sheet.cell(row, case_id_col).value or "").strip() == case_id:
            return row
    return None


def _next_serial_no(sheet: Any) -> int:
    return sum(_row_has_business_data(sheet, row) for row in range(2, sheet.max_row + 1)) + 1


def _materialize_serial_numbers(sheet: Any) -> None:
    serial_no = 0
    for row in range(2, sheet.max_row + 1):
        if _row_has_business_data(sheet, row):
            serial_no += 1
            sheet.cell(row, 1).value = serial_no
        elif isinstance(sheet.cell(row, 1).value, str) and sheet.cell(row, 1).value.startswith("=ROW("):
            sheet.cell(row, 1).value = None


def _next_append_row(sheet: Any) -> int:
    for row in range(sheet.max_row, 1, -1):
        if _row_has_business_data(sheet, row):
            return row + 1
    return 2


def _row_has_business_data(sheet: Any, row: int) -> bool:
    return any(
        sheet.cell(row, column).value not in (None, "")
        for column in range(2, min(sheet.max_column, 26) + 1)
    )


def _copy_row_style(sheet: Any, source_row: int, target_row: int) -> None:
    if source_row == target_row:
        return
    sheet.row_dimensions[target_row].height = sheet.row_dimensions[source_row].height
    for column in range(1, sheet.max_column + 1):
        source = sheet.cell(source_row, column)
        target = sheet.cell(target_row, column)
        _copy_cell_style(source, target)


def _repair_written_cell_styles(sheet: Any, source_row: int, target_row: int) -> None:
    fallback = None
    for column in range(1, sheet.max_column + 1):
        source = sheet.cell(source_row, column)
        if _has_visible_border(source):
            fallback = source
        target = sheet.cell(target_row, column)
        if target.value in (None, "") or _has_visible_border(target) or fallback is None:
            continue
        _copy_cell_style(fallback, target)


def _copy_cell_style(source: Any, target: Any) -> None:
    target._style = copy(source._style)
    target.font = copy(source.font)
    target.fill = copy(source.fill)
    target.border = copy(source.border)
    target.number_format = source.number_format
    target.alignment = copy(source.alignment)
    target.protection = copy(source.protection)


def _has_visible_border(cell: Any) -> bool:
    border = cell.border
    return any((border.left.style, border.right.style, border.top.style, border.bottom.style))


def _cell_value(field_name: str, value: Any) -> Any:
    if isinstance(value, dict):
        value = value.get("link") or value.get("text") or ""
    if field_name in DATETIME_FIELDS:
        return _datetime_value(value)
    return "" if value is None else value


def _datetime_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return value


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
