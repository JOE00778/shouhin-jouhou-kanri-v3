"""模块 #4 销售数据查询 · 数据源严格对齐【ASEAN】店舗別売上 集計専用.xls 12 列原表.

源表 12 列 (R7):
A=FB_店舗 / B=アイテム / C=UPCコード / D=取扱区分 / E=表示名
F=販売数量 / G=総収益 / H=定義原価 / I=粗利 / J=粗利率
K=メーカー名 / L=商品ランク

目标输出: SKU 一元管理表格 3月 sheet 22 列格式.

业务流程:
1. 加载 sales_line (本身就是从 ASEAN 集計専用.xls ingest 来的, 12 列原始数据齐全)
2. 按 SKU(item_code) 聚合: 多店铺多行 → 单 SKU 1 行
   - qty_sold / revenue / defined_cost / gross_profit 求和
   - gross_margin = sum(gp) / sum(rev) 重算
   - maker / rank / handling_status / display_name 取最新非空
3. join warehouse_stock (JD 在庫) 取 stock_available
4. 计算衍生指标: 单价 / 库存金额 / 月周转 / 平均在庫日数 / 交叉比率(月/年)
   / 动销率 / 月售罄率 / 在庫販売比率 / 利益貢献度 / 等级评价
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("销售数据查询"), page_icon="📊", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("📊 销售数据查询"))
st.caption(t(
    "数据源:【ASEAN】店舗別売上 集計専用 .xls (12 列源表) → sales_line → SKU 聚合 + JD库存 join · "
    "对齐 SKU 一元管理表格 3月 22 列"
))


def _df(sql: str, params=None) -> pd.DataFrame:
    rs = conn.execute(sql, params or {}).fetchall()
    return pd.DataFrame([dict(r) for r in rs])


# ============================================================
# 数据加载
# ============================================================
sales_count = conn.execute("SELECT COUNT(*) AS c FROM sales_line").fetchone()["c"]
if sales_count == 0:
    st.warning(t(
        "⚠️ `sales_line` 表为空。请到「⚙️ 数据导入与设置」上传 "
        "`【ASEAN】店舗別売上　集計専用.xls`。"
    ))
    st.stop()

# ============================================================
# 期间筛选
# ============================================================
period_opts = conn.execute(
    "SELECT DISTINCT period_start, period_end FROM sales_line "
    "ORDER BY period_start DESC"
).fetchall()
periods = [(r["period_start"], r["period_end"]) for r in period_opts]

c1, c2, c3 = st.columns([1.5, 1.5, 1])
with c1:
    sel_period = st.selectbox(
        t("期间"), periods,
        format_func=lambda p: f"{p[0]} ~ {p[1]}" if p[0] else t("(无期间)"),
    )
with c2:
    keyword = st.text_input(t("搜索: 商品代码 / 商品名 / JAN / 品牌"), "")
with c3:
    show_zero_sales = st.checkbox(t("含销量为 0 的 SKU"), value=False)

# 仅取 ASEAN/輸出 月度源（按店铺×SKU 拆行的版本）
df_raw = _df(
    """
    SELECT store, item_code, upc, display_name, handling_status, maker, rank,
           qty_sold, revenue, defined_cost, gross_profit, gross_margin,
           source
    FROM sales_line
    WHERE period_start = :p_start AND period_end = :p_end
      AND source IN ('asean_monthly', 'export_store')
    """,
    {"p_start": sel_period[0], "p_end": sel_period[1]},
)
if df_raw.empty:
    # fallback: 任何 source
    df_raw = _df(
        """
        SELECT store, item_code, upc, display_name, handling_status, maker, rank,
               qty_sold, revenue, defined_cost, gross_profit, gross_margin,
               source
        FROM sales_line
        WHERE period_start = :p_start AND period_end = :p_end
        """,
        {"p_start": sel_period[0], "p_end": sel_period[1]},
    )

if df_raw.empty:
    st.info(t("当前条件下无数据。"))
    st.stop()

# 数值化
for c in ("qty_sold", "revenue", "defined_cost", "gross_profit", "gross_margin"):
    df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce").fillna(0)

# ============================================================
# 按 SKU 聚合（多店铺 → 单 SKU）
# ============================================================
agg = df_raw.groupby("item_code", as_index=False).agg(
    upc=("upc", "first"),
    display_name=("display_name", "last"),
    handling_status=("handling_status", "last"),
    maker=("maker", lambda s: s.dropna().iloc[-1] if s.dropna().size else ""),
    rank=("rank", lambda s: s.dropna().iloc[-1] if s.dropna().size else ""),
    qty_sold=("qty_sold", "sum"),
    revenue=("revenue", "sum"),
    defined_cost=("defined_cost", "sum"),
    gross_profit=("gross_profit", "sum"),
)
agg["gross_margin"] = (
    agg["gross_profit"] / agg["revenue"]
).where(agg["revenue"] > 0).fillna(0)

# ============================================================
# JD 库存 join
# ============================================================
df_warehouse = _df("SELECT product_code, jan, stock_available FROM warehouse_stock")
if not df_warehouse.empty:
    df_warehouse["product_code"] = df_warehouse["product_code"].astype(str).str.strip()
    df_warehouse["stock_available"] = pd.to_numeric(
        df_warehouse["stock_available"], errors="coerce"
    ).fillna(0).astype(int)
    jd_by_code = (
        df_warehouse.groupby("product_code", as_index=False)["stock_available"]
        .sum().rename(columns={"product_code": "item_code", "stock_available": "qty_on_hand"})
    )
    agg = agg.merge(jd_by_code, on="item_code", how="left")
    # 若按 item_code 找不到, 试 jan(=upc)
    miss = agg[agg["qty_on_hand"].isna()].copy()
    if not miss.empty and "upc" in agg.columns:
        jd_by_jan = (
            df_warehouse.groupby("jan", as_index=False)["stock_available"]
            .sum().rename(columns={"jan": "upc", "stock_available": "qty_on_hand_jan"})
        )
        agg = agg.merge(jd_by_jan, on="upc", how="left")
        agg["qty_on_hand"] = agg["qty_on_hand"].fillna(agg["qty_on_hand_jan"])
        agg = agg.drop(columns=["qty_on_hand_jan"])
agg["qty_on_hand"] = agg.get("qty_on_hand", 0).fillna(0).astype(int) \
    if "qty_on_hand" in agg.columns else 0

# 关键词过滤
if keyword.strip():
    kw = keyword.strip()
    cond = (
        agg["item_code"].astype(str).str.contains(kw, na=False)
        | agg["upc"].astype(str).str.contains(kw, na=False)
        | agg["display_name"].astype(str).str.contains(kw, na=False)
        | agg["maker"].astype(str).str.contains(kw, na=False, case=False)
    )
    agg = agg[cond]

if not show_zero_sales:
    agg = agg[agg["qty_sold"] > 0]

if agg.empty:
    st.info(t("当前条件下无数据。"))
    st.stop()

# ============================================================
# KPI
# ============================================================
total_qty = int(agg["qty_sold"].sum())
total_rev = int(agg["revenue"].sum())
total_gp = int(agg["gross_profit"].sum())
total_mgn = (total_gp / total_rev * 100) if total_rev else 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("SKU 数"), f"{len(agg):,}")
c2.metric(t("总销售数量"), f"{total_qty:,}")
c3.metric(t("总营业额 ¥"), f"{total_rev:,}")
c4.metric(t("毛利 ¥"), f"{total_gp:,}")
c5.metric(t("毛利率"), f"{total_mgn:.2f}%")

st.divider()

# ============================================================
# 衍生指标 + 22 列输出
# ============================================================
agg["unit_price"] = (
    agg["revenue"] / agg["qty_sold"]
).where(agg["qty_sold"] > 0).fillna(0)
agg["inv_value"] = agg["qty_on_hand"] * (
    agg["defined_cost"] / agg["qty_sold"]
).where(agg["qty_sold"] > 0).fillna(0)
agg["turnover_m"] = (
    agg["qty_sold"] / agg["qty_on_hand"]
).where(agg["qty_on_hand"] > 0).fillna(0)
agg["doh"] = (30.0 / agg["turnover_m"]).where(agg["turnover_m"] > 0).fillna(0)
agg["cross_ratio_m"] = agg["turnover_m"] * agg["gross_margin"] * 100
agg["turnover_y"] = agg["turnover_m"] * 12
agg["cross_ratio_y"] = agg["cross_ratio_m"] * 12
agg["sku_active"] = agg["qty_sold"].apply(
    lambda q: t("动销") if q > 0 else t("不动")
)
denom = agg["qty_sold"] + agg["qty_on_hand"]
agg["sellout_rate"] = (agg["qty_sold"] / denom).where(denom > 0).fillna(0)
agg["inv_sales_ratio"] = (
    agg["qty_on_hand"] / agg["qty_sold"]
).where(agg["qty_sold"] > 0).fillna(0)
total_gp_f = float(agg["gross_profit"].sum())
agg["profit_contribution"] = (
    agg["gross_profit"] / total_gp_f * 100
) if total_gp_f else 0


def _grade(row):
    if str(row.get("handling_status", "")).strip() in ("取扱中止", "メーカー取扱中止"):
        return t("⚫ 中止")
    if row["qty_sold"] <= 0:
        return t("⚪ 不动")
    cr = row["cross_ratio_m"]
    if cr >= 100:
        return t("🟢 A")
    if cr >= 50:
        return t("🟡 B")
    if cr >= 20:
        return t("🟠 C")
    return t("🔴 D")


agg[t("等级评价")] = agg.apply(_grade, axis=1)

# ============================================================
# Tab 视图
# ============================================================
tab_unified, tab_simple, tab_raw = st.tabs([
    t("📋 SKU 一元一览（22 列）"),
    t("📋 简明视图"),
    t("📋 按店铺 × SKU 原始明细"),
])

with tab_unified:
    out = pd.DataFrame({
        t("SKU"): agg["item_code"],
        t("品牌"): agg["maker"].fillna(""),
        t("产品名"): agg["display_name"].fillna(""),
        t("RANK"): agg["rank"].fillna(""),
        t("总销售数量"): agg["qty_sold"].astype(int),
        t("总营业额"): agg["revenue"].round(0).astype(int),
        t("单价"): agg["unit_price"].round(0).astype(int),
        t("毛利"): agg["gross_profit"].round(0).astype(int),
        t("毛利率"): agg["gross_margin"].apply(lambda x: f"{x*100:.1f}%"),
        t("库存数量"): agg["qty_on_hand"].astype(int),
        t("库存金额"): agg["inv_value"].round(0).astype(int),
        t("库存周转率"): agg["turnover_m"].round(2),
        t("平均在庫日数"): agg["doh"].round(0).astype(int),
        t("交叉比率"): agg["cross_ratio_m"].round(1),
        t("库存周转率(年)"): agg["turnover_y"].round(1),
        t("交叉比率(年)"): agg["cross_ratio_y"].round(0).astype(int),
        t("动销率"): agg["sku_active"],
        t("月售罄率"): agg["sellout_rate"].apply(lambda x: f"{x*100:.1f}%"),
        t("在庫販売比率"): agg["inv_sales_ratio"].round(2),
        t("利益貢献度"): agg["profit_contribution"].apply(lambda x: f"{x:.2f}%"),
        t("等级评价"): agg[t("等级评价")],
    })
    out = out.sort_values(t("总营业额"), ascending=False)
    st.dataframe(out, use_container_width=True, hide_index=True, height=560)
    st.caption(t(f"共 {len(out):,} 条 SKU · 期间 {sel_period[0]} ~ {sel_period[1]} · 按总营业额降序"))
    csv = out.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        t("📥 SKU 一元 CSV 下载"),
        data=csv,
        file_name=f"sku_unified_{sel_period[0]}_{sel_period[1]}.csv",
        mime="text/csv",
    )

with tab_simple:
    simple = pd.DataFrame({
        t("SKU"): agg["item_code"],
        t("JAN"): agg["upc"],
        t("品牌"): agg["maker"].fillna(""),
        t("产品名"): agg["display_name"].fillna(""),
        t("取扱区分"): agg["handling_status"].fillna(""),
        t("RANK"): agg["rank"].fillna(""),
        t("总销售数量"): agg["qty_sold"].astype(int),
        t("总营业额"): agg["revenue"].round(0).astype(int),
        t("毛利"): agg["gross_profit"].round(0).astype(int),
        t("毛利率"): agg["gross_margin"].apply(lambda x: f"{x*100:.1f}%"),
        t("库存数量"): agg["qty_on_hand"].astype(int),
    }).sort_values(t("总营业额"), ascending=False)
    st.dataframe(simple, use_container_width=True, hide_index=True, height=560)
    csv = simple.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        t("📥 简明 CSV 下载"),
        data=csv,
        file_name=f"sales_simple_{sel_period[0]}.csv",
        mime="text/csv",
    )

with tab_raw:
    raw_show = df_raw.copy()
    raw_show["gross_margin"] = (raw_show["gross_margin"] * 100).round(2).astype(str) + "%"
    st.dataframe(raw_show, use_container_width=True, hide_index=True)
    st.caption(t(f"共 {len(raw_show):,} 行 · 店舗 × SKU 拆分 (含 12 列源字段)"))
    csv = df_raw.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        t("📥 原始明细 CSV"),
        data=csv,
        file_name=f"sales_raw_{sel_period[0]}.csv",
        mime="text/csv",
    )
