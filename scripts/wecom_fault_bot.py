"""企业微信智能机器人 (方案二 WebSocket) ↔ 故障闭环 V1 pipeline 桥接

设计目标 (Windows 本地值班版, 2026-07-10 用户确认):
1. 收企业微信群里 @机器人 文本消息
2. 调 pipeline.run() 处理 (默认 dry-run, 不连外部)
3. 收到消息立即回复“收到！”，邮件真实发送成功后回复“邮件已发送”
4. 失败时回复真实阻断原因
5. bot_id / bot_secret 只从 env 读, 不进代码/文档/日志
6. 真实写飞书/发邮件 = 双 env gate:
   - FAULT_AUTO_SEND_ON_VALIDATED=1
   - FAULT_CONFIRM_LIVE_WRITE=LIVE-WRITE
   两个必须都设, 否则走 dry-run

本地 mock 测试: 用 --mock 选项, 绕过 aibot SDK, 直接跑 handle_text(),
可在没有真实 WeCom 凭据的情况下做端到端验证。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.orchestrator.pipeline import RunOptions, run


_HANDLE_TEXT_LOCK = threading.Lock()
_WS_WATCH_INTERVAL_SECONDS = 5.0


# ---------------------------------------------------------------------------
# 凭据与 gate 校验
# ---------------------------------------------------------------------------

# 真实写入的 env gate 期望值
_LIVE_WRITE_GATE_ENV = "FAULT_CONFIRM_LIVE_WRITE"
_LIVE_WRITE_GATE_VALUE = "LIVE-WRITE"
_AUTO_SEND_ENV = "FAULT_AUTO_SEND_ON_VALIDATED"
_AUTO_SEND_ON = "1"
# 第三道闸门: 真生产 case 必须在 env 里显式声明, 否则 bot 永远走 dry-run
#   设 FAULT_PROD_CASE_TOKEN=PROD-CASE-2026  (任意非空字符串)  → 允许真发
#   未设/空                                                    → bot 走 dry-run (不管前两个 gate 是否开)
_PROD_CASE_TOKEN_ENV = "FAULT_PROD_CASE_TOKEN"
# 触发 dry-run-only 的 case_id 前缀 (agent/测试/演示), 用于拦截 agent 自己跑 --apply
_DENY_CASE_PREFIXES = ("test", "agent", "demo", "e2e", "mock", "smoke")


def _bot_credentials_or_die() -> tuple[str, str]:
    """读 WECHAT_BOT_ID/SECRET, 缺则报错退出. 不打印实际值."""
    bot_id = os.environ.get("WECHAT_BOT_ID", "").strip()
    secret = os.environ.get("WECHAT_BOT_SECRET", "").strip()
    if not bot_id or not secret:
        raise SystemExit(
            "Set WECHAT_BOT_ID and WECHAT_BOT_SECRET before starting the bot\n"
            "  (PowerShell)  $env:WECHAT_BOT_ID='...'; $env:WECHAT_BOT_SECRET='...'\n"
            "  (cmd.exe)     set WECHAT_BOT_ID=...  &&  set WECHAT_BOT_SECRET=...\n"
            "  (bash)        export WECHAT_BOT_ID=...  &&  export WECHAT_BOT_SECRET=..."
        )
    return bot_id, secret


def _live_write_enabled(case_id: str = "", run_mode: str = "dry-run") -> tuple[bool, str]:
    """运行模式 + 三 env gate 校验. 任一不满足 → 走 dry-run.

    Gate 1: FAULT_AUTO_SEND_ON_VALIDATED == "1"  (即用户期望 auto-send)
    Gate 2: FAULT_CONFIRM_LIVE_WRITE == "LIVE-WRITE"  (显式确认)
    Gate 3: FAULT_PROD_CASE_TOKEN 非空  (只有人在 bot 启动时显式声明"这次是真生产"才开)
    Gate 4 (defense in depth): case_id 不在 _DENY_CASE_PREFIXES (拦截 agent/测试/demo 前缀)

    教训 (2026-07-10): agent 误写序号 76+77 到飞书表, 是因为 agent 绕过 bot
    直接调 run_closed_loop.py --apply, 且当时只检查了 gate 1+2. 加重 gate 3 防 agent
    默认 dry-run; 加 gate 4 兜底: 即使 agent 配齐 3 个 env, 凡 case_id 是 test/agent/
    demo/e2e/mock/smoke 前缀, 仍走 dry-run, 防止 agent 误伤生产表.
    """
    if run_mode != "live":
        return False, f"run_mode={run_mode} 不是 live"

    gate1 = os.environ.get(_AUTO_SEND_ENV, "").strip() == _AUTO_SEND_ON
    gate2 = os.environ.get(_LIVE_WRITE_GATE_ENV, "").strip() == _LIVE_WRITE_GATE_VALUE
    gate3 = bool(os.environ.get(_PROD_CASE_TOKEN_ENV, "").strip())

    if not (gate1 and gate2 and gate3):
        return False, "双 gate 缺一 (或缺少 FAULT_PROD_CASE_TOKEN)"

    # 第四道闸门: case_id 前缀拦截
    if case_id:
        prefix = case_id.split("_", 1)[0].lower()
        if prefix in _DENY_CASE_PREFIXES:
            return False, f"case_id 前缀 '{prefix}' 在 dry-run-only 白名单里"

    return True, "三 gate 全开 + case_id 不在 deny list"


# ---------------------------------------------------------------------------
# case_id 生成
# ---------------------------------------------------------------------------

def _case_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in prefix).strip("_")
    return f"{safe_prefix or 'wecom'}_{stamp}_{digest}"


# ---------------------------------------------------------------------------
# 群回执 (按用户要求: 6 字段)
# ---------------------------------------------------------------------------

# 状态机 -> 中文
_STATUS_LABEL = {
    "ok": "通过 (含所有产物)",
    "validated": "通过 (报告和台账已生成)",
    "need_review": "需补充 (有缺字段)",
    "error": "失败 (有错误)",
}


def _reply_message(
    *,
    received: bool,
    case_id: str,
    result: dict[str, Any] | None,
    error: str | None = None,
) -> str:
    """构造群回执 (按用户要求 6 字段).

    6 字段:
      1. 是否收到消息
      2. case_id
      3. 校验是否通过
      4. 报告路径
      5. 台账路径
      6. 邮件预览路径
    失败时附: 缺哪些字段

    V13+ Hermes 转发模式:
      - mode=hermes_forward 时, 6 字段改为:
        1) 收到消息
        2) case_id
        3) 已提交 Hermes
        4) run_id
        5) delivery_id
        6) 等待第二轮回执 (Hermes 跑完后会发第二轮: 报告/台账/邮件)
    """
    lines: list[str] = []
    # V13+: Hermes 转发模式特化
    mode = (result or {}).get("mode") if result else None
    if mode == "hermes_forward":
        lines.append(f"1) 收到消息：{'是' if received else '否'}")
        lines.append(f"2) case_id：{case_id or '(无)'}")
        if error:
            lines.append(f"3) 提交 Hermes：失败 — {error}")
            return "\n".join(lines)
        run_id = (result or {}).get("run_id", "(无)")
        delivery_id = (result or {}).get("delivery_id", "(无)")
        autopilot_id = (result or {}).get("autopilot_id", "(无)")
        lines.append("3) 提交 Hermes：成功")
        lines.append(f"4) run_id：{run_id}")
        lines.append(f"5) delivery_id：{delivery_id}")
        lines.append(f"6) autopilot_id：{autopilot_id}")
        lines.append("")
        lines.append("⏳ Hermes 跑完后会发第二轮回执 (报告/台账/邮件预览)")
        return "\n".join(lines)

    lines.append(f"1) 收到消息：{'是' if received else '否'}")
    lines.append(f"2) case_id：{case_id or '(无)'}")

    if error:
        lines.append(f"3) 校验：失败 — {error}")
        return "\n".join(lines)

    if result is None:
        lines.append("3) 校验：未执行")
        return "\n".join(lines)

    status = str(result.get("status", "unknown"))
    label = _STATUS_LABEL.get(status, status)
    lines.append(f"3) 校验：{label}")

    outputs = result.get("outputs") or {}
    report_path = outputs.get("report_docx") or "(未生成)"
    ledger_path = outputs.get("offline_record_workbook") or "(未生成)"
    # pipeline 的 key 是 "mail_preview" (不是 "mail_preview_path")
    mail_path = outputs.get("mail_preview") or outputs.get("mail_preview_path") or outputs.get("mail") or "(未生成)"
    lines.append(f"4) 报告路径：{report_path}")
    lines.append(f"5) 台账路径：{ledger_path}")
    lines.append(f"6) 邮件预览：{mail_path}")

    # 缺字段 / 错误
    missing = result.get("missing_fields") or []
    if missing:
        lines.append("")
        lines.append("缺字段（请补充后重发）：")
        for m in missing:
            lines.append(f"  - {m}")
    prompts = (result.get("missing_field_prompts") or [])
    if prompts:
        for p in prompts[:6]:
            field = p.get("field", "?")
            prompt = p.get("prompt", "")
            lines.append(f"  - {field}: {prompt}")

    errs = result.get("errors") or []
    if errs:
        lines.append("")
        lines.append("错误：")
        for e in errs:
            lines.append(f"  - {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 业务核心: handle_text (不依赖 aibot SDK, 可单测)
# ---------------------------------------------------------------------------

def handle_text(
    content: str,
    *,
    case_prefix: str = "wecom",
    online_record_url: str = "",
    run_mode: str = "dry-run",
) -> dict[str, Any]:
    """处理一条文本: 调 pipeline.run() 并打包结构化结果.

    返回 dict, 包含:
      - received: bool
      - case_id: str
      - live_write_enabled: bool (双 gate 校验结果)
      - result: pipeline.run() 返回值 (成功时)
      - error: str (失败时)
    """
    received = bool(content and content.strip())
    case_id = _case_id(case_prefix, content) if received else ""

    if not received:
        live_write, reason = _live_write_enabled(run_mode=run_mode)
        return {
            "received": False,
            "case_id": "",
            "live_write_enabled": live_write,
            "live_write_reason": reason,
            "result": None,
            "error": "empty_content",
        }

    live_write, reason = _live_write_enabled(case_id, run_mode=run_mode)
    try:
        first = run(
            ROOT,
            RunOptions(text=content, case_id=case_id, incident_context_only=True),
        )
        if first.get("status") != "validated":
            first["mode"] = "pipeline_two_step"
            return {
                "received": True,
                "case_id": case_id,
                "live_write_enabled": live_write,
                "live_write_reason": reason,
                "result": first,
                "error": None,
            }

        context_path = Path(first["outputs"]["incident_context"])
        smtp_user = os.environ.get("FAULT_SMTP_USER", "").strip()
        smtp_host = os.environ.get("FAULT_SMTP_HOST", "smtp.163.com").strip() or "smtp.163.com"
        smtp_port = int(os.environ.get("FAULT_SMTP_PORT", "465").strip() or "465")
        result = run(
            ROOT,
            RunOptions(
                incident_context=context_path,
                all_dry_run=not live_write,
                apply=live_write,
                send_mail=live_write,
                confirm_live_write=_LIVE_WRITE_GATE_VALUE if live_write else "",
                online_record_url=online_record_url,
                smtp_user=smtp_user,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                mail_from=smtp_user,
                automatic=live_write,
            ),
        )
        result["mode"] = "pipeline_two_step"
        return {
            "received": True,
            "case_id": case_id,
            "live_write_enabled": live_write,
            "live_write_reason": reason,
            "result": result,
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - live bot boundary
        return {
            "received": True,
            "case_id": case_id,
            "mode": "pipeline_two_step",
            "live_write_enabled": live_write,
            "live_write_reason": reason,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _handle_text_async(
    content: str,
    *,
    case_prefix: str = "wecom",
    online_record_url: str = "",
    run_mode: str = "dry-run",
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _handle_text_serialized,
        content,
        case_prefix=case_prefix,
        online_record_url=online_record_url,
        run_mode=run_mode,
    )


def _handle_text_serialized(content: str, **kwargs: Any) -> dict[str, Any]:
    # ponytail: one process-wide lock closes the dedupe/create race; split only if throughput requires it.
    with _HANDLE_TEXT_LOCK:
        return handle_text(content, **kwargs)


# ---------------------------------------------------------------------------
# WeCom aibot SDK 集成 (生产路径)
# ---------------------------------------------------------------------------

def _load_sdk() -> tuple[Any, Any, Any]:
    try:
        from aibot import WSClient, WSClientOptions, generate_req_id
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: install with `python -m pip install -r requirements-wecom.txt`"
        ) from exc
    return WSClient, WSClientOptions, generate_req_id


async def _recover_stalled_websocket(client: Any) -> bool:
    """补 SDK 1.0.1 正常关闭/异常退出后接收循环不再重连的缺口。"""
    # ponytail: SDK 暂无公开健康状态；升级 SDK 后若提供 public API，可删掉这层私有字段兼容。
    manager = getattr(client, "_ws_manager", None)
    receive_task = getattr(manager, "_receive_task", None)
    if (
        manager is None
        or receive_task is None
        or not receive_task.done()
        or getattr(manager, "_is_manual_close", False)
    ):
        return False
    try:
        error = receive_task.exception()
    except asyncio.CancelledError:
        return False
    print(json.dumps({
        "event": "websocket_receive_loop_stopped",
        "reason": repr(error) if error else "normal_close",
        "action": "reconnect",
    }, ensure_ascii=False), file=sys.stderr, flush=True)
    await manager.connect()
    return True


async def _run_ws_client(client: Any) -> None:
    async def watchdog() -> None:
        while True:
            await asyncio.sleep(_WS_WATCH_INTERVAL_SECONDS)
            await _recover_stalled_websocket(client)

    watcher = asyncio.create_task(watchdog())
    try:
        await client.connect()
        await asyncio.Event().wait()
    finally:
        watcher.cancel()


def _shorten(text: str, max_chars: int = 1800) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "\n..."


async def _reply_text(client: Any, frame: dict[str, Any], generate_req_id: Any, text: str) -> None:
    stream_id = generate_req_id("fault")
    await client.reply_stream(frame, stream_id, _shorten(text), True)


def _completion_reply(outcome: dict[str, Any]) -> str:
    result = outcome.get("result") or {}
    mail_result_path = (result.get("outputs") or {}).get("mail_send_result")
    if mail_result_path:
        try:
            mail_result = json.loads(Path(mail_result_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            mail_result = {}
        if mail_result.get("status") == "已发送":
            return "邮件已发送"
    reasons = [outcome.get("error"), *(result.get("errors") or []), *(result.get("missing_fields") or [])]
    reason = next((str(item) for item in reasons if item), "未确认邮件发送成功")
    return f"处理未完成：{reason}"


def _outcome_reply(outcome: dict[str, Any], *, run_mode: str) -> str:
    if run_mode == "live":
        return _completion_reply(outcome)
    reply = _reply_message(
        received=bool(outcome.get("received")),
        case_id=str(outcome.get("case_id") or ""),
        result=outcome.get("result"),
        error=outcome.get("error"),
    )
    return reply


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WeCom AI bot <-> fault closed-loop V1 bridge (Windows local duty version)")
    parser.add_argument(
        "--run-mode",
        choices=("probe", "dry-run", "live"),
        default=os.environ.get("FAULT_WECOM_RUN_MODE", "dry-run"),  # 2026-07-10: 改默认 dry-run (用户误启 probe 看不到报告)
        help="probe: 收到消息后只回 'bot 在线' 探活 (供首次部署时验证连接); dry-run: 跑 pipeline 生成产物但不真写 (默认); live: 真写飞书 + 发邮件 (需 3 env gate 全开 + --run-mode live)",
    )
    parser.add_argument(
        "--case-prefix",
        default="wecom",
        help="case_id 前缀 (默认 wecom)",
    )
    parser.add_argument(
        "--online-record-url",
        default=os.environ.get("FAULT_ONLINE_RECORD_URL", ""),
        help="在线表 URL (写入邮件预览, 默认空)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="mock 模式: 不连 WeCom WebSocket, 直接跑一条假消息 (用于本地端到端测试)",
    )
    parser.add_argument(
        "--mock-text",
        default=None,
        help="mock 模式下注入的故障文本 (默认: 内置 BOMC 例子)",
    )
    return parser.parse_args()


def _run_mock(args: argparse.Namespace) -> int:
    """Mock 模式: 不连 WebSocket, 走完整 handle_text + 回执构造, 打印到 stdout."""
    text = args.mock_text or (
        "BOMC-cas 服务异常\n"
        "发生时间: 2026-07-10 14:23\n"
        "影响范围: 客户无法登录\n"
        "恢复手段: 重启 cas 服务后恢复\n"
        "恢复时间: 2026-07-10 14:33\n"
        "处理人: 梁铭图"
    )

    print("=" * 60)
    print("MOCK 模式: 模拟收到一条 @机器人 消息")
    print("=" * 60)
    print(f"输入文本:\n{text}\n")

    outcome = handle_text(
        text,
        case_prefix=args.case_prefix,
        online_record_url=args.online_record_url,
        run_mode="dry-run",
    )

    reply = _reply_message(
        received=outcome["received"],
        case_id=outcome["case_id"],
        result=outcome.get("result") or outcome,  # V13+ hermes_forward 模式: outcome 本身就是 result
        error=outcome["error"],
    )
    print("=" * 60)
    print("群回执 (bot 会发到群里的内容):")
    print("=" * 60)
    print(reply)
    print()
    print("=" * 60)
    print("内部状态 (供 debug, 不发到群):")
    print("=" * 60)
    # V13+ hermes_forward 模式 vs 老模式不同字段
    is_hermes = (outcome.get("mode") == "hermes_forward")
    debug_obj = {
        "case_id": outcome["case_id"],
        "mode": outcome.get("mode"),
        "live_write_enabled": outcome.get("live_write_enabled", False),
        "live_write_reason": outcome.get("live_write_reason"),
        "received": outcome["received"],
        "error": outcome["error"],
    }
    if is_hermes:
        debug_obj["run_id"] = outcome.get("run_id")
        debug_obj["delivery_id"] = outcome.get("delivery_id")
        debug_obj["autopilot_id"] = outcome.get("autopilot_id")
        debug_obj["status"] = outcome.get("status")
    else:
        debug_obj["result_status"] = (outcome.get("result") or {}).get("status")
        debug_obj["result_outputs"] = (outcome.get("result") or {}).get("outputs")
    print(json.dumps(debug_obj, ensure_ascii=False, indent=2))

    # mock 模式退出码: 收到+成功=0, 失败=1
    return 0 if outcome["received"] and outcome["error"] is None else 1


def main() -> int:
    args = _parse_args()

    # mock 模式: 跳过 SDK + 凭据
    if args.mock:
        return _run_mock(args)

    # 生产路径: 校验凭据, 连 WebSocket
    WSClient, WSClientOptions, generate_req_id = _load_sdk()
    bot_id, secret = _bot_credentials_or_die()
    live_write, reason = _live_write_enabled(run_mode=args.run_mode)
    print(json.dumps({
        "event": "bot_start",
        "bot_id_mask": bot_id[:4] + "***" + bot_id[-4:] if len(bot_id) >= 8 else "***",
        "run_mode": args.run_mode,
        "live_write_enabled": live_write,
        "live_write_reason": reason,
    }, ensure_ascii=False), flush=True)

    client = WSClient(WSClientOptions(
        bot_id=bot_id,
        secret=secret,
        max_reconnect_attempts=-1,
    ))

    @client.on("connected")
    def _on_connected() -> None:
        print("WeCom bot websocket connected", flush=True)

    @client.on("authenticated")
    def _on_authenticated() -> None:
        print("WeCom bot authenticated", flush=True)

    @client.on("error")
    def _on_error(error: Exception) -> None:
        print(f"WeCom bot error: {error}", file=sys.stderr, flush=True)

    @client.on("message.text")
    async def _on_text(frame: dict[str, Any]) -> None:
        content = str(frame.get("body", {}).get("text", {}).get("content") or "").strip()

        if args.run_mode == "probe":
            await _reply_text(
                client, frame, generate_req_id,
                "连接正常。启动参数改为 --run-mode dry-run 后会处理故障信息。",
            )
            return

        await _reply_text(client, frame, generate_req_id, "收到")

        # 调业务核心
        outcome = await _handle_text_async(
            content,
            case_prefix=args.case_prefix,
            online_record_url=args.online_record_url,
            run_mode=args.run_mode,
        )

        reply = _outcome_reply(outcome, run_mode=args.run_mode)

        # 日志侧 (结构化, 不暴露凭据)
        print(json.dumps({
            "event": "fault_handled",
            "case_id": outcome["case_id"],
            "live_write_enabled": outcome["live_write_enabled"],
            "received": outcome["received"],
            "error": outcome["error"],
            "result_status": (outcome["result"] or {}).get("status"),
        }, ensure_ascii=False), flush=True)

        # 群回执
        await _reply_text(client, frame, generate_req_id, reply)

    asyncio.run(_run_ws_client(client))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
