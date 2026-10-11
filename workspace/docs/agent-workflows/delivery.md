# 便携交付和部署入口

只在打包、部署或恢复任务中读取。本页给出需要保持的交付约束；精确文件集合和行为以当前 `tools/build_portable_release.ps1`、安装与验收脚本为准。

## 包边界

- 保留统一入口、业务源码、模板与手册、安装/验证脚本、测试及 `docs/`；离线依赖需要时使用 `wheelhouse/`。`PACKAGE_MANIFEST.txt` 由构建脚本生成。
- 正式私有配置不得进入 Git 或普通交付包。`config/private/feishu.template.json`、`config/private/wecom_bot.env.template` 是脱敏模板，不能用真实值填充后分发。
- `config/private/feishu.json` 只有明确要求私有交付时才考虑构建器的 `-IncludePrivateConfig` 分支；不要读取或输出其凭据。`wecom_bot.dpapi.json` 绑定原 Windows 用户，禁止入包。
- `outputs/`、`work/` 和普通包的私有配置目录只保留构建器要求的占位；历史台账快照、`.workbuddy/报告模板/`、临时备份不作为默认交付内容。正式联系人映射沿用现有入包规则，不额外扩散。
- `docs/agent-workflows/` 跟随现有 `docs/` 入包规则，根 AGENTS.md 的流程链接不依赖隐藏技能目录。项目 `.agents/skills/` 是 Codex 的可选任务入口，不擅自改变构建器文件集合。

## 入口区别

| 用途 | 现有入口及边界 |
|---|---|
| 安装依赖 | `install_env.*`、`INSTALL_DEPENDENCIES.*`，Windows 依赖放入项目 `.venv/` |
| 完整便携验收 | `validate_package.*`，运行现有测试并生成离线样例；只在任务需要完整验收时使用 |
| 本地体验 | `START_LOCAL_DEMO.cmd`、`run_local_demo.*`；前者也会安装依赖，先检查当前依赖是否已齐备 |
| 首次生产切换 | `NEW_PC_SETUP.*`、`ONE_CLICK_NEW_PC.bat`、`RUN_KEEP_OPEN.cmd` 涉及真实凭据、自启动和 live 机器人，需对应授权 |
| 已部署机器人恢复 | `recover_after_reboot.ps1`、`RECOVER_AFTER_REBOOT.cmd` 只适用于同机、同 Windows 用户、同目录的已部署实例，不替代首次生产配置 |

`validate_package`、`run_local_demo` 在 Windows 必须直接使用项目 `.venv/Scripts/python.exe`。本地测试成功、安装成功、进程存活与生产业务验收分别报告。

如需完整测试，沿用项目 unittest discover 入口，保留 `tests/__init__.py`。依据当前输出判断成功，不要求历史固定测试数量。交付 DOCX 在 Windows 使用 Word 只读导出及 Poppler 检查，不自动安装或启动 LibreOffice。
