"""现有 order-management-app 源码（清洁版）。

来源：Boss 在 2026-05-02 直接提供
状态：⚠️ **由于会话单消息 50,000 字符限制，本次接收到的源码在
`mode == "monthly_sales"` 的 `fetch_data` 函数定义中途被截断。**

已包含的 mode（完整）：
  - home / order_ai / search_item / purchase_history / price_improve
  - csv_upload（含 sales / purchase_data / item_master / warehouse_stock / benten_stock 全部上传逻辑）

部分包含 / 缺失的 mode：
  - monthly_sales（开头到一半截断）
  - difficult_items
  - order（发注书作成）
  - store_profit
  - daily_sales
  - expiry_manage（含 Lark Sheets 同步）

这些缺失部分会在 Phase 5 之前补齐 —— 已请求 Boss 通过 GitHub URL
或 zip 提供完整源码（避免再受单消息字符限制）。

为何此文件不能直接 `streamlit run`：
  1. 截断不完整，最后一段函数体不完整会语法错误
  2. 全程依赖 .streamlit/secrets.toml 配 SUPABASE_URL / SUPABASE_KEY 等
  3. 依赖 streamlit_javascript（未在 pyproject.toml 中声明）
"""
import streamlit as st

st.markdown("""
<style>
/* ===== multiselect の選択タグ（黒文字・目に優しい） ===== */

/* タグ本体 */
[data-baseweb="tag"] {
    background-color: #D1D5DB !important;   /* 薄いグレー */
    color: #111827 !important;              /* 黒（少し柔らかめ） */
    border-radius: 6px !important;
    font-weight: 500 !important;
}

/* テキスト部分（念のため明示） */
[data-baseweb="tag"] span {
    color: #111827 !important;
}

/* × ボタン */
[data-baseweb="tag"] svg {
    color: #374151 !important;              /* 濃いグレー */
}

/* hover */
[data-baseweb="tag"]:hover {
    background-color: #9CA3AF !important;   /* 少し濃いグレー */
}
</style>
""", unsafe_allow_html=True)


# ✅ ページ設定を追加
st.set_page_config(
    page_title="【ASEAN】一元管理システム",
    layout="wide",                 # 横幅を最大化
    initial_sidebar_state="expanded"
)

import pandas as pd
import requests
import datetime
import os
import math
import re
import hashlib
import time
from zoneinfo import ZoneInfo
from streamlit_javascript import st_javascript

# ============================================================
# 注：完整源码（约 1,500 行）由于单消息 50,000 字符限制被截断。
# 当前文件保留头部以记录来源与风格；完整内容待 Boss 通过 GitHub /
# 本地路径 / 多段消息提供后填充。
#
# 已读取的关键事实见同目录 README.md
# ============================================================
