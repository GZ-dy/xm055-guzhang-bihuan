from __future__ import annotations

import json
import mimetypes
import uuid
from http.client import IncompleteRead
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


API_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_CONFIG = Path("config/private/feishu.json")


class FeishuConfigError(RuntimeError):
    pass


class FeishuApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    app_token: str
    table_id: str
    view_id: str = ""
    table_url: str = ""
    field_map: dict[str, str] = field(default_factory=dict)


def load_feishu_config(root: Path, config_path: Path | None = None) -> FeishuConfig:
    path = config_path or root / DEFAULT_CONFIG
    if not path.exists():
        raise FeishuConfigError(f"missing Feishu config: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    required = ("app_id", "app_secret", "app_token", "table_id")
    missing = [key for key in required if not str(payload.get(key, "")).strip()]
    if missing:
        raise FeishuConfigError(f"missing Feishu config fields: {', '.join(missing)}")
    return FeishuConfig(
        app_id=str(payload["app_id"]).strip(),
        app_secret=str(payload["app_secret"]).strip(),
        app_token=str(payload["app_token"]).strip(),
        table_id=str(payload["table_id"]).strip(),
        view_id=str(payload.get("view_id", "")).strip(),
        table_url=str(payload.get("table_url", "")).strip(),
        field_map={str(k): str(v) for k, v in dict(payload.get("field_map", {})).items() if str(v).strip()},
    )


def create_record(record_payload: dict[str, Any], config: FeishuConfig) -> dict[str, Any]:
    token = _tenant_access_token(config)
    field_items = list_fields(config, token)
    fields, skipped = _prepare_fields(record_payload.get("fields", {}), field_items, config.field_map)
    case_id_target = config.field_map.get("case_id", "case_id")
    if case_id_target not in fields:
        return {
            "api_called": False,
            "write_mode": "live",
            "status": "失败",
            "blocked_reason": (
                f"required case_id field {case_id_target!r} is missing or incompatible; "
                "configure a dedicated writable text field before creating records"
            ),
            "skipped_fields": skipped,
            "available_fields": [_field_summary(item) for item in field_items],
        }
    if not fields:
        return {
            "api_called": False,
            "write_mode": "live",
            "status": "失败",
            "blocked_reason": "no payload fields matched Feishu table fields",
            "skipped_fields": skipped,
            "available_fields": [_field_summary(item) for item in field_items],
        }

    data = _request_json(
        "POST",
        f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records",
        token=token,
        payload={"fields": fields},
    )
    record = data.get("data", {}).get("record", {})
    record_id = record.get("record_id", "")
    readback = get_record(config, record_id, token) if record_id else {}
    readback_fields = readback.get("fields", {})
    return {
        "api_called": True,
        "write_mode": "live",
        "status": "已写入",
        "record_id": record_id,
        "record_url": record_url(config, record_id),
        "fields": fields,
        "skipped_fields": skipped,
        "readback": {
            "checked": bool(readback_fields),
            "missing_fields": [name for name in fields if readback_fields.get(name) in (None, "")],
            "fields": readback_fields,
        },
        "available_fields": [_field_summary(item) for item in field_items],
        "raw_response": data.get("data", {}),
    }


def update_record(record_id: str, record_payload: dict[str, Any], config: FeishuConfig) -> dict[str, Any]:
    if not record_id:
        raise FeishuConfigError("missing Feishu record id")
    token = _tenant_access_token(config)
    field_items = list_fields(config, token)
    fields, skipped = _prepare_fields(record_payload.get("fields", {}), field_items, config.field_map)
    if not fields:
        return {
            "api_called": False,
            "write_mode": "live",
            "status": "失败",
            "blocked_reason": "no payload fields matched Feishu table fields",
            "record_id": record_id,
            "skipped_fields": skipped,
            "available_fields": [_field_summary(item) for item in field_items],
        }
    data = _request_json(
        "PUT",
        f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records/{parse.quote(record_id)}",
        token=token,
        payload={"fields": fields},
    )
    readback = get_record(config, record_id, token)
    readback_fields = readback.get("fields", {})
    return {
        "api_called": True,
        "write_mode": "live",
        "status": "已更新",
        "record_id": record_id,
        "record_url": record_url(config, record_id),
        "fields": fields,
        "skipped_fields": skipped,
        "readback": {
            "checked": bool(readback_fields),
            "missing_fields": [name for name in fields if readback_fields.get(name) in (None, "")],
            "fields": readback_fields,
        },
        "available_fields": [_field_summary(item) for item in field_items],
        "raw_response": data.get("data", {}),
    }


def delete_record(record_id: str, config: FeishuConfig) -> dict[str, Any]:
    """
    2026-07-11 09:55 新增: 删除飞书记录 (用户硬性要求: 邮件发送失败时, 删除飞书表记录)

    DELETE /bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}
    """
    if not record_id:
        raise FeishuConfigError("missing Feishu record id")
    token = _tenant_access_token(config)
    data = _request_json(
        "DELETE",
        f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records/{parse.quote(record_id)}",
        token=token,
    )
    return {
        "api_called": True,
        "write_mode": "live",
        "status": "已删除",
        "record_id": record_id,
        "raw_response": data.get("data", {}),
    }


def get_record(config: FeishuConfig, record_id: str, token: str | None = None) -> dict[str, Any]:
    if not record_id:
        return {}
    access_token = token or _tenant_access_token(config)
    data = _request_json(
        "GET",
        f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records/{parse.quote(record_id)}",
        token=access_token,
    )
    return data.get("data", {}).get("record", {})


def list_records(config: FeishuConfig, token: str | None = None) -> list[dict[str, Any]]:
    access_token = token or _tenant_access_token(config)
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query = {"page_size": "50"}
        if config.view_id:
            query["view_id"] = config.view_id
        if page_token:
            query["page_token"] = page_token
        data = _request_json(
            "GET",
            f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records",
            token=access_token,
            query=query,
        )
        page = data.get("data", {})
        records.extend(page.get("items", []))
        if not page.get("has_more"):
            return records
        page_token = str(page.get("page_token", ""))
        if not page_token:
            return records


def find_record_by_system_time(
    system_name: str,
    occurred_at: int | str,
    config: FeishuConfig,
    token: str | None = None,
) -> dict[str, Any]:
    """按 (系统名称, 故障发生时间) 查找已有飞书记录 (2026-07-11 12:00 BUGFIX12 新增)

    用户要求: 同一系统同一时间应该覆盖掉之前的. 如果已存在同系统同时
    间的记录, 返回该 record_id, 调用方用 update_record 覆盖.
    多条同时匹配: 选最早创建的那条 (作为被覆盖对象).
    """
    from datetime import datetime
    if not system_name or occurred_at is None:
        return {}
    sys_name = str(system_name).strip()
    if not sys_name:
        return {}
    # occurred_at 可能是 timestamp (ms) 或字符串
    target_ts = None
    if isinstance(occurred_at, (int, float)):
        target_ts = int(occurred_at)
    else:
        # 尝试解析字符串
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(str(occurred_at), fmt)
                target_ts = int(dt.timestamp() * 1000)
                break
            except (ValueError, TypeError):
                continue
    if target_ts is None:
        return {}
    records = list_records(config, token)
    matches = []
    for record in records:
        fields = record.get("fields") or {}
        rec_sys = (fields.get("系统名称") or fields.get("系统") or "")
        if str(rec_sys).strip() != sys_name:
            continue
        rec_tm = fields.get("故障发生时间") or fields.get("时间")
        if rec_tm is None:
            continue
        # 飞书时间字段可能是 int(ms) 或 string
        rec_ts = None
        if isinstance(rec_tm, (int, float)):
            rec_ts = int(rec_tm)
        else:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(str(rec_tm), fmt)
                    rec_ts = int(dt.timestamp() * 1000)
                    break
                except (ValueError, TypeError):
                    continue
        if rec_ts is None:
            continue
        # 1 分钟内视为同一时间 (允许小误差)
        if abs(rec_ts - target_ts) < 60000:
            matches.append(record)
    if not matches:
        return {}
    if len(matches) > 1:
        return {
            "record_id": "",
            "fields": {},
            "record_url": "",
            "blocked_reason": (
                f"system={sys_name!r}, occurred_at={target_ts!r} matched "
                f"{len(matches)} Feishu rows; dedupe ambiguous, refusing to auto-resolve."
            ),
        }
    # 唯一匹配时才允许更新。
    matches.sort(key=lambda r: int(r.get("created_time") or r.get("last_modified_time") or 0))
    first = matches[0]
    record_id = str(first.get("record_id") or "")
    return {
        "record_id": record_id,
        "fields": first.get("fields") or {},
        "record_url": record_url(config, record_id) if record_id else "",
    }


def find_record_by_case_id(
    case_id: str,
    config: FeishuConfig,
    token: str | None = None,
) -> dict[str, Any]:
    """Look up an existing Feishu record whose case_id field equals case_id.

    Used by the V1 dedupe check: when ``run_closed_loop.py`` is invoked without
    ``--feishu-record-id`` but the same case_id already has a row in the table,
    we should ``update_record`` instead of creating a duplicate.

    Returns:
        dict with ``record_id``, ``fields`` and ``record_url`` when matched,
        or ``{}`` when no row exists or case_id is empty. Multiple matches are
        treated as a dedupe miss (returns ``{}`` with ``blocked_reason`` set) —
        callers should surface the ambiguity to the operator rather than
        silently picking one.
    """
    if not case_id or not case_id.strip():
        return {}
    target = case_id.strip()
    case_id_field = config.field_map.get("case_id", "case_id")
    records = list_records(config, token)
    matches: list[dict[str, Any]] = []
    for record in records:
        fields = record.get("fields") or {}
        record_case_id = fields.get(case_id_field)
        record_case_id_text = str(record_case_id).strip() if record_case_id is not None else ""
        if record_case_id_text == target:
            matches.append(record)
    if not matches:
        return {}
    if len(matches) > 1:
        return {
            "record_id": "",
            "fields": {},
            "record_url": "",
            "blocked_reason": (
                f"case_id={target!r} matched {len(matches)} Feishu rows; "
                "dedupe ambiguous, refusing to auto-resolve."
            ),
        }
    first = matches[0]
    record_id = str(first.get("record_id") or "")
    return {
        "record_id": record_id,
        "fields": first.get("fields") or {},
        "record_url": record_url(config, record_id) if record_id else "",
    }


def batch_create_records(records: list[dict[str, Any]], config: FeishuConfig, token: str | None = None) -> dict[str, Any]:
    access_token = token or _tenant_access_token(config)
    created: list[dict[str, Any]] = []
    for chunk in _chunks(records, 100):
        data = _request_json(
            "POST",
            f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records/batch_create",
            token=access_token,
            payload={"records": chunk},
        )
        created.extend(data.get("data", {}).get("records", []))
    return {"created_count": len(created), "records": created}


def batch_update_records(records: list[dict[str, Any]], config: FeishuConfig, token: str | None = None) -> dict[str, Any]:
    access_token = token or _tenant_access_token(config)
    updated: list[dict[str, Any]] = []
    for chunk in _chunks(records, 100):
        data = _request_json(
            "POST",
            f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records/batch_update",
            token=access_token,
            payload={"records": chunk},
        )
        updated.extend(data.get("data", {}).get("records", []))
    return {"updated_count": len(updated), "records": updated}


def batch_delete_records(record_ids: list[str], config: FeishuConfig, token: str | None = None) -> dict[str, Any]:
    access_token = token or _tenant_access_token(config)
    deleted = 0
    for chunk in _chunks(record_ids, 100):
        _request_json(
            "POST",
            f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/records/batch_delete",
            token=access_token,
            payload={"records": chunk},
        )
        deleted += len(chunk)
    return {"deleted_count": deleted}


def upload_bitable_attachment(path: Path, config: FeishuConfig, token: str | None = None) -> dict[str, Any]:
    if not path.exists():
        raise FeishuConfigError(f"missing attachment: {path}")
    access_token = token or _tenant_access_token(config)
    payload = _multipart_body(
        {
            "file_name": path.name,
            "parent_type": "bitable_file",
            "parent_node": config.app_token,
            "size": str(path.stat().st_size),
        },
        "file",
        path,
    )
    data = _request_multipart(
        "POST",
        f"{API_BASE}/drive/v1/medias/upload_all",
        token=access_token,
        body=payload["body"],
        content_type=payload["content_type"],
    )
    file_token = str(data.get("data", {}).get("file_token", ""))
    if not file_token:
        raise FeishuApiError("Feishu did not return file_token")
    return {"file_token": file_token, "name": path.name}


def prepare_fields_for_feishu(
    payload_fields: dict[str, Any],
    field_items: list[dict[str, Any]],
    field_map: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    return _prepare_fields(payload_fields, field_items, field_map or {})


def list_fields(config: FeishuConfig, token: str | None = None) -> list[dict[str, Any]]:
    access_token = token or _tenant_access_token(config)
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query = {"page_size": "100"}
        if page_token:
            query["page_token"] = page_token
        data = _request_json(
            "GET",
            f"{API_BASE}/bitable/v1/apps/{parse.quote(config.app_token)}/tables/{parse.quote(config.table_id)}/fields",
            token=access_token,
            query=query,
        )
        page = data.get("data", {})
        items.extend(page.get("items", []))
        if not page.get("has_more"):
            return items
        page_token = str(page.get("page_token", ""))
        if not page_token:
            return items


def field_discovery(config: FeishuConfig) -> dict[str, Any]:
    token = _tenant_access_token(config)
    fields = list_fields(config, token)
    return {
        "api_called": True,
        "write_mode": "read_only",
        "status": "已读取",
        "app_token": config.app_token,
        "table_id": config.table_id,
        "view_id": config.view_id,
        "field_count": len(fields),
        "fields": [_field_summary(item) for item in fields],
    }


def record_url(config: FeishuConfig, record_id: str) -> str:
    if not record_id:
        return config.table_url
    if config.table_url:
        separator = "&" if "?" in config.table_url else "?"
        return f"{config.table_url}{separator}record={parse.quote(record_id)}"
    query = {"table": config.table_id}
    if config.view_id:
        query["view"] = config.view_id
    query["record"] = record_id
    return f"https://open.feishu.cn/base/{config.app_token}?{parse.urlencode(query)}"


def _tenant_access_token(config: FeishuConfig) -> str:
    data = _request_json(
        "POST",
        f"{API_BASE}/auth/v3/tenant_access_token/internal",
        payload={"app_id": config.app_id, "app_secret": config.app_secret},
    )
    token = str(data.get("tenant_access_token", ""))
    if not token:
        raise FeishuApiError("Feishu did not return tenant_access_token")
    return token


def _prepare_fields(
    payload_fields: dict[str, Any],
    field_items: list[dict[str, Any]],
    field_map: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    fields_by_name = {str(item.get("field_name", "")): item for item in field_items}
    result: dict[str, Any] = {}
    skipped: list[dict[str, str]] = []
    for source_name, raw_value in payload_fields.items():
        target_name = field_map.get(source_name, source_name)
        meta = fields_by_name.get(target_name)
        if meta is None:
            skipped.append({"field": source_name, "target": target_name, "reason": "missing target field"})
            continue
        value = _coerce_value(raw_value, meta)
        if value == "":
            reason = "empty value" if raw_value in (None, "") else "incompatible field type/value"
            skipped.append({"field": source_name, "target": target_name, "reason": reason})
            continue
        result[target_name] = value
    return result, skipped


def _coerce_value(value: Any, field_meta: dict[str, Any]) -> Any:
    if value is None:
        return ""
    if isinstance(value, dict) and value.get("link"):
        if field_meta.get("type") == 15 or str(field_meta.get("ui_type", "")).lower() in {"url", "link"}:
            return {"text": str(value.get("text") or value["link"]), "link": str(value["link"])}
        return str(value.get("link") or "")
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000) if field_meta.get("type") == 5 else value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day).timestamp() * 1000) if field_meta.get("type") == 5 else value.isoformat()
    text = str(value).strip() if not isinstance(value, (int, float, bool)) else value
    field_type = field_meta.get("type")
    if field_type == 1:
        return str(value).strip()
    if field_type == 2:
        if isinstance(value, (int, float)):
            return value
        try:
            return float(str(value).strip())
        except ValueError:
            return ""
    if field_type == 3:
        return str(text).strip()
    if field_type == 5 and isinstance(text, str):
        return _datetime_ms(text)
    if field_type == 15:
        if isinstance(value, dict):
            link = str(value.get("link", "")).strip()
            label = str(value.get("text", "") or link).strip()
            return {"link": link, "text": label} if link else ""
        link = str(value).strip()
        return {"link": link, "text": link} if link else ""
    if field_type == 17:
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and value.get("file_token"):
            return [value]
        return ""
    return text


