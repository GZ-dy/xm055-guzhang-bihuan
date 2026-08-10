@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0NEW_PC_SETUP.ps1"
exit /b %ERRORLEVEL%
