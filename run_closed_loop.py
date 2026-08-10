from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from src.orchestrator.pipeline import DEFAULT_MAIL_TO, DEFAULT_ONLINE_RECORD_URL, RunOptions, run


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the rewritten minimal fault closed loop")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="fault text pasted from chat or incident notes")
    source.add_argument("--input-file", type=Path, help="UTF-8 text file containing fault text")
    source.add_argument("--incident-context", type=Path, help="existing incident_context.json")
    parser.add_argument("--case-id", default="", help="stable case id; default is a hash of the input text")
    parser.add_argument("--screenshot", action="append", type=Path, default=[], help="fault screenshot path; may be repeated; optional .ocr.txt sidecar is used as OCR text")
    parser.add_argument("--log", action="append", type=Path, default=[], help="fault log path; may be repeated")
    parser.add_argument("--attachment", action="append", type=Path, default=[], help="supporting attachment path; may be repeated")
    parser.add_argument("--incident-context-only", action="store_true", help="build and validate incident_context.json, then stop")
    parser.add_argument("--feishu-dry-run", action="store_true", help="write a Feishu field preview JSON without API calls")
    parser.add_argument("--feishu-apply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--apply", action="store_true", help="enable live writes; requires --confirm-live-write LIVE-WRITE")
    parser.add_argument("--confirm-live-write", default="", help="must be LIVE-WRITE for any live write")
    parser.add_argument("--feishu-record-id", default="", help="existing Feishu record id to backfill instead of creating a new record")
    parser.add_argument("--feishu-config", type=Path, default=None, help="private Feishu config JSON path")
    parser.add_argument("--mail-dry-run", action="store_true", help="write a mail preview JSON without SMTP")
    parser.add_argument("--manual-dry-run", action="store_true", help="write a manual-match preview JSON without API calls")
    parser.add_argument("--all-dry-run", action="store_true", help="write all dry-run previews")
    parser.add_argument("--online-record-url", default="", help=f"online fault-record URL; default: {DEFAULT_ONLINE_RECORD_URL}")
    parser.add_argument("--send-mail", action="store_true", help="send the generated report email inside a confirmed --apply run")
    parser.add_argument("--confirm-send", default="", help=argparse.SUPPRESS)
    parser.add_argument("--mail-to", action="append", default=[], help=f"reserved; current recipient is fixed to {', '.join(DEFAULT_MAIL_TO)}")
    parser.add_argument("--mail-cc", action="append", default=[], help="reserved; CC is resolved from the system owner contact mapping")
    parser.add_argument("--mail-from", default="", help="report mail sender")
    parser.add_argument("--smtp-host", default="smtp.163.com", help="SMTP server host")
    parser.add_argument("--smtp-port", type=int, default=465, help="SMTP server port")
    parser.add_argument("--smtp-user", default=os.environ.get("FAULT_SMTP_USER", ""), help="SMTP login username; falls back to env FAULT_SMTP_USER")
    parser.add_argument("--smtp-password-env", default="FAULT_SMTP_PASSWORD", help="environment variable containing SMTP password")
    args = parser.parse_args()
    if args.feishu_apply:
        raise SystemExit("--feishu-apply is retired; use --apply --confirm-live-write LIVE-WRITE")
    if args.confirm_send:
        raise SystemExit("--confirm-send is retired; use --apply --confirm-live-write LIVE-WRITE")

    # FAULT_AUTO_SEND_ON_VALIDATED: 当且仅当 incident_context 验证通过后,
    # 把"显式确认 LIVE-WRITE + apply + send-mail"自动注入到本次调用。
    # SKILL.md 第 253-259 行契约:
    #   - plain `--text ... --case-id ...` 第一步只能生成 incident_context,
    #     不允许直接进 live (会报 "live writes require --incident-context")
    #   - 第二步传 `--incident-context work/cases/<case>/incident_context.json`
    #     时, 验证已通过的 context 才触发 live+mail 注入
    #   - 显式传 --incident-context-only / --all-dry-run / --mail-dry-run /
    #     --feishu-dry-run / --manual-dry-run 的不重写, 尊重用户意图
    auto_send_env = os.environ.get("FAULT_AUTO_SEND_ON_VALIDATED", "").strip() == "1"
    auto_live_confirmed = os.environ.get("FAULT_CONFIRM_LIVE_WRITE", "").strip() == "LIVE-WRITE"
    prod_case_token = os.environ.get("FAULT_PROD_CASE_TOKEN", "").strip()
    auto_send_injected = False
    explicit_dry_run = (
        args.incident_context_only
        or args.all_dry_run
        or args.mail_dry_run
        or args.feishu_dry_run
        or args.manual_dry_run
    )
    if auto_send_env and not explicit_dry_run and not args.apply and not args.send_mail:
        if args.incident_context:
            if auto_live_confirmed and prod_case_token:
                args.apply = True
                args.send_mail = True
                args.confirm_live_write = "LIVE-WRITE"
                auto_send_injected = True
                print("[auto-send] 三闸门已确认 + --incident-context → 注入 --apply --send-mail --confirm-live-write LIVE-WRITE", file=sys.stderr)
            else:
                print("[auto-send] 阻断自动 live：还需 FAULT_CONFIRM_LIVE_WRITE=LIVE-WRITE 且 FAULT_PROD_CASE_TOKEN 非空；保持原参数，不执行 live。", file=sys.stderr)
        else:
            print("[auto-send] FAULT_AUTO_SEND_ON_VALIDATED=1 但本次只有 --text/--screenshot/--input-file (无 --incident-context); 按 SKILL 第 259 行契约, 不会自动 live 写入。先跑 --incident-context-only 生成 context, 第二轮再传 --incident-context。", file=sys.stderr)

    # CASE_ID_DEDUP_HOOK (2026-07-09 用户偏好):
    # 当 apply=True (或被 auto-send 注入) 且没显式传 --feishu-record-id 时,
    # 查飞书表里 case_id 是否已存在 record, 存在则自动注入 record_id
    # 走 update 路径, 避免重复建 record + 重发邮件
    # 只在有 case_id 且要真发时才查重 (避免 --incident-context-only 阶段不必要调 API)
    effective_apply = args.apply or args.send_mail
    effective_case_id = args.case_id or (args.incident_context and Path(args.incident_context).parent.name)
    if effective_apply and not args.feishu_record_id and effective_case_id and not explicit_dry_run:
        try:
            # 复用 feishu client 查飞书表
            sys.path.insert(0, str(ROOT))
            from src.feishu_ledger.client import find_record_by_case_id, load_feishu_config
            config = load_feishu_config(ROOT, args.feishu_config)
            existing = find_record_by_case_id(effective_case_id, config)
            if existing.get("blocked_reason"):
                print(f"[dedup] 阻断自动选记录: {existing['blocked_reason']}", file=sys.stderr)
            elif existing.get("record_id"):
                args.feishu_record_id = str(existing["record_id"])
                existing_mail_status = str(existing.get("fields", {}).get("邮件闭环状态", "(空)"))
                print(f"[dedup] case_id={effective_case_id} 已在飞书表里 (record_id={args.feishu_record_id}, 邮件闭环状态={existing_mail_status}); 自动注入 --feishu-record-id 走 update 路径, 不会新建 record。", file=sys.stderr)
                if auto_send_injected and existing_mail_status.strip() == "已发送":
                    args.send_mail = False
                    print("[auto-send] 该 case 邮件闭环状态已发送；保留 Feishu update，跳过重复发信。", file=sys.stderr)
            else:
                print(f"[dedup] case_id={effective_case_id} 在飞书表里未找到, 本次按 create 走。", file=sys.stderr)
        except Exception as exc:
            # pipeline 会再次查重并 fail-closed；入口层不自行猜测目标记录。
            print(f"[dedup] 入口查重失败 ({exc!r}); 交给 pipeline 再次查重。", file=sys.stderr)

    result = run(
        ROOT,
        RunOptions(
            text=args.text or "",
            input_file=args.input_file,
            incident_context=args.incident_context,
            case_id=args.case_id,
            screenshots=args.screenshot,
            logs=args.log,
            attachments=args.attachment,
            incident_context_only=args.incident_context_only,
            feishu_dry_run=args.feishu_dry_run,
            apply=args.apply,
            confirm_live_write=args.confirm_live_write,
            feishu_record_id=args.feishu_record_id,
            feishu_config=args.feishu_config,
            mail_dry_run=args.mail_dry_run,
            manual_dry_run=args.manual_dry_run,
            all_dry_run=args.all_dry_run,
            online_record_url=args.online_record_url,
            send_mail=args.send_mail,
            mail_to=args.mail_to,
            mail_cc=args.mail_cc,  # BUGFIX36 (2026-07-13): 项目小组成员抄送
            mail_from=args.mail_from,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_password_env=args.smtp_password_env,
            automatic=auto_send_injected,
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
