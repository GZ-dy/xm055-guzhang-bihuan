# 故障报告话术知识库

本目录用于存放历史故障报告的脱敏参考材料，并从中沉淀运维报告话术、同类案例表达和后续措施写法。

## 目录结构

```text
data/reference/report_style_kb/
  README.md
  style_rules.md
  source_reports/
    .gitkeep
  extracted_cases.jsonl
```

## 使用边界

- 历史报告只能作为话术、同类案例和处置表达参考。
- 这里的“历史报告”指你们以前真实流转、人工确认过的故障报告，不包括本项目、Codex 或其他 AI 工具生成的报告。
- 本次故障的系统、时间、影响范围、故障原因、处理过程和恢复结果，必须来自当前 `incident_context` 或本次输入证据。
- 历史报告不得覆盖、补写或反推本次故障事实。
- 当前证据缺失的字段应进入待确认状态，不得用历史报告默认值填充。
- 没有匹配到真实历史报告案例时，可以使用通用运维专业话术进行表达润色，但不得新增事实结论。
- 放入 `source_reports/` 的文件应先脱敏，去除人员、账号、手机号、内网地址、客户信息和其他敏感内容。

## 放置规则

优先放入以下格式：

- `.docx`
- `.txt`
- `.md`

暂不建议放入截图、压缩包或未脱敏原始附件。需要保留来源时，可以在文件名中使用系统和故障类型，例如：

```text
BOMC-cas服务异常-脱敏.docx
OPENAPI-主机软死锁-脱敏.md
集中化工单-flow服务503-脱敏.txt
```

不要把 `outputs/reports/` 下生成的报告、测试样例、模板示例、AI 生成的模拟报告放入 `source_reports/`。

## 后续抽取

历史报告经人工确认后，可抽取为 `extracted_cases.jsonl`，每行一个案例，建议字段包括：

- `id`
- `system`
- `fault_type`
- `keywords`
- `symptom_phrases`
- `cause_phrases`
- `handling_phrases`
- `recovery_check_phrases`
- `followup_phrases`
- `forbidden_phrases`
- `source_file`
