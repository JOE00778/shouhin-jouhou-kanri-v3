@echo off
REM CMS V2.3 一键重新部署 (含 builder 缓存清理)
REM 双击运行即可: 拉最新代码 → 清 builder 缓存 → 重建镜像 → 显示日志
chcp 65001 >nul
cd /d "%~dp0..\..\"

echo.
echo ========================================
echo   CMS V2.3 重新部署 (拉代码 + 清缓存 + 重建)
echo ========================================
echo.

echo [1/5] 拉取 GitHub 最新代码...
git pull origin main
if errorlevel 1 (
    echo.
    echo [ERROR] git pull 失败. 检查网络 / 是否有未提交的本地改动.
    pause
    exit /b 1
)

echo.
echo [2/5] 清理 docker builder 缓存 (轻量,只清没用的层)...
docker builder prune -f

echo.
echo [3/5] 重建 streamlit 镜像并启动 (up -d --build)...
docker compose -f deploy\windows\docker-compose.yml up -d --build streamlit
if errorlevel 1 (
    echo.
    echo [ERROR] docker compose 失败. 看 Docker Desktop 是否在运行.
    pause
    exit /b 1
)

echo.
echo [4/5] 容器状态:
docker compose -f deploy\windows\docker-compose.yml ps

echo.
echo [5/5] 最近 30 行日志:
docker compose -f deploy\windows\docker-compose.yml logs --tail 30 streamlit

echo.
echo ========================================
echo   完成! 打开 https://smikie-cms.cc 验证
echo   (如果改动还没生效, 跑 redeploy-clean.bat 完全重建)
echo ========================================
echo.
pause
