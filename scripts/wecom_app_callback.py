from __future__ import annotations

import argparse
import base64
import hashlib
import os
import random
import socket
import string
import struct
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.orchestrator.pipeline import RunOptions, run


class WeComAppCrypt:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        self.token = token
        self.receive_id = receive_id
        padded = encoding_aes_key.strip() + "=" * (-len(encoding_aes_key.strip()) % 4)
        self.key = base64.b64decode(padded)
        if len(self.key) != 32:
            raise ValueError("WECOM_APP_ENCODING_AES_KEY must decode to 32 bytes")

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        self._verify_signature(msg_signature, timestamp, nonce, echostr)
        return self._decrypt(echostr)

    def decrypt_message(self, post_data: bytes, msg_signature: str, timestamp: str, nonce: str) -> str:
        root = ET.fromstring(post_data)
        encrypt = _xml_text(root, "Encrypt")
        self._verify_signature(msg_signature, timestamp, nonce, encrypt)
        return self._decrypt(encrypt)

    def encrypt_reply(self, plain_xml: str, nonce: str, timestamp: str | None = None) -> str:
        timestamp = timestamp or str(int(time.time()))
        encrypt = self._encrypt(plain_xml)
        signature = _sha1_signature(self.token, timestamp, nonce, encrypt)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )

    def _verify_signature(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> None:
        expected = _sha1_signature(self.token, timestamp, nonce, encrypt)
        if expected != msg_signature:
            raise ValueError("invalid msg_signature")

    def _decrypt(self, encrypted_text: str) -> str:
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16]))
        decryptor = cipher.decryptor()
        padded = decryptor.update(base64.b64decode(encrypted_text)) + decryptor.finalize()
        unpadder = PKCS7(256).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        content = plain[16:]
        msg_len = socket.ntohl(struct.unpack("I", content[:4])[0])
        msg = content[4 : 4 + msg_len].decode("utf-8")
        from_receive_id = content[4 + msg_len :].decode("utf-8")
        if self.receive_id and from_receive_id != self.receive_id:
            raise ValueError("receive_id mismatch")
        return msg

    def _encrypt(self, plain_text: str) -> str:
        plain_bytes = plain_text.encode("utf-8")
        packed = (
            _random_bytes(16)
            + struct.pack("I", socket.htonl(len(plain_bytes)))
            + plain_bytes
            + self.receive_id.encode("utf-8")
        )
        padder = PKCS7(256).padder()
        padded = padder.update(packed) + padder.finalize()
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16]))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(encrypted).decode("utf-8")


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    values = [token, timestamp, nonce, encrypt]
    values.sort()
    return hashlib.sha1("".join(values).encode("utf-8")).hexdigest()


def _xml_text(root: ET.Element, name: str) -> str:
    node = root.find(name)
    return "" if node is None or node.text is None else node.text


def _random_bytes(length: int) -> bytes:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length)).encode("ascii")


def _case_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in prefix).strip("_")
    return f"{safe_prefix or 'wecom_app'}_{stamp}_{digest}"


def _reply_summary(result: dict[str, Any]) -> str:
    outputs = result.get("outputs") or {}
    lines = [f"处理状态：{result.get('status', 'unknown')}", f"case_id：{result.get('case_id', '')}"]
    if outputs.get("report_docx"):
        lines.append(f"报告：{outputs['report_docx']}")
    if outputs.get("offline_record_workbook"):
        lines.append(f"离线记录表：{outputs['offline_record_workbook']}")
    if result.get("missing_fields"):
        lines.append("缺失字段：" + "、".join(str(item) for item in result["missing_fields"]))
    if result.get("errors"):
        lines.append("错误：" + "；".join(str(item) for item in result["errors"]))
    return "\n".join(lines)


def _cdata(text: str) -> str:
    return text.replace("]]>", "]]]]><![CDATA[>")


