"""模块 #7 商品情报检索（legacy `search_item` 替代）。

数据：多表 JOIN
- inventory_snapshot（按 internal_id 聚合 SUM(qty)、MAX(handling/cost/maker)）
- sales_line（按 item_code 聚合 SUM(qty/revenue)）
- inventory_turnover（按 item_code 取 turnover_rate / avg_days_on_hand）

统一视图给出每个 SKU 的:
- 基础信息：item_code / UPC / 商品名 / メーカー / ランク / 取扱区分
- 库存：在庫合計 / 確保済 / バックオーダー / 在庫金額
- 成本：std_cost / avg_cost
- 销售：直近期间 销量 / 売上 / 毛利率
- 周转：回転率 / 平均手持日数
"""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st
from shared.i18n import t, lang_selector

from shared.db import get_connection

st.set_page_config(page_title=t("商品情报检索"), page_icon="🔍", layout="wide")
conn = get_connection()

st.title(t("🔍 商品情报检索"))
st.caption(
    "库存 + 销售 + 周转率 多源 JOIN · 多维筛选 · CSV 导出 · "
    "🔒 自动过滤为「輸出」部门商品"
)

inv_count = conn.execute("SELECT COUNT(*) AS c FROM inventory_snapshot").fetchone()["c"]
if inv_count == 0:
    st.warning("⚠️ `inventory_snapshot` 表为空。请到「⚙️ 数据导入与设置」上传库存数据 .xls。")
    st.stop()


# ============================================================
# 拼一份 SKU-level 视图（cache 5 分钟）
# ============================================================
@st.cache_data(ttl=300)
def load_sku_view() -> pd.DataFrame:
    sql = """
        WITH inv AS (
            SELECT
                internal_id,
                MAX(item_code) AS item_code,
                MAX(upc) AS upc,
                MAX(display_name) AS display_name,
                MAX(handling_status) AS handling_status,
                MAX(department) AS department,
                MAX(owner) AS owner,
                MAX(avg_cost) AS avg_cost,
                MAX(std_cost) AS std_cost,
                SUM(qty_on_hand) AS qty_on_hand,
                SUM(qty_committed) AS qty_committed,
                SUM(qty_backorder) AS qty_backorder,
                SUM(total_amount) AS total_amount
            FROM inventory_snapshot
            WHERE department LIKE '%輸出%'
            GROUP BY internal_id
        ),
        sales_agg AS (
            SELECT
                item_code,
                SUM(qty_sold) AS qty_sold,
                SUM(revenue) AS revenue,
                SUM(gross_profit) AS gross_profit,
                MAX(rank) AS rank
            FROM sales_line
            GROUP BY item_code
        ),
        turnover AS (
            SELECT
                item_code,
                MAX(turnover_rate) AS turnover_rate,
                MAX(avg_days_on_hand) AS avg_days_on_hand
            FROM inventory_turnover
            GROUP BY item_code
        )
        SELECT
            inv.internal_id,
            inv.item_code,
            inv.upc,
            inv.display_name,
            inv.handling_status,
            inv.department,
            inv.owner,
            sales_agg.rank,
            inv.qty_on_hand,
            inv.qty_committed,
            inv.qty_backorder,
            inv.avg_cost,
            inv.std_cost,
            inv.total_amount,
            sales_agg.qty_sold,
            sales_agg.revenue,
            sales_agg.gross_profit,
            turnover.turnover_rate,
            turnover.avg_days_on_hand
        FROM inv
        LEFT JOIN sales_agg ON sales_agg.item_code = inv.item_code
        LEFT JOIN turnover ON turnover.item_code = inv.item_code
    """
    return pd.DataFrame([dict(r) for r in conn.execute(sql).fetchall()])


df = load_sku_view()
total_skus = len(df)


# ============================================================
# 筛选 UI
# ============================================================
ALL = "全部"

c1, c2 = st.columns(2)
with c1:
    keyword_code = st.text_input("商品コード / JAN", placeholder="例: 4515061012818")
with c2:
    keyword_name = st.text_input("商品名（部分一致）", placeholder="例: パーフェクトジェル")

multi_jan = st.text_area(
    "批量 JAN（换行 / 逗号分隔）",
    placeholder="4901234567890\n4987654321098",
    height=100,
)

c3, c4, c5, c6 = st.columns(4)

with c3:
    handle_opts = sorted([h for h in df["handling_status"].dropna().unique().tolist()])
    handle_pick = st.selectbox("取扱区分", [ALL, "在扱中（除取扱中止）"] + handle_opts)

with c4:
    rank_opts = sorted([
        r for r in df["rank"].dropna().unique().tolist()
        if r and r != "取扱中止"
    ])
    rank_pick = st.selectbox("商品ランク", [ALL] + rank_opts)

with c5:
    dept_opts = sorted([d for d in df["department"].dropna().unique().tolist()])
    dept_pick = st.selectbox("部門", [ALL] + dept_opts)

with c6:
    show_only_in_stock = st.checkbox("仅有库存（qty > 0）", value=False)


# ============================================================
# Apply filters
# ============================================================
df_view = df.copy()

# 多 JAN 优先
jan_list = [j.strip() for j in re.split(r"[,\n\r]+", multi_jan) if j.strip()]
if jan_list:
    df_view = df_view[
        df_view["upc"].astype(str).isin(jan_list)
        | df_view["item_code"].astype(str).isin(jan_list)
    ]
elif keyword_code:
    kw = keyword_code.strip()
    df_view = df_view[
        df_view["item_code"].astype(str).str.contains(kw, case=False, na=False)
        | df_view["upc"].astype(str).str.contains(kw, case=False, na=False)
        | df_view["internal_id"].astype(str).str.contains(kw, case=False, na=False)
    ]

