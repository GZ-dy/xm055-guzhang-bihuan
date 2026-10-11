"""
post_to_hermes_webhook.py
2026-07-10 V12+: 本地 bot 调 Hermes/Multica Webhook 触发故障闭环

用法:
  # 1. 触发 + 等结果 (默认 120s timeout)
  python post_to_hermes_webhook.py --text "系统: BOMC ..." --case-id wecom_xxx

  # 2. 只触发不等待 (返回 run_id 给 bot 立刻回执)
  python post_to_hermes_webhook.py --text "..." --no-wait

  # 3. 拿已有 run_id 的结果
  python post_to_hermes_webhook.py --get-run <run_id> --autopilot-id ce844cd4-1351-4f5b-8363-7413defac45a

凭据:
  - WEBHOOK_URL 在 ~/.hermes/.env.multica (chmod 600) 存
  - 不传 URL 时用默认 autopilot (故障闭环 V12)
  - 不传 token 时不带 Authorization (Generic webhook 无 secret)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# 故障闭环 V12 默认 autopilot + webhook
DEFAULT_AUTOPILOT_ID = "ce844cd4-1351-4f5b-8363-7413defac45a"
DEFAULT_WEBHOOK_URL = "http://192.168.30.22:8080/api/webhooks/autopilots/awt_uTPMTDxZuQC7L17qaWIvoVLCW5D6FCzQp5JoviC6npQ"
DEFAULT_MULTICA_SERVER = "http://192.168.30.22:8080"

ENV_FILE = os.path.expanduser("~/.hermes/.env.multica")


def load_env():
    """Load ~/.hermes/.env.multica (KEY=VALUE per line, # comment)"""
    env = {}
    if not os.path.isfile(ENV_FILE):
        return env
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def http_post_json(url: str, payload: dict, timeout: int = 10) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def multica_get(path: str, token: str, timeout: int = 10) -> dict:
    """调 multica server 拿数据 (需要 token)"""
    url = f"{DEFAULT_MULTICA_SERVER}{path}"
    req = urllib.request.Request(url, method="GET",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_post(args):
    payload = {
        "case_id": args.case_id or f"wecom_{time.strftime('%Y%m%d_%H%M%S')}_bot",
        "text": args.text,
        "system": args.system or "BOMC",
        "mode": "dry-run" if args.dry_run else "live",
        "source": "wecom_bot_local",
        "test": args.test,
    }
    print(f"[POST] -> {args.webhook_url}", file=sys.stderr)
    print(f"[PAYLOAD] {json.dumps(payload, ensure_ascii=False)[:200]}", file=sys.stderr)
    resp = http_post_json(args.webhook_url, payload, timeout=15)
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    if args.no_wait:
        return 0
    # 等结果
    run_id = resp.get("run_id")
    if not run_id:
        print("No run_id in response", file=sys.stderr)
        return 1
    print(f"\n[WAIT] run_id={run_id}, timeout={args.timeout}s ...", file=sys.stderr)
    deadline = time.time() + args.timeout
    last_status = None
    while time.time() < deadline:
        time.sleep(args.poll_interval)
        # 用 multica CLI 查结果
        import subprocess
        r = subprocess.run(
            ["multica", "autopilot", "runs", args.autopilot_id, "--output", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            continue
        runs = json.loads(r.stdout).get("runs", [])
        for run in runs:
            if run.get("id") == run_id:
                status = run.get("status")
                if status != last_status:
                    print(f"  [STATUS] {status}  ({int(time.time())%1000})", file=sys.stderr)
                    last_status = status
                if status in ("succeeded", "failed", "completed"):
                    print(json.dumps(run, ensure_ascii=False, indent=2))
                    return 0 if status == "succeeded" else 2
                break
    print(f"[TIMEOUT] run {run_id} still not done after {args.timeout}s", file=sys.stderr)
    return 3


def cmd_get_run(args):
    """查已有 run 的结果"""
    import subprocess
    r = subprocess.run(
        ["multica", "autopilot", "runs", args.autopilot_id, "--output", "json"],
        capture_output=True, text=True, timeout=15,
    )
    runs = json.loads(r.stdout).get("runs", [])
    for run in runs:
        if run.get("id") == args.run_id:
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return 0
    print(f"run_id {args.run_id} not found in autopilot {args.autopilot_id}", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description="POST to Hermes/Multica webhook + poll result")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("post", help="触发 webhook (默认等结果)")
    p1.add_argument("--text", required=True, help="故障描述文本 (BOMC 6 段格式)")
    p1.add_argument("--case-id", default="", help="case_id (默认自动生成 wecom_YYYYMMDD_HHMMSS_bot)")
    p1.add_argument("--system", default="BOMC")
    p1.add_argument("--webhook-url", default=DEFAULT_WEBHOOK_URL)
    p1.add_argument("--autopilot-id", default=DEFAULT_AUTOPILOT_ID)
    p1.add_argument("--no-wait", action="store_true", help="不轮询结果, 立刻返回 run_id")
    p1.add_argument("--timeout", type=int, default=180, help="轮询超时 (s)")
    p1.add_argument("--poll-interval", type=int, default=10, help="轮询间隔 (s)")
    p1.add_argument("--test", action="store_true", help="标记为测试")
    p1.add_argument("--dry-run", action="store_true", default=True, help="dry-run 模式 (默认)")
    p1.add_argument("--live", dest="dry_run", action="store_false", help="live 模式 (需 3 gate)")
    p1.set_defaults(func=cmd_post)

    p2 = sub.add_parser("get-run", help="查已有 run 的结果")
    p2.add_argument("--autopilot-id", default=DEFAULT_AUTOPILOT_ID)
    p2.add_argument("--run-id", required=True)
    p2.set_defaults(func=cmd_get_run)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()