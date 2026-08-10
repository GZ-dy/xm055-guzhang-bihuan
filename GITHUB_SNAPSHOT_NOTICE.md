# GitHub 私有仓库与发布边界

本仓库是“故障闭环系统 V1”的私有可运行快照，按 `duoyu-xxx` 命名规则与其他项目区分。它供已授权成员下载、本地验收、内部维护和后续迁移使用，不是只能浏览的源码存档。

- GitHub 仓库：`GZ-dy/duoyu-fault-closed-loop`
- `main`：当前源码、模板、文档和构建脚本。
- [Releases](https://github.com/GZ-dy/duoyu-fault-closed-loop/releases)：已验收的 Windows 便携 ZIP、SHA-256 校验文件和版本验收信息。
- 旧中转副本：`GZ-dy/company-ai-fault-system` 的 `codex/v1-current`（保留，不与本仓库合并）。

每个交付包的文件名、源码提交、测试结果和 SHA-256 以对应 Release 页面为准。不要用 GitHub 自动生成的 Source code ZIP 替代项目构建脚本生成的正式便携包。

默认构建已排除真实飞书配置、企业微信/SMTP 凭据、DPAPI 文件、`.venv`、历史 `outputs/` 和 `work/` 数据。仓库和 Release 仍包含内部联系人映射、处置手册、报告样本、模板和拓扑资料，必须保持私有；未经业务负责人确认，不得公开、镜像或转发给无关账号。

后续迁移到 Gitee 时，应迁移已发布 Release 对应的同一 `main` 提交。如果 Gitee 已有不同历史，先推送独立分支供人工核对；不使用 `--force` 或 `--mirror` 覆盖未知远端历史。
