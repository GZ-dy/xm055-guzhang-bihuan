from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.feishu_ledger.client import (
    FeishuApiError,
    FeishuConfig,
    FeishuConfigError,
    create_record,
    find_record_by_case_id,
    find_record_by_system_time,  # 2026-07-11 12:00 BUGFIX12: 同系统同时查重
    get_record,
    list_fields,
    list_records,
    load_feishu_config,
    prepare_fields_for_feishu,
    record_url as feishu_record_url,
    update_record,
    upload_bitable_attachment,
)
from src.feishu_ledger.preview import build_record_payload
from src.field_contracts.contract import FACT_SAFETY_VERSION, validate_context
from src.input_collection.ocr import extract_screenshot_texts
from src.mail_sender.preview import build_mail_preview
from src.mail_sender.smtp_sender import MAIL_SENT, SmtpOptions, send_mail
from src.manuals_review.preview import build_manual_result
from src.offline_ledger.workbook import DEFAULT_TEMPLATE as OFFLINE_RECORD_TEMPLATE
from src.offline_ledger.workbook import MASTER_TABLE_PATH as OFFLINE_MASTER_TABLE_PATH
from src.offline_ledger.workbook import write_offline_record_workbook
from src.report_generation.docx_writer import write_report_docx


SCHEMA_VERSION = "1.0"
DEFAULT_ONLINE_RECORD_URL = "https://scnd6sq56g0o.feishu.cn/base/FaDPb5vM7atBvRslf1Rc2Yw7nUg?table=tbl2F6czmEWi5YnY&view=vewcSTmbiE"
DEFAULT_MAIL_TO = ("gydyjzhwhz@gd.chinamobile.com",)
ONLINE_WRITE_REQUIRED_FIELDS = ("case_id", "故障报告", "故障报告附件", "资料状态", "资料完整度", "看板优先级", "手册状态")
# 2026-07-11 08:35 修改: 不再因故障手册/故障手册附件缺失而阻塞邮件发送
# 用户硬性要求: "@故障信息一定要发邮件", 手册缺失写"待补充" (在在线表/邮件中)
PRE_MAIL_REQUIRED_FIELDS = ONLINE_WRITE_REQUIRED_FIELDS  # 2026-07-11 08:35 去掉 故障手册/故障手册附件
MANUAL_LINK_FIELDS = ("故障手册", "故障手册附件")
MAIL_STATUS_FIELD = "邮件闭环状态"


@dataclass(frozen=True)
class RunOptions:
    text: str = ""
    input_file: Path | None = None
    incident_context: Path | None = None
    case_id: str = ""
    screenshots: list[Path] = field(default_factory=list)
    logs: list[Path] = field(default_factory=list)
    attachments: list[Path] = field(default_factory=list)
    incident_context_only: bool = False
    feishu_dry_run: bool = False
    apply: bool = False
    confirm_live_write: str = ""
    feishu_record_id: str = ""
    feishu_config: Path | None = None
    mail_dry_run: bool = False
    manual_dry_run: bool = False
    all_dry_run: bool = False
    online_record_url: str = ""
    send_mail: bool = False
    mail_to: list[str] = field(default_factory=list)
    mail_cc: list[str] = field(default_factory=list)  # BUGFIX36 (2026-07-13): 项目小组成员抄送
    mail_from: str = ""
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password_env: str = "FAULT_SMTP_PASSWORD"
    automatic: bool = False
    source_event: dict[str, Any] = field(default_factory=dict)
    source_messages: list[dict[str, Any]] = field(default_factory=list)


