# 企业微信接入

当前企业后台没有“智能机器人”入口，因此优先使用“自建应用接收消息”。

企业微信只做入口；飞书在线表仍由现有闭环流程写入或 dry-run 预览。

## 自建应用资料

在企业微信后台收集这些字段：

- `CorpID`：`我的企业` -> `企业信息`
- `AgentId`：`应用管理` -> 自建应用详情
- `Secret`：自建应用详情，用于后续主动发消息；第一版接收回调暂不需要
- `Token`：自建应用 `接收消息` -> `设置API接收`
- `EncodingAESKey`：自建应用 `接收消息` -> `设置API接收`
- `URL`：填公网 HTTPS 回调地址

不要把 `Secret`、`Token`、`EncodingAESKey` 写入代码、文档示例、JSON 或交付包。

## 安装依赖

```powershell
python -m pip install -r requirements-wecom.txt
```

## 本机环境变量

临时设置当前 PowerShell：

```powershell
$env:WECOM_APP_CORP_ID = "企业ID"
$env:WECOM_APP_TOKEN = "API接收里的Token"
$env:WECOM_APP_ENCODING_AES_KEY = "API接收里的EncodingAESKey"
```

长期写入当前 Windows 用户：

```powershell
setx WECOM_APP_CORP_ID "企业ID"
setx WECOM_APP_TOKEN "API接收里的Token"
setx WECOM_APP_ENCODING_AES_KEY "API接收里的EncodingAESKey"
```

## 启动回调服务

先只测连接：

```powershell
python -B scripts\wecom_app_callback.py --run-mode probe
```

默认监听：

```text
http://0.0.0.0:8124/wecom/app-callback
```

企业微信后台 `URL` 不能填 `localhost`，必须填公网可访问的 HTTPS 地址。可以用内网穿透工具把本机 `8124` 暴露出去，再把公网 HTTPS 地址填到 `设置API接收`。

确认企业微信能验证 URL 后，再切本地 dry-run：

```powershell
python -B scripts\wecom_app_callback.py --run-mode dry-run
```

收到文本消息后会生成：

- `work/cases/<case_id>/incident_context.json`
- `outputs/reports/<case_id>/...docx`
- `outputs/ledger/<case_id>/...xlsx`
- dry-run 预览 JSON

## 约束

- 当前只接收文本消息。
- 当前只做本地 `all-dry-run`。
- 飞书真实写入和邮件发送继续走现有 `LIVE-WRITE` gate。

## 智能机器人 WebSocket 方案

企业后台已提供智能机器人 Bot ID/Secret 时，使用项目正式启动器：

```powershell
.\install_env.ps1
.\start_wecom_ai_bot_dry_run.ps1
```

该方案需要 `WECHAT_BOT_ID` 和 `WECHAT_BOT_SECRET`。机器人收到群文本后会自动执行：

1. 生成并校验 `incident_context`；
2. 自动运行闭环，生成报告、离线台账、邮件预览和手册匹配结果；
3. 在群内返回 case_id、校验状态、报告路径、台账路径和邮件预览路径。

本地交付验收可先执行：

```powershell
.\start_wecom_ai_bot_dry_run.ps1 -Mock
```

真实飞书和邮件仍只允许通过 `start_wecom_ai_bot_live.ps1` 加完整环境闸门启动。
