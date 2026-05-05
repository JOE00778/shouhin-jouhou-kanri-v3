"""模块 #8 销售数据查询。

支持：
- 多源（asean_monthly / asean_daily / export_item / export_store）
- 多维筛选（期间 / 店铺 / SKU / メーカー / ランク）
- 明细 / 聚合两个视图
- CSV 导出
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from shared.i18n import t, lang_selector

from shared.db import get_connection
from shared.markets import ALL_MARKETS, add_market_column

st.set_page_config(page_title=t("销售数据查询"), page_icon="📊", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("📊 销售数据查询"))
st.caption(t("基于 NetSuite 销售导出 · 多维筛选 · 明细 + 聚合"))


sales_count = conn.execute("SELECT COUNT(*) AS c FROM sales_line").fetchone()["c"]
if sales_count == 0:
    st.warning(
        t("⚠️ `sales_line` 表为空。请到「⚙️ 数据导入与设置」上传 ASEAN/輸出 销售 .xls。")
    )
    st.stop()


# ============================================================
# 筛选 UI
# ============================================================
src_opts = [r["source"] for r in conn.execute("SELECT DISTINCT source FROM sales_line").fetchall()]
src_label = {
    "asean_monthly": "ASEAN 月度（含店铺）",
    "asean_daily": "ASEAN 日度",
    "export_item": "輸出 SKU 维度",
    "export_store": "輸出 店铺×SKU",
}

ALL_LABEL = "全部"

c1, c2, c3 = st.columns(3)
with c1:
    src_choices = [ALL_LABEL] + src_opts
    src_pick = st.selectbox(
        t("数据源"), src_choices,
        format_func=lambda s: src_label.get(s, s) if s != ALL_LABEL else t("全部"),
    )
    sel_srcs = src_opts if src_pick == ALL_LABEL else [src_pick]

with c2:
    period_opts = conn.execute(
        "SELECT DISTINCT period_start, period_end FROM sales_line ORDER BY period_start DESC"
    ).fetchall()
    period_choices = [(r["period_start"], r["period_end"]) for r in period_opts]
    sel_period = st.selectbox(
        t("期间"), period_choices,
        format_func=lambda p: f"{p[0]} ~ {p[1]}" if p[0] else t("(无期间)"),
    )

with c3:
    rank_opts = [
        r["rank"] for r in conn.execute(
            "SELECT DISTINCT rank FROM sales_line WHERE rank IS NOT NULL ORDER BY rank"
        ).fetchall()
    ]
    rank_choices = [ALL_LABEL] + rank_opts
    rank_pick = st.selectbox(t("商品ランク（如有）"), rank_choices)
    sel_ranks = rank_opts if rank_pick == ALL_LABEL else [rank_pick]


c4, c5, c6 = st.columns(3)
with c4:
    keyword = st.text_input(t("搜索 アイテム / 商品名"), "")
with c5:
    market_choices = [ALL_LABEL] + ALL_MARKETS
    market_pick = st.selectbox(t("市场"), market_choices)
with c6:
    store_opts = [
        r["store"] for r in conn.execute(
            "SELECT DISTINCT store FROM sales_line WHERE store IS NOT NULL ORDER BY store"
        ).fetchall()
    ]
    store_choices = [ALL_LABEL] + store_opts
    store_pick = st.selectbox(t("店铺（如有）"), store_choices)
    sel_stores = store_opts if store_pick == ALL_LABEL else [store_pick]


# ============================================================
# 查询
# ============================================================
where = ["period_start = :p_start AND period_end = :p_end"]
params: dict = {"p_start": sel_period[0], "p_end": sel_period[1]}

if sel_srcs:
    placeholders = ",".join(f":s{i}" for i in range(len(sel_srcs)))
    where.append(f"source IN ({placeholders})")
    params.update({f"s{i}": v for i, v in enumerate(sel_srcs)})

if sel_ranks:
    placeholders = ",".join(f":r{i}" for i in range(len(sel_ranks)))
    where.append(f"(rank IS NULL OR rank IN ({placeholders}))")
    params.update({f"r{i}": v for i, v in enumerate(sel_ranks)})

if sel_stores:
    placeholders = ",".join(f":st{i}" for i in range(len(sel_stores)))
    where.append(f"(store IS NULL OR store IN ({placeholders}))")
    params.update({f"st{i}": v for i, v in enumerate(sel_stores)})

if keyword:
    where.append("(item_code LIKE :kw OR display_name LIKE :kw)")
    params["kw"] = f"%{keyword.strip()}%"

where_sql = " AND ".join(where)

df = pd.DataFrame([dict(r) for r in conn.execute(
    f"""
    SELECT source, store, item_code, display_name, handling_status, rank,
           qty_sold, unit_purchase_price, revenue, defined_cost,
           gross_profit, gross_margin
    FROM sales_line WHERE {where_sql}
    """,
    params,
).fetchall()])


# ============================================================
# KPI + 视图
# ============================================================
if df.empty:
    st.info(t("当前条件下无数据。"))
    st.stop()

# 加 market 列 + 市场过滤
df = add_market_column(df, store_col="store")
if market_pick != ALL_LABEL:
    df = df[df["market"] == market_pick]
if df.empty:
    st.info(t("此市场下无数据。"))
    st.stop()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("行数"), f"{len(df):,}")
c2.metric(t("总销量"), f"{int(df['qty_sold'].fillna(0).sum()):,}")
c3.metric(t("总売上 ¥"), f"{df['revenue'].fillna(0).sum():,.0f}")
c4.metric(t("毛利 ¥"), f"{df['gross_profit'].fillna(0).sum():,.0f}")
total_rev = df["revenue"].fillna(0).sum()
total_gp = df["gross_profit"].fillna(0).sum()
c5.metric(t("毛利率"), f"{(total_gp/total_rev*100 if total_rev else 0):.2f}%")

st.divider()

tab_detail, tab_by_market, tab_by_sku, tab_by_store = st.tabs(
    [t("📋 明细"), t("🌐 按市场聚合"), t("🏆 按 SKU 聚合"), t("🏪 按店铺聚合")]
)

with tab_detail:
    st.dataframe(df, use_container_width=True, hide_index=True)
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        t("📥 明细 CSV"), data=csv, file_name=f"sales_detail_{len(df)}.csv", mime="text/csv"
    )

with tab_by_market:
    g = df.groupby("market", as_index=False).agg(
        销量=("qty_sold", lambda s: int(s.fillna(0).sum())),
        売上=("revenue", lambda s: s.fillna(0).sum()),
        毛利=("gross_profit", lambda s: s.fillna(0).sum()),
        店铺数=("store", "nunique"),
        SKU数=("item_code", "nunique"),
    )
    g["毛利率"] = (g["毛利"] / g["売上"]).where(g["売上"] > 0).fillna(0) * 100
    g = g.sort_values("売上", ascending=False)
    st.dataframe(g, use_container_width=True, hide_index=True)
    if len(g) > 0:
        st.bar_chart(g.set_index("market")[["売上"]], horizontal=True)

with tab_by_sku:
    g = df.groupby(["item_code", "display_name"], as_index=False).agg(
        销量=("qty_sold", lambda s: int(s.fillna(0).sum())),
        売上=("revenue", lambda s: s.fillna(0).sum()),
        毛利=("gross_profit", lambda s: s.fillna(0).sum()),
    )
    g["毛利率"] = (g["毛利"] / g["売上"]).where(g["売上"] > 0).fillna(0) * 100
    g = g.sort_values("売上", ascending=False)
    st.dataframe(g, use_container_width=True, hide_index=True)

with tab_by_store:
    df_with_store = df[df["store"].notna()]
    if df_with_store.empty:
        st.info(t("当前数据无店铺维度。"))
    else:
        g = df_with_store.groupby("store", as_index=False).agg(
            销量=("qty_sold", lambda s: int(s.fillna(0).sum())),
            売上=("revenue", lambda s: s.fillna(0).sum()),
            毛利=("gross_profit", lambda s: s.fillna(0).sum()),
            SKU数=("item_code", "nunique"),
        )
        g["毛利率"] = (g["毛利"] / g["売上"]).where(g["売上"] > 0).fillna(0) * 100
        g = g.sort_values("毛利", ascending=False)
        st.dataframe(g, use_container_width=True, hide_index=True)
