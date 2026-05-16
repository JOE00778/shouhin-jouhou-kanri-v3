#!/usr/bin/env bash
# 在 Mac 端打包 Smikie N8N Windows 安装包 → .zip
# 用法：./build-installer.sh [version]   (默认 1.0)
set -euo pipefail

VERSION="${1:-1.8}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_NAME="Smikie-N8N-Installer-v${VERSION}"
STAGING="${SCRIPT_DIR}/.build/${PKG_NAME}"
OUT_DIR="${SCRIPT_DIR}/dist"

echo "==> 清理 staging"
rm -rf "${SCRIPT_DIR}/.build"
mkdir -p "${STAGING}"
mkdir -p "${OUT_DIR}"

echo "==> 复制安装文件"
cp -v "${SCRIPT_DIR}/docker-compose.yml" "${STAGING}/"
cp -v "${SCRIPT_DIR}/.env.template" "${STAGING}/"
cp -v "${SCRIPT_DIR}/README.md" "${STAGING}/"

mkdir -p "${STAGING}/installer"
cp -v "${SCRIPT_DIR}/installer/install.bat" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/install.ps1" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/check-prerequisites.ps1" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/enable-wsl.ps1" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/install-docker.ps1" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/uninstall.bat" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/uninstall.ps1" "${STAGING}/installer/"

mkdir -p "${STAGING}/workflows"
cp -v "${SCRIPT_DIR}"/workflows/*.json "${STAGING}/workflows/" 2>/dev/null || echo "  (no workflows yet)"

# stock_monitor 容器源码（改廃监控）
echo "==> 复制 stock_monitor 源码"
mkdir -p "${STAGING}/stock_monitor/scripts"
mkdir -p "${STAGING}/stock_monitor/cookies"
mkdir -p "${STAGING}/stock_monitor/state"
mkdir -p "${STAGING}/stock_monitor/reports"
mkdir -p "${STAGING}/stock_monitor/logs"
cp -v "${SCRIPT_DIR}/stock_monitor/Dockerfile" "${STAGING}/stock_monitor/"
cp -v "${SCRIPT_DIR}/stock_monitor/webhook_server.py" "${STAGING}/stock_monitor/"
cp -v "${SCRIPT_DIR}"/stock_monitor/scripts/*.py "${STAGING}/stock_monitor/scripts/" 2>/dev/null || true
cat > "${STAGING}/stock_monitor/cookies/README.txt" <<'EOF'
把供应商登录 cookies 放在这里。
具体文件命名见 ../scripts/scraper.py 顶部说明。
没有 cookies 也能跑，仅免登录公开页可访问。
EOF

# image_processor 容器源码（商品图处理：抠图/超分/SPU 多图合成）
echo "==> 复制 image_processor 源码"
mkdir -p "${STAGING}/image_processor/assets"
cp -v "${SCRIPT_DIR}/image_processor/Dockerfile" "${STAGING}/image_processor/"
cp -v "${SCRIPT_DIR}/image_processor/requirements.txt" "${STAGING}/image_processor/"
cp -v "${SCRIPT_DIR}/image_processor/app.py" "${STAGING}/image_processor/"
cp -v "${SCRIPT_DIR}"/image_processor/assets/*.png "${STAGING}/image_processor/assets/" 2>/dev/null || true

# 占位空目录（首次启动后会填充；要让 Windows 上 zip 能解出空目录）
mkdir -p "${STAGING}/data/n8n"
mkdir -p "${STAGING}/data/files"
echo "Smikie N8N data directory · 不要手动改这里的内容（容器自管）" \
    > "${STAGING}/data/README.txt"

# 写一份版本信息
cat > "${STAGING}/VERSION.txt" <<EOF
Smikie N8N Installer
Version: ${VERSION}
Build Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')
Build Host: $(hostname)
Source Repo: CMS-v230 / deploy/n8n
EOF

echo "==> 转换 Windows 友好行尾（CRLF）"
# .bat 必须是 CRLF；.ps1/.md/.yml/.template 用 LF 也能工作但还是统一 CRLF 保险
for f in $(find "${STAGING}" -type f \( \
    -name '*.bat' -o -name '*.ps1' -o -name '*.template' \
    -o -name '*.md' -o -name '*.yml' -o -name '*.txt' \)); do
    if command -v unix2dos >/dev/null 2>&1; then
        unix2dos -q "${f}"
    else
        # macOS 没 unix2dos：手动 sed 加 CR
        tmp="${f}.tmp"
        sed -e 's/$/\r/' "${f}" > "${tmp}" && mv "${tmp}" "${f}"
    fi
done

echo "==> 给 .ps1 加 UTF-8 BOM (Windows PowerShell 5 GBK 默认编码兼容)"
# 中文 Windows 自带 PS 5.x 默认用 ANSI/GBK 读 .ps1。
# 无 BOM 的 UTF-8 中文会被当 GBK 解码乱码 → PS 解析失败。
# 加 UTF-8 BOM (EF BB BF) 让 PS 强制走 UTF-8。
for f in $(find "${STAGING}" -type f -name '*.ps1'); do
    # 前 3 字节不是 EF BB BF 才加（防止重复）
    if ! head -c 3 "${f}" | xxd -p 2>/dev/null | grep -qi '^efbbbf'; then
        tmp="${f}.bom.tmp"
        printf '\xEF\xBB\xBF' > "${tmp}"
        cat "${f}" >> "${tmp}"
        mv "${tmp}" "${f}"
    fi
done

echo "==> 给 .bat 顶部插 chcp 65001 (cmd UTF-8 代码页，让中文 echo 不乱码)"
# .bat 文件不能加 BOM (cmd 处理 BOM 有 bug)，
# 改成在 @echo off 之后插一行 chcp 65001 >nul 让 cmd 切到 UTF-8 代码页。
for f in $(find "${STAGING}" -type f -name '*.bat'); do
    # 跳过已经有 chcp 的
    if ! grep -q 'chcp 65001' "${f}"; then
        tmp="${f}.chcp.tmp"
        # 在第一行 (@echo off) 之后插入 chcp 65001 行
        awk 'NR==1 {print; print "chcp 65001 >nul\r"; next} {print}' "${f}" > "${tmp}"
        mv "${tmp}" "${f}"
    fi
done

echo "==> 打 zip"
ZIP_PATH="${OUT_DIR}/${PKG_NAME}.zip"
rm -f "${ZIP_PATH}"
(cd "${SCRIPT_DIR}/.build" && zip -r "${ZIP_PATH}" "${PKG_NAME}" >/dev/null)

echo "==> 完成"
echo "   产出: ${ZIP_PATH}"
ls -lh "${ZIP_PATH}"
echo
echo "把 ${PKG_NAME}.zip 发给 Boss → Windows 解压 → 双击 installer/install.bat"