def _plain_text_reply(incoming_xml: str, content: str) -> str:
    root = ET.fromstring(incoming_xml)
    to_user = _xml_text(root, "FromUserName")
    from_user = _xml_text(root, "ToUserName")
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{_cdata(to_user)}]]></ToUserName>"
        f"<FromUserName><![CDATA[{_cdata(from_user)}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{_cdata(content[:1800])}]]></Content>"
        "</xml>"
    )


def _handle_plain_message(plain_xml: str, run_mode: str, case_prefix: str, online_record_url: str) -> str:
    root = ET.fromstring(plain_xml)
    msg_type = _xml_text(root, "MsgType")
    if msg_type != "text":
        return "已收到消息。当前只支持文本故障信息。"
    content = _xml_text(root, "Content").strip()
    if not content:
        return "未收到可处理的故障文本。"
    if run_mode == "probe":
        return "连接正常，已收到文本。启动参数改为 --run-mode dry-run 后会生成本地报告和离线记录表。"

    case_id = _case_id(case_prefix, content)
    result = run(
        ROOT,
        RunOptions(
            text=content,
            case_id=case_id,
            all_dry_run=True,
            online_record_url=online_record_url,
        ),
    )
    return _reply_summary(result)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive WeCom self-built app message callbacks")
    parser.add_argument("--host", default=os.environ.get("WECOM_APP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WECOM_APP_PORT", "8124")))
    parser.add_argument(
        "--run-mode",
        choices=("probe", "dry-run"),
        default=os.environ.get("FAULT_WECOM_RUN_MODE", "probe"),
    )
    parser.add_argument("--case-prefix", default="wecom_app")
    parser.add_argument("--online-record-url", default="")
    return parser.parse_args()


def _load_crypt() -> WeComAppCrypt:
    token = os.environ.get("WECOM_APP_TOKEN", "").strip()
    aes_key = os.environ.get("WECOM_APP_ENCODING_AES_KEY", "").strip()
    corp_id = os.environ.get("WECOM_APP_CORP_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("WECOM_APP_TOKEN", token),
            ("WECOM_APP_ENCODING_AES_KEY", aes_key),
            ("WECOM_APP_CORP_ID", corp_id),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Set required environment variables: " + ", ".join(missing))
    return WeComAppCrypt(token, aes_key, corp_id)


def _query(path: str) -> dict[str, str]:
    parsed = urlparse(path)
    return {key: values[0] for key, values in parse_qs(parsed.query).items() if values}


def _make_handler(crypt: WeComAppCrypt, run_mode: str, case_prefix: str, online_record_url: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = _query(self.path)
            try:
                echo = crypt.verify_url(
                    query.get("msg_signature", ""),
                    query.get("timestamp", ""),
                    query.get("nonce", ""),
                    query.get("echostr", ""),
                )
            except Exception as exc:
                self._send(403, f"verify failed: {exc}", "text/plain; charset=utf-8")
                return
            self._send(200, echo, "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            query = _query(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                plain_xml = crypt.decrypt_message(
                    body,
                    query.get("msg_signature", ""),
                    query.get("timestamp", ""),
                    query.get("nonce", ""),
                )
                reply_text = _handle_plain_message(plain_xml, run_mode, case_prefix, online_record_url)
                reply_xml = _plain_text_reply(plain_xml, reply_text)
                encrypted = crypt.encrypt_reply(reply_xml, query.get("nonce", ""), query.get("timestamp") or None)
            except Exception as exc:
                self._send(500, f"callback failed: {exc}", "text/plain; charset=utf-8")
                return
            self._send(200, encrypted, "text/xml; charset=utf-8")

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}", flush=True)

        def _send(self, status: int, content: str, content_type: str) -> None:
            data = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> int:
    args = _parse_args()
    crypt = _load_crypt()
    handler = _make_handler(crypt, args.run_mode, args.case_prefix, args.online_record_url)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"WeCom app callback listening on http://{args.host}:{args.port}/wecom/app-callback", flush=True)
    print(f"run_mode={args.run_mode}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping WeCom app callback server", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
