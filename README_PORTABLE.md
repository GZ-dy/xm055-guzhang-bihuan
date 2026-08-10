# Fault Closed Loop V1 Portable Package

This package contains only the current local V1 workspace resources, per AGENTS.md section 2 "包内必备清单":

Required (always included):
- `run_closed_loop.py`
- `AGENTS.md` (handover doc)
- `requirements.txt`
- `requirements-wecom.txt`
- `README.md`
- `src/`, `data/`, `docs/`, `scripts/`, `tests/`, `tools/`

Optional (included if present at build time, see comments):
- `wheelhouse/` (only when `-IncludeWheelhouse` is given)
- `config/private/feishu.template.json` (sanitized template)
- `config/private/wecom_bot.env.template` (sanitized template)
- `install_env.ps1` / `install_env.bat` / `validate_package.ps1` / `validate_package.bat` / `run_local_demo.ps1` / `run_local_demo.bat`
- `start_wecom_ai_bot_dry_run.ps1` / `.bat`, `start_wecom_ai_bot_live.ps1`, `install_wecom_bot_autostart.ps1`, `setup_env.ps1`, `verify_env.ps1`, `diagnose_bot.ps1`, `stop_bot.ps1`
- `INSTALL_DEPENDENCIES.ps1` / `.bat`, `NEW_PC_SETUP.ps1` / `.bat`, `ONE_CLICK_NEW_PC.bat`, `RUN_KEEP_OPEN.cmd`, `README_DEPENDENCIES.md`
- `render_docx.py` (LibreOffice wrapper)

It does not call the hermes workspace or the old `故障报告` / `更新故障记录表` / `故障手册知识库` directories.

Dry-run example:

```powershell
python -B run_closed_loop.py --text "故障信息..." --all-dry-run
```

Private Feishu config is excluded by default. Use `-IncludePrivateConfig` only for a trusted local handoff.
WeCom, SMTP, and DPAPI credential files are never copied. Configure them again on the recipient PC.

New-PC one-click setup: extract the ZIP completely, then double-click `RUN_KEEP_OPEN.cmd`.
The command window and `dependency_install.log` stay available if setup fails.