def run(root: Path, options: RunOptions) -> dict[str, Any]:
    context = _load_or_build_context(root, options)
    _ensure_runtime_meta(context, options)
    case_id = context["meta"]["case_id"]
    paths = _paths(root, case_id)
    _reset_outputs(paths)

    # BUGFIX38 (2026-07-13 13:00): 先按 contacts.json 自动填 handling.owner/group
    # (必须在 validate_context + report write 之前填, 否则报告用旧 owner)
    _contacts: dict[str, Any] = {}
    _sys_name = ""
    try:
        _contacts = _load_contacts_for_cc(root)
        if context.get("incident"):
            _sys_name = str(context["incident"].get("system_name") or "").strip()
        _owner_auto, _group_auto, _owner_info = _lookup_contacts_owner(_contacts, _sys_name)
        _handling = context.setdefault("handling", {})
        _u_owner = str(_handling.get("owner") or "").strip()
        _u_group = str(_handling.get("handling_group") or "").strip()
        _DEFAULT_HANDLER_NAMES = ("", "多域维护组", "基础平台组")
        def _is_default_handler(n):
            n = (n or "").strip()
            return not n or any(kw and (kw == n or kw in n) for kw in _DEFAULT_HANDLER_NAMES if kw)
        _auto_filled = False
        # BUGFIX39 (2026-07-13 13:45): 处理人 = 整个小组成员
        # 优先级: contacts 查出的多真名 (含 "、") > 用户填的 1 个人名
        # 原因: 用户填的 1 个人名只能算 A 角, 不算"明确指定整个小组"
        # 只有当 contacts 没结果 (返单名或空) 时, 才保留用户填值
        if _owner_auto and _owner_auto != _u_owner:
            _auto_has_multi = "、" in _owner_auto  # 多真名 = BUGFIX39 标志
            _u_is_default = _is_default_handler(_u_owner)
            if _auto_has_multi or _u_is_default:
                _handling["owner"] = _owner_auto
                _auto_filled = True
        if _is_default_handler(_u_group) and _group_auto:
            _handling["handling_group"] = _group_auto
        # BUGFIX41 (2026-07-13 14:30, 用户原话 "处理组要记得都是多域维护组"):
        # 报告/邮件/incident_context.handling_group **统一"多域维护组"**
        # (跟具体系统的小组解耦, 报告/邮件正文都硬编码"多域维护组")
        # 飞书表"处理人员" / 离线表"多域处理人员" 仍写 3 人真名
        # 覆盖规则: 任何时候 (不管用户填什么/contacts 查到什么) 都改成 "多域维护组"
        _handling["handling_group"] = "多域维护组"
        if _owner_info:
            context.setdefault("meta", {})["owner_match"] = _owner_info
            if _auto_filled:
                context["meta"]["owner_auto_filled"] = True
            # BUGFIX39: 标记 contacts 提供了多少真名 (1/2/3)
            context["meta"]["owner_count"] = _owner_auto.count("、") + 1 if _owner_auto else 0
    except Exception:
        pass  # BUGFIX38 失败不影响主流程

    # BUGFIX38 (2026-07-13 13:00): 修改后再次落盘 incident_context.json
    # (因为 _write_json 在 line 99 调用, 早于 BUGFIX38)
    try:
        _ctx_path = paths["case"] / "incident_context.json"
        with open(_ctx_path, "w", encoding="utf-8") as _cf:
            json.dump(context, _cf, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass

    validation = validate_context(context, require_report=False)
    if options.incident_context_only or validation["status"] != "validated":
        return _write_stopped_result(paths, case_id, context, validation)

    template_path = _clean_report_template(root)
    report_path = write_report_docx(context, paths["report"] / _report_filename(context), template_path)

    validation = validate_context(context, report_path)
    context["meta"]["status"] = validation["status"]
    context.setdefault("evidence", {})["missing_fields"] = validation["missing_fields"]
    context.setdefault("evidence", {})["missing_field_prompts"] = validation.get("missing_field_prompts", [])
    _write_json(paths["case"] / "incident_context.json", context)
    _write_json(paths["validation"] / "validation_result.json", validation)

    outputs: dict[str, str] = {
        "incident_context": str(paths["case"] / "incident_context.json"),
        "report_docx": str(report_path),
        "validation_result": str(paths["validation"] / "validation_result.json"),
    }
    if validation["status"] != "validated":
        return {
            "case_id": case_id,
            "status": validation["status"],
            "outputs": outputs,
            "missing_fields": validation["missing_fields"],
            "missing_field_prompts": validation.get("missing_field_prompts", []),
            "errors": validation["errors"],
        }

    want_all = options.all_dry_run
    live_requested = _live_requested(options)
    manual_result = (
        build_manual_result(context, root / "data" / "reference" / "manuals_index" / "manual_index.json")
        if want_all or options.manual_dry_run or options.feishu_dry_run or live_requested
        else None
    )
    online_record_url = options.online_record_url or DEFAULT_ONLINE_RECORD_URL
    # 2026-07-11 09:00 修改: 用户硬性要求"故障报告在线表必须存在"
    if not online_record_url:
        raise ValueError(
            "故障报告在线表必须存在! 请设置 --online-record-url 或配置 DEFAULT_ONLINE_RECORD_URL"
        )
    initial_mail_status = "待发送" if want_all or options.mail_dry_run or options.send_mail else "不适用"
    write_online_payload = want_all or options.feishu_dry_run or live_requested
    record_payload = build_record_payload(
        context,
        report_path,
        mail_status=initial_mail_status,
        manual_status=(manual_result or {}).get("status", "待补充"),
        manual_result=manual_result,
    )
    if not options.feishu_record_id:
        _fill_handler_from_previous_system(root, options, context, record_payload)
    live_gate_error = _live_gate_error(options)
    feishu_target: dict[str, Any] | None = None
    if options.apply and write_online_payload and not live_gate_error:
        feishu_target = _resolve_feishu_target(root, options, record_payload)
    # Always generate the case snapshot first.  The cumulative master is a
    # second-phase commit and must wait for a successful Feishu write/readback.
    offline_record_path = _write_offline_record_if_needed(
        root,
        paths,
        context,
        record_payload,
        options,
        commit_master=False,
    )
    if offline_record_path is not None:
        outputs["offline_record_workbook"] = str(offline_record_path)
    existing_record_plan = (
        None
        if feishu_target is not None and feishu_target.get("status") != "ready"
        else _existing_record_backfill_plan(root, options, record_payload)
    )

    execution_plan = _execution_plan(
        options,
        record_payload if write_online_payload else None,
        manual_result,
        report_path,
        offline_record_path,
        existing_record_plan,
    )
    if execution_plan is not None:
        outputs["execution_plan"] = str(_write_json(paths["ledger"] / "execution_plan.json", execution_plan))
    if live_gate_error:
        if write_online_payload:
            outputs["record_payload"] = str(_write_json(paths["ledger"] / "record_payload.json", record_payload))
        if manual_result is not None:
            outputs["manual_match_result"] = str(_write_json(paths["manuals"] / "manual_match_result.json", manual_result))
        if options.send_mail:
            outputs["pre_mail_check"] = str(
                _write_json(paths["mail"] / "pre_mail_check.json", {"status": "blocked", "blocked_reason": live_gate_error})
            )
        return {
            "case_id": case_id,
            "status": "need_review",
            "outputs": outputs,
            "missing_fields": validation["missing_fields"],
            "missing_field_prompts": validation.get("missing_field_prompts", []),
            "errors": validation["errors"] + [live_gate_error],
        }
    runtime_errors: list[str] = []
    feishu_write_error = ""
    feishu_result: dict[str, Any] | None = None
    if options.apply and write_online_payload:
        feishu_target = feishu_target or _resolve_feishu_target(root, options, record_payload)
        attachment_result = (
            _prepare_feishu_attachments(root, options, record_payload, report_path, manual_result, feishu_target)
            if feishu_target.get("status") == "ready"
            else {
                "status": "failed",
                "uploads": [],
                "blocked_reason": feishu_target.get("blocked_reason", "Feishu dedupe preflight failed"),
            }
        )
        outputs["attachment_upload_result"] = str(_write_json(paths["ledger"] / "attachment_upload_result.json", attachment_result))
        if attachment_result.get("status") != "ready":
            feishu_write_error = str(attachment_result.get("blocked_reason") or "Feishu attachment upload failed")
            runtime_errors.append(feishu_write_error)
            outputs["record_payload"] = str(
                _write_json(
                    paths["ledger"] / "record_payload.json",
                    {
                        "api_called": False,
                        "write_mode": "live",
                        "status": "失败",
                        "blocked_reason": feishu_write_error,
                        "fields": record_payload.get("fields", {}),
                        "case_id_dedup": {
                            "case_id": record_payload.get("case_id", ""),
                            "matched_existing_record": False,
                            "explicit_record_id": bool(options.feishu_record_id),
                            "ambiguous": "dedupe ambiguous" in feishu_write_error,
                            "blocked_reason": feishu_write_error,
                        },
                    },
                )
            )
            if manual_result is not None:
                outputs["manual_match_result"] = str(_write_json(paths["manuals"] / "manual_match_result.json", manual_result))
            return {
                "case_id": case_id,
                "status": "need_review",
                "outputs": outputs,
                "missing_fields": validation["missing_fields"],
                "missing_field_prompts": validation.get("missing_field_prompts", []),
                "errors": validation["errors"] + runtime_errors,
            }
        else:
            effective_options = options
            effective_existing_plan = existing_record_plan
            if feishu_target.get("record_id"):
                effective_options = replace(options, feishu_record_id=str(feishu_target["record_id"]))
                existing_fields = feishu_target.get("existing_fields") or {}
                missing_fields = _missing_existing_record_fields(
                    existing_fields,
                    record_payload,
                    getattr(feishu_target.get("config"), "field_map", {}),
                )
                effective_existing_plan = {
                    "status": "ready" if missing_fields else "complete",
                    "record_id": str(feishu_target["record_id"]),
                    "missing_fields": missing_fields,
                    "blocked_reason": "",
                }
            execution_plan = _execution_plan(
                effective_options,
                record_payload,
                manual_result,
                report_path,
                offline_record_path,
                effective_existing_plan,
            )
            outputs["execution_plan"] = str(
                _write_json(paths["ledger"] / "execution_plan.json", execution_plan)
            )
            feishu_result = _write_feishu_record(root, options, record_payload, feishu_target)
            outputs["record_payload"] = str(
                _write_json(paths["ledger"] / "record_payload.json", feishu_result)
            )
            if feishu_result.get("status") not in ("已写入", "已更新"):
                feishu_write_error = feishu_result.get("blocked_reason", "Feishu write failed")
                runtime_errors.append(feishu_write_error)
                if not options.send_mail:
                    return {
                        "case_id": case_id,
                        "status": "need_review",
                        "outputs": outputs,
                        "missing_fields": validation["missing_fields"],
                        "missing_field_prompts": validation.get("missing_field_prompts", []),
                        "errors": validation["errors"] + runtime_errors,
                    }
                online_record_url = ""
            else:
                online_record_url = feishu_result.get("record_url") or online_record_url
                try:
                    offline_record_path = _write_offline_record_if_needed(
                        root,
                        paths,
                        context,
                        record_payload,
                        options,
                        commit_master=True,
                        strict_live=True,
                    )
                    if offline_record_path is not None:
                        outputs["offline_record_workbook"] = str(offline_record_path)
                except Exception as exc:
                    runtime_errors.append(f"offline master commit failed: {type(exc).__name__}: {exc}")
                    return {
                        "case_id": case_id,
                        "status": "need_review",
                        "outputs": outputs,
                        "missing_fields": validation["missing_fields"],
                        "missing_field_prompts": validation.get("missing_field_prompts", []),
                        "errors": validation["errors"] + runtime_errors,
                    }
    elif write_online_payload:
        outputs["record_payload"] = str(_write_json(paths["ledger"] / "record_payload.json", record_payload))

    mail_sender = options.mail_from or options.smtp_user
    mail_recipients = DEFAULT_MAIL_TO
    matched_cc, _cc_match = _lookup_contacts_cc(_contacts, _sys_name)
    mail_cc = tuple(dict.fromkeys(email for email in matched_cc if email and email not in mail_recipients))
    # (BUGFIX38 owner 自动填 段已在 line 84+ 处理, 此处不重复)

    pre_mail_check = (
        _failed_online_write_pre_mail_check(feishu_write_error, report_path, offline_record_path, record_payload)
        if feishu_write_error
        else _pre_mail_online_check(root, options, online_record_url, record_payload)
    )
    mail_send_allowed = _mail_send_allowed(pre_mail_check)
    if options.send_mail and not mail_send_allowed:
        online_record_url = ""
    elif pre_mail_check.get("record_url"):
        online_record_url = str(pre_mail_check["record_url"])
    mail_preview = (
        build_mail_preview(
            context,
            report_path,
            online_record_url=online_record_url,
            offline_record_path=offline_record_path,
            sender=mail_sender,
            recipients=mail_recipients,
            cc=mail_cc,  # BUGFIX36 (2026-07-13): 项目小组成员抄送
        )
        if want_all or options.mail_dry_run or options.send_mail
        else None
    )
    if mail_preview is not None and options.send_mail and not mail_send_allowed:
        mail_preview["status"] = "失败"
        mail_preview["blocked_reason"] = _pre_mail_blocked_reason(pre_mail_check)
    if options.send_mail and not mail_send_allowed:
        pre_mail_error = _pre_mail_blocked_reason(pre_mail_check)
        if pre_mail_error and pre_mail_error not in runtime_errors:
            runtime_errors.append(pre_mail_error)
    smtp_options = SmtpOptions(
        host=options.smtp_host,
        port=options.smtp_port,
        username=options.smtp_user,
        sender=mail_sender,
        recipients=mail_recipients,
        cc=mail_cc,
        password_env=options.smtp_password_env,
    )
    mail_send_result = (
        _send_mail_with_guard(
            root,
            options,
            str((feishu_result or {}).get("record_id") or (feishu_target or {}).get("record_id") or ""),
            (feishu_target or {}).get("existing_fields") or {},
            (feishu_target or {}).get("config"),
            mail_preview,
            smtp_options,
        )
        if options.send_mail and mail_preview is not None and mail_send_allowed
        else _blocked_mail_send_result(mail_preview, mail_sender, mail_recipients, pre_mail_check)
        if options.send_mail and mail_preview is not None
        else None
    )
    if options.send_mail and mail_send_allowed and mail_send_result and mail_send_result.get("status") != MAIL_SENT:
        runtime_errors.append(mail_send_result.get("blocked_reason") or "mail send failed")
    mail_status_backfill = _backfill_mail_status_after_send(
        root,
        options,
        feishu_result,
        mail_send_result,
        mail_send_allowed,
    )
    if mail_status_backfill["status"] != "not_required":
        outputs["mail_status_backfill_result"] = str(
            _write_json(paths["ledger"] / "mail_status_backfill_result.json", mail_status_backfill)
        )
        if mail_status_backfill["status"] == "失败":
            runtime_errors.append(mail_status_backfill.get("blocked_reason", "mail status backfill failed"))
    if mail_preview is not None:
        outputs["mail_preview"] = str(_write_json(paths["mail"] / "mail_preview.json", mail_preview))
    if options.send_mail:
        outputs["pre_mail_check"] = str(_write_json(paths["mail"] / "pre_mail_check.json", pre_mail_check))
    if mail_send_result is not None:
        outputs["mail_send_result"] = str(_write_json(paths["mail"] / "mail_send_result.json", mail_send_result))
    if manual_result is not None:
        outputs["manual_match_result"] = str(_write_json(paths["manuals"] / "manual_match_result.json", manual_result))

    return {
        "case_id": case_id,
        "status": "need_review" if runtime_errors else validation["status"],
        "outputs": outputs,
        "missing_fields": validation["missing_fields"],
        "missing_field_prompts": validation.get("missing_field_prompts", []),
        "errors": validation["errors"] + runtime_errors,
    }


def _prepare_feishu_attachments(
    root: Path,
    options: RunOptions,
    record_payload: dict[str, Any],
    report_path: Path,
    manual_result: dict[str, Any] | None,
    feishu_target: dict[str, Any],
) -> dict[str, Any]:
    fields = record_payload.setdefault("fields", {})
    uploads: list[dict[str, str]] = []
    errors: list[str] = []
    try:
        config = feishu_target["config"]
        existing_fields = feishu_target.get("existing_fields") or {}
        report_attachment_field = config.field_map.get("故障报告附件", "故障报告附件")
        if _empty_backfill_value(existing_fields.get(report_attachment_field)):
            report_upload = upload_bitable_attachment(report_path, config)
            fields["故障报告"] = report_path.name
            fields["故障报告附件"] = [{"file_token": report_upload["file_token"]}]
            uploads.append({"field": "故障报告附件", "name": report_path.name})
        else:
            fields["故障报告附件"] = existing_fields[report_attachment_field]
        if (manual_result or {}).get("status") == "已匹配":
            manual_path = _manual_docx_path(root, manual_result or {})
            if not manual_path:
                errors.append("matched manual docx not found")
            else:
                manual_attachment_field = config.field_map.get("故障手册附件", "故障手册附件")
                if _empty_backfill_value(existing_fields.get(manual_attachment_field)):
                    manual_upload = upload_bitable_attachment(manual_path, config)
                    fields["故障手册附件"] = [{"file_token": manual_upload["file_token"]}]
                    uploads.append({"field": "故障手册附件", "name": manual_path.name})
                else:
                    fields["故障手册附件"] = existing_fields[manual_attachment_field]
        _sync_material_completeness(fields, existing_fields, getattr(config, "field_map", {}))
    except (AttributeError, FeishuApiError, FeishuConfigError, OSError) as exc:
        errors.append(str(exc))
    return {
        "status": "ready" if not errors else "failed",
        "uploads": uploads,
        "blocked_reason": "; ".join(errors),
    }


def _sync_material_completeness(
    fields: dict[str, Any],
    existing_fields: dict[str, Any] | None = None,
    field_map: dict[str, str] | None = None,
) -> str:
    existing = existing_fields or {}
    mapping = field_map or {}

    def value(name: str) -> Any:
        return fields.get(name) or existing.get(mapping.get(name, name))

    has_report = not _empty_backfill_value(value("故障报告"))
    has_attachment = not _empty_backfill_value(value("故障报告附件"))
    completeness = (
        "基础资料已齐"
        if has_report and has_attachment
        else "缺附件"
        if has_report
        else "缺报告索引"
        if has_attachment
        else "缺报告和附件"
    )
    fields["资料完整度"] = completeness
    return completeness


def _resolve_feishu_target(
    root: Path,
    options: RunOptions,
    record_payload: dict[str, Any],
) -> dict[str, Any]:
    """Resolve an existing row before any attachment upload or record write."""
    case_id = str(record_payload.get("case_id", "")).strip()
    try:
        config = load_feishu_config(root, options.feishu_config)
        case_id_field = _validate_feishu_case_id_field(config, case_id)
        effective_record_id = options.feishu_record_id
        dedupe_match: dict[str, Any] = {}
        if not effective_record_id and case_id:
            dedupe_match = find_record_by_case_id(case_id, config)
            effective_record_id = str(dedupe_match.get("record_id") or "")
            if dedupe_match.get("blocked_reason"):
                return {"status": "failed", "blocked_reason": str(dedupe_match["blocked_reason"])}
            fields = record_payload.get("fields", {}) or {}
            system_name = str(fields.get("系统名称") or fields.get("系统") or "").strip()
            occurred_at = fields.get("故障发生时间") or fields.get("时间")
            if not effective_record_id and system_name and occurred_at is not None and isinstance(config, FeishuConfig):
                collision = find_record_by_system_time(system_name, occurred_at, config)
                if collision.get("blocked_reason"):
                    return {"status": "failed", "blocked_reason": str(collision["blocked_reason"])}
                if collision.get("record_id"):
                    case_id_field = config.field_map.get("case_id", "case_id")
                    collision_case_id = str((collision.get("fields") or {}).get(case_id_field) or "").strip()
                    return {
                        "status": "failed",
                        "blocked_reason": (
                            f"system={system_name!r}, occurred_at={occurred_at!r} already belongs to "
                            f"case_id={collision_case_id or '(missing)'}; refusing to overwrite case_id={case_id!r}."
                        ),
                    }
        existing_fields = (
            (get_record(config, effective_record_id).get("fields") or {})
            if effective_record_id
            else {}
        )
        existing_case_id = str(existing_fields.get(case_id_field) or "").strip()
        if effective_record_id and existing_case_id and existing_case_id != case_id:
            return {
                "status": "failed",
                "blocked_reason": (
                    f"record_id={effective_record_id!r} belongs to case_id={existing_case_id!r}; "
                    f"refusing to write case_id={case_id!r}."
                ),
            }
        return {
            "status": "ready",
            "config": config,
            "record_id": effective_record_id,
            "existing_fields": existing_fields,
            "dedupe_match": dedupe_match,
            "case_id_field": case_id_field,
        }
    except (AttributeError, FeishuApiError, FeishuConfigError) as exc:
        return {"status": "failed", "blocked_reason": str(exc)}


def _validate_feishu_case_id_field(config: FeishuConfig, case_id: str) -> str:
    if not case_id:
        raise FeishuConfigError("missing case_id before Feishu preflight")
    target = config.field_map.get("case_id", "case_id")
    prepared, skipped = prepare_fields_for_feishu(
        {"case_id": case_id},
        list_fields(config),
        config.field_map,
    )
    if target not in prepared:
        detail = next(
            (str(item.get("reason") or "") for item in skipped if item.get("field") == "case_id"),
            "missing or incompatible target field",
        )
        raise FeishuConfigError(
            f"required case_id field {target!r} is not writable ({detail}); "
            "configure a dedicated text field before any attachment upload or record write"
        )
    return target


def _manual_docx_path(root: Path, manual_result: dict[str, Any]) -> Path | None:
    manual_root = root / "data" / "reference" / "manuals"
    match_path = Path(str(manual_result.get("match_path") or ""))
    if match_path and not match_path.is_absolute():
        match_path = root / match_path
    if match_path.suffix.lower() == ".md":
        try:
            rel = match_path.resolve().relative_to(manual_root.resolve())
            candidate = manual_root / "导出手册" / "docx" / Path(*rel.parts[1:]).with_suffix(".docx")
            if candidate.exists():
                return candidate
        except ValueError:
            pass
    domain = str(manual_result.get("manual_domain") or "").strip()
    system = str(manual_result.get("manual_system") or "").strip()
    name = str(manual_result.get("match_name") or "").strip()
    if domain and system and name:
        candidate = manual_root / "导出手册" / "docx" / domain / system / f"{system}-{name}.docx"
        if candidate.exists():
            return candidate
    return None


def _compute_next_sequence(config: FeishuConfig, record_payload: dict[str, Any] | None = None) -> int:
    """计算下一个序号 (按规则 2-6: 故障发生时间升序, 相同时间按 case_id).

    2026-07-10 22:45 改: 序号 = 该 case 的排位 (从 1 开始, 每年从 1 开始).
    读 取 该 case 故障发生时间, 跟同年度全部记录比:
      - 早  -> 序号靠前
      - 同  -> 按 case_id 升序
    在线记录读取失败时抛出异常，由 live 写入外层阻断创建；只有缺少可排序时间时才返回 1。
    """
    records = list_records(config)
    if not record_payload:
        return 1
    fields = record_payload.get("fields", {}) or {}
    case_id = str(record_payload.get("case_id", "")).strip()
    occurred_at = _parse_feishu_datetime(fields.get("故障发生时间"))
    if not occurred_at:
        return 1
    target_year = occurred_at.year
    target_key = (occurred_at, case_id)
    case_id_field = config.field_map.get("case_id", "case_id")
    def _occurred(r: dict[str, Any]) -> datetime:
        dt = _parse_feishu_datetime((r.get("fields") or {}).get("故障发生时间"))
        return dt or datetime.min
    same_year = [r for r in records if (lambda d: d is not None and d.year == target_year)(_parse_feishu_datetime((r.get("fields") or {}).get("故障发生时间")))]
    ranked = sorted(same_year, key=lambda r: (
        _occurred(r),
        str((r.get("fields") or {}).get(case_id_field, "")).strip(),
    ))
    position = 1
    for r in ranked:
        r_key = (
            _parse_feishu_datetime((r.get("fields") or {}).get("故障发生时间")),
            str((r.get("fields") or {}).get(case_id_field, "")).strip(),
        )
        if r_key < target_key:
            position += 1
    return position


def _parse_feishu_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # 2026-07-10 23:08 修复: 飞书表里时间是毫秒时间戳 (如 1767493080000)
        try:
            return datetime.fromtimestamp(value / 1000)
        except (ValueError, TypeError, OSError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # 也支持纯数字字符串
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text) / 1000)
        except (ValueError, TypeError, OSError):
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _readback_feishu_record(config: FeishuConfig, record_id: str, expected_serial: int) -> dict[str, Any]:
    """规则 7: 回读校验. 验证新写入的记录的序号与期望一致."""
    if not record_id:
        return {"checked": False, "reason": "empty record_id"}
    try:
        record = get_record(config, record_id)
        actual_serial = (record.get("fields") or {}).get("序号")
        actual_int = int(str(actual_serial).strip()) if actual_serial is not None else None
        if actual_int == expected_serial:
            return {"checked": True, "match": True, "expected": expected_serial, "actual": actual_int, "fields": record.get("fields", {})}
        return {"checked": True, "match": False, "expected": expected_serial, "actual": actual_int, "fields": record.get("fields", {})}
    except (AttributeError, FeishuApiError, FeishuConfigError) as e:
        return {"checked": False, "reason": str(e)}


