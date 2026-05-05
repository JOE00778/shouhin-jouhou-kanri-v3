"""模块 #6 店铺别毛利。

数据来源：sales_line 表（4 类销售导出共用）。
- ASEAN 月度（asean_monthly）含店铺
- 出口店铺别（export_store）含店铺
- ASEAN 日（asean_daily）只 SKU 维度
- 出口 アイテム別（export_item）只 SKU 维度

业务：
- 按店铺 × 月份 聚合 总売上 / 総定義原価 / 粗利 / 粗利率
- 店铺级排序、月份对比
- TOP N 商品贡献分析
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.markets import ALL_MARKETS, add_market_column

st.set_page_config(page_title="店铺别毛利", page_icon="🏪", layout="wide")
conn = get_connection()

st.title("🏪 店铺别毛利")
st.caption("基于 NetSuite 销售报表 · 自带毛利+毛利率，零计算直接展示")


sales_count = conn.execute(
    "SELECT COUNT(*) AS c FROM sales_line WHERE store IS NOT NULL"
).fetchone()["c"]
if sales_count == 0:
    st.warning(
        "⚠️ 没有店铺级销售数据。请到「⚙️ 数据导入与设置」上传 "
        "`【ASEAN】店舗別売上 集計専用.xls` 或 `【輸出】店舗別売上.xls`。"
    )
    st.stop()


# ============================================================
# 选择源 + 期间
# ============================================================
src_opts = [r["source"] for r in conn.execute(
    "SELECT DISTINCT source FROM sales_line WHERE store IS NOT NULL"
).fetchall()]
src_label = {
    "asean_monthly": "ASEAN 月度（含店铺）",
    "asean_daily": "ASEAN 日度（含店铺）",
    "export_item": "輸出 SKU 维度",
    "export_store": "輸出 店铺×SKU",
}
sel_src = st.selectbox(
    "数据源", src_opts, format_func=lambda s: src_label.get(s, s)
)

period_opts = conn.execute(
    "SELECT DISTINCT period_start, period_end FROM sales_line WHERE source = ? ORDER BY period_start DESC",
    (sel_src,),
).fetchall()
period_choices = [(r["period_start"], r["period_end"]) for r in period_opts]
sel_period = st.selectbox(
    "期间",
    period_choices,
    format_func=lambda p: f"{p[0]} ~ {p[1]}",
)

# 加载明细
df = pd.DataFrame([dict(r) for r in conn.execute(
    """
    SELECT store, item_code, display_name, qty_sold, revenue,
           defined_cost, gross_profit, gross_margin, rank
    FROM sales_line
    WHERE source = ? AND period_start = ? AND period_end = ?
        AND store IS NOT NULL
    """,
    (sel_src, sel_period[0], sel_period[1]),
).fetchall()])

if df.empty:
    st.info("此条件下无数据。")
    st.stop()

# 加 market 列（基于 store）
df = add_market_column(df, store_col="store")

# 市场过滤
mk_choices = ["全部市场"] + ALL_MARKETS
mk_pick = st.selectbox("市场", mk_choices, index=0)
if mk_pick != "全部市场":
    df = df[df["market"] == mk_pick]
if df.empty:
    st.info("此市场下无数据。")
    st.stop()


# ============================================================
# 顶部 KPI（总）
# ============================================================
total_qty = int(df["qty_sold"].fillna(0).sum())
total_rev = df["revenue"].fillna(0).sum()
total_cost = df["defined_cost"].fillna(0).sum()
total_gp = df["gross_profit"].fillna(0).sum()
total_margin = (total_gp / total_rev * 100) if total_rev else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("总销量", f"{total_qty:,}")
c2.metric("总售价（¥）", f"{total_rev:,.0f}")
c3.metric("总成本（¥）", f"{total_cost:,.0f}")
c4.metric("毛利（¥）", f"{total_gp:,.0f}")
c5.metric("毛利率", f"{total_margin:.2f}%")

st.divider()

tab_market, tab_store, tab_top_skus = st.tabs(
    ["🌐 按市场聚合", "📊 按店铺聚合", "🏆 TOP SKU 贡献"]
)

# ============================================================
# Tab 0：按市场（东南亚 / 韩国 / 日本）
# ============================================================
with tab_market:
    g = df.groupby("market", as_index=False).agg(
        销量=("qty_sold", lambda s: int(s.fillna(0).sum())),
        总售价=("revenue", lambda s: s.fillna(0).sum()),
        总成本=("defined_cost", lambda s: s.fillna(0).sum()),
        毛利=("gross_profit", lambda s: s.fillna(0).sum()),
        店铺数=("store", "nunique"),
        SKU数=("item_code", "nunique"),
    )
    g["毛利率"] = (g["毛利"] / g["总售价"]).where(g["总售价"] > 0).fillna(0) * 100
    g = g.sort_values("毛利", ascending=False)

    g_disp = g.copy()
    g_disp["总售价"] = g_disp["总售价"].apply(lambda x: f"{x:,.0f}")
    g_disp["总成本"] = g_disp["总成本"].apply(lambda x: f"{x:,.0f}")
    g_disp["毛利"] = g_disp["毛利"].apply(lambda x: f"{x:,.0f}")
    g_disp["毛利率"] = g_disp["毛利率"].apply(lambda x: f"{x:.2f}%")

    st.dataframe(g_disp, use_container_width=True, hide_index=True)
    if len(g) > 0:
        st.bar_chart(g.set_index("market")[["毛利"]], horizontal=True)


# ============================================================
# Tab 1：按店铺
# ============================================================
with tab_store:
    g = df.groupby("store", as_index=False).agg(
        销量=("qty_sold", lambda s: int(s.fillna(0).sum())),
        总售价=("revenue", lambda s: s.fillna(0).sum()),
        总成本=("defined_cost", lambda s: s.fillna(0).sum()),
        毛利=("gross_profit", lambda s: s.fillna(0).sum()),
        SKU数=("item_code", "nunique"),
    )
    g["毛利率"] = (g["毛利"] / g["总售价"]).where(g["总售价"] > 0).fillna(0) * 100
    g = g.sort_values("毛利", ascending=False)
    g_disp = g.copy()
    g_disp["总售价"] = g_disp["总售价"].apply(lambda x: f"{x:,.0f}")
    g_disp["总成本"] = g_disp["总成本"].apply(lambda x: f"{x:,.0f}")
    g_disp["毛利"] = g_disp["毛利"].apply(lambda x: f"{x:,.0f}")
    g_disp["毛利率"] = g_disp["毛利率"].apply(lambda x: f"{x:.2f}%")

    st.dataframe(g_disp, use_container_width=True, hide_index=True)
    st.bar_chart(g.set_index("store")[["毛利"]], horizontal=True)

# ============================================================
# Tab 2：TOP SKU
# ============================================================
with tab_top_skus:
    n_top = st.slider("Top N", 10, 100, 30, 10)
    g = df.groupby(["item_code", "display_name"], as_index=False).agg(
        销量=("qty_sold", lambda s: int(s.fillna(0).sum())),
        总售价=("revenue", lambda s: s.fillna(0).sum()),
        毛利=("gross_profit", lambda s: s.fillna(0).sum()),
    )
    g["毛利率"] = (g["毛利"] / g["总售价"]).where(g["总售价"] > 0).fillna(0) * 100
    g = g.sort_values("毛利", ascending=False).head(n_top)
    g_disp = g.copy()
    g_disp["总售价"] = g_disp["总售价"].apply(lambda x: f"{x:,.0f}")
    g_disp["毛利"] = g_disp["毛利"].apply(lambda x: f"{x:,.0f}")
    g_disp["毛利率"] = g_disp["毛利率"].apply(lambda x: f"{x:.2f}%")
    st.dataframe(g_disp, use_container_width=True, hide_index=True)


st.divider()
st.caption(f"数据源：{src_label.get(sel_src, sel_src)} · 期间：{sel_period[0]} ~ {sel_period[1]}")
