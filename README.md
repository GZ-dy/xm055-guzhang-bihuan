# 故障闭环系统 V1

> 面向 Windows 运维团队的本地故障收尾自动化工具：把一次已经处理过的用户报障整理为统一事实记录，并生成 Word 故障报告、Excel 台账、处置手册匹配和邮件预览；只有显式通过生产安全闸门后，才会写入飞书或发送正式邮件。

`Windows 10/11 x64` · `PowerShell 5.1+` · `Python 3.10+` · `CLI 默认纯本地 dry-run`

[授权用户下载](#已获权限下载并安全试用) · [查看生成结果](#4-查看生成结果) · [手动处理故障](#手动处理一条故障) · [生产安全闸门](#生产写入安全闸门) · [交接操作手册](docs/HANDOVER_OPERATIONS.md)

## 已获权限：下载并安全试用

本私有仓库不只用于浏览源码。已获授权的同事不需要会 Git，也不需要预先安装 Python；下载已验收的 Windows 便携包后，可以直接安装本地依赖、运行安全 Demo，并查看生成的 Word 报告、Excel 台账、手册匹配和邮件预览。

| 使用目的 | 下载后是否可用 | 入口 | 外部影响 |
|---|---:|---|---|
| 看效果、验收本地产物 | 是 | `START_LOCAL_DEMO.cmd` | 不连接业务系统 |
| 用自己的故障文字生成本地产物 | 是 | `run_closed_loop.py --all-dry-run` | 不连接业务系统 |
| 连接企业微信但保持业务 dry-run | 需本机凭据 | `start_wecom_ai_bot_dry_run.bat` | 会连接并回复企业微信 |
| 正式写飞书、发邮件、设置自启动 | 不能直接启用 | 按交接手册执行 | 有生产写入 |

### 1. 下载正式交付包

1. 使用已被授权的 GitHub 账号登录；如刚收到邀请，先接受仓库邀请。
2. 打开 [最新版本下载页](https://github.com/GZ-dy/duoyu-fault-closed-loop/releases/latest)。
3. 在 **Assets** 中下载：
   - `duoyu-fault-closed-loop-v0.2.0-windows-online.zip`
   - `duoyu-fault-closed-loop-v0.2.0-windows-online.zip.sha256`

> [!IMPORTANT]
> 请下载 Assets 中以 `duoyu-fault-closed-loop-` 开头的 Windows 便携包。不要把 GitHub 自动生成的 `Source code (zip)` 或 `Source code (tar.gz)` 当作正式交付包。

如果看不到 Release 或下载失败，先确认当前 GitHub 账号已获得本私有仓库的读取权限。请勿把下载地址或包转发给未授权人员。

### 2. 校验并完整解压

将 ZIP 和 `.sha256` 文件放在同一目录，在该目录打开 PowerShell，执行：

```powershell
$zip = ".\duoyu-fault-closed-loop-v0.2.0-windows-online.zip"
$expected = ((Get-Content "$zip.sha256" -Raw).Trim() -split "\s+")[0]
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "SHA-256 校验失败，请重新下载" }
"SHA-256 校验通过：$actual"
```

右键 ZIP 选择“全部解压”，建议解压到本地普通目录，例如 `%USERPROFILE%\duoyu-fault-closed-loop`。不要在 ZIP 预览窗口、网盘同步目录或网络共享目录中直接运行。

### 3. 双击安全体验入口

在解压后的根目录双击：

```text
START_LOCAL_DEMO.cmd
```

该入口会自动检查 Python 3.10+；没有 Python 时，安装器会优先用 `winget` 安装 Python 3.13 x64，失败时再从 Python 官方地址下载并校验安装包；随后在当前目录创建 `.venv`、安装依赖、运行当前测试集和本地 Demo。首次安装需要连网，但不会连接企业微信、飞书或 SMTP。

看到以下结果即表示本地试用通过：

```text
[OK] offline demo validated with five artifact groups
[OK] package validation passed
[DONE] Safe local demo passed.
```

### 4. 查看生成结果

| 生成内容 | 目录 |
|---|---|
| Word 故障报告 | `outputs\reports\portable_package_demo\` |
| Excel 离线台账 | `outputs\ledger\portable_package_demo\` |
| 邮件预览 | `outputs\mail\portable_package_demo\` |
| 处置手册匹配 | `outputs\manuals\portable_package_demo\` |
| 校验结果 | `outputs\validation\portable_package_demo\` |

> [!WARNING]
> 普通试用人员不要运行 `RUN_KEEP_OPEN.cmd`、`ONE_CLICK_NEW_PC.bat`、`NEW_PC_SETUP.bat/.ps1`、`install_wecom_bot_autostart.ps1` 或 `start_wecom_ai_bot_live.ps1`。这些文件只用于获批后的生产切换，会配置真实凭据、创建登录自启动任务并启动 live 机器人，可能写入飞书或发送正式邮件。正式启用必须按 [交接操作手册](docs/HANDOVER_OPERATIONS.md) 执行。

## 30 秒了解这个项目

运维故障恢复后，处理人通常还要重复整理事实、填写报告、登记台账、查找处置手册和准备通知。人工复制不仅耗时，还容易让 Word、Excel、飞书和邮件中的内容互相矛盾。

本项目先把真实故障信息校验为一份 `incident_context`，再由它派生所有产物：

```text
用户报障 / 文本 / 截图 / 日志 / 附件
                    │
                    ▼
            提取并校验故障事实
              │             │
        信息不足             信息完整
              │             │
              ▼             ▼
     need_review + 缺失字段  incident_context
                                  │
                 ┌────────────────┼────────────────┐
                 ▼                ▼                ▼
             DOCX 报告        XLSX 台账       手册与邮件预览
                                  │
                         获批并通过生产闸门
                                  │
                                  ▼
                           飞书写入 + SMTP 发信
```

`incident_context` 是唯一可信事实源；同一故障使用稳定的 `case_id` 做查重和幂等控制。信息不足时系统只返回待补字段，不猜测时间、原因、处理过程或负责人。

## 能做什么

- 接收故障描述、UTF-8 文本文件、截图、日志和附件。
- 校验故障事实完整性，并明确输出 `need_review` 与 `missing_fields`。
- 一次生成 DOCX 故障报告、XLSX 离线台账、手册匹配、邮件预览和校验结果。
- 通过企业微信机器人接收消息，并复用同一套闭环流水线。
- 在明确批准的 live 模式下写入飞书、上传附件并发送 SMTP 邮件。
- 支持重复记录检查、邮件幂等、Windows 登录自启动、运行诊断和停止脚本。

## 不做什么

- 不是监控或告警采集平台，不会主动发现服务器故障。
- 不是自动根因分析系统，不会替代处理人判断故障原因。
- 主流程是确定性的规则与模板流水线，不依赖大模型才能运行。
- 不会从报告、邮件或旧产物反推故障事实。
- 默认不会读取飞书、写入飞书或发送邮件。

## 当前状态

| 项目 | 说明 |
|---|---|
| 项目阶段 | V1，本地 CLI 与企业微信入口均已落地 |
| 统一 CLI 入口 | `run_closed_loop.py` |
| 唯一可信主数据 | `work/cases/<case_id>/incident_context.json` |
| 默认模式 | 纯本地 dry-run |
| 外部集成 | 企业微信机器人、飞书多维表格、SMTP |
| 验收标准 | 以当前 checkout 的 `validate_package.ps1` 和实际测试结果为准 |
| 授权交付 | GitHub 私有 Releases 中的 Windows 便携包 + SHA-256 |

## 维护人员：获取源码与构建交付

只有需要修改代码或构建新交付包的维护人员才需要 Git。使用已授权的 GitHub 账号获取当前主分支：

```powershell
git clone https://github.com/GZ-dy/duoyu-fault-closed-loop.git
cd duoyu-fault-closed-loop
```

> 普通使用人员应从 Releases 下载已验收便携包，不要克隆仓库、不要自行构建，也不要使用 GitHub 自动生成的 Source code 压缩包作为正式交付。

正式交付或换机时，应由已验收的维护机生成便携包：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_portable_release.ps1
```

默认便携包不包含 `.venv`、真实飞书配置、企业微信/SMTP 凭据、历史运行数据和缓存。构建后必须执行本地验收、敏感文件检查和 SHA-256 校验，再上传到 GitHub Release。换机与人员交接都应以 [交接操作手册](docs/HANDOVER_OPERATIONS.md) 的“先停旧端、再启新端”顺序为准。

## 命令行方式：5 分钟安全体验

已通过 `START_LOCAL_DEMO.cmd` 完成试用的人员可跳过本节。下面是给熟悉 PowerShell 的维护人员使用的等价命令行流程；它只生成本地产物，不连接飞书、不发送邮件，也不启动正式企业微信机器人。

### 1. 准备项目环境

电脑已安装 Python 3.10+ 时执行：

```powershell
.\install_env.ps1
```

脚本使用项目根目录的 `.venv`，并安装 `requirements.txt` 和 `requirements-wecom.txt` 中声明的依赖。如果目录中已有从其他电脑复制来的 `.venv`，不要直接复用；应按交接文档在当前电脑重建环境。

电脑没有 Python 时，改用：

```powershell
.\INSTALL_DEPENDENCIES.bat
```

### 2. 运行离线 Demo

```powershell
.\run_local_demo.ps1
```

成功时会显示：

```text
[OK] offline demo validated with five artifact groups
```

并在 `outputs/` 下生成报告、台账、邮件预览、手册匹配和校验结果。

### 3. 执行完整本地验收

```powershell
.\validate_package.ps1
```

验收脚本会运行当前测试集并重新生成离线 Demo。看到以下结果，说明代码、模板和主要本地入口在当前电脑可用：

```text
[OK] package validation passed
```

本地验收通过不等于生产环境已经接通。飞书、SMTP、企业微信凭据、邮件路由和单实例切换仍需按交接手册单独确认。

## 手动处理一条故障

手工闭环分为两个阶段：先确认事实，再生成产物。

### 阶段一：生成并校验事实

```powershell
$python = ".\.venv\Scripts\python.exe"

& $python -B run_closed_loop.py `
  --text "完整、真实的用户报障信息" `
  --case-id bomc_case `
  --incident-context-only
```

如果结果为 `need_review`，根据 `missing_fields` 补齐真实信息后，再使用同一个 `case_id` 重试。

| 参数 | 用途 |
|---|---|
| `--text` | 直接传入故障描述或聊天记录 |
| `--input-file` | 读取 UTF-8 故障文本文件 |
| `--screenshot` | 传入故障截图，可重复使用；支持 `.ocr.txt` 旁路 |
| `--log` | 传入故障日志，可重复使用 |
| `--attachment` | 传入补充材料，可重复使用 |

### 阶段二：生成本地产物

```powershell
& $python -B run_closed_loop.py `
  --incident-context work\cases\bomc_case\incident_context.json `
  --all-dry-run
```

查看完整参数：

```powershell
.\.venv\Scripts\python.exe -B run_closed_loop.py --help
```

## 一次运行会生成什么

| 结果 | 作用 | 默认位置 |
|---|---|---|
| `incident_context.json` | 本次故障的唯一可信事实记录 | `work/cases/<case_id>/` |
| DOCX 故障报告 | 归档、复盘或提交 | `outputs/reports/<case_id>/` |
| XLSX 离线台账 | 保存本地故障记录和飞书字段计划 | `outputs/ledger/<case_id>/` |
| 手册匹配结果 | 根据故障系统查找本地处置手册 | `outputs/manuals/<case_id>/` |
| 邮件预览/结果 | 检查正文、附件、收件人和发送状态 | `outputs/mail/<case_id>/` |
| 校验结果 | 记录通过项、缺失字段和阻断原因 | `outputs/validation/<case_id>/` |
| 飞书记录（live） | 正式在线台账记录及附件 | 配置的飞书多维表格 |

## 运行模式

| 模式 | 入口 | 外部连接 | 飞书写入 | SMTP 发信 |
|---|---|---|---:|---:|
| CLI 离线闭环 | `run_closed_loop.py --all-dry-run` | 无 | 否 | 否 |
| 机器人 Mock | `start_wecom_ai_bot_dry_run.ps1 -Mock` | 无 | 否 | 否 |
| 企业微信接入 dry-run | `start_wecom_ai_bot_dry_run.ps1` | 仅连接企业微信收发消息 | 否 | 否 |
| 正式 CLI 闭环 | `run_closed_loop.py --apply ...` | 飞书；加 `--send-mail` 后使用 SMTP | 通过闸门后 | 仅加 `--send-mail` 后 |
| 正式机器人自启动 | `install_wecom_bot_autostart.ps1` | 企业微信、飞书、SMTP | 通过闸门后 | 通过闸门后 |

### 企业微信机器人

企业微信只是故障入口。机器人收到群消息后，调用与 CLI 相同的闭环流水线：信息不足时返回待补字段；信息完整时生成本地产物；只有 live 条件全部满足后，才执行飞书和邮件动作。

```powershell
# 本地 Mock，不连接真实企业微信
.\start_wecom_ai_bot_dry_run.ps1 -Mock

# 连接真实企业微信，但保持业务 dry-run
.\start_wecom_ai_bot_dry_run.ps1

# 诊断与停止
.\diagnose_bot.ps1
.\stop_bot.ps1
```

正式接入、自启动、日志和排障见 [企业微信机器人说明](docs/WECOM_BOT.md) 与 [Windows 本地运行说明](docs/LOCAL_RUN.md)。

## 生产写入安全闸门

飞书 live 写入必须基于已校验的 `incident_context`，并同时给出 `--apply` 和固定确认值；如果还要完成邮件闭环，再加 `--send-mail`：

```powershell
.\.venv\Scripts\python.exe -B run_closed_loop.py `
  --incident-context work\cases\bomc_case\incident_context.json `
  --apply `
  --confirm-live-write LIVE-WRITE `
  --send-mail
```

以下任一 live 条件不满足时，系统返回 `need_review`，不会执行对应的外部写入：

- 未提供已校验的 `--incident-context`；
- 未提供 `--apply`；
- `--confirm-live-write` 不是 `LIVE-WRITE`；
- 故障事实、报告字段或发信前检查未通过；
- 飞书、SMTP、联系人映射或目标记录状态不满足要求。

`--send-mail` 不是单独的飞书写入条件，但它是发送正式邮件的显式开关；不提供时只执行获批的飞书动作，不发送 SMTP 邮件。

生产运行还必须确认当前邮件路由、目标飞书表和同一企业微信机器人的单实例状态。不要用新的 `case_id` 绕过查重，也不要在未核对外部状态时重复发送。

## 配置与凭据

| 配置 | 位置或方式 | 版本库/交付边界 |
|---|---|---|
| 飞书配置模板 | `config/private/feishu.template.json` | 可提交，必须保持脱敏 |
| 企业微信环境模板 | `config/private/wecom_bot.env.template` | 可提交，必须保持脱敏 |
| 飞书真实配置 | `config/private/feishu.json` | 仅本机保存，不进默认交付包 |
| 企业微信与 SMTP 凭据 | Windows DPAPI / 环境变量 | 绑定本机用户，不跨电脑复用 |

任何提交、文档、日志或交付包都不得包含飞书 `app_secret`、企业微信 Bot Secret、SMTP 授权码、Gitee 私钥或其他明文凭据。新电脑必须重新建立本机环境和凭据，不能把旧电脑的 `.venv` 或 DPAPI 文件当作可移植配置。

## 项目结构

| 路径 | 职责 |
|---|---|
| `run_closed_loop.py` | 统一手工/CLI 入口 |
| `src/orchestrator/` | 闭环编排 |
| `src/field_contracts/` | 字段契约与校验规则 |
| `src/input_collection/` | 文本、截图和日志采集 |
| `src/report_generation/` | DOCX 报告生成 |
| `src/offline_ledger/` | XLSX 离线台账 |
| `src/feishu_ledger/` | 飞书记录读写 |
| `src/mail_sender/` | SMTP 邮件生成与发送 |
| `src/manuals_review/` | 处置手册匹配 |
| `scripts/wecom_fault_bot.py` | 企业微信入口，复用闭环编排 |
| `data/templates/` | 只读报告和台账模板 |
| `data/reference/` | 联系人、手册和参考数据 |
| `tests/` | 自动化测试 |
| `tools/` | 构建、备份和恢复工具 |

## 测试与验收

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

测试数量会随版本变化，以当前 `unittest discover` 的 `0 fail / 0 error` 为准。交付前还应执行：

```powershell
.\validate_package.ps1
.\start_wecom_ai_bot_dry_run.ps1 -Mock
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 项目接手、业务边界和代理执行规则 |
| [docs/HANDOVER_OPERATIONS.md](docs/HANDOVER_OPERATIONS.md) | 人员交接、切换、验收、值守与回退 |
| [docs/LOCAL_RUN.md](docs/LOCAL_RUN.md) | Windows 本地运行与机器人排障 |
| [docs/WECOM_BOT.md](docs/WECOM_BOT.md) | 企业微信机器人接入 |
| [docs/OUTPUT_POLICY.md](docs/OUTPUT_POLICY.md) | 正式、临时和历史产物边界 |
| [docs/DIRECTORY_MAP.md](docs/DIRECTORY_MAP.md) | 目录职责说明 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本变更记录 |
| [正式闭环规则源](src/field_contracts/contracts/FAULT_CLOSED_LOOP_RULES.md) | 字段、报告、台账与邮件规则 |

## 维护原则

- `run_closed_loop.py` 是统一 CLI 入口；其他入口复用同一业务流水线，不另造平行事实源。
- `incident_context` 是唯一可信主数据，不从报告、台账或历史产物反推事实。
- `data/templates/official/` 在运行时只读。
- `archive/legacy/` 只用于历史参考，不参与主流程。
- 所有正式外部写入都必须经过生产闸门和业务确认。
