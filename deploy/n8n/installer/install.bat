@echo off
:: ============================================================
:: Smikie N8N All-in-One Installer · 双击入口
:: 编排：UAC 提权 → 检查前置依赖 → 缺啥装啥 → N8N + 改廃监控部署
:: ============================================================

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "PREREQ=%SCRIPT_DIR%check-prerequisites.ps1"
set "ENABLE_WSL=%SCRIPT_DIR%enable-wsl.ps1"
set "INSTALL_DOCKER=%SCRIPT_DIR%install-docker.ps1"
set "INSTALL_PS=%SCRIPT_DIR%install.ps1"

:: ---------- 检查所有脚本都在 ----------
for %%F in ("%PREREQ%" "%ENABLE_WSL%" "%INSTALL_DOCKER%" "%INSTALL_PS%") do (
    if not exist "%%~F" (
        echo [ERROR] 缺少安装脚本: %%~F
        echo 请确保整个 installer\ 目录完整解压。
        pause
        exit /b 1
    )
)

:: ---------- UAC 提权（管理员权限是 WSL/Docker 安装必需） ----------
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] 需要管理员权限。正在弹出 UAC 提权...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b 0
)

cls
echo ============================================================
echo  Smikie N8N All-in-One Installer
echo  这一个安装包搞定: WSL2 + Docker Desktop + N8N + 改廃监控
echo ============================================================
echo.

:: ---------- Step 1: 前置依赖检查 ----------
echo [Step 1/4] 检查前置依赖
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PREREQ%"
set "PREREQ_CODE=%errorlevel%"

if %PREREQ_CODE% equ 0 (
    echo.
    echo [INFO] 全部依赖就绪，跳过 WSL/Docker 自动安装
    goto :n8n_install
)

if %PREREQ_CODE% equ 1 (
    echo.
    echo [ABORT] 致命问题需要您人工修复。请按上面的提示处理后重新运行。
    pause
    exit /b 1
)

if %PREREQ_CODE% neq 2 (
    echo.
    echo [ERROR] 前置检查异常退出（代码 %PREREQ_CODE%）
    pause
    exit /b 1
)

:: ---------- Step 2: 自动启用 WSL2 ----------
echo.
echo [Step 2/4] 启用 WSL2 子系统
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ENABLE_WSL%"
set "WSL_CODE=%errorlevel%"

if %WSL_CODE% equ 3 (
    echo.
    echo ============================================================
    echo  Windows 功能已启用，需要重启系统才能继续。
    echo  请保存工作 -^> 重启电脑 -^> 重新双击 install.bat
    echo ============================================================
    pause
    exit /b 0
)

if %WSL_CODE% neq 0 (
    echo [ERROR] WSL2 启用失败
    pause
    exit /b 1
)

:: ---------- Step 3: 自动安装 Docker Desktop ----------
echo.
echo [Step 3/4] 安装 Docker Desktop
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_DOCKER%"
set "DOCKER_CODE=%errorlevel%"

if %DOCKER_CODE% equ 3 (
    echo.
    echo ============================================================
    echo  Docker Desktop 已安装但 daemon 未就绪。
    echo  请按提示手动启动 Docker Desktop，等图标变绿后重新双击 install.bat
    echo ============================================================
    pause
    exit /b 0
)

if %DOCKER_CODE% neq 0 (
    echo [ERROR] Docker Desktop 安装失败
    pause
    exit /b 1
)

:n8n_install
:: ---------- Step 4: N8N + 改廃监控部署 ----------
echo.
echo [Step 4/4] 部署 N8N + 改廃监控
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_PS%"
set "N8N_CODE=%errorlevel%"

if %N8N_CODE% neq 0 (
    echo.
    echo [ERROR] N8N 部署失败（代码 %N8N_CODE%）
    pause
    exit /b %N8N_CODE%
)

echo.
echo ============================================================
echo  ✅ 全套安装完成
echo ============================================================

endlocal
exit /b 0
