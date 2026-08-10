@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0INSTALL_DEPENDENCIES.ps1" (
    echo [FAILED] Extract the complete ZIP before running this file.
    pause
    exit /b 1
)
if not exist "%~dp0run_closed_loop.py" (
    echo [FAILED] Missing run_closed_loop.py in the current folder.
    echo Extract this package into the fault-system workspace root.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_DEPENDENCIES.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo [FAILED] Send dependency_install.log or a screenshot.
if "%EXIT_CODE%"=="0" echo [DONE] Dependencies installed.
pause
exit /b %EXIT_CODE%
