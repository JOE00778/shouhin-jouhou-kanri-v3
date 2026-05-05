"""模块 #9 発注履歴 · 订货历史查询.

数据源: purchase_history 表
功能: JAN 单条/多条搜索 + Order ID 搜索 + CSV 导出
"""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("発注履歴"), page_icon="📜", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("📜 発注履歴"))
st.caption(t("订货历史查询·支持单 JAN / 多 JAN / Order ID 部分匹配"))

col1, col2 = st.columns(2)
with col1:
    jan_filter_single = st.text_input(t("🔍 JAN 单条搜索（部分匹配）"), "")
    order_id_filter = st.text_input(t("🔍 Order ID 搜索（部分匹配）"), "")
with col2:
    jan_filter_multi = st.text_area(
        t("🔍 多 JAN 搜索（换行 / 逗号分隔）"),
        placeholder="例:\n4901234567890\n4987654321098",
        height=120,
    )

df = pd.DataFrame([dict(r) for r in conn.execute("SELECT * FROM purchase_history").fetchall()])

if df.empty:
    st.info(t("暂无订货历史数据。"))
    st.stop()

df["jan"] = df["jan"].astype(str)
df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce").dt.date

jan_list = [j.strip() for j in re.split(r"[,\n\r]+", jan_filter_multi) if j.strip()]
if jan_list:
    df = df[df["jan"].isin(jan_list)]
elif jan_filter_single:
    df = df[df["jan"].str.contains(jan_filter_single, na=False)]

if order_id_filter and "order_id" in df.columns:
    df = df[df["order_id"].astype(str).str.contains(order_id_filter, na=False)]

cols = [c for c in ["jan", "quantity", "order_date", "order_id", "memo"] if c in df.columns]
df_show = df[cols].sort_values("jan")

st.success(t(f"✅ 命中 {len(df_show)} 条记录"))
st.dataframe(df_show, use_container_width=True, hide_index=True)

csv = df_show.to_csv(index=False).encode("utf-8-sig")
st.download_button(t("📥 CSV 下载"), data=csv, file_name="purchase_history.csv", mime="text/csv")
