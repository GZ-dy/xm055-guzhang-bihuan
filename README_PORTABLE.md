# 故障闭环系统 V1 便携包

本包包含当前 V1 运行代码、模板、处置手册、Windows 安装脚本和本地验收脚本。完整文件清单见 `PACKAGE_MANIFEST.txt`，详细使用说明见 `README.md`。

## 普通授权用户：安全体验

1. 完整解压 ZIP，不要在 ZIP 预览窗口中运行。
2. 双击 `START_LOCAL_DEMO.cmd`。
3. 看到 `[DONE] Safe local demo passed.` 后，打开 `outputs` 目录查看 Word 报告、Excel 台账、手册匹配和邮件预览。

首次安装 Python 和依赖需要联网。上述入口不会配置生产凭据、不会启动 live 机器人、不会写入飞书、不会发送邮件。

## 包含与排除边界

- 包含：`run_closed_loop.py`、`src/`、`data/`、`docs/`、`scripts/`、`tests/`、`tools/`、脱敏配置模板和 Windows 交付脚本。
- 默认排除：`.venv`、真实飞书配置、企业微信/SMTP 凭据、DPAPI 文件、历史 `outputs/` 和 `work/` 数据。
- `wheelhouse/` 只在构建时显式使用 `-IncludeWheelhouse` 才入包；默认发布包不是断网安装包。

## 生产切换警告

`RUN_KEEP_OPEN.cmd`、`ONE_CLICK_NEW_PC.bat`、`NEW_PC_SETUP.*` 会配置本机真实凭据、创建登录自启动并启动 live 机器人。它们不是普通试用入口。只能在旧机器人已停止、切换已获批且按 `docs/HANDOVER_OPERATIONS.md` 核对完成后使用。
