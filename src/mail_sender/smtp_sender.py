from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any


MAIL_SENT = "已发送"
MAIL_FAILED = "失败"


@dataclass(frozen=True)
class SmtpOptions:
    host: str = "smtp.163.com"
    port: int = 465
    username: str = ""
    password_env: str = "FAULT_SMTP_PASSWORD"
    sender: str = ""
    recipients: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()  # BUGFIX36 (2026-07-13): 项目小组成员抄送


def send_mail(preview: dict[str, Any], options: SmtpOptions) -> dict[str, Any]:
    blocked = _blocked_reason(preview, options)
    result = {
        "smtp_called": False,
        "status": MAIL_FAILED,
        "from": options.sender,
        "to": list(options.recipients),
        "cc": list(options.cc),  # BUGFIX36 (2026-07-13): 项目小组成员抄送
        "subject": preview.get("subject", ""),
        "attachment": preview.get("attachment", ""),
        "attachments": _attachment_paths(preview),
        "blocked_reason": blocked,
    }
    if blocked:
        return result

    message = _message(preview, options)
    try:
        password = os.environ[options.password_env]
        if options.port == 465:
            with smtplib.SMTP_SSL(options.host, options.port, timeout=30) as smtp:
                smtp.login(options.username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(options.host, options.port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(options.username, password)
                smtp.send_message(message)
    except Exception as exc:  # pragma: no cover - network and server dependent
        result["smtp_called"] = True
        result["blocked_reason"] = f"{type(exc).__name__}: {exc}"
        return result

    result["smtp_called"] = True
    result["status"] = MAIL_SENT
    result["blocked_reason"] = ""
    return result


def _blocked_reason(preview: dict[str, Any], options: SmtpOptions) -> str:
    reasons: list[str] = []
    if preview.get("status") != "待发送":
        reasons.append(preview.get("blocked_reason") or "mail preview is not ready")
    if not options.host:
        reasons.append("missing SMTP host")
    if not options.username:
        reasons.append("missing SMTP username")
    if not options.sender:
        reasons.append("missing mail sender")
    if not options.recipients:
        reasons.append("missing mail recipients")
    if not os.environ.get(options.password_env):
        reasons.append(f"missing SMTP password env {options.password_env}")
    attachments = [Path(path) for path in _attachment_paths(preview)]
    if not attachments:
        reasons.append("missing attachments")
        return "; ".join(reasons)
    for attachment in attachments:
        if not attachment.exists():
            reasons.append(f"missing attachment {attachment}")
    if not any(path.exists() and path.suffix.lower() == ".docx" for path in attachments):
        reasons.append("missing DOCX attachment")
    if not any(path.exists() and path.suffix.lower() == ".xlsx" for path in attachments):
        reasons.append("missing offline XLSX attachment")
    return "; ".join(reasons)


def _message(preview: dict[str, Any], options: SmtpOptions) -> EmailMessage:
    message = EmailMessage()
    message["From"] = options.sender
    message["To"] = ", ".join(options.recipients)
    # BUGFIX36 (2026-07-13): 加 Cc header (项目小组成员抄送)
    if options.cc:
        message["Cc"] = ", ".join(options.cc)
    message["Subject"] = str(preview.get("subject") or "故障报告")
    message.set_content(str(preview.get("body") or ""))

    for attachment_text in _attachment_paths(preview):
        attachment = Path(attachment_text)
        payload = attachment.read_bytes()
        maintype, subtype = _mime_type(attachment)
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=attachment.name)
    return message


def _attachment_paths(preview: dict[str, Any]) -> list[str]:
    attachments = [str(path) for path in preview.get("attachments", []) if str(path).strip()]
    if attachments:
        return attachments
    attachment = str(preview.get("attachment") or "").strip()
    return [attachment] if attachment else []


def _mime_type(path: Path) -> tuple[str, str]:
    if path.suffix.lower() == ".xlsx":
        return "application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"
