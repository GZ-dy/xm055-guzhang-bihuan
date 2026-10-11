# 故障闭环系统交接操作手册（Windows）

> 适用范围：将当前 Windows 本地运行的故障闭环系统交给另一名维护人或迁移到另一台 Windows 电脑。
>
> 目标：新接手人能独立安装、验收、启动、诊断和停止系统；旧运行端退出；凭据不进入代码、文档、聊天或交付包。

## 1. 先明确系统怎么运行

当前系统由本机企业微信机器人接收群消息，再调用 `run_closed_loop.py` 生成 `incident_context`、报告、台账和邮件。正式 live 模式还会写飞书并发送 SMTP 邮件。

Windows 登录自启动任务名为：

```text
FaultClosedLoopWeComBot
```

本地电脑关机、断网或运行该任务的 Windows 用户退出登录后，机器人不可用。锁屏不等于退出登录。

正式业务只有一个可信事实源：

```text
work/cases/<case_id>/incident_context.json
```

信息不全时必须返回 `need_review`。不得补写或猜测故障时间、原因、处理过程和负责人。

## 2. 交接双方职责

| 角色 | 必须完成的事项 |
|---|---|
| 原维护人 | 验证当前版本、生成交付包和 SHA256、说明当前邮件规则、停止旧机器人、移交未结事项 |
| 新维护人 | 在新电脑重建环境和本机凭据、完成验收、确认日志和任务状态、接管日常值守 |
| 业务确认人 | 确认飞书目标表、邮件收件人/抄送规则、正式切换时间和首个真实案例 |

交接完成不以“文件发过去了”为准，必须以第 8 节验收表全部通过为准。

## 3. 交接材料清单

原维护人应交付：

- 由 `tools/build_portable_release.ps1` 生成的 ZIP；
- ZIP 的 SHA256；
- ZIP 内自动生成的 `PACKAGE_MANIFEST.txt`；
- 当前版本或 Git commit、构建时间和已知问题；
- 飞书、企业微信机器人和 SMTP 凭据的“取得方式/负责人”，不包含真实密钥；
- 当前邮件收件人和抄送规则的业务确认记录；
- 尚未闭环的 `case_id` 清单及处理状态。

默认不得交付：

- `.venv/`、缓存、临时日志和测试产物；
- `config/private/feishu.json`；
- `config/private/wecom_bot.dpapi.json`；
- SMTP 授权码、企业微信 Bot Secret、飞书 App Secret；
- `work/`、`outputs/` 中与接手无关的历史运行数据。

`wecom_bot.dpapi.json` 绑定原 Windows 用户，复制到新电脑或新用户下不能解密，必须在新电脑重新输入凭据生成。

## 4. 原维护人：交付前准备

### 4.1 记录版本并验收

在项目根目录执行：

```powershell
git status --short
git rev-parse HEAD
.\validate_package.ps1
.\start_wecom_ai_bot_dry_run.ps1 -Mock
```

要求：

- 清楚记录未提交文件；不要把来源不明的临时修改混入交付包；
- `validate_package.ps1` 的测试和离线 Demo 均通过；
- Mock 仅生成本地产物，不写飞书、不发邮件；
- 测试数量以当次 `unittest discover` 输出为准，不用旧文档中的固定数量代替。

### 4.2 核对正式邮件路由

正式切换前必须由业务确认人核对当前代码中的主收件人和联系人抄送规则：

```powershell
Select-String -Path .\src\orchestrator\pipeline.py -Pattern 'DEFAULT_MAIL_TO','_lookup_contacts_cc'
Select-String -Path .\src\field_contracts\contracts\FAULT_CLOSED_LOOP_RULES.md -Pattern '主收件人','抄送'
```

当前 checkout 的默认邮件行为以代码和正式规则源为准，不能只凭旧截图、旧说明或口头记忆。若业务确认结果与代码不一致，先保持机器人停止，修正并验收后再启用 live。

### 4.3 生成交付包

