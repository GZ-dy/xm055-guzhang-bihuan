# 故障闭环系统：项目约定

本目录的业务入口是 `run_closed_loop.py`，编排实现在 `src/orchestrator/pipeline.py`。先按当前问题定位入口或模块；仅在需要了解目录职责时读取 `docs/DIRECTORY_MAP.md`。

## 事实和产物

- `incident_context` 是派生报告、台账、邮件和手册匹配的事实源。校验不足时报告缺失的具体事实，不补造时间、人员或处理过程。
- 交付字段和措辞以 `src/field_contracts/contracts/FAULT_CLOSED_LOOP_RULES.md` 为准；修改这些内容时读取该文件。
- `data/templates/` 与手册源在运行时只读。保持 `official/` 模板、手册索引中的 Windows 路径和原始 `match_target`；运行时 `match_path` 使用当前操作系统路径。
- 正式产物进入 `outputs/{reports,ledger,mail,manuals,validation}/<case_id>/`，过程文件进入 `work/cases/<case_id>/`。不从派生报告反推事实或在线表，不把 `archive/legacy/`、`phase*_outputs`、台账快照恢复为当前模板或主流程输入。

## 执行范围

- 用户要求的本地修复、离线生成及相应验证连续完成；诊断范围随具体问题确定。
- 离线执行显式使用 `--incident-context-only` 或 dry-run 参数，避免已有自动发送环境影响执行。真实飞书写入、SMTP 发送和机器人启动按本次及已有明确授权执行；本地修改不授予这些操作权限。
- 保留 `--incident-context`、`--apply`、`--confirm-live-write LIVE-WRITE` 和发送参数的现有校验、查重及防重发规则。使用流程见 [执行与验收](docs/agent-workflows/execution.md)。
- 不读取、复制或输出私有凭据；脱敏模板与正式配置分开。安装、自启动和生产恢复入口的区别见 [交付与部署](docs/agent-workflows/delivery.md)。

## 按任务读取

| 当前工作 | 读取内容 |
|---|---|
| 从故障材料生成产物、解释 need_review、核验结果 | [执行与验收](docs/agent-workflows/execution.md) |
| 改字段、报告或邮件内容 | 上述正式交付规则及涉及的模板/模块 |
| 制作便携包、新电脑部署、恢复已部署机器人 | [交付与部署](docs/agent-workflows/delivery.md) |
| 机器人运行问题 | `docs/WECOM_BOT.md`、`docs/HANDOVER_OPERATIONS.md` 中相关段落 |

## 验证和完成

- Windows 运行项目代码使用项目 `.venv/Scripts/python.exe`；交付脚本不得回退系统 Python。依赖和完整交付验证以现有安装/验收脚本为准。
- 代码修改运行受影响的检查；打包、跨模块改动或发布验收需要时再运行完整验收。不为纯说明文字改动启动业务流程或重复全量测试。报告实际命令、结果及未验证部分，不固定历史测试数量。
- Windows 上的 DOCX 视觉检查使用 Microsoft Word：只读打开、`ExportAsFixedFormat` 导出 PDF、关闭本次自动化拥有的对象，再用 Poppler 转图。保持源文件不变；仅用户明确要求时改用 LibreOffice 处理 DOCX。
- 完成用户要求的实现、产物检查和相关修复后交付。离线产物、接口提交结果和业务收件/在线回读分别举证。
