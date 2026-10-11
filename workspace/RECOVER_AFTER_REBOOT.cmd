@echo off
setlocal
title Fault Closed Loop - Recover After Reboot
cd /d "%~dp0"

if not exist "%~dp0recover_after_reboot.ps1" (
    echo [FAILED] Missing recover_after_reboot.ps1
    echo Copy both recovery files into the folder containing run_closed_loop.py.
    pause
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recover_after_reboot.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [DONE] Recovery script completed successfully.
) else if "%RC%"=="3" (
    echo [CANCELLED] Nothing was changed.
) else (
    echo [FAILED] Recovery script returned exit code %RC%.
)
pause
exit /b %RC%
