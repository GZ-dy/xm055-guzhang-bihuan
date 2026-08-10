# 新目录职责

## src/input_collection

输入采集。承接故障文字、截图、日志或附件，输出标准 `incident_context` 或 case 输入包。

## src/field_contracts

字段契约与校验。保存 V1 `incident_context` schema、闭环规则、报告字段校验和在线表字段校验。

## src/report_generation

报告生成。负责把 `incident_context` 填入 Word 模板，生成最终故障报告 DOCX。

## src/feishu_ledger

在线记录表。负责从 `incident_context` 和本模块结果生成记录 payload；不得从报告反推字段。

## src/mail_sender

邮件模块。负责从 `incident_context` 和报告附件生成邮件预览或发送结果；不把邮件产物写进源码目录。

## src/manuals_review

手册关联。负责从 `incident_context` 生成手册匹配结果；手册索引接入前不得引用旧工作区路径。

## src/orchestrator

统一编排入口。只调用 V1 功能模块，并统一把过程文件写入 `work/`、正式产物写入 `outputs/`。

## config

环境模板、运行配置、目标表配置等。禁止放运行输出。

## data

可复用静态数据。当前保留模板、确认后的静态基准、只读手册索引、Markdown 手册正文和导出手册；旧知识库代码仍统一放 `archive/legacy/reference/`。

## outputs

正式产物。报告、在线记录表、邮件、手册和校验结果按功能分开。

## work

临时输出。输入暂存、单次 case 工作目录、scratch、日志、tmp。这里的内容默认可重建。

## archive

历史产物。旧 phase 输出、历史快照、旧交付包、旧结构说明和旧实现参考全部放这里，不参与主工程运行。

## docs

目录说明、输出策略和 V1 执行边界。旧规则和迁移清单只放 `archive/legacy/reference/docs/`。
