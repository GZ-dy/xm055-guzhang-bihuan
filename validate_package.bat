@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0validate_package.ps1"
exit /b %errorlevel%
