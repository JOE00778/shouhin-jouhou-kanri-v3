@echo off
REM CMS V2.3 完全重建部署 (彻底清缓存)
REM 适用场景: 改了 Dockerfile / requirements.txt / 或 redeploy.bat 没生效时
REM 比 redeploy.bat 慢 2-3 分钟,但保证从零重新构建
chcp 65001 >nul
cd /d "%~dp0..\..\"

echo.
echo ========================================
echo   CMS V2.3 完全重建 (--no-cache 全量)
echo ========================================
echo.

echo [1/6] 拉取 GitHub 最新代码...
git pull origin main
if errorlevel 1 (
    echo.
    echo [ERROR] git pull 失败.
    pause
    exit /b 1
)

echo.
echo [2/6] 停止并删除现有容器...
docker compose -f deploy\windows\docker-compose.yml down

echo.
echo [3/6] 清理所有 docker builder 缓存 (-af 全量)...
docker builder prune -af

echo.
echo [4/6] 删除 streamlit 镜像 (强制下次完全重建)...
for /f "tokens=*" %%i in ('docker images -q smikie-cms-streamlit 2^>nul') do docker rmi -f %%i
for /f "tokens=*" %%i in ('docker images -q windows-streamlit 2^>nul') do docker rmi -f %%i

echo.
echo [5/6] 完全重建镜像 (--no-cache, 慢)...
docker compose -f deploy\windows\docker-compose.yml build --no-cache streamlit
if errorlevel 1 (
    echo.
    echo [ERROR] build 失败.
    pause
    exit /b 1
)

echo.
echo [6/6] 启动容器...
docker compose -f deploy\windows\docker-compose.yml up -d streamlit
if errorlevel 1 (
    echo.
    echo [ERROR] up 失败.
    pause
    exit /b 1
)

echo.
echo 容器状态:
docker compose -f deploy\windows\docker-compose.yml ps
echo.
echo 最近 30 行日志:
docker compose -f deploy\windows\docker-compose.yml logs --tail 30 streamlit

echo.
echo ========================================
echo   完全重建完成! 打开 https://smikie-cms.cc
echo ========================================
echo.
pause
