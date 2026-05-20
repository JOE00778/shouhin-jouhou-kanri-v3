@echo off
REM CMS · 轻量 git pull · bind mount 自动加载新代码（不重建镜像）
REM 使用：放桌面双击 / 元川さん 远程登录后双击
REM
REM 跟 redeploy.bat 区别：
REM   - redeploy.bat = 拉代码 + 清缓存 + docker compose build + restart （重型）
REM   - update-cms.bat = 仅 git pull + 显示容器状态（轻量 · 日常 push 后用这个）
REM
REM 何时改用 redeploy.bat:
REM   - 新增 Python 依赖（pyproject.toml / requirements.txt 改了）
REM   - 改 .env / docker-compose.yml / Dockerfile

chcp 65001 >nul
title CMS Update · 元川さん
cd /d C:\Users\smiki\CMS-v230

echo.
echo ============================================
echo   CMS git pull · %date% %time%
echo ============================================
echo.

echo [1/3] git pull origin main...
git pull origin main
if errorlevel 1 (
    echo.
    echo [ERROR] git pull 失败 · 看上面错误
    echo  - 常见：本地有未提交改动 / 网络问题 / merge conflict
    pause
    exit /b 1
)

echo.
echo [2/3] 当前 HEAD:
git log -1 --oneline

echo.
echo [3/3] Streamlit 容器状态:
docker compose -f deploy\windows\docker-compose.yml ps streamlit

echo.
echo ============================================
echo   完成 · Streamlit bind mount 已加载新代码
echo ============================================
echo.
echo 如新增依赖 / 改 .env / 改 Dockerfile · 请改跑 redeploy.bat
echo.
pause
