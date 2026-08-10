# AGENTS.md — 故障闭环系统 V1 项目接手说明

> 适用对象:任何接手本 V1 项目根目录的代理(Agent)或人工。
> 适用范围:`/tmp/zip_in/out/搭建故障闭环系统/`(以及后续迁移到 `~/.hermes` 或 Windows 的同名结构)。
> 阅读顺序:先看本文档的"入口"再去看子模块源码;不要直接 grep `outputs` 或 `phase*` 历史目录。

---

## 1. 项目身份

- 名称:**故障闭环系统 V1**(Closed-Loop Fault System V1)
- 形态:纯本地 Python CLI,不连外部服务,只在 `--apply --confirm-live-write LIVE-WRITE` 时连飞书/SMTP
- 主入口:`run_closed_loop.py`(解析 CLI、执行自动 live 三环境门/查重幂等,再调用 `src/orchestrator/pipeline.py:run()`)
- 主数据:**`incident_context`**(由 `--text/--screenshot/--log` 生成,校验通过后驱动所有产物)
- 项目阶段:V1 最小闭环(可派单使用);L2 半自动推荐是调研稿 [SNC-148](mention://issue/3aa324e7-abbb-4d9e-a9e5-985161fd78e0),不是本仓代码。

## 2. 包内必备清单(Portable Package Manifest)

V1 便携包由 `tools/build_portable_release.ps1` 生成,默认入包(根目录视角):

| # | 路径 | 类型 | 备注 |
|---|---|---|---|
| 1 | `run_closed_loop.py` | 文件 | V1 统一入口 |
| 2 | `AGENTS.md` | 文件 | 本文档(代理接手第一站) |
| 3 | `README.md` | 文件 | 用户视角的快速上手 |
| 4 | `requirements.txt` | 文件 | Python 依赖(便于 `pip install -r`) |
| 5 | `src/` | 目录 | 业务代码(`feishu_ledger`/`field_contracts`/`input_collection`/`mail_sender`/`manuals_review`/`offline_ledger`/`orchestrator`/`report_generation` 8 子包) |
| 6 | `data/` | 目录 | 模板与参考(`templates/` + `reference/manuals/` + `reference/manuals_index/`) |
| 7 | `docs/` | 目录 | 目录职责/产物边界/过渡规范(3 个 md) |
| 8 | `scripts/` | 目录 | 4 个 feishu 探索/写入/读回脚本 |
| 9 | `tests/` | 目录 | 单元测试(`test_minimal_closed_loop.py`) |
| 10 | `tools/` | 目录 | 构建/部署脚本(`build_portable_release.ps1`) |
| 11 | `wheelhouse/` | 目录 | **可选** 离线依赖包,只在断网/无 PyPI 时入包 |

根目录附加脚本的入包规则:
- `config/private/feishu.json`(私有飞书配置,只在 `-IncludePrivateConfig` 时入包)
- `config/private/feishu.template.json`(**脱敏模板**,新成员参考用,默认入包可见)
- `install_env.ps1/.bat`、`validate_package.ps1/.bat`、`run_local_demo.ps1/.bat` 存在时自动入包
- `start_wecom_ai_bot_dry_run.ps1/.bat`、`start_wecom_ai_bot_live.ps1`、`diagnose_bot.ps1`、`stop_bot.ps1` 存在时自动入包
- `install_wecom_bot_autostart.ps1`、`setup_env.ps1`、`verify_env.ps1` 存在时自动入包，供接收方在新电脑重新配置机器人和 SMTP 凭据
- `INSTALL_DEPENDENCIES.ps1/.bat`、`NEW_PC_SETUP.ps1/.bat`、`ONE_CLICK_NEW_PC.bat`、`RUN_KEEP_OPEN.cmd`、`README_DEPENDENCIES.md` 存在时自动入包，供新电脑完整解压后一键安装并启动
- `render_docx.py`(需要 LibreOffice/soffice 的视觉 QA 渲染模块)
- `docs/CHANGELOG.md` 仓库内**应保留**(变更日志,新成员溯源用)
- `PACKAGE_MANIFEST.txt` 由 build 脚本自动生成,不存仓库
- `outputs/`、`work/`、`config/private/` 在构建时只有 `.gitkeep` 占位
- `.workbuddy/报告模板/`(6 个 docx,用户视角的本地模板,不入便携包)
- `data/reference/contacts/` 的正式联系人 JSON 和已核对的仅姓名版源表会入包，`.bak*`/临时备份不会入包
- `config/private/wecom_bot.env.template` 是脱敏模板，默认入包；`wecom_bot.dpapi.json` 绑定原 Windows 用户，禁止入包

> 可执行入口不要再造新名字:`run_closed_loop.py` 是唯一正门;所有调用必须从该入口进入。

## 3. 推荐运行栈

| 平台 | Python | 说明 |
|---|---|---|
| Linux/macOS | 3.10+ | `python3 -B run_closed_loop.py ...` |
| Windows | 3.10+ | `python -B run_closed_loop.py ...` 或 `py -B run_closed_loop.py ...` |

**依赖**(`requirements.txt`):
- `openpyxl`(离线表读写)
- `pytesseract`(可选,截图 OCR;无该库时截图走 `.ocr.txt` 旁路)

## 4. 目录职责总览

| 目录 | 职责 | 边界 |
|---|---|---|
| `src/` | 业务逻辑(orchestrator / field_contracts / input_collection / feishu_ledger / offline_ledger / report_generation / mail_sender / manuals_review) | **只放 .py 子包**,不放模板、不放产物 |
| `data/templates/` | 离线表 + 报告 docx + 报告 clean 模板 | **只读**;运行时禁止修改 `official/` |
| `data/reference/manuals_index/` | 手册索引 JSON | 索引文件本身 |
| `data/reference/manuals/` | 真正的手册 md/html | 运行时只读 |
| `data/reference/ledger_snapshots/` | 离线表归档快照 | 历史参考,不入便携包 |
| `outputs/{reports,ledger,mail,manuals,validation}/<case_id>/` | 正式产物 | 每次运行重新生成 |
| `work/cases/<case_id>/` | 临时过程文件(`incident_context.json` 等) | 不进便携包 |
| `archive/legacy/` | 历史 phase*/旧 AGENTS/历史 handoff | **只读参考**,主流程禁止恢复 |
| `.workbuddy/` | 用户视角的本地报告模板(Word 文档) | 不入便携包 |
| `tests/` | 单元测试 | `tests/__init__.py` 必须存在,全量以 unittest discover 为准 |

> 业务边界铁律:主流程禁止恢复 `phase*_outputs`,禁止从报告反推在线表,禁止运行时修改 `official/` 模板,禁止从 `data/reference/ledger_snapshots/` 还原模板。

## 5. 两步式执行(Pipeline)

```powershell
# 第一步:从短描述/截图/文本生成并校验 incident_context
python -B run_closed_loop.py --text "【BOMC-cas服务异常】..." --case-id bomc_smoke_20260709 --incident-context-only

# 第二步:从已校验 incident_context 派生所有产物(不连外部)
python -B run_closed_loop.py --incident-context work/cases/bomc_smoke_20260709/incident_context.json --all-dry-run

# 第三步(可选):live 写飞书/发邮件(三件套缺一返回 need_review)
python -B run_closed_loop.py --incident-context work/cases/bomc_smoke_20260709/incident_context.json --apply --confirm-live-write LIVE-WRITE
```

Live 闸门:--apply / --send-mail / --confirm-live-write LIVE-WRITE / 必须 `--incident-context <path>`。**任一缺失都返回 `need_review`**,不会写飞书也不会发邮件。

环境变量自动 live 还必须同时满足 `FAULT_AUTO_SEND_ON_VALIDATED=1`、`FAULT_CONFIRM_LIVE_WRITE=LIVE-WRITE`、`FAULT_PROD_CASE_TOKEN` 非空；缺一只提示阻断,不改写 CLI 参数。自动模式查重命中且`邮件闭环状态=已发送`时保留飞书 update、跳过重复发信；人工显式 `--send-mail` 不受该幂等抑制。所有 dry-run 都是纯本地流程,不读取飞书。

## 6. 关键模块索引(从主入口一路深入)

| 关心点 | 去看 |
|---|---|
| 主流程编排 | `src/orchestrator/pipeline.py`(1313 行,`run()` 主函数) |
| 字段契约/校验 | `src/field_contracts/contract.py` |
| 截图 OCR | `src/input_collection/ocr.py`(优先 `.ocr.txt` 旁路,pytesseract 退化) |
| 飞书在线表 | `src/feishu_ledger/client.py`(私有 config=`config/private/feishu.json`) |
| 离线表写入 | `src/offline_ledger/workbook.py`(从模板复制,逐 sheet 保留表头+格式) |
| DOCX 报告生成 | `src/report_generation/docx_writer.py`(从 `official/` 模板填充) |
| SMTP 邮件 | `src/mail_sender/smtp_sender.py`(授权码走 env `FAULT_SMTP_PASSWORD`) |
| 手册匹配 | `src/manuals_review/preview.py`(`_guide_paths` 处理 windows/linux 双风格路径) |

## 7. 测试约定

```bash
python3 -B -m unittest discover -s tests -v
```

- 必须有 `tests/__init__.py`(空文件即可),否则严格 unittest 模式 ImportError。
- 当前全量至少 72 个测试,数量随独立交付测试增长；以 discover 输出的 0 fail / 0 error 为准。
- 跨平台路径断言必须用 `os.sep` 或 `pathlib.PureWindowsPath/PurePosixPath` 归一化,**不要写死 `\\` 或 `/`**。

## 8. 路径风格统一约定(2026-07-09 第三轮新增,重要)

手册索引 `data/reference/manuals_index/manual_index.json` 中的 `path` 与 `project_root` 字段来自 Windows 原始环境,使用 **反斜杠**(`\`);但 V1 项目已迁移到 Linux 测试,L1 修复做了"路径双风格兼容"(`_guide_paths` 中 `PureWindowsPath` 拆 parts 再 `Path(*parts)`)。

统一约定(任何代理都必须遵守):

1. **索引里**(`manual_index.json` / `index["systems"][*]["guides"][*]["path"]`):保持原始 windows 反斜杠字符串,**不要批量改成正斜杠**——下游 `match_path` 已经做了跨平台兼容。
2. **`match_path` 输出**(运行时/产物):**始终用当前 OS 的 `Path` 字符串**(Linux 是正斜杠,Windows 是反斜杠),不强制统一。
3. **`match_target` 输出**:沿用索引原始字段值(不改 windows 反斜杠)。
4. **测试断言**:对运行时路径用 `endswith` 时,**先 `str.replace('\\', os.sep)` 归一化,再用 `endswith(os.path.join(...))`**。对索引 `project_root`,**用 `Path()` + `.resolve()` + `is_relative_to()` 比较前全部归一化**。
5. **不要在源码里再写死反斜杠字面量**(`Path("a\\b")`);要么用 `Path(*parts)`,要么用 `os.path.join`,要么用 `pathlib.PureWindowsPath`。

## 9. 飞书配置约定

| 文件 | 入包? | 内容 | 备注 |
|---|---|---|---|
| `config/private/feishu.json` | ❌ 仅 `-IncludePrivateConfig` | 实测 app_id / app_secret / app_token / table_id | **明文密钥**,不入仓不入包 |
| `config/private/feishu.template.json` | ✅ | 同上结构,但 app_id/app_secret 留空 + 注释 `REPLACE_ME` | **脱敏模板**,新成员参考 |

任何代理都**不要**把 `feishu.json` 的密钥复制到 `feishu.template.json`、AGENTS.md、注释或 commit message。

## 10. 安装 / 验收 / Demo 脚本

(`install_env` / `validate_package` / `run_local_demo` 都是单个 power-shell + 单个 bat 双胞胎,跨平台共享同一逻辑。)

Windows 依赖统一安装到项目根目录 `.venv/`；`validate_package` 和 `run_local_demo` 必须直接使用 `.venv\Scripts\python.exe`，不得回退到系统 Python。

```powershell
# 一键安装 Python 依赖(有网走 PyPI,无网走 wheelhouse/)
.\install_env.ps1
.\install_env.bat

# 一键验收:跑 unittest discover 全量测试 + 生成一份 demo 报告
.\validate_package.ps1
.\validate_package.bat

# 新人 demo:跑 BOMC 案例全 dry-run
.\run_local_demo.ps1
.\run_local_demo.bat
```

## 11. 视觉 QA(render_docx.py)

`render_docx.py` 用 LibreOffice 把 docx 渲染成 PDF 或 PNG 做视觉对照,**不**是 V1 闭环的必需环节。该模块当前**不在项目根里**,代理若被要求做视觉 QA,需要先:

```bash
# Ubuntu/Debian
sudo apt-get install -y libreoffice
# macOS
brew install --cask libreoffice
# Windows
# 装 LibreOffice 并把 soffice.exe 加到 PATH
```

然后从 `src/report_generation/docx_writer.py` 的输出 docx 直接 `soffice --headless --convert-to pdf <docx>`。如果项目里以后真要放 `render_docx.py`,请放到 `tools/` 下而非 `src/report_generation/`(后者只放业务代码)。

## 12. 文档集

| 文档 | 用途 |
|---|---|
| `README.md` | 用户视角的快速上手 |
| `AGENTS.md`(**本文档**) | 代理接手第一站 |
| `docs/DIRECTORY_MAP.md` | 新目录职责 |
| `docs/OUTPUT_POLICY.md` | 正式产物 / 临时产物 / 历史归档边界 |
| `docs/TRANSITION_PLAN.md` | V1 执行边界 + 后续接入 |
| `docs/CHANGELOG.md` | 变更日志(从 0.1.0 起) |
| `archive/legacy/reference/docs/legacy/rules/AGENTS.old.md` | 历史代理规则(只读参考) |
| `src/field_contracts/contracts/FAULT_CLOSED_LOOP_RULES.md` | **正式交付规则源**——报告措辞 / 邮件正文 / 在线表字段 / 阻塞条件 |

## 13. 接手检查清单(新代理 5 分钟自检)

1. [ ] `python3 -B -m unittest discover -s tests -v` 至少 72 个且 0 fail / 0 error
2. [ ] Windows 运行 `run_local_demo.bat`（其他平台按第 5 节完整事实样例执行）且全 dry-run 通过；只有“BOMC-cas服务异常”的短描述应进入 `need_review`，不得编造时间和处理过程
3. [ ] `outputs/{reports,ledger,mail,manuals,validation}/<case_id>/` 5 类产物齐全
4. [ ] `work/cases/<case_id>/incident_context.json` `status=validated`
5. [ ] 没有 `phase*_outputs/`、`hermes/`、`F:\` 路径残留在产物
6. [ ] `config/private/feishu.json` 未被复制到 `feishu.template.json`
7. [ ] `archive/legacy/` 未被任何主流程代码引用

## 14. 不要做的事

- 不要从 `outputs/` 产物反推在线表 / 反推 `incident_context`
- 不要把 `feishu.json` 的密钥写到 `feishu.template.json`、AGENTS.md 或 git commit
- 不要在主流程里读 `archive/legacy/` 或 `phase*_outputs/`
- 不要在没有 `--confirm-live-write LIVE-WRITE` 时调 `--apply` 或 `--send-mail`
- 不要在测试里写死 `\\` 或 `/` 路径字面量(用 `os.sep` / `pathlib.Pure*Path`)
- 不要把截图/邮件正文/报告 docx 当成 `incident_context` 的可信来源

---

**版本**:0.1.0(2026-07-09 第三轮对账后落地)
**维护**:何周辉 拍板,代理(Dispatch-DY)落盘
