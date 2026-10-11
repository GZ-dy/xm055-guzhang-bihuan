---
name: fault-closed-loop
description: 在 protect10 故障闭环项目中，从故障材料生成和核验报告、台账、邮件预览，或准备便携交付；不用于一般 SQL 审计。
---

# 故障闭环交付

本技能属于当前项目，命令从项目根目录执行。将用户材料经 `incident_context` 校验后派生所需产物，完成对应核验与修复。

- 生成产物、定位 `need_review` 或核验真实执行时，读取 [执行与验收](../../../docs/agent-workflows/execution.md)。
- 便携包、安装或机器人恢复任务读取 [交付与部署](../../../docs/agent-workflows/delivery.md)。
- 修改业务字段或措辞时读取项目 `src/field_contracts/contracts/FAULT_CLOSED_LOOP_RULES.md`。

只加载当前分支。保持用户材料、模板和当前授权范围；真实发送不因选中本技能而获得许可。本地修复和离线产物持续完成到用户要求的可验证结果。
