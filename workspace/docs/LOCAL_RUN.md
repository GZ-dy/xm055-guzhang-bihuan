# Windows 本地值班版运行说明 (企业微信智能机器人 + 故障闭环 V1)

> 适用范围: Windows 10/11 + PowerShell 5.1+ 或 cmd.exe
> 项目根: `搭建故障闭环系统\`
> 入口: `start_wecom_ai_bot_dry_run.ps1` 或 `.bat`
> 默认模式: **dry-run** (不真发飞书/邮件, 只生成本地产物)

## 1. 三句话原理

```
你 @机器人 发故障信息
  -> 本地 bot 脚本通过企业微信 WebSocket 收消息
  -> 调 run_closed_loop.py / pipeline.run() (默认 dry-run)
  -> 生成 incident_context + 报告 + 台账 + 邮件预览
  -> 把 6 字段处理结果回复到群里
```

## 2. 凭据规则 (铁律, 不动)

| 凭据 | 来源 | 写在哪儿 |
|---|---|---|
| `WECHAT_BOT_ID` | 企业微信后台 → 智能机器人 → BotID | **只在你的 PowerShell `export`** |
| `WECHAT_BOT_SECRET` | 企业微信后台 → 智能机器人 → Secret | **只在你的 PowerShell `export`** |
| 飞书 app_secret | `config\private\feishu.json` | 文件权限 600, 不入仓 |
| `FAULT_SMTP_PASSWORD` | 163 邮箱授权码 | **只在你的 PowerShell `export`** |

**严禁**:
- ❌ 把任何凭据贴到 chat / issue 评论 / commit message
- ❌ 把凭据写进代码 / 文档 / 配置文件
- ❌ 把凭据 echo 到日志 (bot 脚本已做脱敏)

## 3. 一次性环境准备

### 3.1 装 Python 3.11+ (已经装好可跳过)

```powershell
# PowerShell (管理员) - 用 winget 装 Python 3.11
winget install -e --id Python.Python.3.11

# 验证
python --version
```

### 3.2 装项目依赖

```powershell
# PowerShell
cd <项目根>\搭建故障闭环系统

# 创建项目根目录 .venv，并安装业务依赖和企业微信 bot SDK
.\install_env.ps1
```

后续 `validate_package.ps1` 和 `run_local_demo.ps1` 固定使用项目 `.venv`，无需手工激活，也不会读取系统 Python 的包状态。

### 3.3 准备飞书配置 (可选, dry-run 不需要)

dry-run 不读取飞书。配置字段使用 `app_id`、`app_secret`、`app_token`、`table_id`。

```powershell
# 1. 复制脱敏模板
copy config\private\feishu.template.json config\private\feishu.json

# 2. 用记事本编辑 feishu.json, 把 REPLACE_ME 替换成真实值
notepad config\private\feishu.json

# 3. 文件权限 (本机已 OK, 共享盘需注意)
icacls config\private\feishu.json /inheritance:r /grant:r "%USERNAME%:R"
```

## 4. 启动 bot (分两模式)

### 4.1 Mock 模式 (无真凭据, 端到端测试用)

**用途**: 不连 WeCom WebSocket, 跑内置 BOMC 例子验证管道

```powershell
cd <项目根>\搭建故障闭环系统
.\start_wecom_ai_bot_dry_run.bat --mock
```

**输出**:
```
1) 收到消息：是
2) case_id：wecom_20260710_xxxxxx_xxxxxxxx
3) 校验：通过 (含所有产物)
4) 报告路径：...\outputs\reports\<case_id>\report.docx
5) 台账路径：...\outputs\ledger\<case_id>\ledger.xlsx
6) 邮件预览：...\outputs\mail\<case_id>\mail_preview.txt
```

### 4.2 真实模式 (连 WeCom, dry-run)

**步骤**:
```powershell
# 1. 临时设凭据 (本 PowerShell 窗口有效)
$env:WECHAT_BOT_ID='<your_bot_id>'
$env:WECHAT_BOT_SECRET='<your_bot_secret>'

# 2. 启动 bot (默认 dry-run, 不真发飞书/邮件)
.\start_wecom_ai_bot_dry_run.bat
```

**输出**:
```
[OK] 凭据完整:
     WECHAT_BOT_ID     = aibz***ePaI (长度 35)
     WECHAT_BOT_SECRET = *** (长度 47)
[OK] dry-run 模式 (三 gate 未全开):
     FAULT_AUTO_SEND_ON_VALIDATED = '' (需 '1')
     FAULT_CONFIRM_LIVE_WRITE    = '' (需 'LIVE-WRITE')
     FAULT_PROD_CASE_TOKEN        = '' (需非空字符串)
```

### 4.3 真实写飞书/邮件 (双/三 gate 全开 + 真实 case)

**铁律 (2026-07-10)**: 用户 @机器人发的是**真实故障数据**, agent 测试数据**不许**进飞书表. bot 设计了 3 道闸门, 必须全开才真发:

| # | env | 值 | 作用 |
|---|---|---|---|
| gate 1 | `FAULT_AUTO_SEND_ON_VALIDATED` | `1` | 用户期望 auto-send |
| gate 2 | `FAULT_CONFIRM_LIVE_WRITE` | `LIVE-WRITE` | 显式确认 live 写入 |
| gate 3 | `FAULT_PROD_CASE_TOKEN` | 任意非空字符串 | 真生产 case 显式声明 (防 agent 误伤) |
| gate 4 | case_id 前缀 | 非 test/agent/demo/e2e/mock/smoke | 拦截 agent 测试 case (defense in depth) |

三环境门只控制自动注入；人工显式 CLI 仍按 `--incident-context --apply --confirm-live-write LIVE-WRITE [--send-mail]` 执行。自动模式查重命中且`邮件闭环状态=已发送`时只保留飞书 update,不重复发邮件。

**连接企业微信但保持 dry-run**:
```powershell
$env:WECHAT_BOT_ID='<bot_id>'
$env:WECHAT_BOT_SECRET='REDACTED_BACKUP_SECRET'

