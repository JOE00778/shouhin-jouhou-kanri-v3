@echo off
:: Smikie N8N Uninstaller · 双击入口
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%uninstall.ps1"

if not exist "%PS_SCRIPT%" (
    echo [ERROR] 找不到 uninstall.ps1
    pause
    exit /b 1
)

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] 需要管理员权限。正在提权...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
pause
endlocal
