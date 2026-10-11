# 输出收口规则

## 正式产物

正式产物只放 `outputs/`，按 `case_id` 分目录：

- `outputs/reports/<case_id>/<系统><月>月<日>日故障报告.docx`
- `outputs/validation/<case_id>/validation_result.json`
- `outputs/ledger/<case_id>/execution_plan.json`
- `outputs/ledger/<case_id>/record_payload.json`
- `outputs/mail/<case_id>/mail_preview.json`
- `outputs/mail/<case_id>/pre_mail_check.json`
- `outputs/mail/<case_id>/mail_send_result.json`
- `outputs/manuals/<case_id>/manual_match_result.json`

其中 dry-run、live gate 和邮件发送结果 JSON 只在命令显式要求或阻断时生成。默认不保留重复预览、未定义 gate 文件、tmp JSON 或旧报告快照。

## 临时产物

临时产物只放 `work/`：

- `work/cases/<case_id>/incident_context.json`
- `work/cases/_message_registry.sqlite3` 是企微消息与 `case_id` 的持久幂等登记簿，不属于可删除缓存
- `work/intake/` 用于原始输入暂存
- `work/tmp/` 用于可删除的工具临时文件

同一个 `case_id` 重跑会覆盖同名文件，避免同一故障反复堆积多轮输出。

清理、迁移或重装时必须保留 `_message_registry.sqlite3`；删除它会丢失跨重启去重和多轮补充关联。它只保存消息身份、会话作用域、业务文本及本地处理结果，不保存企微密钥或回调 URL。

## 历史归档

旧项目报告、旧预览、旧 dry-run、旧模板快照、旧手册索引、旧知识库和旧 phase 输出统一放 `archive/legacy/`。这些文件只用于追溯和人工对照，不参与主流程运行。

不允许把历史文件重新放回 `outputs/`、`src/`、`data/` 或项目根目录。
