@echo off
setlocal
title Fault Closed Loop - New PC Setup
cd /d "%~dp0"

for %%F in (run_closed_loop.py INSTALL_DEPENDENCIES.ps1 NEW_PC_SETUP.ps1 install_wecom_bot_autostart.ps1) do (
    if not exist "%~dp0%%F" (
        echo [FAILED] Missing %%F
        echo Extract the complete one-click package into the folder containing run_closed_loop.py.
        pause
        exit /b 1
    )
)

echo [1/2] Installing Python and runtime dependencies...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_DEPENDENCIES.ps1"
if errorlevel 1 goto :failed

echo.
echo [2/2] Configuring autostart and starting the bot...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0NEW_PC_SETUP.ps1" -SkipDependencyInstall
if errorlevel 1 goto :failed

echo.
echo [DONE] New-PC setup completed.
pause
exit /b 0

:failed
echo.
echo [FAILED] Send a screenshot or dependency_install.log for diagnosis.
pause
exit /b 1
