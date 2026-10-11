@echo off
setlocal
title Duoyu Fault Closed Loop - Safe Local Demo
cd /d "%~dp0"

for %%F in (run_closed_loop.py INSTALL_DEPENDENCIES.ps1 validate_package.ps1) do (
    if not exist "%~dp0%%F" (
        echo [FAILED] Missing %%F
        echo Extract the complete Release ZIP before running this file.
        pause
        exit /b 1
    )
)

echo [SAFE MODE] This installs local dependencies and runs the offline validation only.
echo [SAFE MODE] It does not configure credentials, start the live bot, write Feishu, or send mail.
echo.
echo [1/2] Installing or repairing local dependencies...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_DEPENDENCIES.ps1"
if errorlevel 1 goto :failed

echo.
echo [2/2] Running tests and the offline demo...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0validate_package.ps1"
if errorlevel 1 goto :failed

echo.
echo [DONE] Safe local demo passed.
echo Results: %~dp0outputs
pause
exit /b 0

:failed
echo.
echo [FAILED] Send a screenshot or dependency_install.log for diagnosis.
pause
exit /b 1
