"""模块 #2 库存健康监控 · 仪表盘 + 死钱清单。

数据来源：
- health_grade_monthly（T-014 计算结果）
- nst_inventory_snapshot（库存快照）
- item_master_netsuite（商品等级）

业务：
- 健康度 4 档分布（优秀/健康/注意/死钱）
- 等级 × 健康度 4×4 联动矩阵（A/B/C/停售 × 4 档）
- 进货周期桶分布（短/正常/长）
- 死钱清单（按金额降序）
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from shared.i18n import t, lang_selector
from shared.i18n_columns import localize_df
import plotly.express as px

from modules.inventory_health.metrics import THRESHOLD, batch_calc
from shared.db import get_connection
from shared.v2_browser import render_v2_quickview

st.set_page_config(page_title=t("库存健康监控"), page_icon="📦", layout="wide")
from shared.auth import require_password
require_password()
lang_selector()
conn = get_connection()
render_v2_quickview(conn, key_prefix="page06_")

st.title(t("📦 库存健康监控（JD-千叶仓库）"))
st.caption(t(
    "主判定：库存月数（ratio_months）· 健康黄金区 0.7-2.0 月 · "
    "🟢 ≤0.7 / 🟡 0.7-2 / 🟠 2-6 / 🔴 >6 月 · "
    "停售强制 🔴 · A/B 档 + qty=0 + 销>0 → 优秀（断货畅销）"
))
st.info(t(
    "📍 当前数据范围: 仅 JD-物流-千葉 仓库 · "
    "弁天倉庫健康度分开判断功能 后续支持 (需改 schema + metrics 参数化)"
))

# ============================================================
# 月度选择器 + 重算按钮
# ============================================================
col_ym, col_recalc = st.columns([2, 1])

with col_ym:
    ym = st.selectbox(t("月度"), ["2026-04"], index=0)

with col_recalc:
    if st.button(t("🔄 重新计算")):
        with st.spinner(t("计算中...")):
            records = batch_calc(ym)
        st.success(t(f"✅ 已计算 {len(records)} 个 SKU"))

# ============================================================
# 数据加载
# ============================================================
df = pd.DataFrame([dict(r) for r in conn.execute(
    "SELECT * FROM health_grade_monthly WHERE year_month = ?",
    (ym,)
).fetchall()])

if df.empty:
    st.warning(t("⚠️ 该月无数据。请先点【🔄 重新计算】或在「⚙️ 数据导入与设置」上传上游数据。"))
    st.stop()

# ============================================================
# KPI 卡片（5 列）
# ============================================================
g_counts = df["grade"].value_counts()
dead_total = df[df["grade"] == "🔴 死钱"]["dead_money_jpy"].fillna(0).sum()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("🟢 优秀"), g_counts.get("🟢 优秀", 0))
c2.metric(t("🟡 健康"), g_counts.get("🟡 健康", 0))
c3.metric(t("🟠 注意"), g_counts.get("🟠 注意", 0))
c4.metric(t("🔴 死钱"), g_counts.get("🔴 死钱", 0))
c5.metric(t("死钱总额"), f"¥{dead_total:,.0f}")

st.divider()

# ============================================================
# 4×4 联动矩阵（行：等级 A/B/C/停售，列：4 健康度）
# ============================================================
st.subheader(t("等级 × 健康度 联动矩阵"))

# 从 item_master_netsuite 读等级，做 SKU 维度 join
rank_df = pd.DataFrame([dict(r) for r in conn.execute(
    "SELECT internal_id as sku, rank FROM item_master_netsuite"
).fetchall()])


def map_rank(r):
    """商品ランク 简化为 A/B/C/停売"""
    if not r:
        return "C"
    r_str = str(r)
    if any(x in r_str for x in ("取扱中止", "メーカー取扱中止")):
        return "停売"
    if "A" in r_str:
        return "A"
    if "B" in r_str:
        return "B"
    if "C" in r_str:
        return "C"
    return "C"


rank_df["rank_mapped"] = rank_df["rank"].apply(map_rank)
rank_df = rank_df[["sku", "rank_mapped"]].rename(columns={"rank_mapped": "rank"})

# join
merged = df.merge(rank_df[["sku", "rank"]], on="sku", how="left").fillna({"rank": "C"})

# pivot 构建矩阵
matrix = merged.pivot_table(
    index="rank",
    columns="grade",
    values="sku",
    aggfunc="count",
    fill_value=0
)

# 确保 4×4 结构
for r in ["A", "B", "C", "停売"]:
    if r not in matrix.index:
        matrix.loc[r] = 0
for g in ["🟢 优秀", "🟡 健康", "🟠 注意", "🔴 死钱"]:
    if g not in matrix.columns:
        matrix[g] = 0

# 按顺序重排
matrix = matrix.loc[["A", "B", "C", "停売"], ["🟢 优秀", "🟡 健康", "🟠 注意", "🔴 死钱"]]
st.dataframe(localize_df(matrix), use_container_width=True)

# ============================================================
# 进货周期桶分布饼图
# ============================================================
st.subheader(t("进货周期桶分布"))
bucket_dist = df["bucket"].value_counts().reset_index()
bucket_dist.columns = ["bucket", "count"]
bucket_order = {"short": t("短（≤12月）"), "normal": t("正常（6-12月）"), "long": t("长（>12月）")}
bucket_dist["bucket_label"] = bucket_dist["bucket"].map(bucket_order)

fig = px.pie(
    bucket_dist,
    values="count",
    names="bucket_label",
    hole=0.4,
    color_discrete_map={
        t("短（≤12月）"): "#4361ee",
        t("正常（6-12月）"): "#2d6a4f",
        t("长（>12月）"): "#f77f00",
    },
)
fig.update_traces(textposition="inside", textinfo="percent+label")
st.plotly_chart(fig, use_container_width=True)

# ============================================================
# 死钱清单（🔴 SKU 按金额降序）
# ============================================================
st.subheader(t("🔴 死钱清单（按金额降序）"))

dead = df[df["grade"] == "🔴 死钱"].sort_values("dead_money_jpy", ascending=False, na_position="last")

if len(dead) == 0:
    st.info(t("暂无 🔴 死钱 SKU"))
else:
    # 从 nst_inventory_snapshot 读库存信息
    inv = pd.DataFrame([dict(r) for r in conn.execute(
        "SELECT item_code as sku, display_name, qty_on_hand, std_cost FROM nst_inventory_snapshot"
    ).fetchall()])

    dead_full = dead.merge(inv, on="sku", how="left")
    dead_full = dead_full.merge(rank_df[["sku", "rank"]], on="sku", how="left").fillna(
        {"rank": "C"}
    )

    # 显示列（表示名 / 等级 / 进货周期桶 / 跨比 / 库存量 / 定义原价 / 死钱金额）
    display_cols = ["sku", "display_name", "rank", "bucket", "cross_ratio", "qty_on_hand", "std_cost", "dead_money_jpy"]
    display_cols = [c for c in display_cols if c in dead_full.columns]

    dead_full_display = dead_full[display_cols].copy()
    dead_full_display.columns = [t("SKU"), t("商品名"), t("等级"), t("进货周期"), t("跨比"), t("库存数"), t("定价原価"), t("死钱(¥)")]

    st.dataframe(localize_df(dead_full_display.head(100)), use_container_width=True, height=400)
    st.caption(t(f"显示前 100 行 / 共 {len(dead)} 条 🔴 死钱"))
