# 故障闭环系统

> **XM055** · 历史资料 · 将已处理的用户报障整理为统一事实，生成报告、台账、手册匹配和通知材料

本项目面向 Windows 运维场景，把故障事实校验、文档生成和后续登记串成一个流程。主流程使用规则和模板，CLI 默认走本地 dry-run；正式写入在线台账或发送邮件需要另外配置并通过相应门禁。

仓库保存历史源码和 v0.2.0 Windows 发布包。本机原目录尚未找回，不能确认是否另有未上传改动；以下入口和行为以仓库中的版本为准。

## 功能

- 校验故障事实与字段契约，缺少必要信息时指出缺口。
- 生成 Word 故障报告、Excel 离线台账和校验结果。
- 根据故障材料匹配处置手册，并保留匹配依据。
- 生成邮件预览，区分本地产物与正式发送。
- 提供 CLI、本地演示和企业微信机器人入口。
- 记录重复检查、发送状态和恢复处理，支持交接核验。

## 工作方式

先形成并校验统一事实，再生成报告、台账、手册匹配和邮件预览。正式飞书写入与邮件发送使用独立门禁，不能因为本地文档已经生成就认定外部投递成功。

## 技术栈

| 部分 | 技术与要求 |
|---|---|
| 运行环境 | Windows 10/11 x64、PowerShell 5.1 或以上 |
| 源码运行 | Python 3.10 或以上，项目独立虚拟环境 |
| 流程 | Python 规则、字段契约与文档模板 |
| 本地产物 | Word、Excel、邮件预览及校验记录 |
| 可选外部接入 | 企业微信、飞书、SMTP，分别配置与验收 |

## 目录结构

| 路径 | 内容 |
|---|---|
| [run_closed_loop.py](run_closed_loop.py) | 统一 CLI 入口 |
| `src/field_contracts/` | 字段契约与校验规则 |
| `src/orchestrator/` | 流程编排 |
| `src/report_generation/` | 故障报告生成 |
| [config/](config/) | 配置模板与本机配置位置 |
| `data/templates/` | 文档模板 |
| `data/reference/` | 手册与参考材料 |
| `outputs/` | 本地报告、台账、预览和校验结果 |
| [docs/](docs/) | 本地运行、交接和机器人说明 |
| `tests/` | 规则与流程测试 |

## 快速开始

已获授权的使用者可从 [v0.2.0 发布页](https://github.com/GZ-dy/xm055-guzhang-bihuan/releases/tag/v0.2.0) 下载 Windows 便携包和对应 SHA-256 文件，校验后完整解压，再运行 `START_LOCAL_DEMO.cmd`。

维护源码时，先准备 Python 3.10 或以上，在项目根目录执行原有环境与离线演示入口：

```powershell
.\install_env.ps1
.\run_local_demo.ps1
```

演示结果保存在 `outputs/`。需要检查本地代码、模板和入口时，使用 `validate_package.ps1`；详细步骤见 [本地运行说明](docs/LOCAL_RUN.md) 与 [原项目说明](README_整理前.md)。

本地 Demo 不连接业务系统。企业微信 dry-run 入口仍会连接并回复企微，只是保持业务写入为 dry-run；两种运行方式不要混用。

## 配置

| 配置 | 位置或方式 |
|---|---|
| 飞书模板 | `config/private/feishu.template.json` |
| 企业微信模板 | `config/private/wecom_bot.env.template` |
| 真实凭据 | 当前运行机的 Windows DPAPI 或环境变量 |
| 运行与交接要求 | [HANDOVER_OPERATIONS.md](docs/HANDOVER_OPERATIONS.md) |

模板保持脱敏。新电脑应重建环境与本机凭据，不复用从其他电脑复制的 `.venv` 或 DPAPI 文件。正式发送、在线台账写入和自启动按交接手册单独确认。

## 分支与版本

| 入口 | 用途 |
|---|---|
| `main` | 现存历史源码与中文说明 |
| [v0.2.0](https://github.com/GZ-dy/xm055-guzhang-bihuan/releases/tag/v0.2.0) | 历史 Windows 发布包与校验附件 |
| [backup/workspace-20261010](https://github.com/GZ-dy/xm055-guzhang-bihuan/tree/backup/workspace-20261010) | 2026-10-10 工作区保存分支，与默认分支分别保留 |

发布包能否在当前电脑使用，仍需重新检查环境、模板和本地 Demo；历史验收记录不代表当前生产连接可用。

## 备份与恢复

2026-10-27 已下载并恢复固定提交 [`9588b6925458`](https://github.com/GZ-dy/xm055-guzhang-bihuan/tree/9588b6925458689bb44e5042aa65763c44ca104f) 的 319 个文件，逐个 Git 对象核对一致。首页之后的文档变化不改变该核验保存点。

这是现存 GitHub 历史源码的恢复依据，不证明包含原电脑最后未提交内容。源码、可运行发布包、私有配置、业务数据和外部发送记录分别核对。

旧工作区恢复包的对应关系先查私有恢复仓库的 [project-sources.json](https://github.com/GZ-dy/xiangmu-wanzheng-huifu-beifen/blob/main/project-sources.json) 与发布说明，再确认日期、附件和密钥要求。

所有恢复先进入独立目录。完成本地产物校验后，再按交接手册处理外部接入与历史待发记录，不能把恢复旧目录视为授权补发任务。
