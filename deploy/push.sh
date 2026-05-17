#!/bin/bash
# 一键 push 到 GitHub 部署 streamlit.app
# 用法：bash deploy/push.sh https://github.com/你的用户名/你的repo.git

set -e

REPO_URL="${1:-}"
if [ -z "$REPO_URL" ]; then
  echo "❌ 用法：bash deploy/push.sh <github-repo-url>"
  echo "   例：bash deploy/push.sh https://github.com/joe-mitsukin/shouhin-v3.git"
  exit 1
fi

cd "$(dirname "$0")/.."

# 1. .gitignore
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
data_warehouse/warehouse.db
data_warehouse/warehouse.db-*
.streamlit/secrets.toml
data/inputs/
data/outputs/
*.xlsx
*.xls
EOF

# 2. .streamlit/secrets.toml 模板（不会 commit · gitignore 已忽略真值）
mkdir -p .streamlit
cat > .streamlit/secrets.toml.example <<'EOF'
BACKEND = "supabase"
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY = "YOUR_SERVICE_ROLE_KEY_HERE"
UPLOAD_PASSWORD = "pass1234"
LARK_APP_ID = "cli_xxxxxxxxxxxxxxxx"
LARK_APP_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
LARK_SPREADSHEET_TOKEN = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
LARK_SHEET_ID = "xxxxxx"
EOF

# 3. requirements.txt（如果没有）
if [ ! -f requirements.txt ]; then
  cat > requirements.txt <<'EOF'
streamlit>=1.32
pandas>=2.0
plotly>=5.0
openpyxl>=3.1
supabase>=2.0
sqlalchemy>=2.0
requests>=2.31
EOF
fi

# 4. git init + commit + push
if [ ! -d .git ]; then
  git init
  git branch -M main
fi

git add .
git commit -m "feat: CMS v3（11 新 page + 4 modules + 5 ingestor + 16 表）"

if ! git remote | grep -q origin; then
  git remote add origin "$REPO_URL"
else
  git remote set-url origin "$REPO_URL"
fi

git push -u origin main

echo ""
echo "✅ 推送完成！下一步："
echo ""
echo "1. 打开 https://share.streamlit.io"
echo "2. New app → 选刚才推的 repo + branch=main"
echo "3. Main file: cms.py"
echo "4. Advanced → Secrets → 粘贴 secrets.toml.example 内容（替换真凭证）"
echo "5. Deploy 🚀"