使用默认安全模式构建，不带私有配置：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_portable_release.ps1
```

脚本会在 `dist/` 生成目录和 ZIP，并在输出中显示 SHA256。再做一次独立核对：

```powershell
Get-FileHash -Algorithm SHA256 .\dist\<实际文件名>.zip
```

交接时不要使用 `-IncludePrivateConfig`。只有业务负责人明确批准、双方在同一可信本地介质上交付时，才考虑该参数；跨电脑迁移仍建议在新电脑重新录入。

## 5. 新维护人：新电脑预安装（不切 live）

### 5.1 解压和校验

1. 把 ZIP 完整解压到固定本地目录，例如 `D:\fault-closed-loop`。
2. 不要放在 ZIP 预览窗口、临时目录或网盘同步目录中运行。
3. 核对 SHA256：

```powershell
cd D:\fault-closed-loop
Get-FileHash -Algorithm SHA256 <收到的ZIP完整路径>
```

4. 对照 `PACKAGE_MANIFEST.txt` 确认包完整。

### 5.2 先装依赖和做离线验收

推荐采用分阶段方式，避免依赖刚装好就直接启用 live：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_DEPENDENCIES.ps1
.\validate_package.ps1
.\start_wecom_ai_bot_dry_run.ps1 -Mock
```

安装器会在项目根目录创建或修复 `.venv`。如果失败，保留窗口并提交项目根目录的 `dependency_install.log`，日志中不得附加任何明文密钥。

如果只需要最简一键安装，也可双击 `RUN_KEEP_OPEN.cmd`，但它会继续配置正式凭据、创建 live 自启动任务并立即启动机器人。因此必须先完成第 6 节的切换前检查。

## 6. 正式切换窗口

### 6.1 切换前四项确认

在启动新电脑 live 之前，三方共同确认：

- 新电脑依赖、测试和 Mock 已通过；
- 飞书目标表和邮件路由已确认；
- 群内没有人正在提交测试故障；
- 已约定回退负责人和回退截止时间。

### 6.2 停止旧电脑

在旧电脑项目根目录执行：

```powershell
.\stop_bot.ps1
Disable-ScheduledTask -TaskName FaultClosedLoopWeComBot
Get-ScheduledTask -TaskName FaultClosedLoopWeComBot | Select-Object TaskName,State
```

`stop_bot.ps1` 只停止本次运行，下次登录仍可能自动启动，所以交接时还必须执行 `Disable-ScheduledTask`。

确认旧电脑不再存在 `wecom_fault_bot.py` 进程后，记录停止时间。任何时候都不要让两台电脑同时运行同一个机器人，否则可能重复处理消息、重复写飞书或重复发信。

### 6.3 在新电脑配置凭据并启动

