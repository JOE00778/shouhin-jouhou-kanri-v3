"""模块 #18 每日销售 · 店舗別前日売上(最新日).

数据源: store_profit_daily_lines
显示:
- 全店合计 KPI（数量/売上/定義原価/粗利/粗利率）
- 店铺别明细排序表
- CSV 下载（合计 + 店铺别）
"""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("每日销售"), page_icon="📆", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("📆 店铺别前日销售（最新日）"))
st.caption(t("基于 store_profit_daily_lines · 仅 detail 行去重聚合"))

df = pd.DataFrame([dict(r) for r in conn.execute("SELECT * FROM store_profit_daily_lines").fetchall()])
if df.empty:
    st.warning(t("⚠️ store_profit_daily_lines 表为空。"))
    st.stop()

required = {"report_date", "line_type", "store", "item", "qty", "revenue", "defined_cost", "gross_profit"}
missing = required - set(df.columns)
if missing:
    st.error(t(f"必要列不足: {missing}"))
    st.stop()

df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce").dt.date
for c in ("qty", "revenue", "defined_cost", "gross_profit"):
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

latest_date = df["report_date"].max()
cur = df[df["report_date"] == latest_date].copy()

pat_agg = r"^(合計|総計|計)\b"
cur_detail = cur[cur["line_type"] == "detail"].copy()
cur_detail = cur_detail[~cur_detail["item"].astype(str).str.match(pat_agg, na=False)]
if "item_name" in cur_detail.columns:
    cur_detail = cur_detail[~cur_detail["item_name"].astype(str).str.fullmatch(r"\s*EMPTY\s*", na=False)]

tot_qty = int(cur_detail["qty"].sum())
tot_rev = int(cur_detail["revenue"].sum())
tot_cost = int(cur_detail["defined_cost"].sum())
tot_gp = int(cur_detail["gross_profit"].sum())
tot_mgn = round((tot_gp / tot_rev * 100) if tot_rev else 0.0, 2)


def _fmt_int(x):
    return f"{int(x):,}"


def _fmt_pct(x):
    return f"{float(x):.2f}%"


# KPI 全店合计
st.subheader(t("🧮 全店合计"))
st.caption(t(f"对象日: {latest_date}"))
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("数量"), _fmt_int(tot_qty))
c2.metric(t("销售额"), _fmt_int(tot_rev))
c3.metric(t("定义成本"), _fmt_int(tot_cost))
c4.metric(t("毛利"), _fmt_int(tot_gp))
c5.metric(t("毛利率"), _fmt_pct(tot_mgn))

df_total = pd.DataFrame([{
    t("对象日"): latest_date,
    t("数量"): _fmt_int(tot_qty),
    t("销售额"): _fmt_int(tot_rev),
    t("定义成本"): _fmt_int(tot_cost),
    t("毛利"): _fmt_int(tot_gp),
    t("毛利率"): _fmt_pct(tot_mgn),
}])
st.download_button(
    t("📥 全店合计 CSV 下载"),
    df_total.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"daily_sales_total_{latest_date}.csv",
    mime="text/csv",
)

st.markdown("---")
st.subheader(t("店铺别"))
cur_g = (
    cur_detail.groupby("store", as_index=False)
    .agg(qty=("qty", "sum"), revenue=("revenue", "sum"),
         defined_cost=("defined_cost", "sum"), gross_profit=("gross_profit", "sum"))
)
cur_g["gross_margin"] = (
    (cur_g["gross_profit"] / cur_g["revenue"].replace({0: pd.NA}) * 100)
    .astype(float).round(2).fillna(0.0)
)

disp = cur_g.rename(columns={
    "store": t("店铺"),
    "qty": t("数量"),
    "revenue": t("销售额"),
    "defined_cost": t("定义成本"),
    "gross_profit": t("毛利"),
    "gross_margin": t("毛利率"),
}).copy()
for col in (t("数量"), t("销售额"), t("定义成本"), t("毛利")):
    if col in disp.columns:
        disp[col] = disp[col].map(_fmt_int)
if t("毛利率") in disp.columns:
    disp[t("毛利率")] = disp[t("毛利率")].map(_fmt_pct)

disp = disp.sort_values(
    by=t("销售额"),
    ascending=False,
    key=lambda s: s.str.replace(",", "", regex=False).astype(int),
)
st.dataframe(disp, use_container_width=True, hide_index=True)
st.download_button(
    t("📥 店铺别 CSV 下载（数值原样）"),
    cur_g.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"daily_sales_by_store_{latest_date}.csv",
    mime="text/csv",
)
