import streamlit as st
from shared.i18n import t, lang_selector
import streamlit.components.v1 as components
from pathlib import Path

st.set_page_config(page_title=t("商品登录"), page_icon="📝", layout="wide")
from shared.auth import require_password
require_password()
lang_selector()

st.title(t("📝 商品登录"))
st.caption(t("现有商品登録ツール（HTML 版）· 输出 NetSuite/JD/BM CSV · 第 2 步将加 Supabase 同步（T-022）"))

st.info(t("📌 这是现有商品登録ツール的 iframe 嵌入版。功能跟桌面 app 完全一致，入口集中到 Streamlit。"))

# repo 内 assets/ 相对路径（生产/本地都能找到）
html_path = Path(__file__).resolve().parent.parent / "assets" / "商品登録ツール_0418.html"
if html_path.exists():
    components.html(html_path.read_text(encoding='utf-8'), height=1500, scrolling=True)
else:
    st.error(t(f"❌ 找不到 HTML 文件：{html_path}"))
    st.markdown(t("请确认 `assets/商品登録ツール_0418.html` 已 push 到 repo"))