分阶段安装已完成时，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\NEW_PC_SETUP.ps1 -SkipDependencyInstall
```

按提示输入：

- 飞书 App ID、App Secret、App Token、Table ID、View ID 和表格 URL；
- 企业微信 Bot ID、Bot Secret；
- SMTP 发件邮箱和 SMTP 授权码。

如果新电脑已存在 `config/private/feishu.json`，脚本会保留该文件，不会再次询问飞书配置。应先确认它确实属于当前生产表。

脚本会生成本机 DPAPI 加密文件、创建 `FaultClosedLoopWeComBot` 登录任务并启动 live 机器人。凭据只在新电脑本地输入，不通过聊天、邮件、工单或本文档传递。

## 7. 新维护人：运行与日常值守

### 7.1 查看任务和日志

```powershell
Get-ScheduledTask -TaskName FaultClosedLoopWeComBot | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName FaultClosedLoopWeComBot | Format-List LastRunTime,LastTaskResult
Get-Content .\work\bot\wecom_bot_$(Get-Date -Format yyyyMMdd).log -Tail 50
.\diagnose_bot.ps1
```

日志应看到：

```text
WeCom bot websocket connected
WeCom bot authenticated
Received heartbeat ack
```

`diagnose_bot.ps1` 在单独打开的 PowerShell 中读取不到计划任务进程内部的环境变量，因此其“3 个生产 gate”显示为空时，不能单独据此判断计划任务已降级。应结合进程启动模式、计划任务状态和当日日志判断。

### 7.2 停止、重启和禁用

临时停止本次运行：

```powershell
.\stop_bot.ps1
```

使用已保存的本机 DPAPI 凭据重新启动：

```powershell
Start-ScheduledTask -TaskName FaultClosedLoopWeComBot
```

长期停用，防止下次登录自动启动：

```powershell
.\stop_bot.ps1
Disable-ScheduledTask -TaskName FaultClosedLoopWeComBot
```

恢复登录自启动：

```powershell
Enable-ScheduledTask -TaskName FaultClosedLoopWeComBot
Start-ScheduledTask -TaskName FaultClosedLoopWeComBot
```

### 7.3 凭据轮换

企业微信 Secret、SMTP 授权码或发件邮箱变化时：

```powershell
.\stop_bot.ps1
.\install_wecom_bot_autostart.ps1
```

该脚本会重新录入凭据、覆盖本机 DPAPI 文件、重建并启动计划任务。飞书凭据变化时，应在本机安全地更新 `config/private/feishu.json`；不得把真实值提交到 Git。

## 8. 交接验收表

建议把下表复制到交接记录中，由双方填写，不要填任何密钥正文。

| 验收项 | 结果/证据 |
|---|---|
| 项目版本或 Git commit |  |
| 交付 ZIP 文件名 |  |
| ZIP SHA256 一致 | 通过 / 不通过 |
| `PACKAGE_MANIFEST.txt` 已核对 | 通过 / 不通过 |
| `validate_package.ps1` | 通过 / 不通过；测试数： |
| Mock 闭环 | 通过 / 不通过；case_id： |
| 邮件主收件人和抄送规则已由业务确认 | 确认人/时间： |
| 旧电脑机器人已停且计划任务已禁用 | 时间： |
| 新电脑任务为 `FaultClosedLoopWeComBot` | 通过 / 不通过 |
| connected / authenticated / heartbeat 三项日志 | 通过 / 不通过 |
| 首个经批准的真实案例 | case_id： |
| 飞书记录读回正确 | record_id/结果： |
| SMTP 成功且群内收到最终回执 | 通过 / 不通过 |
| 新维护人能独立停止和重启 | 通过 / 不通过 |
| 遗留问题与负责人 |  |

只有以下条件全部满足，才算正式交接完成：

1. 旧运行端已退出，新运行端只有一个；
2. 新维护人能独立完成诊断、停止和重启；
3. 本地测试和 Mock 通过；
4. 邮件路由经过业务确认；
5. 首个经批准的真实案例完成飞书读回和 SMTP 成功验证；
6. 交付包、文档和聊天记录中没有明文密钥。

## 9. 回退方案

如果新电脑在验收期内认证失败、持续断线或正式闭环异常：

1. 立即停止并禁用新电脑任务：

```powershell
.\stop_bot.ps1
Disable-ScheduledTask -TaskName FaultClosedLoopWeComBot
```

2. 确认新电脑没有 `wecom_fault_bot.py` 进程。
3. 只有在旧电脑凭据仍有效、旧版本仍是已验收版本时，才在旧电脑恢复：

```powershell
Enable-ScheduledTask -TaskName FaultClosedLoopWeComBot
Start-ScheduledTask -TaskName FaultClosedLoopWeComBot
```

4. 记录回退时间、受影响的 `case_id`、飞书 record_id 和邮件状态，先查重再重试；不要用新 `case_id` 绕过幂等检查。

## 10. 常见问题

| 现象 | 处理 |
|---|---|
| 安装窗口一闪而过 | 从解压后的目录双击 `RUN_KEEP_OPEN.cmd`，不要在 ZIP 预览窗口运行 |
| 没有 Python | `INSTALL_DEPENDENCIES.ps1` 会优先使用 `winget`，必要时下载安装官方 Python |
| 依赖安装失败 | 查看 `dependency_install.log`，重新执行安装器；不要复制旧电脑 `.venv` |
| 机器人认证失败 | 在企业微信后台确认 Bot ID/Secret，必要时重置 Secret 后重新运行自启动安装器 |
| 群消息无响应 | 查计划任务、进程和当日日志；确认电脑在线且当前用户未退出登录 |
| 信息不足 | 按 `missing_fields` 补充真实事实，不得编造字段 |
| 可能重复发信 | 先停机器人，按同一 `case_id`、飞书记录和邮件状态查重，不要直接重发 |
| 凭据疑似泄露 | 立即停止机器人，在对应平台轮换密钥，再在新电脑重新生成 DPAPI 配置 |

## 11. 相关文档

- `README.md`：项目总览和标准入口；
- `AGENTS.md`：业务边界、包清单和代理接手规则；
- `docs/LOCAL_RUN.md`：Windows 本地运行和排障；
- `docs/WECOM_BOT.md`：企业微信接入说明；
- `docs/OUTPUT_POLICY.md`：正式产物和临时产物边界；
- `src/field_contracts/contracts/FAULT_CLOSED_LOOP_RULES.md`：正式字段和邮件规则源。