if keyword_name:
    df_view = df_view[
        df_view["display_name"].astype(str).str.contains(keyword_name.strip(), case=False, na=False)
    ]

if handle_pick == "在扱中（除取扱中止）":
    df_view = df_view[df_view["handling_status"] != "取扱中止"]
elif handle_pick != ALL:
    df_view = df_view[df_view["handling_status"] == handle_pick]

if rank_pick != ALL:
    df_view = df_view[df_view["rank"] == rank_pick]

if dept_pick != ALL:
    df_view = df_view[df_view["department"] == dept_pick]

if show_only_in_stock:
    df_view = df_view[df_view["qty_on_hand"].fillna(0) > 0]


# ============================================================
# 顶部统计 + 表格
# ============================================================
hl, hr = st.columns([1, 0.2])
hl.subheader("商品一覧")
hr.markdown(
    f"<h4 style='text-align:right; margin-top: .6em;'>{len(df_view):,} / {total_skus:,} 件</h4>",
    unsafe_allow_html=True,
)

if df_view.empty:
    st.info("当前条件下没有任何 SKU。调整过滤再试。")
    st.stop()

# 排序：默认按销量降序
sort_options = {
    "销量降序": ("qty_sold", False),
    "销量升序": ("qty_sold", True),
    "库存降序": ("qty_on_hand", False),
    "库存金额降序": ("total_amount", False),
    "周转率降序": ("turnover_rate", False),
    "周转率升序（最差先）": ("turnover_rate", True),
    "商品コード": ("item_code", True),
}
sort_pick = st.selectbox("排序", list(sort_options.keys()))
sort_col, sort_asc = sort_options[sort_pick]
df_view = df_view.sort_values(sort_col, ascending=sort_asc, na_position="last")

# 显示用：重命名列、格式化金额
display_cols = [
    "internal_id", "item_code", "upc", "display_name",
    "handling_status", "rank", "department",
    "qty_on_hand", "qty_committed", "qty_backorder",
    "std_cost", "avg_cost", "total_amount",
    "qty_sold", "revenue", "gross_profit",
    "turnover_rate", "avg_days_on_hand",
]
df_show = df_view[display_cols].copy()
df_show = df_show.rename(columns={
    "internal_id": "Internal ID",
    "item_code": "アイテム",
    "upc": "JAN",
    "display_name": "商品名",
    "handling_status": "取扱区分",
    "rank": "ランク",
    "department": "部門",
    "qty_on_hand": "在庫合計",
    "qty_committed": "確保済",
    "qty_backorder": "バックオーダー",
    "std_cost": "定義原価",
    "avg_cost": "平均原価",
    "total_amount": "在庫金額",
    "qty_sold": "販売数",
    "revenue": "売上",
    "gross_profit": "粗利",
    "turnover_rate": "回転率",
    "avg_days_on_hand": "平均手持日数",
})

st.dataframe(df_show, use_container_width=True, hide_index=True)

# CSV 下载
csv = df_show.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "📥 当前视图 CSV",
    data=csv,
    file_name=f"item_search_{len(df_show)}.csv",
    mime="text/csv",
)


# ============================================================
# 单 SKU 详情卡片
# ============================================================
st.divider()
st.subheader("🔎 SKU 详情卡片")

if len(df_view) > 0:
    sku_choices = df_view.apply(
        lambda r: f"{r['item_code']} · {r['display_name'] or '(无商品名)'}",
        axis=1
    ).tolist()
    pick = st.selectbox("选择 SKU", sku_choices)
    pick_row = df_view.iloc[sku_choices.index(pick)]

    cd1, cd2, cd3 = st.columns(3)
    with cd1:
        st.markdown(f"**商品コード**: `{pick_row['item_code']}`")
        st.markdown(f"**JAN (UPC)**: `{pick_row['upc']}`")
        st.markdown(f"**Internal ID**: `{pick_row['internal_id']}`")
        st.markdown(f"**商品名**: {pick_row['display_name']}")
    with cd2:
        st.markdown(f"**取扱区分**: {pick_row['handling_status']}")
        st.markdown(f"**ランク**: {pick_row['rank'] or '—'}")
        st.markdown(f"**部門**: {pick_row['department'] or '—'}")
        st.markdown(f"**担当者**: {pick_row['owner'] or '—'}")
    with cd3:
        st.metric("在庫合計", f"{int(pick_row['qty_on_hand'] or 0):,}")
        st.metric("販売実績", f"{int(pick_row['qty_sold'] or 0):,}")
        if pick_row['turnover_rate'] is not None:
            st.metric("回転率", f"{pick_row['turnover_rate']:.2f}")

    # 各仓库库存细分
    st.markdown("**各仓库库存细分**")
    inv_detail = pd.DataFrame([dict(r) for r in conn.execute(
        """
        SELECT location, bin_number, qty_on_hand, qty_committed, qty_backorder, std_cost, avg_cost
        FROM inventory_snapshot
        WHERE internal_id = ?
        ORDER BY location, bin_number
        """,
        (pick_row["internal_id"],),
    ).fetchall()])
    if not inv_detail.empty:
        st.dataframe(inv_detail, use_container_width=True, hide_index=True)

    # 销售明细
    st.markdown("**销售明细**")
    sales_detail = pd.DataFrame([dict(r) for r in conn.execute(
        """
        SELECT source, period_start, period_end, store, qty_sold, revenue, gross_profit, gross_margin
        FROM sales_line
        WHERE item_code = ?
        ORDER BY period_start DESC, store
        """,
        (pick_row["item_code"],),
    ).fetchall()])
    if sales_detail.empty:
        st.caption("（无销售记录）")
    else:
        st.dataframe(sales_detail, use_container_width=True, hide_index=True)
