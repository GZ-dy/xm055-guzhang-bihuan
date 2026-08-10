# V1 执行边界

## 当前状态

根目录 `run_closed_loop.py` 已切到 V1 最小闭环：输入文本或 `incident_context` 后，只生成主数据、最终 DOCX、校验结果和显式要求的 dry-run JSON。需要先核查主数据时，使用 `--incident-context-only` 只生成并校验 `incident_context` 后停止。

Hermes 只作为功能样板，用来确认“要做成什么能力”。当前工作区不复制它的目录组织、legacy 代码、phase 输出和杂乱中间态。

## V1 主流程

1. 输入采集：故障文字、截图 OCR、文本日志或文本附件统一进入 `incident_context`；图片和附件解析结果只作为输入来源，不扩散成新的主流程。
2. 字段契约与校验：以 `src/field_contracts/contracts/incident_context_schema.json` 固定字段边界。
3. 报告生成：从 `incident_context` 填充 clean Word 模板，生成最终 DOCX。
4. 在线记录表：从 `incident_context` 和本模块状态生成记录 payload，不从报告反推。
5. 邮件：从 `incident_context`、报告附件和离线表生成邮件内容；正式邮件默认同时包含在线记录链接、故障报告和离线表；正式发送前必须确认在线记录链接、报告附件、手册链接和手册附件已回填；在线表写入失败或关键字段缺失时阻断发信。
6. 手册：从 `incident_context` 生成手册关联结果；存在 `data/reference/manuals_index/manual_index.json` 和 `data/reference/manuals/` 时做只读匹配，未命中则输出待补充状态。
7. 正式写入闸门：在线表写入和邮件发送只允许通过 `--incident-context <path> --apply --confirm-live-write LIVE-WRITE` 进入。
8. 统一编排：只保留少产物闭环，不恢复 `phase*_outputs`。

## 历史参考处理

- 旧源码、旧脚本、旧规则、旧迁移清单：归档到 `archive/legacy/reference/`，不参与主流程。
- 旧 phase 输出：归档到 `archive/legacy/phase_outputs/`，不参与主流程。
- 旧模板快照：归档到 `archive/legacy/templates/`；正式模板在 `data/templates/reports/official/` 和 `clean/` 管理，同时保留 `.workbuddy/报告模板/` 作为旧路径兼容镜像。
- 旧知识库代码归档到 `archive/legacy/reference/data/`；确认可复用的手册索引、Markdown 手册正文和导出手册已提升到 `data/reference/`，主流程只读使用。
