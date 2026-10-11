# 变更日志 (Changelog)

本日志记录故障闭环系统 V1 的所有可见改动。语义化版本 [SemVer 2.0.0](https://semver.org/lang/zh-CN/)。"新增"/"修改"/"修复"/"移除"四类按时间倒序排列，最近的变动在最上。

---

## [0.2.0] - 2026-08-10

### 修复 (Fixes)
- 邮件事件时间改为 `M月D日H点MM分`；OPENAPI 操作过程按运维口径补充“多域维护组”，故障原因段落首行缩进两字符。
- 邮件主收件人改为 `gydyjzhwhz@gd.chinamobile.com`；抄送按故障系统从正式联系人表匹配对应负责人。
- `run_closed_loop.py` 入口查重复用 `find_record_by_case_id()`；同一 case_id 多条命中不再擅自选记录，由 pipeline 按歧义 fail-closed。
- `start_wecom_ai_bot_dry_run.bat` 改为纯 ASCII PowerShell 委托器，修复错误切到父目录、中文批处理解析报错、重复执行和错误退出码问题。
- `diagnose_bot.ps1` 改为 CIM 优先读取进程命令行，`wmic` 仅兼容回退；产物提示改用当前项目根。
- `docs/LOCAL_RUN.md` 区分 dry-run bat 与 live ps1，真写说明补齐三环境门；AGENTS 接手 smoke 不再要求用缺字段短描述通过校验。
- SMTP 失败回归测试名称与“不得回填飞书”的实际契约保持一致。
- 便携包加入当前企业微信 dry-run/live/诊断/停止脚本，并排除联系人维护备份，只保留正式联系人数据。
- AGENTS.md 落盘到项目根,填补"包内必备清单"第 2 项缺口。
- `tests/__init__.py` 加空文件,严格 unittest discovery 不再 ImportError。
- 2 个 unittest 路径断言改为跨平台 / PureWindowsPath 归一化(回归 L1 修复 `src/manuals_review/preview.py` 的 PureWindowsPath 改动)。
- audit 报告 D 段确认的两个 fail (`test_manual_dry_run_matches_reference_index` + `test_workspace_manual_index_is_self_contained`) 已修复；全量回归改以 `unittest discover` 为准。
- `run_closed_loop.py` 自动 live 改为三环境门；自动查重命中已发送邮件时保留飞书 update、跳过重复发信。
- dry-run 不再读取飞书；离线表序号按连续固定整数落盘。
- 飞书配置字段统一为 `app_token`。
- 写 `config/private/feishu.template.json`(脱敏模板,app_id/secret 留空)。
- 写 `requirements.txt`(从 src import 反推,含 openpyxl required + pytesseract/Pillow optional)。

### 新增 (Additions)
- GitHub 私有 Release 交付流程：授权用户下载版本化 Windows ZIP 和 SHA-256 校验文件，不再以源码压缩包作为正式交付。
- `START_LOCAL_DEMO.cmd`：只安装本地依赖并运行测试与离线 Demo，不配置凭据、不启动 live 机器人。
- `docs/CHANGELOG.md` 本文件。
- AGENTS.md 第 8 节"路径风格统一约定",为后续迁移到 Linux/Win 都打稳根。
- 交付脚本集合：`install_env.ps1` / `install_env.bat`、`validate_package.ps1` / `validate_package.bat`、`run_local_demo.ps1` / `run_local_demo.bat`。
- 全量测试当前至少 72 个；固定数量不再作为契约，以 discover 结果为准。

## [0.1.0] - 2026-07-09

### 修复
- **L1 关键修复**:`src/manuals_review/preview.py::_guide_paths` 把 `PureWindowsPath` 拆 parts 后再 `Path(*parts)` 拼接,处理手册索引里 windows 反斜杠路径在 Linux 下不被识别为分隔符的 bug。
- BOMC-cas 案例当时的全量烟测通过,全部 5 类产物(reports/ledger/mail/manuals/validation)生成。
- 上传 70 行离线故障记录表到 [SNC-153](mention://issue/519a4be6-8b12-45e7-9083-a16e44f6beb5) 评论附件。
- 上传 BOMC 7月8日故障报告 DOCX (29.4 KB) 到 [SNC-153](mention://issue/519a4be6-8b12-45e7-9083-a16e44f6beb5) 评论附件。

### 新增
- `data/reference/ledger_snapshots/【2026】多域系统故障记录表_user_upload_20260709.xlsx` 用户上传 xlsx 的归档快照,不影响权威模板。
- `archive/legacy/reference/docs/legacy/rules/AGENTS.old.md` 标记为历史只读参考,主流程不引用。

### 已识别未解决 (Known Limitations)
- `config/private/feishu.json` 的飞书 live 凭据已与何周辉 04:00 上传的一致,但 `--apply --confirm-live-write LIVE-WRITE` 烟测尚未跑(等用户拍板)。
- 26 个手册 md 中 35 个已就位(含 README 占位),部分 `导出手册/html/` 仍是 README 占位而非故障页(姊妹任务「补手册索引」跟进)。
- render_docx.py 缺(AGENTS.md 第 11 节注明现状)。
- install_env / validate_package / run_local_demo 各两套(ps1+bat)在该版本当时暂缺；当前交付集见 Unreleased。

### 文档
- `README.md` 加"两步式执行"章节 + dry-run/live 闸门说明 + SMTP live 发件前必须 `pre_mail_check`。

---

## 版本约定 (SemVer)

- MAJOR: 改 V1 主入口契约(`run_closed_loop.py` CLI 行为 / `incident_context` 主数据结构)。
- MINOR: 加新子模块 + 新产物类型 + 新 README 章节。
- PATCH: 修 bug / 改路径 / 改 build 脚本清单 / 改 doc 措辞。

本仓库起始版本号从 0.1.0 起,不预设 1.0.0 触发条件(用户拍板)。