def _reorder_feishu_sequence_by_time(config: FeishuConfig) -> dict[str, Any]:
    """规则 4 + 6 + 7: 补录中间时间的故障后, 重排该年度全部记录的序号 (按时间升序, 相同时间按 case_id).
    规则 7: 只更新序号发生变化的记录, 并回读校验.
    """
    try:
        records = list_records(config)
    except (AttributeError, FeishuApiError, FeishuConfigError) as e:
        return {"checked": False, "reason": str(e)}
    case_id_field = config.field_map.get("case_id", "case_id")
    by_year: dict[int, list[dict[str, Any]]] = {}
    for r in records:
        dt = _parse_feishu_datetime((r.get("fields") or {}).get("故障发生时间"))
        if dt is None:
            continue
        by_year.setdefault(dt.year, []).append(r)
    updates: list[dict[str, Any]] = []
    for year, year_records in by_year.items():
        ranked = sorted(year_records, key=lambda r: (
            _parse_feishu_datetime((r.get("fields") or {}).get("故障发生时间")),
            str((r.get("fields") or {}).get(case_id_field, "")).strip(),
        ))
        for position, r in enumerate(ranked, 1):
            current = (r.get("fields") or {}).get("序号")
            current_int = int(str(current).strip()) if current is not None else None
            if current_int != position:
                updates.append({"record_id": r.get("record_id", ""), "case_id": (r.get("fields") or {}).get(case_id_field, ""), "old_serial": current_int, "new_serial": position})
    # 真正更新飞书
    applied: list[dict[str, Any]] = []
    for u in updates:
        try:
            r = get_record(config, u["record_id"])
            r["fields"]["序号"] = u["new_serial"]
            update_record(u["record_id"], r, config)
            # 回读校验
            rb = get_record(config, u["record_id"])
            rb_serial = int(str((rb.get("fields") or {}).get("序号") or "").strip())
            applied.append({**u, "readback_match": rb_serial == u["new_serial"]})
        except (AttributeError, FeishuApiError, FeishuConfigError) as e:
            applied.append({**u, "error": str(e)})
    return {"checked": True, "updates_planned": len(updates), "updates_applied": len(applied), "applied": applied}


