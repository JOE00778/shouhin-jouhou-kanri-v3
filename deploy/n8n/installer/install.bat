@echo off
:: ============================================================
:: Smikie N8N Installer · 双击入口
:: 自动 elevate 到管理员权限后调 install.ps1
:: ============================================================

setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%install.ps1"

if not exist "%PS_SCRIPT%" (
    echo [ERROR] 找不到 install.ps1（路径: %PS_SCRIPT%）
    echo 请确保 install.bat 与 install.ps1 在同一文件夹下。
    pause
    exit /b 1
)

:: 检查管理员权限
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :run
) else (
    echo [INFO] 需要管理员权限。正在提权...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b 0
)

:run
echo ============================================================
echo  Smikie N8N Installer
echo ============================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] 安装失败（代码 %EXIT_CODE%）。请检查上方错误信息。
    pause
)

endlocal
exit /b %EXIT_CODE%