def _datetime_ms(value: str) -> int | str:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(value, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return ""


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _field_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "field_id": item.get("field_id", ""),
        "field_name": item.get("field_name", ""),
        "type": item.get("type", ""),
        "property": item.get("property", {}),
    }


def _multipart_body(fields: dict[str, str], file_field: str, path: Path) -> dict[str, Any]:
    boundary = f"----codex{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return {"body": b"".join(chunks), "content_type": f"multipart/form-data; boundary={boundary}"}


def _request_multipart(
    method: str,
    url: str,
    *,
    token: str,
    body: bytes,
    content_type: str,
) -> dict[str, Any]:
    headers = {"Content-Type": content_type, "Accept-Encoding": "identity", "Authorization": f"Bearer {token}"}
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FeishuApiError(f"Feishu HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise FeishuApiError(f"Feishu request failed: {exc.reason}") from exc
    code = data.get("code")
    if code not in (None, 0):
        raise FeishuApiError(f"Feishu API error {code}: {data.get('msg', '')}")
    return data


def _request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=body, method=method, headers=headers)
    for attempt in range(2):
        try:
            with request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except IncompleteRead as exc:
            if attempt == 1:
                raise FeishuApiError(f"Feishu response incomplete: {len(exc.partial)} bytes read") from exc
        except TimeoutError as exc:
            if attempt == 1:
                raise FeishuApiError("Feishu request timed out") from exc
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise FeishuApiError(f"Feishu HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            if attempt == 0:
                continue
            raise FeishuApiError(f"Feishu request failed: {exc.reason}") from exc
    else:
        raise FeishuApiError("Feishu request failed")

    code = data.get("code")
    if code not in (None, 0):
        raise FeishuApiError(f"Feishu API error {code}: {data.get('msg', '')}")
    return data
