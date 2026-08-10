# GitHub 私有仓库说明

本仓库是“故障闭环系统 V1”的私有源码快照，按 `duoyu-xxx` 命名规则与其他项目区分，供后续迁移到 Gitee 使用。

- GitHub 仓库：`GZ-dy/duoyu-fault-closed-loop`
- 发布分支：`main`
- 旧中转副本：`GZ-dy/company-ai-fault-system` 的 `codex/v1-current`（保留，不与本仓库合并）
- 本地 Git 基线：`2ef6f4eea567dcb6c6ccfd56f38bbbf0871b6a26`
- 构建时间：`2026-08-10 19:53:58 +08:00`
- 原始便携 ZIP SHA-256：`15F96B41FDED4677B4A3D63B5EEE37DC7F78363E9B9201A3B5D6995BB6309898`

该快照来自构建时的当前工作区，因此包含当时尚未提交的源码、README、安装脚本和交接文档；它不继承原工作区中含 `.venv`、运行产物和本机状态的旧 Git 历史。

默认构建已排除真实飞书配置、企业微信/SMTP 凭据、DPAPI 文件、`.venv`、历史 `outputs/` 和 `work/` 数据。为保证系统可交付运行，快照仍包含内部联系人映射、处置手册、模板和拓扑资料，仓库及本分支必须保持私有，未经业务负责人确认不得公开、派生公开镜像或向无关账号转移。

后续迁移到 Gitee 时，应迁移本仓库 `main` 的同一提交。如果 Gitee 已有不同历史，先推送独立分支供人工核对；不使用 `--force` 或 `--mirror` 覆盖未知远端历史。
