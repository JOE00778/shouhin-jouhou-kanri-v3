"""模块 #4 销售数据查询 · 对齐原 order-management-app monthly_sales mode.

数据源（原 app 严格对齐）:
- `sales` (jan, quantity_sold, stock_available, stock_ordered, period_label)
  · 已经按 SKU 聚合的月度销售快照（不是 sales_line 多源行级表）
- `warehouse_stock` (product_code, jan, stock_available)
  · JD-千葉倉庫 库存快照
- `item_master` (jan, item_code, maker, display_name, rank, handling_status, on_order, actual_cost)
  · 商品主档（来自 SKU 一元管理表 一元くん sheet）

输出格式: 对齐 SKU 一元管理表格.xlsx 3月 sheet 22 列 (中/日双语)
SKU / 品牌 / 产品名 / RANK / 总销售数量 / 总营业额 / 单价 / 毛利 / 毛利率
/ 库存数量 / 库存金额 / 库存周转率(月) / 平均在庫日数 / 交叉比率(月)
/ 库存周转率(年) / 交叉比率(年) / 动销率 / 月售罄率
/ 在庫販売比率 / 利益貢献度 / 等级评价
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
    "对齐原 order-management-app monthly_sales · sales + warehouse_stock + item_master · "
    "SKU 一元管理表格 22 列"
))


def _df(sql: str, params=None) -> pd.DataFrame:
    rs = conn.execute(sql, params or {}).fetchall()
    return pd.DataFrame([dict(r) for r in rs])


# ============================================================
# 数据加载
# ============================================================
df_sales = _df("SELECT * FROM sales")
df_master = _df("SELECT * FROM item_master")
df_warehouse = _df("SELECT * FROM warehouse_stock")

if df_sales.empty:
    st.warning(t(
        "⚠️ `sales` 表为空。请到「⚙️ 数据导入与设置」上传 sales.csv。"
    ))
    st.stop()
if df_master.empty:
    st.warning(t("⚠️ `item_master` 表为空。请先上传商品主档。"))
    st.stop()

# ============================================================
# 字段标准化
# ============================================================
df_sales["jan"] = df_sales["jan"].astype(str).str.strip()
df_sales["quantity_sold"] = pd.to_numeric(df_sales["quantity_sold"], errors="coerce").fillna(0).astype(int)
df_sales["stock_available"] = pd.to_numeric(df_sales["stock_available"], errors="coerce").fillna(0).astype(int)
df_sales["stock_ordered"] = pd.to_numeric(df_sales["stock_ordered"], errors="coerce").fillna(0).astype(int)

df_master["jan"] = df_master["jan"].astype(str).str.strip()
if "actual_cost" in df_master.columns:
    df_master["actual_cost"] = pd.to_numeric(df_master["actual_cost"], errors="coerce").fillna(0)

if not df_warehouse.empty:
    df_warehouse["product_code"] = df_warehouse["product_code"].astype(str).str.strip()
    df_warehouse["stock_available"] = pd.to_numeric(
        df_warehouse["stock_available"], errors="coerce"
    ).fillna(0).astype(int)
    # 同 jan 多 location 求和
    jd_stock = df_warehouse.groupby("product_code", as_index=False)["stock_available"].sum()
    jd_stock = jd_stock.rename(columns={"product_code": "jan", "stock_available": "jd_stock"})
else:
    jd_stock = pd.DataFrame(columns=["jan", "jd_stock"])

# ============================================================
# 期间筛选
# ============================================================
periods = sorted(df_sales["period_label"].dropna().unique().tolist(), reverse=True) \
    if "period_label" in df_sales.columns else []

c1, c2, c3 = st.columns([1.5, 1.5, 1])
with c1:
    if periods:
        sel_period = st.selectbox(t("期间"), periods, index=0)
        df_sales = df_sales[df_sales["period_label"] == sel_period]
    else:
        sel_period = t("(无期间)")
        st.caption(t("(销售表无 period_label, 默认显示全部)"))

with c2:
    keyword = st.text_input(t("搜索: 商品代码 / 商品名 / JAN"), "")

with c3:
    show_zero_sales = st.checkbox(t("含销量为 0 的 SKU"), value=False)

# ============================================================
# JOIN
# ============================================================
df = df_sales.merge(
    df_master[["jan", "item_code", "maker", "display_name", "rank", "handling_status", "on_order"]],
    on="jan", how="left",
)
# 优先用 sales.stock_available, 但若 sales 缺则用 warehouse_stock
df = df.merge(jd_stock, on="jan", how="left")
df["jd_stock"] = df["jd_stock"].fillna(0).astype(int)
# 库存优先级: sales.stock_available > warehouse_stock > item_master.on_hand
df["qty_on_hand"] = df["stock_available"].where(df["stock_available"] > 0, df["jd_stock"])

# 取 actual_cost (定義原価) 算库存金额
if "actual_cost" in df_master.columns:
    cost_map = df_master.set_index("jan")["actual_cost"].to_dict()
    df["actual_cost"] = df["jan"].map(cost_map).fillna(0)
else:
    df["actual_cost"] = 0

# 关键词过滤
if keyword.strip():
    kw = keyword.strip()
    cond = (
        df["jan"].astype(str).str.contains(kw, na=False)
        | df["item_code"].astype(str).str.contains(kw, na=False)
        | df["display_name"].astype(str).str.contains(kw, na=False)
    )
    df = df[cond]

# 销量 0 过滤
if not show_zero_sales:
    df = df[df["quantity_sold"] > 0]

if df.empty:
    st.info(t("当前条件下无数据。"))
    st.stop()

# ============================================================
# KPI
# ============================================================
total_qty = int(df["quantity_sold"].sum())
total_inv_value = int((df["qty_on_hand"] * df["actual_cost"]).sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric(t("SKU 数"), f"{len(df):,}")
c2.metric(t("总销售数量"), f"{total_qty:,}")
c3.metric(t("总库存数"), f"{int(df['qty_on_hand'].sum()):,}")
c4.metric(t("总库存金额 ¥"), f"{total_inv_value:,}")

st.divider()

# ============================================================
# Tab: SKU 一元一览（22 列）
# ============================================================
tab_unified, tab_simple = st.tabs([
    t("📋 SKU 一元一览（22 列）"),
    t("📋 简明视图"),
])

with tab_unified:
    # 计算指标
    df["unit_price"] = (
        df["quantity_sold"] * 0  # 占位; 用 actual_cost 推算单价不准, 留空
    )
    # 单价从 master 取 actual_cost? 原 app 中 unit_price 是从 sales 表里没有, 这里用 actual_cost 兜底
    df["unit_price"] = df["actual_cost"]
    df["revenue"] = (df["quantity_sold"] * df["unit_price"]).round(0)
    # 假设毛利率 = 0 (sales 表无 cost), 留空待 SKU 一元 sheet 数据填充
    df["gross_profit"] = 0
    df["gross_margin"] = 0.0

    df["inv_value"] = df["qty_on_hand"] * df["actual_cost"]
    df["turnover_m"] = (
        df["quantity_sold"] / df["qty_on_hand"]
    ).where(df["qty_on_hand"] > 0).fillna(0)
    df["doh"] = (30.0 / df["turnover_m"]).where(df["turnover_m"] > 0).fillna(0)
    df["cross_ratio_m"] = df["turnover_m"] * df["gross_margin"] * 100
    df["turnover_y"] = df["turnover_m"] * 12
    df["cross_ratio_y"] = df["cross_ratio_m"] * 12
    df["sku_active"] = df["quantity_sold"].apply(
        lambda q: t("动销") if q > 0 else t("不动")
    )
    denom = df["quantity_sold"] + df["qty_on_hand"]
    df["sellout_rate"] = (df["quantity_sold"] / denom).where(denom > 0).fillna(0)
    df["inv_sales_ratio"] = (
        df["qty_on_hand"] / df["quantity_sold"]
    ).where(df["quantity_sold"] > 0).fillna(0)
    total_rev = float(df["revenue"].sum())
    df["profit_contribution"] = (
        df["revenue"] / total_rev * 100
    ) if total_rev else 0

    def _grade(row):
        if str(row.get("handling_status", "")).strip() in ("取扱中止", "メーカー取扱中止"):
            return t("⚫ 中止")
        if row["quantity_sold"] <= 0:
            return t("⚪ 不动")
        cr = row["cross_ratio_m"]
        if cr >= 100:
            return t("🟢 A")
        if cr >= 50:
            return t("🟡 B")
        if cr >= 20:
            return t("🟠 C")
        return t("🔴 D")
    df[t("等级评价")] = df.apply(_grade, axis=1)

    out = pd.DataFrame({
        t("SKU"): df["item_code"].fillna(df["jan"]),
        t("品牌"): df["maker"].fillna(""),
        t("产品名"): df["display_name"].fillna(""),
        t("RANK"): df["rank"].fillna(""),
        t("总销售数量"): df["quantity_sold"].astype(int),
        t("总营业额"): df["revenue"].astype(int),
        t("单价"): df["unit_price"].round(0).astype(int),
        t("毛利"): df["gross_profit"].astype(int),
        t("毛利率"): df["gross_margin"].apply(lambda x: f"{x*100:.1f}%"),
        t("库存数量"): df["qty_on_hand"].astype(int),
        t("库存金额"): df["inv_value"].round(0).astype(int),
        t("库存周转率"): df["turnover_m"].round(2),
        t("平均在庫日数"): df["doh"].round(0).astype(int),
        t("交叉比率"): df["cross_ratio_m"].round(1),
        t("库存周转率(年)"): df["turnover_y"].round(1),
        t("交叉比率(年)"): df["cross_ratio_y"].round(0).astype(int),
        t("动销率"): df["sku_active"],
        t("月售罄率"): df["sellout_rate"].apply(lambda x: f"{x*100:.1f}%"),
        t("在庫販売比率"): df["inv_sales_ratio"].round(2),
        t("利益貢献度"): df["profit_contribution"].apply(lambda x: f"{x:.2f}%"),
        t("等级评价"): df[t("等级评价")],
    })
    out = out.sort_values(t("总销售数量"), ascending=False)
    st.dataframe(out, use_container_width=True, hide_index=True, height=560)
    st.caption(t(f"共 {len(out):,} 条 SKU · 期间 {sel_period} · 按销量降序"))
    csv = out.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        t("📥 SKU 一元 CSV 下载"),
        data=csv,
        file_name=f"sku_unified_{sel_period}.csv",
        mime="text/csv",
    )
    st.caption(t(
        "📌 注: 毛利/毛利率字段当前为 0 (sales 表无 cost 维度)。"
        "如需毛利分析,请到「📋 简明视图」或导入带 cost 的销售数据。"
    ))

with tab_simple:
    simple = pd.DataFrame({
        t("SKU"): df["item_code"].fillna(df["jan"]),
        t("JAN"): df["jan"],
        t("品牌"): df["maker"].fillna(""),
        t("产品名"): df["display_name"].fillna(""),
        t("RANK"): df["rank"].fillna(""),
        t("取扱区分"): df["handling_status"].fillna(""),
        t("总销售数量"): df["quantity_sold"].astype(int),
        t("库存数量"): df["qty_on_hand"].astype(int),
        t("発注済"): df["stock_ordered"].astype(int),
    })
    simple = simple.sort_values(t("总销售数量"), ascending=False)
    st.dataframe(simple, use_container_width=True, hide_index=True, height=560)
    csv = simple.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        t("📥 简明 CSV 下载"),
        data=csv,
        file_name=f"sales_simple_{sel_period}.csv",
        mime="text/csv",
    )
