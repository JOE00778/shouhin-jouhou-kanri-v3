"""模块 #4 销售数据查询 · 三个 NetSuite 源表 join,直接拉取基础字段.

数据源（严格对齐 NetSuite 导出原始数据,不自己算）:
1. 销售/毛利/品牌/等级 ← `sales_line`
   ← 来自【ASEAN】店舗別売上 集計専用.xls 12 列 (A-L)
2. 库存数量/库存金额/定義原価 ← `nst_inventory_snapshot`
   ← 来自 輸出通常在庫数残数検索結果.xls 16 列 (qty_on_hand=I 列「手持合計」)
3. 库存周转率/平均在庫日数 ← `nst_turnover`
   ← 来自【ASEAN】在庫回転率.xls 8 列 (turnover_rate=G 列「回転率」)

目标输出: SKU 一元管理表格 3月 sheet 22 列格式.

业务流程:
1. 加载 sales_line + nst_inventory_snapshot + nst_turnover
2. 按 SKU 聚合 sales_line（多店铺 → 单 SKU）
3. join 库存表 (按 item_code SUM qty_on_hand / total_amount)
4. join 周转表 (按 item_code 取 turnover_rate / avg_days_on_hand)
5. 仅做必要的衍生计算: 单价 / 交叉比率(月/年) / 月周转(年) / 月售罄率
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
    "数据源 3 张源表: sales_line(销售/毛利) + nst_inventory_snapshot(库存数/金额) "
    "+ nst_turnover(回転率/平均在庫日数) · 对齐 SKU 一元管理表格 22 列 · "
    "🔑 聚合基准: UPC (=JAN), UPC 空白行已在 ingest 时跳过"
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
# 按 UPC (JAN) 聚合 — Boss 决定 UPC 是销售数据基准
# UPC 空的行已在 ingester 跳过 (xls_ingest.py)
# 多店铺 × 同 UPC → 1 行
# ============================================================
df_raw = df_raw[df_raw["upc"].notna() & (df_raw["upc"].astype(str).str.strip() != "")]
agg = df_raw.groupby("upc", as_index=False).agg(
    item_code=("item_code", "first"),
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
# 库存 join · nst_inventory_snapshot
# Boss 决定: 销售数据库存数仅看 JD-物流-千葉 仓库 (弁天 / 本社 / Amazon 不计入)
# 库存健康监控 (page 06) 才需要分 JD 和 弁天分开判断
# ============================================================
df_inv = _df(
    "SELECT item_code, upc, qty_on_hand, total_amount, location, department "
    "FROM nst_inventory_snapshot "
    "WHERE location = 'JD-物流-千葉'"
)
if not df_inv.empty:
    df_inv["qty_on_hand"] = pd.to_numeric(df_inv["qty_on_hand"], errors="coerce").fillna(0)
    df_inv["total_amount"] = pd.to_numeric(df_inv["total_amount"], errors="coerce").fillna(0)
    df_inv["upc"] = df_inv["upc"].astype(str).str.strip()
    inv_agg = df_inv[df_inv["upc"] != ""].groupby("upc", as_index=False).agg(
        qty_on_hand=("qty_on_hand", "sum"),
        inv_value=("total_amount", "sum"),
    )
    agg = agg.merge(inv_agg, on="upc", how="left")
# 确保两列存在(空表/未 join 上时填 0)
if "qty_on_hand" not in agg.columns:
    agg["qty_on_hand"] = 0
if "inv_value" not in agg.columns:
    agg["inv_value"] = 0
agg["qty_on_hand"] = pd.to_numeric(agg["qty_on_hand"], errors="coerce").fillna(0).astype(int)
agg["inv_value"] = pd.to_numeric(agg["inv_value"], errors="coerce").fillna(0)

# ============================================================
# 库存周转率 join · nst_turnover (item_code → upc 映射后再 join)
# 因 nst_turnover 没 upc 列, 通过 inventory_snapshot 拿 item_code↔upc 映射
# ============================================================
df_turn = _df(
    "SELECT item_code, turnover_rate, avg_days_on_hand, department "
    "FROM nst_turnover"
)
if not df_turn.empty:
    # 仅取 輸出事業 部门（如有 department 字段）
    if "department" in df_turn.columns:
        mask = df_turn["department"].astype(str).str.contains("輸出", na=False)
        if mask.any():
            df_turn = df_turn[mask | df_turn["department"].isna()]
    df_turn["turnover_rate"] = pd.to_numeric(df_turn["turnover_rate"], errors="coerce")
    df_turn["avg_days_on_hand"] = pd.to_numeric(df_turn["avg_days_on_hand"], errors="coerce")
    # 通过 inventory_snapshot 把 item_code 映射到 upc
    if not df_inv.empty:
        code_to_upc = (
            df_inv[df_inv["upc"] != ""][["item_code", "upc"]]
            .drop_duplicates("item_code")
        )
        df_turn = df_turn.merge(code_to_upc, on="item_code", how="left")
    else:
        df_turn["upc"] = None
    df_turn = df_turn[df_turn["upc"].notna() & (df_turn["upc"].astype(str).str.strip() != "")]
    turn_agg = df_turn.groupby("upc", as_index=False).agg(
        turnover_rate=("turnover_rate", "max"),       # 同 SKU 多行取最大
        avg_days_on_hand=("avg_days_on_hand", "max"),
    )
    agg = agg.merge(turn_agg, on="upc", how="left")
if "turnover_rate" not in agg.columns:
    agg["turnover_rate"] = 0
if "avg_days_on_hand" not in agg.columns:
    agg["avg_days_on_hand"] = 0
agg["turnover_rate"] = pd.to_numeric(agg["turnover_rate"], errors="coerce").fillna(0)
agg["avg_days_on_hand"] = pd.to_numeric(agg["avg_days_on_hand"], errors="coerce").fillna(0)

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
# 衍生指标 (公式严格对齐 SKU 一元管理表格.xlsx)
# - 月周转率 / 平均在庫日数 / 库存数量 / 库存金额 直接从源表读
# - 交叉比率(月) O = M*J  → turnover_m * gross_margin (不×100)
# - 动销率 R     = IF(E="取扱中止"|空, "中止", IF(K空|≤0, "", IF(F>0, "", "不動")))
# - 月售罄率 S    = 空白 (Boss 决定先空着)
# - 库存销售比 T = IFERROR(L/G) → inv_value / revenue
# - 利益貢献度 U = IFERROR(I/$I$2) → gross_profit / 整体总毛利
# ============================================================
agg["unit_price"] = (
    agg["revenue"] / agg["qty_sold"]
).where(agg["qty_sold"] > 0).fillna(0)
agg["turnover_m"] = agg["turnover_rate"]
agg["doh"] = agg["avg_days_on_hand"]
# O = M*J （Excel 公式严格对齐, 不再 ×100）
agg["cross_ratio_m"] = agg["turnover_m"] * agg["gross_margin"]
agg["turnover_y"] = agg["turnover_m"] * 12
agg["cross_ratio_y"] = agg["cross_ratio_m"] * 12


def _sku_active(row) -> str:
    """R 列动销率: IF(OR(E5="取扱中止",E5=""), "中止",
                     IF(OR(K5="",K5<=0), "",
                        IF(F5>0, "", "不動")))"""
    rank = str(row.get("rank", "")).strip()
    if rank in ("取扱中止", "メーカー取扱中止", ""):
        return t("中止")
    qty_inv = row.get("qty_on_hand", 0)
    if qty_inv is None or qty_inv <= 0:
        return ""
    if row.get("qty_sold", 0) > 0:
        return ""
    return t("不動")


agg["sku_active"] = agg.apply(_sku_active, axis=1)

# 月售罄率: 留空（按 Boss 决定）
agg["sellout_rate_str"] = ""

# 库存销售比 T = L/G = 库存金额 / 总营业额
agg["inv_sales_ratio"] = (
    agg["inv_value"] / agg["revenue"]
).where(agg["revenue"] > 0).fillna(0)

# 利益貢献度 U = I/$I$2
# Excel $I$2 = R2 表头单元格「取扱中商品売上」的总毛利
# 即 仅 handling_status="取扱中" SKU 的毛利总和 (排除取扱中止 / メーカー取扱中止)
_active_mask = ~df_raw["handling_status"].astype(str).str.strip().isin(
    ("取扱中止", "メーカー取扱中止")
)
total_gp_active = float(df_raw[_active_mask]["gross_profit"].sum())
agg["profit_contribution"] = (
    agg["gross_profit"] / total_gp_active
) if total_gp_active else 0


def _grade(row):
    if str(row.get("handling_status", "")).strip() in ("取扱中止", "メーカー取扱中止"):
        return t("⚫ 中止")
    if row["qty_sold"] <= 0:
        return t("⚪ 不动")
    # 等级评价用月交叉比率 (无 ×100 后, 阈值缩小 100 倍)
    cr = row["cross_ratio_m"]
    if cr >= 1.0:
        return t("🟢 A")
    if cr >= 0.5:
        return t("🟡 B")
    if cr >= 0.2:
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
        t("交叉比率"): agg["cross_ratio_m"].round(2),
        t("库存周转率(年)"): agg["turnover_y"].round(1),
        t("交叉比率(年)"): agg["cross_ratio_y"].round(2),
        t("动销率"): agg["sku_active"],
        t("月售罄率"): agg["sellout_rate_str"],   # 留空
        t("在庫販売比率"): agg["inv_sales_ratio"].round(2),
        t("利益貢献度"): agg["profit_contribution"].apply(lambda x: f"{x*100:.2f}%"),
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