def _write_feishu_record(
    root: Path,
    options: RunOptions,
    record_payload: dict[str, Any],
    feishu_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 2026-07-10 22:45 修复: case_id 必须是 record_payload["case_id"] (永 久 唯 一 键)
    # 不 再 用 record_payload["fields"]["序号"] 当 case_id (那 是 错 误 的)
    case_id = str(record_payload.get("case_id", "")).strip()
    try:
        target = feishu_target or _resolve_feishu_target(root, options, record_payload)
        if target.get("status") != "ready":
            blocked_reason = str(target.get("blocked_reason") or "Feishu dedupe preflight failed")
            return {
                "api_called": False,
                "write_mode": "live",
                "status": "失败",
                "blocked_reason": blocked_reason,
                "fields": record_payload.get("fields", {}),
                "case_id_dedup": {
                    "case_id": case_id,
                    "matched_existing_record": False,
                    "explicit_record_id": False,
                    "ambiguous": True,
                    "blocked_reason": blocked_reason,
                },
            }
        config = target["config"]
        dedupe_match = target.get("dedupe_match") or {}
        effective_record_id = str(target.get("record_id") or "")
        if effective_record_id:
            existing_fields = target.get("existing_fields") or {}
            missing_fields = _missing_existing_record_fields(existing_fields, record_payload, getattr(config, "field_map", {}))
            if missing_fields:
                result = update_record(effective_record_id, _payload_with_fields(record_payload, missing_fields), config)
                result["missing_only_fields"] = missing_fields
            else:
                result = {
                    "api_called": False,
                    "write_mode": "live",
                    "status": "已更新",
                    "record_id": effective_record_id,
                    "record_url": feishu_record_url(config, effective_record_id),
                    "fields": {},
                    "skipped_fields": [],
                    "missing_only_fields": [],
                    "readback": {"checked": True, "missing_fields": [], "fields": existing_fields},
                }
            result["case_id_dedup"] = {
                "case_id": case_id,
                "matched_existing_record": bool(dedupe_match.get("record_id")),
                "explicit_record_id": bool(options.feishu_record_id),
                "ambiguous": False,
                "blocked_reason": "",
            }
        else:
            # 2026-07-10 22:45 修复: 序号 = 该 case 的排位 (按故障发生时间升序, 同时间按 case_id)
            # 不 再 是 max+1 (那 是 错 误 的)
            new_sequence = _compute_next_sequence(config, record_payload)
            record_payload["fields"]["序号"] = new_sequence
            result = create_record(record_payload, config)
            # 2026-07-10 22:50 规则 7: 回读校验 + 规则 4: 补录中间时间时重排全部序号
            new_record_id = result.get("record_id", "")
            if isinstance(config, FeishuConfig):
                readback = _readback_feishu_record(config, new_record_id, expected_serial=new_sequence)
                result["readback"] = readback
                # 规则 4: 补录中间时间的故障后, 插入对应位置, 后续序号全部 +1
                # 规则 7: 只更新序号发生变化的记录, 并回读校验
                reorder_result = _reorder_feishu_sequence_by_time(config)
                result["reorder"] = reorder_result
            result["case_id_dedup"] = {
                "case_id": case_id,
                "matched_existing_record": False,
                "explicit_record_id": False,
                "ambiguous": False,
                "blocked_reason": "",
            }
            result["assigned_sequence"] = new_sequence
        return _enforce_required_online_fields(result, record_payload, getattr(config, "field_map", {}))
    except (AttributeError, FeishuApiError, FeishuConfigError) as exc:
        return {
            "api_called": False,
            "write_mode": "live",
            "status": "失败",
            "blocked_reason": str(exc),
            "fields": record_payload.get("fields", {}),
            "case_id_dedup": {
                "case_id": case_id if 'case_id' in locals() else "",
                "matched_existing_record": False,
                "explicit_record_id": bool(options.feishu_record_id),
                "ambiguous": False,
                "blocked_reason": "",
            },
        }


def _enforce_required_online_fields(
    feishu_result: dict[str, Any],
    record_payload: dict[str, Any],
    field_map: dict[str, str],
) -> dict[str, Any]:
    if feishu_result.get("status") not in ("已写入", "已更新"):
        return feishu_result
    missing = _missing_written_required_fields(feishu_result, record_payload, field_map)
    if not missing:
        return feishu_result
    result = dict(feishu_result)
    result["status"] = "失败"
    result["blocked_reason"] = "Feishu write missed required backfill fields: " + ", ".join(missing)
    result["missing_required_fields"] = missing
    return result


def _missing_written_required_fields(
    feishu_result: dict[str, Any],
    record_payload: dict[str, Any],
    field_map: dict[str, str],
) -> list[str]:
    required = _online_write_required_fields(record_payload)
    # 2026-07-11 08:55 修改: 如果 record_payload.fields 中某些字段本身就是空值,
    # 则不视为缺失 (用户硬性要求 "@故障信息一定要发邮件", 这些字段缺写"待补充"也可)
    fields_payload = record_payload.get("fields", {})
    effective_required = tuple(
        name for name in required if not _empty_backfill_value(fields_payload.get(name))
    )
    missing: list[str] = []
    skipped = feishu_result.get("skipped_fields") or []
    for item in skipped:
        source = str(item.get("field", ""))
        target = str(item.get("target", ""))
        if source in effective_required or target in effective_required:
            missing.append(source or target)
    readback = feishu_result.get("readback") or {}
    readback_fields = readback.get("fields") or {}
    if not readback.get("checked") or not readback_fields:
        missing.extend(effective_required)
    else:
        for source_name in effective_required:
            target_name = field_map.get(source_name, source_name)
            if _empty_backfill_value(readback_fields.get(target_name)):
                missing.append(source_name)
    readback_missing = readback.get("missing_fields") or []
    # 2026-07-11 08:55 修改: 过滤掉 record_payload.fields 中空值字段 (用户硬性要求)
    # 即: 如果 record_payload.fields["故障报告"] = "" 则 readback_missing["故障报告"] 也忽略
    has_manual_link = any(not _empty_backfill_value(fields_payload.get(name)) for name in MANUAL_LINK_FIELDS)
    filtered_missing = []
    for name in readback_missing:
        name_str = str(name).strip()
        if not name_str:
            continue
        # 如果 record_payload 没有 MANUAL_LINK_FIELDS 值, 过滤掉故障手册/故障手册附件
        if not has_manual_link and name_str in MANUAL_LINK_FIELDS:
            continue
        # 如果 record_payload 中该字段是空值, 视为"待补充", 不阻塞邮件
        if _empty_backfill_value(fields_payload.get(name_str)):
            continue
        filtered_missing.append(name_str)
    missing.extend(filtered_missing)
    return sorted(set(missing))


def _online_write_required_fields(record_payload: dict[str, Any]) -> tuple[str, ...]:
    # 2026-07-11 08:55 修改: 如果 record_payload.fields 中某些字段本身就是空值, 则不要求它们
    # 用户硬性要求 "@故障信息一定要发邮件", 这些字段缺写"待补充"也可
    fields = record_payload.get("fields", {})
    # 3. 过滤掉 record_payload.fields 中空值的字段
    effective = tuple(
        name for name in _online_write_candidate_fields(record_payload) if not _empty_backfill_value(fields.get(name))
    )
    return effective


def _online_write_candidate_fields(record_payload: dict[str, Any]) -> tuple[str, ...]:
    fields = record_payload.get("fields", {})
    required = ONLINE_WRITE_REQUIRED_FIELDS
    if any(not _empty_backfill_value(fields.get(name)) for name in MANUAL_LINK_FIELDS):
        required = (*required, *MANUAL_LINK_FIELDS)
    return required


# BUGFIX36 (2026-07-13): 联系人查表辅助函数 (项目小组成员抄送)
def _load_contacts_for_cc(root: Path) -> dict[str, Any]:
    """加载 contacts.json, 失败返回空 doc (不阻塞主流程)"""
    try:
        from src.mail_sender.contacts import load_contacts
        return load_contacts(root)
    except Exception:
        return {"systems": [], "domain_alias": {}, "groups": {}}


def _lookup_contacts_cc(contacts: dict[str, Any], system_name: str) -> tuple[list[str], dict[str, Any]]:
    """按 system_name 查对应负责人的抄送邮箱。"""
    try:
        from src.mail_sender.contacts import lookup_cc
        return lookup_cc(contacts, system_name)
    except Exception:
        return [], {"matched": False, "match_level": "none", "contacts_count": 0}


def _lookup_contacts_owner(contacts: dict[str, Any], system_name: str) -> tuple[str, str, dict[str, Any]]:
    """BUGFIX38 (2026-07-13 13:00): 按 system_name 查处理人 (中文真名 + 小组名) + match info"""
    try:
        from src.mail_sender.contacts import lookup_owner
        return lookup_owner(contacts, system_name)
    except Exception:
        return "", "", {"matched": False, "match_level": "none", "contacts_count": 0}


def _fill_handler_from_previous_system(
    root: Path,
    options: RunOptions,
    context: dict[str, Any],
    record_payload: dict[str, Any],
) -> None:
    fields = record_payload.get("fields", {})
    system_name = str(fields.get("系统名称") or "").strip()
    if not system_name:
        return
    handling_group = str(context.get("handling", {}).get("handling_group") or "").strip()
    # 2026-07-11 14:20 BUGFIX18 修复: default_handlers 改成"任何含组名兜底词"都视为未明确指定
    # 原始问题: "亚信维护组 多域维护组" 不在原 default (仅 "" / "多域维护组" / handling_group)
    # 即使 owner 已瞎写"亚信维护组 多域维护组", 也被当作"用户明确指定", 不回填
    # 修复: 只要 owner 含"多域维护组"/"基础平台组"/空, 都视为 default, 走回填
    DEFAULT_HANDLER_KEYWORDS = ("", "多域维护组", "基础平台组")
    current_handler = str(fields.get("处理人员") or "").strip()
    is_default_handler = (
        not current_handler
        or any(kw and kw in current_handler for kw in DEFAULT_HANDLER_KEYWORDS if kw)
        or current_handler == handling_group
    )
    if not is_default_handler:
        return  # 用户指定了具体处理人 (如"党万利,徐伟源"), 不回填
    records: list[dict[str, Any]] = []
    if _live_gate_confirmed(options):
        try:
            config = load_feishu_config(root, options.feishu_config)
            records = list_records(config)
        except (AttributeError, FeishuApiError, FeishuConfigError):
            records = []
    # 2026-07-11 14:25 BUGFIX18 修复: 飞书表查不到时, 也读本地 master 表
    # 离线表 xlsx 是用户截图的错误源, 这里必须从那里读
    if not records:
        try:
            from src.offline_ledger.workbook import (
                DEFAULT_TEMPLATE as _OFFLINE_TEMPLATE,
                MASTER_TABLE_PATH as _OFFLINE_MASTER_PATH,
                _sheet_name,
                load_workbook as _load_wb,
            )
            offline_template = root / _OFFLINE_TEMPLATE
            offline_master = root / _OFFLINE_MASTER_PATH
            if offline_template.exists() and offline_master.exists():
                _wb = _load_wb(offline_master)
                _ws = _wb[_sheet_name(_wb.sheetnames, fields)]
                _local_records = []
                for row in range(2, _ws.max_row + 1):
                    _local_records.append({
                        "fields": {
                            "系统名称": _ws.cell(row, 3).value,
                            "处理人员": _ws.cell(row, 18).value,
                            "故障发生时间": _ws.cell(row, 6).value,
                        }
                    })
                records = _local_records
        except Exception:
            pass
    # 2026-07-11 20:30 BUGFIX26 修复: 按"故障发生时间"倒序 (不按 list 顺序)
    # 原始问题: reversed(records) 按 list 顺序倒序, 但 list 是飞书 API 返回顺序 (非时间序)
    # 修复: 按"故障发生时间"字段倒序, 找到该系统最近一条
    # 同时 BUGFIX26 修复: 团队多人汇总 (避免单挑"自己")
    def _get_time_ms(rec):
        f = rec.get("fields") or {}
        t = f.get("故障发生时间") or f.get("业务中断开始时间") or 0
        if isinstance(t, (int, float)):
            return int(t)
        return 0
    sorted_records = sorted(records, key=_get_time_ms, reverse=True)
    for record in sorted_records:
        existing_fields = record.get("fields") or {}
        if str(existing_fields.get("系统名称") or "").strip() != system_name:
            continue
        handler = str(existing_fields.get("处理人员") or "").strip()
        # BUGFIX18: 找到的历史 handler 只要含真实人名(不只 default 关键词) 就用
        if handler and not any(kw and kw == handler for kw in DEFAULT_HANDLER_KEYWORDS if kw) and handler != handling_group:
            # 2026-07-11 20:30 BUGFIX26 修复: 团队多人汇总 (避免命中"自己")
            # 例: 当前 case handler="高喆" (单), 找到历史"沈俊羽、杨文刚、高喆" → 用 3 人团队
            # 规则: 汇总该系统所有真实人名 (按首条出现顺序), 顿号分隔
            # 1. 收集所有真实人名
            all_names: list[str] = []
            seen: set[str] = set()
            # 优先用 current_handler, 然后汇总历史
            for name in handler.replace("、", ",").replace("，", ",").split(","):
                n = name.strip()
                if n and n not in seen:
                    seen.add(n)
                    all_names.append(n)
            # 2. 汇总该系统历史 (按时间倒序), 把新名字追加
            for r2 in sorted_records:
                ef2 = r2.get("fields") or {}
                if str(ef2.get("系统名称") or "").strip() != system_name:
                    continue
                h2 = str(ef2.get("处理人员") or "").strip()
                if h2 and not any(kw and kw == h2 for kw in DEFAULT_HANDLER_KEYWORDS if kw) and h2 != handling_group:
                    for name in h2.replace("、", ",").replace("，", ",").split(","):
                        n = name.strip()
                        if n and n not in seen:
                            seen.add(n)
                            all_names.append(n)
            # 3. 顿号连接
            merged_handler = "、".join(all_names)
            fields["处理人员"] = merged_handler
            return


def _existing_record_backfill_plan(
    root: Path,
    options: RunOptions,
    record_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not options.feishu_record_id:
        return None
    try:
        config = load_feishu_config(root, options.feishu_config)
        record = get_record(config, options.feishu_record_id)
    except (FeishuApiError, FeishuConfigError) as exc:
        return {
            "status": "not_checked",
            "record_id": options.feishu_record_id,
            "missing_fields": [],
            "blocked_reason": str(exc),
        }
    fields = record.get("fields") or {}
    missing = _missing_existing_record_fields(fields, record_payload, config.field_map)
    return {
        "status": "ready" if missing else "complete",
        "record_id": options.feishu_record_id,
        "missing_fields": missing,
        "missing_count": len(missing),
    }


def _missing_existing_record_fields(
    existing_fields: dict[str, Any],
    record_payload: dict[str, Any],
    field_map: dict[str, str],
) -> list[str]:
    missing: list[str] = []
    fields = record_payload.get("fields", {})
    for source_name in _online_write_candidate_fields(record_payload):
        value = fields.get(source_name)
        if _empty_backfill_value(value) and source_name not in {"故障报告附件", "故障手册附件"}:
            continue
        target_name = field_map.get(source_name, source_name)
        if source_name == "资料完整度" and not _empty_backfill_value(value):
            if existing_fields.get(target_name) != value:
                missing.append(source_name)
            continue
        if _empty_backfill_value(existing_fields.get(target_name)):
            missing.append(source_name)
    return missing


def _payload_with_fields(record_payload: dict[str, Any], field_names: list[str]) -> dict[str, Any]:
    fields = record_payload.get("fields", {})
    return {
        **record_payload,
        "fields": {name: fields[name] for name in field_names if name in fields},
    }


def _execution_plan(
    options: RunOptions,
    record_payload: dict[str, Any] | None,
    manual_result: dict[str, Any] | None,
    report_path: Path,
    offline_record_path: Path | None = None,
    existing_record_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if record_payload is None:
        return None
    live_requested = _live_requested(options)
    live_confirmed = _live_gate_confirmed(options)
    write_action = "update_feishu_record" if options.feishu_record_id else "create_feishu_record"
    fields = record_payload.get("fields", {})
    manual_missing = (
        _missing_record_payload_fields(record_payload, MANUAL_LINK_FIELDS)
        if (manual_result or {}).get("status") == "已匹配"
        else []
    )
    pre_mail_missing = _missing_record_payload_fields(record_payload, PRE_MAIL_REQUIRED_FIELDS) if options.send_mail else []
    actions = []
    if options.feishu_record_id and existing_record_plan is not None:
        existing_status = (existing_record_plan or {}).get("status", "not_checked")
        actions.append(
            {
                "name": "read_existing_online_record",
                "mode": "read_only",
                "status": "ready" if existing_status in {"ready", "complete"} else "need_review",
                "record_id": options.feishu_record_id,
                "missing_fields": (existing_record_plan or {}).get("missing_fields", []),
                "blocked_reason": (existing_record_plan or {}).get("blocked_reason", ""),
            }
        )
    actions.extend([
        {
            "name": write_action,
            "mode": "live" if live_requested and live_confirmed else "dry_run",
            "status": "ready" if not live_requested or live_confirmed else "blocked",
            "field_count": len(fields),
            "record_id": options.feishu_record_id,
            "missing_only": bool(options.feishu_record_id),
            "planned_fields": (existing_record_plan or {}).get("missing_fields", sorted(fields)) if options.feishu_record_id else sorted(fields),
        },
        {
            "name": "backfill_report_attachment",
            "mode": "same_record_payload",
            "status": "ready" if Path(report_path).exists() and fields.get("故障报告") else "blocked",
            "fields": ["故障报告", "故障报告附件", "资料完整度"],
        },
        {
            "name": "backfill_manual_attachment",
            "mode": "same_record_payload",
            "status": "ready" if (manual_result or {}).get("status") == "已匹配" and not manual_missing else "need_review",
            "fields": ["手册状态", "故障手册", "故障手册附件", "手册系统", "手册分类", "手册故障模式"],
            "manual_status": (manual_result or {}).get("status", "待补充"),
            "missing_fields": manual_missing,
        },
        {
            "name": "write_offline_record_workbook",
            "mode": "local_file",
            "status": "ready" if offline_record_path and offline_record_path.exists() else "skipped",
            "fields_source": "incident_context",
            "path": str(offline_record_path or ""),
        },
        {
            "name": "pre_mail_online_record_check",
            "mode": "local_gate",
            "status": "ready" if not options.send_mail or ((options.online_record_url or (live_requested and live_confirmed)) and not pre_mail_missing) else "blocked",
            "missing_fields": pre_mail_missing,
        },
    ])
    return {
        "status": "blocked" if any(action["status"] == "blocked" for action in actions) else "ready",
        "live_gate": {
            "requested": live_requested,
            "required_confirm": "LIVE-WRITE" if live_requested else "",
            "confirmed": live_confirmed,
            "entrypoint": "--apply --confirm-live-write LIVE-WRITE" if live_requested else "",
        },
        "existing_record": existing_record_plan,
        "actions": actions,
    }


def _write_offline_record_if_needed(
    root: Path,
    paths: dict[str, Path],
    context: dict[str, Any],
    record_payload: dict[str, Any],
    options: RunOptions,
    *,
    commit_master: bool | None = None,
    strict_live: bool = False,
) -> Path | None:
    template = root / OFFLINE_RECORD_TEMPLATE
    # 正式累计表写入服从与飞书相同的完整 live gate；参数不完整时只生成 case 快照。
    live_flag = (
        bool(options.apply) and not _live_gate_error(options)
        if commit_master is None
        else bool(commit_master)
    )
    return write_offline_record_workbook(
        template,
        paths["ledger"] / _offline_record_filename(context),
        record_payload,
        live=live_flag,
        master_path=root / OFFLINE_MASTER_TABLE_PATH,
        strict_live=strict_live,
    )


def _pre_mail_payload_check(source: str, online_record_url: str, record_payload: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_record_payload_fields(record_payload, PRE_MAIL_REQUIRED_FIELDS)
    if not online_record_url:
        missing.append("在线记录链接")
    return {
        "status": "ready" if not missing else "blocked",
        "source": source,
        "record_url": online_record_url,
        "missing_fields": sorted(set(missing)),
        "blocked_reason": "" if not missing else "pre-mail check requires online record, report attachment, and manual attachment backfill",
    }


def _failed_online_write_pre_mail_check(
    blocked_reason: str,
    report_path: Path,
    offline_record_path: Path | None,
    record_payload: dict[str, Any],
) -> dict[str, Any]:
    missing = []
    if not (report_path and report_path.exists()):
        missing.append("故障报告")
    if not (offline_record_path and offline_record_path.exists()):
        missing.append("离线故障记录表")
    return {
        "status": "blocked",
        "source": "feishu_write",
        "blocked_reason": "online record backfill failed before mail send: " + blocked_reason,
        "missing_fields": missing,
        "record_payload_missing_fields": _missing_record_payload_fields(record_payload, PRE_MAIL_REQUIRED_FIELDS),
        "offline_record": str(offline_record_path or ""),
    }


def _pre_mail_blocked_reason(pre_mail_check: dict[str, Any]) -> str:
    reason = str(pre_mail_check.get("blocked_reason") or "pre-mail online record check failed")
    missing = [str(field) for field in pre_mail_check.get("missing_fields", []) if str(field).strip()]
    if missing:
        return f"{reason}; missing fields: {', '.join(missing)}"
    return reason


def _mail_send_allowed(pre_mail_check: dict[str, Any]) -> bool:
    return pre_mail_check.get("status") == "ready"


def _blocked_mail_send_result(
    mail_preview: dict[str, Any],
    sender: str,
    recipients: tuple[str, ...],
    pre_mail_check: dict[str, Any],
) -> dict[str, Any]:
    return {
        "smtp_called": False,
        "status": "失败",
        "from": sender,
        "to": list(recipients),
        "subject": mail_preview.get("subject", ""),
        "attachment": mail_preview.get("attachment", ""),
        "attachments": mail_preview.get("attachments", []),
        "blocked_reason": _pre_mail_blocked_reason(pre_mail_check),
    }


def _send_mail_with_guard(
    root: Path,
    options: RunOptions,
    record_id: str,
    existing_fields: dict[str, Any],
    config: Any,
    mail_preview: dict[str, Any],
    smtp_options: SmtpOptions,
) -> dict[str, Any]:
    if not options.automatic:
        return send_mail(mail_preview, smtp_options)
    if not record_id:
        return _blocked_automatic_mail_result(mail_preview, "automatic mail requires a stable Feishu record_id")

    mail_status_field = getattr(config, "field_map", {}).get(MAIL_STATUS_FIELD, MAIL_STATUS_FIELD)
    if str(existing_fields.get(mail_status_field) or "").strip() == MAIL_SENT:
        return _deduplicated_mail_result(mail_preview, "feishu", feishu_already_sent=True)

    state = _read_mail_state(root, record_id)
    state_status = str(state.get("status") or "")
    if state_status == "sent":
        return _deduplicated_mail_result(mail_preview, "local_state")
    if state_status in {"attempting", "unknown"}:
        return _blocked_automatic_mail_result(
            mail_preview,
            f"automatic mail blocked by persistent state={state_status}; verify delivery manually",
        )
    if not _claim_mail_send(root, record_id):
        current = _read_mail_state(root, record_id)
        return _blocked_automatic_mail_result(
            mail_preview,
            f"automatic mail already claimed; state={current.get('status', 'unknown')}",
        )

    result = send_mail(mail_preview, smtp_options)
    if result.get("status") == MAIL_SENT:
        _write_mail_state(root, record_id, "sent")
    elif result.get("smtp_called"):
        _write_mail_state(root, record_id, "unknown")
    else:
        _mail_state_path(root, record_id).unlink(missing_ok=True)
    return result


def _mail_state_path(root: Path, record_id: str) -> Path:
    name = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return root / "work" / "cases" / "_mail_state" / f"{name}.json"


def _read_mail_state(root: Path, record_id: str) -> dict[str, Any]:
    path = _mail_state_path(root, record_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown"}


def _claim_mail_send(root: Path, record_id: str) -> bool:
    path = _mail_state_path(root, record_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "record_id": record_id,
        "status": "attempting",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return True
    except FileExistsError:
        return False


def _write_mail_state(root: Path, record_id: str, status: str) -> None:
    path = _mail_state_path(root, record_id)
    temporary = path.with_suffix(".tmp")
    _write_json(
        temporary,
        {
            "record_id": record_id,
            "status": status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    temporary.replace(path)


def _deduplicated_mail_result(
    mail_preview: dict[str, Any],
    source: str,
    *,
    feishu_already_sent: bool = False,
) -> dict[str, Any]:
    return {
        "smtp_called": False,
        "status": MAIL_SENT,
        "deduplicated": True,
        "dedupe_source": source,
        "feishu_already_sent": feishu_already_sent,
        "from": mail_preview.get("from", ""),
        "to": mail_preview.get("to", []),
        "cc": mail_preview.get("cc", []),
        "subject": mail_preview.get("subject", ""),
        "attachment": mail_preview.get("attachment", ""),
        "attachments": mail_preview.get("attachments", []),
        "blocked_reason": "",
    }


def _blocked_automatic_mail_result(mail_preview: dict[str, Any], reason: str) -> dict[str, Any]:
    result = _deduplicated_mail_result(mail_preview, "persistent_guard")
    result.update({"status": "失败", "deduplicated": False, "blocked_reason": reason})
    return result


def _backfill_mail_status_after_send(
    root: Path,
    options: RunOptions,
    feishu_result: dict[str, Any] | None,
    mail_send_result: dict[str, Any] | None,
    mail_send_allowed: bool,
) -> dict[str, Any]:
    if not options.send_mail or not mail_send_allowed or not mail_send_result:
        return {"status": "not_required"}
    if mail_send_result.get("feishu_already_sent"):
        return {"status": "not_required"}
    if (
        mail_send_result.get("status") != MAIL_SENT
        or not (mail_send_result.get("smtp_called") or mail_send_result.get("deduplicated"))
    ):
        return {"status": "not_required"}
    record_id = str((feishu_result or {}).get("record_id") or options.feishu_record_id or "")
    if not record_id:
        return {"status": "失败", "blocked_reason": "mail attempted but Feishu record id is missing"}
    mail_status = MAIL_SENT
    try:
        config = load_feishu_config(root, options.feishu_config)
        result = update_record(record_id, {"fields": {MAIL_STATUS_FIELD: mail_status}}, config)
    except (AttributeError, FeishuApiError, FeishuConfigError) as exc:
        return {"status": "失败", "record_id": record_id, "mail_status": mail_status, "blocked_reason": str(exc)}

    target = config.field_map.get(MAIL_STATUS_FIELD, MAIL_STATUS_FIELD)
    readback_fields = (result.get("readback") or {}).get("fields") or {}
    if result.get("status") != "已更新" or _empty_backfill_value(readback_fields.get(target)):
        return {
            "status": "失败",
            "record_id": record_id,
            "mail_status": mail_status,
            "blocked_reason": "mail status readback missing",
            "write_result": result,
        }
    return {
        "status": "已更新",
        "record_id": record_id,
        "record_url": result.get("record_url", ""),
        "mail_status": mail_status,
        "write_result": result,
    }


def _pre_mail_online_check(
    root: Path,
    options: RunOptions,
    online_record_url: str,
    record_payload: dict[str, Any],
) -> dict[str, Any]:
    if not options.send_mail:
        return {"status": "not_required"}
    if options.feishu_record_id:
        return _pre_mail_feishu_record_check(root, options)
    if options.online_record_url:
        return _pre_mail_payload_check("explicit_online_record_url", online_record_url, record_payload)
    if _live_gate_confirmed(options):
        return _pre_mail_payload_check("confirmed_apply", online_record_url, record_payload)
    return {
        "status": "blocked",
        "blocked_reason": "send-mail requires an explicit --online-record-url or a record URL from confirmed --apply",
    }


def _live_requested(options: RunOptions) -> bool:
    return options.apply or options.send_mail


def _live_gate_confirmed(options: RunOptions) -> bool:
    return options.apply and options.confirm_live_write == "LIVE-WRITE"


def _live_gate_error(options: RunOptions) -> str:
    if not _live_requested(options):
        return ""
    if not options.incident_context:
        return "live writes require --incident-context plus --apply --confirm-live-write LIVE-WRITE"
    if not options.apply:
        return "live writes require --apply --confirm-live-write LIVE-WRITE"
    if options.confirm_live_write != "LIVE-WRITE":
        return "missing confirm-live-write LIVE-WRITE"
    return ""


def _pre_mail_feishu_record_check(root: Path, options: RunOptions) -> dict[str, Any]:
    try:
        config = load_feishu_config(root, options.feishu_config)
        record = get_record(config, options.feishu_record_id)
    except (FeishuApiError, FeishuConfigError) as exc:
        return {"status": "blocked", "source": "feishu_record_read", "record_id": options.feishu_record_id, "blocked_reason": str(exc)}
    fields = record.get("fields", {})
    targets = [(name, config.field_map.get(name, name)) for name in PRE_MAIL_REQUIRED_FIELDS]
    missing = [name for name, target in targets if _empty_backfill_value(fields.get(target))]
    record_url = record_url_from_options(config_table_url=config.table_url, record_id=options.feishu_record_id)
    if not record_url:
        missing.append("在线记录链接")
    return {
        "status": "ready" if not missing else "blocked",
        "source": "feishu_record_read",
        "record_id": options.feishu_record_id,
        "missing_fields": missing,
        "record_url": record_url,
    }


def record_url_from_options(*, config_table_url: str, record_id: str) -> str:
    if not config_table_url:
        return ""
    separator = "&" if "?" in config_table_url else "?"
    return f"{config_table_url}{separator}record={record_id}"


def _missing_record_payload_fields(record_payload: dict[str, Any], required_fields: tuple[str, ...]) -> list[str]:
    fields = record_payload.get("fields", {})
    return [name for name in required_fields if _empty_backfill_value(fields.get(name))]


def _empty_backfill_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not any(str(value.get(key) or "").strip() for key in ("link", "text"))
    if isinstance(value, (list, tuple, set)):
        return not value
    return False


def _load_or_build_context(root: Path, options: RunOptions) -> dict[str, Any]:
    if options.incident_context:
        return json.loads(options.incident_context.read_text(encoding="utf-8-sig"))
    text = options.text
    if options.input_file:
        text = options.input_file.read_text(encoding="utf-8-sig")
    if not text.strip() and not (options.screenshots or options.logs or options.attachments):
        raise SystemExit("需要 --text、--input-file、--screenshot、--log 或 --attachment")
    return _build_context(root, text, options)


def _ensure_runtime_meta(context: dict[str, Any], options: RunOptions) -> None:
    meta = context.setdefault("meta", {})
    meta.setdefault("schema_version", SCHEMA_VERSION)
    meta.setdefault("created_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    meta.setdefault("status", "draft")
    if not meta.get("case_id"):
        source = json.dumps(context, ensure_ascii=False, sort_keys=True)
        meta["case_id"] = options.case_id or "case_" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]


def _resolve_bracket_system_name(bracket: str, known_systems: tuple[str, ...]) -> str:
    """2026-07-09 pitfall fix: bracket content like 'BOMC系统无法打开页面' must not be
    returned wholesale. Look up the known system name (or alias) as a substring of
    the bracket first; only fall back to splitting on '-' when no known system is
    present in the bracket at all. Canonical (non-lower) names are preferred over
    aliases so the resulting system_name keeps the same casing as the manual index."""
    if not bracket:
        return ""
    bracket_lower = bracket.lower()
    canonical = [s for s in known_systems if s and s != s.lower()]
    for name in sorted(canonical, key=len, reverse=True):
        if name.lower() in bracket_lower:
            return name
    aliases = [s for s in known_systems if s and s == s.lower()]
    for name in sorted(aliases, key=len, reverse=True):
        if name in bracket_lower:
            return name
    # No known-systems match. Preserve legacy dash-split so behavior is stable for
    # callers that do not pass a manual_index (e.g. unit-test temp roots).
    return bracket.split("-", 1)[0].strip()


def _load_known_systems(root: Path) -> tuple[str, ...]:
    """2026-07-09: load system name + aliases from manual_index.json for pre-validation
    and bracket system-name resolution."""
    index_path = root / "data" / "reference" / "manuals_index" / "manual_index.json"
    if not index_path.exists():
        return ()
    try:
        index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ()
    names: list[str] = []
    for system in index.get("systems", []):
        names.append(str(system.get("system", "")).strip())
        for alias in system.get("aliases", []):
            names.append(str(alias).strip())
    return tuple(n for n in names if n)


def _build_context(root: Path, text: str, options: RunOptions) -> dict[str, Any]:
    screenshot_texts = extract_screenshot_texts(options.screenshots)
    log_texts = _extract_text_files(options.logs)
    attachment_texts = _extract_text_files(options.attachments)
    clean = _clean_text(_combined_input_text(text, screenshot_texts, log_texts, attachment_texts))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    case_id = options.case_id or "case_" + hashlib.sha1(
        (clean + "|" + "|".join(str(p) for p in options.screenshots + options.attachments)).encode("utf-8")
    ).hexdigest()[:10]
    known_systems = _load_known_systems(root)
    fields = _fields_from_text(clean, known_systems=known_systems)
    context = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "fact_safety_version": FACT_SAFETY_VERSION,
            "case_id": case_id,
            "created_at": now,
            "status": "draft",
            "source_event": dict(options.source_event),
        },
        "input": {
            "text": clean,
            "original_text": _clean_text(text),
            "screenshots": [_file_ref(path) for path in options.screenshots],
            "screenshot_texts": screenshot_texts,
            "logs": [_file_ref(path) for path in options.logs],
            "log_texts": log_texts,
            "attachments": [_file_ref(path) for path in options.attachments],
            "attachment_texts": attachment_texts,
            "messages": [dict(message) for message in options.source_messages],
        },
        "incident": {
            "system_name": fields["system_name"],
            "fault_title": fields["fault_title"],
            "occurred_at": fields["occurred_at"],
            "recovered_at": fields["recovered_at"],
            # BUGFIX42 (2026-07-13 15:30, 用户原话 "时间不采用推断更加好"):
            # duration_minutes 优先用用户输入, 缺时再 fallback 推断
            "duration_minutes": fields.get("user_duration_minutes") or _duration_minutes(fields["occurred_at"], fields["recovered_at"]),
            "diagnosis_at": fields.get("diagnosis_at", ""),    # BUGFIX42: 原因定位时间 (可选)
            "resolved_at": fields.get("resolved_at", ""),      # BUGFIX42: 问题处理完成时间 (可选)
            "discovery_source": fields["discovery_source"],
        },
        "impact": {
            "impact_scope": fields["impact_scope"],
            "affected_business": fields["affected_business"],
            "impact_description": fields["impact_description"],
        },
        "diagnosis": {
            "fault_description": fields["fault_description"],
            "fault_cause": fields["fault_cause"],
            "boundary_tool": fields["boundary_tool"],
        },
        "handling": {
            "owner": fields["owner"],
            "handling_group": fields["handling_group"],
            "process_records": fields["process_records"],
            "process_source": fields.get("process_source", ""),
            "solution": fields["solution"],
            "closure_info": fields["closure_info"],
            "followup_measures": fields["followup_measures"],
        },
        "review": {
            "review_summary": fields["review_summary"],
        },
        "suggestions": fields.get("suggestions", {}),
        "evidence": {
            "source_refs": _source_refs(
                {key: value for key, value in fields.items() if key != "suggestions"},
                text,
                screenshot_texts,
                log_texts,
                attachment_texts,
                options.source_messages,
            ),
            "missing_fields": [],
            "missing_field_prompts": [],
            # 2026-07-09 pitfall #4: pre-validation hint when system_name is not in
            # the manual_index.json system dictionary, so the user knows manual match
            # is likely to fail and they may want to confirm/refine the bracket.
            "system_name_unknown": (
                bool(fields["system_name"])
                and bool(known_systems)
                and not any(
                    fields["system_name"].lower() == name.lower() for name in known_systems
                )
            ),
        },
    }
    return context


def _fields_from_text(text: str, known_systems: tuple[str, ...] = ()) -> dict[str, Any]:
    numbered_records = _numbered_timeline_records(text)
    bracket = _first_match(text, [r"【([^】]+)】"])
    default_date = datetime.now().strftime("%Y-%m-%d")
    time_range = _time_range(text)
    system = _labeled_value(text, ("系统名称", "系统", "应用", "平台", "模块"))
    if not system and bracket:
        # 2026-07-09: prefer known-system lookup over dash-split so that
        # 【BOMC系统无法打开页面】 → "BOMC" instead of "BOMC系统无法打开页面".
        system = _resolve_bracket_system_name(bracket, known_systems)
    if not system:
        system = _plain_system(text, known_systems)
    first_timeline_action = _timeline_action(numbered_records[0]) if numbered_records else ""
    timeline_fault_title = _fault_title_from_timeline(numbered_records)
    fault_title = (
        _labeled_value(text, ("故障标题", "标题", "故障现象", "报错信息", "错误信息", "异常信息", "告警内容"))
        or (f"{_system_label(system)}接口故障" if system and "接口故障" in first_timeline_action else "")
        or bracket
        or timeline_fault_title
        or _plain_fault_title(text)
        or (f"{system}故障" if system else "")
    )

    occurred = _normalize_time(
        _labeled_value(text, ("发生时间", "故障时间", "上报时间", "告警时间", "报错时间", "错误时间", "截图时间"))
        or (time_range[0] if time_range else "")
        or (numbered_records[0][:16] if numbered_records else "")
        or _first_match(text, [r"(\d{4}年\d{1,2}月\d{1,2}日\d{1,2}[:：]\d{1,2})", r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}[:：]\d{1,2})", r"(\d{1,2}月\d{1,2}日\s*\d{1,2}[:：]\d{1,2})"]),
        default_date,
    )
    recovered = _normalize_time(
        _labeled_value(text, ("恢复时间", "关闭时间", "恢复于", "解决时间", "恢复完成时间", "业务恢复时间"))
        or (numbered_records[-1][:16] if numbered_records and _is_verified_recovery(numbered_records[-1]) else "")
        or (time_range[1] if time_range else ""),
        default_date,
    )
    # 2026-07-09: if a '业务恢复时长' / '恢复时长' label gives a duration like '10分钟',
    # derive recovered_at by adding that duration to occurred (date borrowed from default_date).
    if not recovered:
        duration_label = _labeled_value(text, ("业务恢复时长", "恢复时长", "故障时长"))
        if duration_label and occurred:
            minutes = _parse_duration_minutes(duration_label)
            if minutes and minutes > 0:
                recovered = _shift_minutes(occurred, minutes)
    # BUGFIX42 (2026-07-13 15:30, 用户原话 "时间不采用推断更加好"):
    # 加 2 个新时间字段提取: 原因定位时间 + 问题处理完成时间
    # BUGFIX43 (2026-07-13 15:50, 用户原话 "我给的字段是这样 不是两行故障过程记录"):
    # 加 2 个新字段别名:
    # - "原因定位故障时间" (你给的字段名) → diagnosis_at
    # - "问题解决时长" (你给的字段名) → resolved_at
    diagnosis_at = _normalize_time(
        _labeled_value(text, ("原因定位故障时间", "原因定位时间", "定位时间", "原因确定时间")),
        default_date,
    )
    resolved_at = _normalize_time(
        _labeled_value(text, ("问题解决时长", "问题处理完成时间", "处理完成时间", "问题处理时间", "处理完成")),
        default_date,
    )
    # BUGFIX42: 用户输入的"故障闭环时长/故障时长" (e.g. "40分钟") 优先于推断
    user_duration_minutes = None
    duration_label = _labeled_value(text, ("故障闭环时长", "业务恢复时长", "恢复时长", "故障时长"))
    if duration_label:
        user_duration_minutes = _parse_duration_minutes(duration_label)
    # 原推断逻辑保留: 若 recovered 缺, 用 duration_label 推 recovered
    if not recovered and duration_label and occurred:
        minutes = _parse_duration_minutes(duration_label)
        if minutes and minutes > 0:
            recovered = _shift_minutes(occurred, minutes)
    fault_cause = _labeled_value(text, ("故障原因", "原因", "根因"))
    suggested_fault_cause = _cause_from_title(fault_title, system) if not fault_cause else ""
    solution = (
        _labeled_value(text, ("解决方案", "解决方式", "恢复手段", "处理措施", "处理", "恢复操作"))
        or _solution_from_timeline(numbered_records)
        or _plain_solution(text)
    )
    impact_scope = _labeled_value(text, ("影响范围", "业务影响范围", "业务影响", "影响", "影响描述")) or _plain_impact(text, system)
    suggested_impact_scope = f"影响{_system_label(system)}使用" if system and not impact_scope else ""
    affected_business = _labeled_value(text, ("影响业务", "业务"))
    # 2026-07-11 08:55 修改: 处理组自动推断规则
    # 用户规则: solution 里有"通知XXX厂商" → handling_group 自动改为 "XXX维护组 多域维护组"
    # 优先级: 用户明确指定(非默认模板) > 自动推断 > 默认 "多域维护组"
    # 用户输入"基础平台组/多域维护组"是模板默认, 不算明确指定, 走自动推断
    raw_group = _labeled_value(text, ("处理组",))
    DEFAULT_GROUP_VALUES = ("基础平台组", "多域维护组", "")
    if raw_group and raw_group not in DEFAULT_GROUP_VALUES:
        group = raw_group  # 用户明确指定(非默认), 用用户输入
    else:
        group = _infer_handling_group(solution) or "多域维护组"
    owner = _labeled_value(text, ("负责人", "处理人"))
    discovery = (
        _labeled_value(text, ("故障发现来源", "发现来源"))
        or _discovery_from_timeline(numbered_records)
        or "用户报障"
    )
    boundary_tool = _labeled_value(text, ("故障定界工具", "定界工具"))
    process_records = numbered_records or _list_value(text, ("处理过程", "处理记录"))
    followups = _list_value(text, ("后续完善措施", "后续措施"))
    fault_description = (
        _labeled_value(text, ("事件描述", "故障现象", "故障描述", "报错信息", "错误信息", "异常信息", "告警内容"))
        or (f"{discovery}反馈{fault_title}" if numbered_records else "")
        or first_timeline_action
        or _event_description(text)
        or _description_from_title_and_impact(fault_title, impact_scope)
    )
    closure = (
        _labeled_value(text, ("闭环信息", "闭环结论"))
        or (_timeline_action(process_records[-1]) if process_records and _is_verified_recovery(process_records[-1]) else "")
    )
    suggested_followups = _plain_followups(system, fault_cause or suggested_fault_cause) if not followups else []
    suggestions = {
        key: value
        for key, value in {
            "diagnosis.fault_cause": suggested_fault_cause,
            "impact.impact_scope": suggested_impact_scope,
            "handling.followup_measures": suggested_followups,
        }.items()
        if value
    }

    return {
        "system_name": system,
        "fault_title": fault_title,
        "occurred_at": occurred,
        "recovered_at": recovered,
        "diagnosis_at": diagnosis_at,    # BUGFIX42 (2026-07-13 15:30): 原因定位时间
        "resolved_at": resolved_at,      # BUGFIX42 (2026-07-13 15:30): 问题处理完成时间
        "user_duration_minutes": user_duration_minutes,  # BUGFIX42: 用户输入时长 (优先于推断)
        "discovery_source": discovery,
        "impact_scope": impact_scope,
        "affected_business": affected_business or impact_scope,
        "impact_description": _labeled_value(text, ("影响描述",)) or impact_scope,
        "fault_description": fault_description,
        "fault_cause": fault_cause,
        "boundary_tool": boundary_tool,
        "owner": owner,
        "handling_group": group,
        "process_records": process_records,
        "process_source": "actual_timeline" if numbered_records else "",
        "solution": solution,
        "closure_info": closure,
        "followup_measures": followups,
        "review_summary": _labeled_value(text, ("复盘总结",)),
        "suggestions": suggestions,
    }


def _paths(root: Path, case_id: str) -> dict[str, Path]:
    if (
        not isinstance(case_id, str)
        or not case_id.strip()
        or case_id in {".", ".."}
        or "/" in case_id
        or "\\" in case_id
        or Path(case_id).is_absolute()
    ):
        raise ValueError(f"invalid case_id: {case_id!r}")

    roots = {
        "case": root / "work" / "cases",
        "report": root / "outputs" / "reports",
        "validation": root / "outputs" / "validation",
        "ledger": root / "outputs" / "ledger",
        "mail": root / "outputs" / "mail",
        "manuals": root / "outputs" / "manuals",
    }
    paths = {key: base / case_id for key, base in roots.items()}
    if any(not path.resolve().is_relative_to(roots[key].resolve()) for key, path in paths.items()):
        raise ValueError(f"case_id escapes output root: {case_id!r}")
    return paths


def _report_filename(context: dict[str, Any]) -> str:
    incident = context["incident"]
    system = _safe_filename(str(incident.get("system_name") or "故障").strip())
    occurred = _parse_context_time(str(incident.get("occurred_at") or ""))
    if occurred:
        return f"{system}{occurred.month}月{occurred.day}日故障报告.docx"
    return f"{system}故障报告.docx"


def _offline_record_filename(context: dict[str, Any]) -> str:
    case_id = _safe_filename(str(context["meta"].get("case_id") or "case"))
    occurred = _parse_context_time(str(context["incident"].get("occurred_at") or ""))
    year = occurred.year if occurred else datetime.now().year
    return f"【{year}】多域系统故障记录表_{case_id}.xlsx"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip(" .")
    return cleaned or "故障"


def _parse_context_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _clean_report_template(root: Path) -> Path:
    legacy_dir = root / ".workbuddy" / "报告模板"
    for name in (
        "XX系统XXXX年XX月XX日故障报告 -干净模板.docx",
        "XX系统XXXX年XX月XX日故障报告-干净模板.docx",
    ):
        candidate = legacy_dir / name
        if candidate.exists():
            return candidate

    template_dir = root / "data" / "templates" / "reports" / "clean"
    templates = sorted(template_dir.glob("*.docx"))
    if len(templates) != 1:
        raise FileNotFoundError(
            f"expected one clean report template in {legacy_dir} or exactly one in {template_dir}"
        )
    return templates[0]


def _write_stopped_result(
    paths: dict[str, Path],
    case_id: str,
    context: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    context["meta"]["status"] = validation["status"]
    context.setdefault("evidence", {})["missing_fields"] = validation["missing_fields"]
    context.setdefault("evidence", {})["missing_field_prompts"] = validation.get("missing_field_prompts", [])
    incident_context_path = _write_json(paths["case"] / "incident_context.json", context)
    validation_path = _write_json(paths["validation"] / "validation_result.json", validation)
    return {
        "case_id": case_id,
        "status": validation["status"],
        "outputs": {
            "incident_context": str(incident_context_path),
            "validation_result": str(validation_path),
        },
        "missing_fields": validation["missing_fields"],
        "missing_field_prompts": validation.get("missing_field_prompts", []),
        "errors": validation["errors"],
    }


def _reset_outputs(paths: dict[str, Path]) -> None:
    for key in ("report", "validation", "ledger", "mail", "manuals"):
        path = paths[key]
        if path.exists():
            shutil.rmtree(path)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _file_ref(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "name": resolved.name,
        "exists": resolved.exists(),
        "size": resolved.stat().st_size if resolved.exists() else None,
    }


def _extract_text_files(paths: list[Path]) -> list[dict[str, Any]]:
    text_suffixes = {".txt", ".log", ".md", ".json", ".csv"}
    results: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.resolve()
        result: dict[str, Any] = {
            "path": str(resolved),
            "name": resolved.name,
            "exists": resolved.exists(),
            "text": "",
            "status": "missing_file" if not resolved.exists() else "skipped",
            "blocked_reason": "",
        }
        if not resolved.exists():
            result["blocked_reason"] = "file does not exist"
        elif resolved.suffix.lower() not in text_suffixes:
            result["blocked_reason"] = "not a supported text attachment"
        else:
            try:
                result["text"] = resolved.read_text(encoding="utf-8-sig")[:20000].strip()
                result["status"] = "ok" if result["text"] else "empty"
            except (OSError, UnicodeDecodeError) as exc:
                result["blocked_reason"] = f"text decode failed: {type(exc).__name__}"
        results.append(result)
    return results


def _clean_text(text: str) -> str:
    lines = [" ".join(line.strip().split()) for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _combined_input_text(
    text: str,
    screenshot_texts: list[dict[str, Any]],
    log_texts: list[dict[str, Any]] | None = None,
    attachment_texts: list[dict[str, Any]] | None = None,
) -> str:
    parts = [text]
    parts.extend(str(item.get("text") or "") for item in screenshot_texts)
    parts.extend(str(item.get("text") or "") for item in log_texts or [])
    parts.extend(str(item.get("text") or "") for item in attachment_texts or [])
    return "\n".join(part for part in parts if part and part.strip())


def _source_refs(
    fields: dict[str, Any],
    original_text: str,
    screenshot_texts: list[dict[str, Any]],
    log_texts: list[dict[str, Any]] | None = None,
    attachment_texts: list[dict[str, Any]] | None = None,
    source_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    original = _clean_text(original_text)
    ocr_texts = [_clean_text(str(item.get("text") or "")) for item in screenshot_texts]
    extra_sources = [
        (f"input.log_texts[{index}]", _clean_text(str(item.get("text") or "")))
        for index, item in enumerate(log_texts or [])
    ]
    extra_sources.extend(
        (f"input.attachment_texts[{index}]", _clean_text(str(item.get("text") or "")))
        for index, item in enumerate(attachment_texts or [])
    )
    message_sources = [
        (f"input.messages[{index}].text", _clean_text(str(item.get("text") or "")))
        for index, item in enumerate(source_messages or [])
    ]
    for key, value in fields.items():
        if not value:
            continue
        sources: list[str] = []
        for token in _source_tokens(value):
            for source, text in message_sources:
                if token and token in text and source not in sources:
                    sources.append(source)
            if token and token in original and not message_sources and "input.text" not in sources:
                sources.append("input.text")
            for index, text in enumerate(ocr_texts):
                source = f"input.screenshot_texts[{index}]"
                if token and token in text and source not in sources:
                    sources.append(source)
            for source, text in extra_sources:
                if token and token in text and source not in sources:
                    sources.append(source)
        refs.append({"field": key, "source": sources or "input.derived"})
    return refs


def _source_tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    return [str(value).strip()]


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _labeled_value(text: str, labels: tuple[str, ...]) -> str:
    # 2026-07-09 pitfall: 处理组 = ... 四、故障原因 → must also stop at Chinese ordinal
    # markers (一、二、三、...) used in numbered section headers. Negated char class
    # cannot match multi-char sequences, so we strip ordinal-marker tail in post.
    for label in labels:
        match = re.search(label + r"[:：]\s*([^。；;\n]+)", text)
        if match:
            value = match.group(1).strip()
            # Drop everything from the first 一/二/.../十 + 、 (whitespace tolerant).
            trimmed = re.split(r"\s*[一二三四五六七八九十]+、", value, maxsplit=1)[0].strip()
            return trimmed or value
    return ""


def _list_value(text: str, labels: tuple[str, ...]) -> list[str]:
    raw = _labeled_value(text, labels)
    if not raw:
        return []
    return [item.strip(" ；;，,") for item in re.split(r"[|；;]", raw) if item.strip(" ；;，,")]


def _time_range(text: str) -> tuple[str, str] | None:
    full = r"(?:\d{4}年\d{1,2}月\d{1,2}日\s*)?\d{1,2}[:：]\d{1,2}"
    match = re.search(rf"({full})\s*(?:到|至|--|-|~|—|–)\s*({full})", text)
    if not match:
        return None
    return match.group(1), match.group(2)


def _plain_system(text: str, known_systems: tuple[str, ...] = ()) -> str:
    """
    BUGFIX32 修复 (2026-07-12 09:30): 动态从 manual_index.json 加载所有已知系统
    原来只硬编码 5 个系统 (BOMC/OPENAPI/SmartBI/PASS/IT工单系统),
    用户报 "下载中心" / "实时营销" / "自助分析" 等全部识别失败 → incident.system_name 空 → need_review
    """
    # 1. 优先用 known_systems (动态从 manual_index.json 加载)
    if known_systems:
        text_lower = text.lower()
        # 按长度倒序, 优先匹配长名 (避免 "IT" 匹配上 "IT工单系统" 的子串)
        for name in sorted(known_systems, key=len, reverse=True):
            if name and name.lower() in text_lower:
                return name
    # 2. Fallback: 硬编码 5 个老系统
    for name in ("BOMC", "OPENAPI", "SmartBI", "PASS", "IT工单系统"):
        if name.lower() in text.lower():
            return name
    match = re.search(r"([A-Za-z][A-Za-z0-9_-]{1,})", text)
    if match:
        return match.group(1)
    return _first_match(text, [r"([A-Za-z0-9_\-\u4e00-\u9fa5]+系统)"])


def _plain_fault_title(text: str) -> str:
    for clause in re.split(r"[。；;，,\n]", text):
        cleaned = re.sub(r"^\s*@\S+\s*", "", clause).strip()
        if cleaned and re.search(r"(异常|故障|无法访问|不可用|失败|超时)", cleaned):
            return cleaned[:80]
    return ""


def _cause_from_title(title: str, system: str) -> str:
    cause = title
    if system:
        cause = re.sub(re.escape(system), "", cause, flags=re.IGNORECASE).strip(" -_，,。；;")
    return _normalize_service_names(cause) or title


def _plain_solution(text: str) -> str:
    match = re.search(r"(重启[^。；;，,]*)", text, re.IGNORECASE)
    if not match:
        return ""
    solution = re.sub(r"后(?:恢复|正常)$", "", match.group(1).strip())
    return _normalize_service_names(solution)


def _plain_impact(text: str, system: str) -> str:
    for clause in re.split(r"[。；;，,\n]", text):
        cleaned = clause.strip()
        if "无法访问" in cleaned or "不可用" in cleaned:
            return cleaned[:120]
    return ""


def _infer_handling_group(solution: str) -> str:
    """2026-07-11 08:55 新增: 从 solution 推断 handling_group.
    规则: solution 中包含"通知XXX厂商" → "XXX维护组 多域维护组";
    也支持 "XXX技术支持/XXX服务" 等模式;空时返回空字符串.
    """
    if not solution:
        return ""
    import re
    # 1. 匹配"通知XXX技术支持"模式 (优先, 避免被"通知XXX厂商"截断)
    m = re.search(r"通知([\u4e00-\u9fa5A-Za-z0-9]+?)技术支持", solution)
    if m:
        vendor = m.group(1).strip()
        return f"{vendor}技术支持组 多域维护组"
    # 2. 匹配"通知XXX厂商"模式
    m = re.search(r"通知([\u4e00-\u9fa5A-Za-z0-9]+?)厂商", solution)
    if m:
        vendor = m.group(1).strip()
        return f"{vendor}维护组 多域维护组"
    # 3. 匹配"XXX技术支持" (不在"通知"前缀下)
    m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]+?)技术支持", solution)
    if m:
        vendor = m.group(1).strip()
        return f"{vendor}技术支持组 多域维护组"
    return ""


def _plain_process_records(occurred: str, recovered: str, solution: str) -> list[str]:
    records: list[str] = []
    if occurred:
        records.append(f"{occurred[-5:]}接到故障信息")
    if recovered:
        records.append(f"{recovered[-5:]}{solution or '业务恢复'}")
    return records


def _numbered_timeline_records(text: str) -> list[str]:
    default_date = datetime.now()
    date_match = re.search(r"(?:发生日期|故障日期)[:：]\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日?", text)
    if date_match:
        try:
            default_date = datetime(
                int(date_match.group(1)) if date_match.group(1) else default_date.year,
                int(date_match.group(2)),
                int(date_match.group(3)),
            )
        except ValueError:
            return []
    pattern = re.compile(
        r"(?m)^\s*(?:时间\s*)?\d+\s*(?:[\.、\)]|[:：])?\s*(?:(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*)?(\d{1,2})(?:[:：]|[点时])(\d{1,2})(?:分)?\s*[:：]\s*(.+?)\s*$"
    )
    return [
        f"{int(y) if y else default_date.year:04d}-{int(month) if month else default_date.month:02d}-{int(day) if day else default_date.day:02d} {int(hour):02d}:{int(minute):02d} {action.strip()}"
        for y, month, day, hour, minute, action in pattern.findall(text)
        if action.strip()
    ]


def _timeline_action(record: str) -> str:
    return re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*", "", record).strip(" ；;。")


def _is_verified_recovery(record: str) -> bool:
    action = _timeline_action(record)
    return (
        "验证" in action
        and "恢复" in action
        and ("验证业务恢复" in action or any(word in action for word in ("正常", "通过", "成功")))
    )


def _solution_from_timeline(records: list[str]) -> str:
    actions: list[str] = []
    for record in records:
        action = _timeline_action(record)
        if not any(verb in action for verb in ("重启", "切换", "修复", "回滚", "清理", "扩容", "重连")):
            continue
        cleaned = re.sub(r"^多域维护组", "", action).strip(" ；;。")
        if cleaned and cleaned not in actions:
            actions.append(cleaned)
    return "；".join(actions)


def _discovery_from_timeline(records: list[str]) -> str:
    if not records:
        return ""
    match = re.match(r"(.{1,20}?)(?:通知|反馈|报障|发现)", _timeline_action(records[0]))
    return match.group(1).strip() if match else ""


def _fault_title_from_timeline(records: list[str]) -> str:
    """Extract the first reported symptom, excluding the reporter and report verb."""
    if not records:
        return ""
    match = re.match(r".{1,20}?(?:通知|反馈|报障|发现)(.+)", _timeline_action(records[0]))
    return match.group(1).strip(" ，,。；;")[:80] if match else ""


def _normalize_service_names(text: str) -> str:
    return re.sub("cas", "CAS", re.sub("web", "WEB", text, flags=re.IGNORECASE), flags=re.IGNORECASE)


def _plain_followups(system: str, cause: str) -> list[str]:
    target = _system_label(system) if system else "相关系统"
    if _database_responsibility_scope(cause):
        return [
            f"完善{target}数据库连通性拨测和亚信厂商通知机制",  # 2026-07-11 12:00 BUGFIX12: 亚信维护组就是亚信厂商
            f"固化{target}中间件和服务恢复验证清单",
        ]
    if cause:
        return [f"补充{target}{cause}监控告警", "固化故障恢复验证清单"]
    return [f"补充{target}运行状态监控告警", "固化故障恢复验证清单"]


def _database_responsibility_scope(text: str) -> bool:
    source = str(text or "")
    upper = source.upper()
    return any(term in source for term in ("数据库", "数据源", "慢SQL")) or any(
        term in upper for term in ("DB2", "MYSQL", "ORACLE", "POSTGRES", "SQL SERVER")
    )


def _system_label(system: str) -> str:
    return system if system.endswith("系统") else f"{system}系统"


def _event_description(text: str) -> str:
    cleaned = re.sub(r"^【[^】]+】", "", text).strip()
    marker = re.search(r"(发生时间|恢复时间|故障原因|原因|解决方案|解决方式|影响范围|负责人|处理组|处理过程|后续措施)[:：]", cleaned)
    if marker:
        cleaned = cleaned[: marker.start()].strip(" 。；;")
    return cleaned[:500]


def _description_from_title_and_impact(title: str, impact: str) -> str:
    title = title.strip(" 。；;")
    impact = impact.strip(" 。；;")
    if title and impact:
        return f"{title}，{impact}"
    return title or impact


def _normalize_time(value: str, default_date: str = "") -> str:
    if not value:
        return ""
    text = value.strip().replace("：", ":").replace("分", "")
    match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{1,2})", text)
    if match:
        y, mo, d, h, mi = [int(part) for part in match.groups()]
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})", text)
    if match:
        y, mo, d, h, mi = [int(part) for part in match.groups()]
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    # 2026-07-09 pitfall: bare '7月9日19:37' (no year) — use default_date as the year
    # so the resulting timestamp is monotonic with the current run. Without this,
    # the extractor silently drops occurred_at and process_records collapse.
    match = re.match(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{1,2})", text)
    if match and default_date:
        mo, d, h, mi = [int(part) for part in match.groups()]
        year = default_date.split("-", 1)[0] if "-" in default_date else default_date
        return f"{year}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    match = re.match(r"(\d{1,2}):(\d{1,2})", text)
    if match and default_date:
        h, mi = [int(part) for part in match.groups()]
        return f"{default_date} {h:02d}:{mi:02d}"
    return ""


def _duration_minutes(start: str, end: str) -> int | str:
    if not start or not end:
        return ""
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    return max(0, int((end_dt - start_dt).total_seconds() // 60))


def _parse_duration_minutes(label_value: str) -> int | None:
    """2026-07-09: parse '10分钟' / '20 分钟' / '1小时' / '30秒' into minutes. Returns
    None when the label does not contain a recognizable duration."""
    if not label_value:
        return None
    text = str(label_value)
    total = 0
    matched = False
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|h|时)", text, re.IGNORECASE)
    if hour_match:
        total += int(float(hour_match.group(1)) * 60)
        matched = True
    min_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min)", text, re.IGNORECASE)
    if min_match:
        total += int(float(min_match.group(1)))
        matched = True
    sec_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s)", text, re.IGNORECASE)
    if sec_match:
        # Round seconds to nearest minute (sub-minute durations are noise for this domain).
        total += max(1, round(float(sec_match.group(1)) / 60)) if float(sec_match.group(1)) >= 30 else 0
        matched = True
    return total if matched else None


def _shift_minutes(start: str, minutes: int) -> str:
    """2026-07-09: shift a 'YYYY-MM-DD HH:MM' timestamp forward by `minutes`. Empty on
    parse failure so callers can fall back to a default."""
    if not start or not minutes:
        return ""
    try:
        dt = datetime.strptime(start, "%Y-%m-%d %H:%M") + timedelta(minutes=minutes)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""
