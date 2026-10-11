# 故障材料执行与验收

从项目根目录运行。下面的“实际案例ID”和材料路径需要替换为本次输入；不要执行示例占位内容。Windows 使用 `.venv/Scripts/python.exe`，Linux/macOS 使用项目选定的 Python 环境。

## 离线产物

先从用户材料生成事实：

```powershell
.\.venv\Scripts\python.exe -B run_closed_loop.py --input-file "本次故障材料.txt" --case-id "实际案例ID" --incident-context-only
```

输入也支持 `--text`、`--screenshot`、`--log`；截图可使用同名 `.ocr.txt` 旁路。检查 `work/cases/实际案例ID/incident_context.json` 的校验结果。材料不足时保留 `need_review`，说明受影响的事实或输出。

事实校验通过后生成全部离线产物：

```powershell
.\.venv\Scripts\python.exe -B run_closed_loop.py --incident-context "work/cases/实际案例ID/incident_context.json" --all-dry-run
```

显式 dry-run 不进入真实飞书/SMTP 流程，也抑制入口的自动 live 参数注入。不要通过清除或改写用户环境配置达到离线效果。

回读本次 `outputs/` 下报告、台账、邮件预览、手册匹配和校验结果，核对同一 `case_id`、源事实、字段契约和缺失项。模板或报告问题按本次涉及内容做视觉检查，不重复整个历史案例集。

## 已授权的真实执行

仅在用户明确要求对应的真实写入/发送、且目标案例与收件范围已确定时进入此分支。先完成本次离线产物及内容核对，按现有程序传入 `--incident-context`、`--apply`、`--confirm-live-write LIVE-WRITE`；发送邮件还使用 `--send-mail`。这些命令参数是程序门禁，不能替代用户授权。

自动发送路径还依赖三个现有环境条件：`FAULT_AUTO_SEND_ON_VALIDATED=1`、`FAULT_CONFIRM_LIVE_WRITE=LIVE-WRITE`、`FAULT_PROD_CASE_TOKEN` 非空。只核对存在性和运行结果，不输出令牌值，也不为了通过校验擅自设置这些条件。

使用入口既有的 `case_id` 查重。自动模式发现“邮件闭环状态=已发送”会跳过重复发信；人工 `--send-mail` 不受相同抑制，因此结果不明或已发送时先查回执和当前记录，不能直接重跑发送命令。

接口提交、在线字段回读和收件证据各有不同含义。以本次业务要求确定完成证据；现有 JSON、HTTP 成功或 SMTP 接受不自动等于全部业务验收。

## 修改实现时的入口

| 关注点 | 路径 |
|---|---|
| 编排/门禁 | `run_closed_loop.py`、`src/orchestrator/pipeline.py` |
| 字段和事实校验 | `src/field_contracts/contract.py`、`src/field_contracts/contracts/` |
| OCR | `src/input_collection/ocr.py` |
| 飞书 | `src/feishu_ledger/client.py` |
| 台账 | `src/offline_ledger/workbook.py` |
| DOCX | `src/report_generation/docx_writer.py` |
| SMTP | `src/mail_sender/smtp_sender.py` |
| 手册匹配 | `src/manuals_review/preview.py` |

索引保留 Windows 路径字符串；运行时跨平台适配使用 `PureWindowsPath`、`Path` 或 `os.path.join`。断言按对应的索引/运行时语义处理，不批量转换索引，也不在跨平台路径中写死分隔符。
