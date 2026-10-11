# 故障闭环系统：下载包使用说明

本 GitHub Release ZIP 已包含故障闭环系统代码、报告和台账模板、处置手册、Windows 安装脚本和本地验收脚本。它不包含真实飞书、企业微信或 SMTP 凭据，不包含旧电脑 DPAPI 文件、`.venv` 或历史运行数据。

## 普通授权用户：本地安全体验

1. 完整解压 ZIP，不要在 ZIP 预览窗口内直接运行。
2. 双击 `START_LOCAL_DEMO.cmd`。
3. 等待依赖安装、测试和本地 Demo 完成。首次安装需要联网。
4. 看到 `[DONE] Safe local demo passed.` 后，打开 `outputs` 目录查看 Word 报告、Excel 台账、手册匹配和邮件预览。

该入口不要求任何生产凭据，不连接企业微信、飞书或 SMTP，不创建自启动任务。安装失败时，窗口会保留，并在当前目录生成 `dependency_install.log`。

## 生产换机与机器人启用

> [!WARNING]
> `RUN_KEEP_OPEN.cmd`、`ONE_CLICK_NEW_PC.bat`、`NEW_PC_SETUP.*`、`install_wecom_bot_autostart.ps1` 和 `start_wecom_ai_bot_live.ps1` 不是普通试用入口。它们会收集或使用真实凭据、创建登录自启动任务并启动 live 机器人。

只有在以下条件全部满足后，指定生产维护人员才可以按 `docs/HANDOVER_OPERATIONS.md` 执行 `RUN_KEEP_OPEN.cmd`：

- 旧电脑机器人已停止且自启动已禁用；
- 新电脑本地验收已通过；
- 飞书目标表、邮件路由、企业微信 Bot 和单实例状态已核对；
- 凭据由获授权人员在新电脑本地重新录入。

旧电脑的 DPAPI 密文不能在新电脑解密，包内不会保存明文密钥。常规文字机器人流程不需要 LibreOffice 或 Tesseract 桌面程序；只有直接识别截图文字时，才需要另行安装 Tesseract OCR 和中文语言包。
