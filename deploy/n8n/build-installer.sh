#!/usr/bin/env bash
# 在 Mac 端打包 Smikie N8N Windows 安装包 → .zip
# 用法：./build-installer.sh [version]   (默认 1.0)
set -euo pipefail

VERSION="${1:-1.0}"
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
cp -v "${SCRIPT_DIR}/installer/uninstall.bat" "${STAGING}/installer/"
cp -v "${SCRIPT_DIR}/installer/uninstall.ps1" "${STAGING}/installer/"

mkdir -p "${STAGING}/workflows"
cp -v "${SCRIPT_DIR}"/workflows/*.json "${STAGING}/workflows/" 2>/dev/null || echo "  (no workflows yet)"

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

echo "==> 打 zip"
ZIP_PATH="${OUT_DIR}/${PKG_NAME}.zip"
rm -f "${ZIP_PATH}"
(cd "${SCRIPT_DIR}/.build" && zip -r "${ZIP_PATH}" "${PKG_NAME}" >/dev/null)

echo "==> 完成"
echo "   产出: ${ZIP_PATH}"
ls -lh "${ZIP_PATH}"
echo
echo "把 ${PKG_NAME}.zip 发给 Boss → Windows 解压 → 双击 installer/install.bat"
