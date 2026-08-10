# 故障报告模板清单

本目录是故障报告模板业务基准目录。模板文件只读使用，运行时不得修改模板本体。

## official

| 项目 | 内容 |
| --- | --- |
| 用途 | 唯一正式故障报告模板，作为最终报告格式基准 |
| 路径 | `data/templates/reports/official/XX系统XXXX年XX月XX日故障报告.docx` |
| 来源 | 已迁入本工作区 `data/templates/reports/official/` |
| 大小 | 35456 bytes |
| SHA256 | `F923662E09D4FEB2790E1781C4F7F6B5C47B287E2DB7EE16319FC0E3E1E19A76` |
| 规则 | 不允许程序运行时修改；不复制到 `outputs/` 作为模板副本 |

## clean

| 项目 | 内容 |
| --- | --- |
| 用途 | 干净填充底稿，结构确认后用于生成每个 case 的报告副本 |
| 路径 | `data/templates/reports/clean/XX系统XXXX年XX月XX日空白模板故障报告.docx` |
| 来源 | 已迁入本工作区 `data/templates/reports/clean/` |
| 大小 | 33346 bytes |
| SHA256 | `6D6C1C5D37403A2542A8AD1E7BDB7536F32D1C0657296A2369E9B4C81F8C18BA` |
| 规则 | 只有结构与 `official` 一致后，才允许作为填充来源 |

## 目录规则

- 正式模板：`data/templates/reports/official/`
- 干净底稿：`data/templates/reports/clean/`
- 旧路径兼容镜像：`.workbuddy/报告模板/`
- 历史模板：`archive/legacy/templates/`
- 生成报告：`outputs/reports/<case_id>/`

`.workbuddy/报告模板/` 只用于兼容旧流程的模板查找习惯；模板内容必须与 `data/templates/reports/` 的正式模板/干净底稿保持同步。禁止把模板放根目录、复制到 `outputs/`、生成多个模板副本，或在运行过程中修改模板文件。
