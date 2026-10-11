# 新电脑续跑说明（Windows）

本迁移包包含当前源码、模板、联系人/手册、正式主台账、邮件幂等状态，以及企微消息关联登记簿 `work/cases/_message_registry.sqlite3`。它不包含 `.venv`、历史报告、测试运行数据、缓存、备份或任何可直接登录的凭据。

`_message_registry.sqlite3` 不是缓存；迁移前应停止旧机器人后再复制，迁移后先核对文件存在，再启动新机器人。遗漏该文件会丢失跨重启消息去重与“补充同一 case_id”的关联历史。

如果是把系统正式交给另一名维护人，而不只是同一维护人换电脑，请同时按 [docs/HANDOVER_OPERATIONS.md](docs/HANDOVER_OPERATIONS.md) 完成交付包校验、旧端停机、邮件路由确认、验收签字和回退准备。

## 1. 解压并一键配置

把整个目录解压到固定本地路径，例如 `D:\fault-closed-loop`，不要放在临时目录或网盘同步目录。

```powershell
cd D:\fault-closed-loop
.\NEW_PC_SETUP.bat
```

唯一前置条件是 Windows 已安装 Python 3.10 以上版本。一键脚本会创建 `.venv`；如果没有 `feishu.json`，会提示填写飞书参数；随后提示输入企业微信 Bot ID、Bot Secret、SMTP 发件邮箱和授权码，最后创建并启动登录自启动任务。

## 2. 安全恢复飞书配置

`config\private\feishu.json` 含明文密钥，未放进迁移包。可以通过可信介质单独复制旧电脑上的该文件；如果不复制，一键脚本会在新电脑本地提示输入并生成。

不要通过聊天、邮件、工单或文档传输真实密钥。

## 3. 机器人和 SMTP 凭据

旧电脑的 `wecom_bot.dpapi.json` 绑定原 Windows 用户，不能复用。一键脚本会提示输入同一个企业微信机器人的 Bot ID、Bot Secret、SMTP 发件邮箱和 SMTP 授权码，为新 Windows 用户重新生成 DPAPI 密文，并创建、启动 `FaultClosedLoopWeComBot` 登录自启动任务。

## 4. 验证

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
Get-ScheduledTask -TaskName FaultClosedLoopWeComBot
Get-Content .\work\bot\wecom_bot_$(Get-Date -Format yyyyMMdd).log -Tail 30
```

日志必须同时出现：

- `WeCom bot websocket connected`
- `WeCom bot authenticated`
- `Received heartbeat ack`

当前邮件规则：主收件人为 `gydyjzhwhz@gd.chinamobile.com`，抄送按故障系统从正式联系人表匹配负责人；没有匹配时抄送为空。

## 5. 正式切换

确认新电脑已认证并收到心跳后，再在旧电脑执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop_bot.ps1
Disable-ScheduledTask -TaskName FaultClosedLoopWeComBot
```

不要让新旧两台电脑同时运行同一个机器人，否则可能重复处理同一条消息。