.\start_wecom_ai_bot_dry_run.bat
```

**禁止**: 即使配齐 3 个 env, case_id 若是 `test_*` / `agent_*` / `demo_*` / `e2e_*` / `mock_*` / `smoke_*` 前缀, bot 仍走 dry-run, 不真发.
[启动] wecom_fault_bot.py --run-mode dry-run
WeCom bot websocket connected
WeCom bot authenticated
```

**群里 @机器人发一条**:
```
@故障助手 BOMC-cas服务异常
时间: 2026-07-10 14:23
影响: 客户登录失败
处理: 重启 cas 服务
```

**bot 在群里自动回复**:
```
1) 收到消息：是
2) case_id：wecom_20260710_xxxxxx_xxxxxxxx
3) 校验：需补充 (有缺字段)
4) 报告路径：(未生成)
5) 台账路径：(未生成)
6) 邮件预览：(未生成)

缺字段（请补充后重发）：
  - incident.duration_minutes
  - handling.closure_info
```

### 4.3 真实模式 + 真发飞书/邮件 (高级)

⚠️ **副作用**: 真发飞书 record + 真发邮件 (带 LIVE-WRITE)

```powershell
# 1. 凭据
$env:WECHAT_BOT_ID='<bot_id>'
$env:WECHAT_BOT_SECRET='REDACTED_BACKUP_SECRET'

# 2. 三环境门必设
$env:FAULT_AUTO_SEND_ON_VALIDATED='1'
$env:FAULT_CONFIRM_LIVE_WRITE='LIVE-WRITE'
$env:FAULT_PROD_CASE_TOKEN='REAL-2026'

# 3. SMTP 凭据 (发邮件用)
$env:FAULT_SMTP_USER='<your@163.com>'
$env:FAULT_SMTP_PASSWORD='REDACTED_BACKUP_SECRET'

# 4. 启动 live 入口；执行策略受限时也可运行
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_wecom_ai_bot_live.ps1
```

**三环境门缺失时**:
- 缺 `FAULT_AUTO_SEND_ON_VALIDATED=1` -> 不真发, 走 dry-run
- 缺 `FAULT_CONFIRM_LIVE_WRITE=LIVE-WRITE` -> 不真发, 走 dry-run
- 缺 `FAULT_PROD_CASE_TOKEN` -> 不真发, 走 dry-run
- 三个都设且使用 live 启动器 -> 真发 (要慎重)

## 5. 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| `[X] 错误: WECHAT_BOT_ID 未设置` | 没设 env | `$env:WECHAT_BOT_ID='...'` |
| `WeCom bot error: Authentication failed: 853000` | bot_id/secret 错或过期 | 企业微信后台重置 secret |
| `Missing dependency: install with ...` | 依赖没装 | `python -m pip install -r requirements-wecom.txt` |
| 群里发消息 bot 不响应 | bot 没启动 / 网络问题 | 看 PowerShell 窗口日志 |
| 群里发消息 bot 回了"缺字段" | 故障信息太简略 | 按缺字段列表补充 |
| 双 gate 开了还是 dry-run | env 窗口不对 | 确认 `set` 跟启动在同一个 PowerShell 窗口 |

## 6. 关掉 bot

PowerShell 窗口按 `Ctrl+C` —— 脚本会优雅退出。

## 7. 跟 run_closed_loop.py 关系

bot 内部调 `pipeline.run()` 跟 `python run_closed_loop.py --text "..."` 是一回事——**只是入口不同**:

| 入口 | 触发 | 适用 |
|---|---|---|
| `run_closed_loop.py --text "..."` | 命令行手动 | 测试/单次跑 |
| `wecom_fault_bot.py` | 群里 @机器人 | 值班期间持续接消息 |

**两个入口**结果**完全一样**——都走 `src/orchestrator/pipeline.py:run()`。

## 8. 不要做的事

- ❌ 不要把 bot_secret 写进 `start_wecom_ai_bot_dry_run.ps1` (即使是脱敏 placeholder 也不要)
- ❌ 不要把 `feishu.json` 提交到 git (`config/private/` 已在 `.gitignore` 内)
- ❌ 不要用 `--Mock` 模式时还设双 env gate (mock 不读双 gate, 但会困惑)
- ❌ 不要在生产环境用 `--Mock` 模式启动 (它只跑内置例子, 不收消息)

## 9. 测试

```powershell
# 跑全部测试 (业务 + bot)
python -B -m unittest discover -s tests -v

# 只跑 bot 本地 mock 测试
python -B -m unittest tests.test_wecom_fault_bot_local_mock -v

# 只跑 app callback 测试
python -B -m unittest tests.test_wecom_app_callback -v
```

**预期**: 当前至少 72 个测试全过；测试数量以 `unittest discover` 的实际输出为准。
