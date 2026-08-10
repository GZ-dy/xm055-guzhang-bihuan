@echo off
setlocal

rem Keep one source of startup and gate logic in the PowerShell script.
set "PS_ARGS=%*"
set "PS_ARGS=%PS_ARGS:--mock=-Mock%"
set "PS_ARGS=%PS_ARGS:--mock-text=-MockText%"
set "PS_ARGS=%PS_ARGS:--case-prefix=-CasePrefix%"
set "PS_ARGS=%PS_ARGS:--online-record-url=-OnlineRecordUrl%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_wecom_ai_bot_dry_run.ps1" %PS_ARGS%
exit /b %errorlevel%
