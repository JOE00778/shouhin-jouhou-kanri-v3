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
from shared.i18n import t, lang_selector

from shared.db import get_connection
from shared.markets import ALL_MARKETS, add_market_column

st.set_page_config(page_title=t("店铺别毛利"), page_icon="🏪", layout="wide")
from shared.auth import require_password
require_password()
lang_selector()
conn = get_connection()

st.title(t("🏪 店铺别毛利"))
st.caption(t("基于 NetSuite 销售报表 · 自带毛利+毛利率，零计算直接展示"))


sales_count = conn.execute(
    "SELECT COUNT(*) AS c FROM sales_line"
).fetchone()["c"]
if sales_count == 0:
    st.warning(
        t("⚠️ 没有销售数据。请到「⚙️ 数据导入与设置」上传 "
        "`【ASEAN】店舗別売上 集計専用.xls` / `【ASEAN】店舗別売上（前日）.xls` / `【輸出】店舗別売上.xls`。")
    )
    st.stop()


# ============================================================
# 选择 维度（月度 / 前日）+ 期间
# ============================================================
DIM_TO_SOURCES = {
    t("📅 月度"): ["asean_monthly", "export_store"],
    t("📊 前日"): ["asean_daily"],
}
sel_dim = st.radio(t("维度"), list(DIM_TO_SOURCES.keys()), horizontal=True)
allowed_srcs = DIM_TO_SOURCES[sel_dim]

# ============================================================
# 🩺 诊断：无条件显示 sales_line 表全源概况，方便排查上传问题
# ============================================================
with st.expander("🩺 sales_line 表诊断（点开看每个 source/期间的行数）", expanded=False):
    diag_all = pd.DataFrame([dict(r) for r in conn.execute("""
        SELECT source, period_start, period_end, COUNT(*) AS total,
               SUM(CASE WHEN store IS NOT NULL AND store != '' THEN 1 ELSE 0 END) AS with_store,
               COUNT(DISTINCT store) AS distinct_stores
        FROM sales_line
        GROUP BY source, period_start, period_end
        ORDER BY period_start DESC, source
    """).fetchall()])
    if diag_all.empty:
        st.error("sales_line 整表为空：还没有任何销售文件 ingest 成功")
    else:
        st.dataframe(diag_all, hide_index=True, use_container_width=True)
        st.caption("当 with_store=0 时 page 05 会过滤掉所有行（店铺名未识别为 Shopee/Lazada/Tokopedia/Coupang 前缀）。")

# 期间选项：当前维度下可用的所有期间（先不过滤 store）
src_placeholders = ",".join("?" * len(allowed_srcs))
period_opts = conn.execute(
    f"SELECT period_start, period_end, COUNT(*) AS total, "
    f"SUM(CASE WHEN store IS NOT NULL AND store != '' THEN 1 ELSE 0 END) AS with_store "
    f"FROM sales_line WHERE source IN ({src_placeholders}) "
    f"GROUP BY period_start, period_end ORDER BY period_start DESC",
    allowed_srcs,
).fetchall()
period_choices = [(r["period_start"], r["period_end"]) for r in period_opts]
if not period_choices:
    st.warning(
        f"📊 当前选择「{sel_dim}」对应 source = {allowed_srcs}，"
        f"但 sales_line 表里没有任何这种 source 的数据。"
    )
    # 内嵌上传：直接路由到该维度的第一个 ingester（asean_daily / asean_monthly）
    target_ingester = allowed_srcs[0]
    st.markdown(f"#### 🚀 直接上传文件到 `{target_ingester}` ingester")
    st.caption(
        "绕过文件名识别，强制路由。适合文件名已变 / 自动识别失败的情况。"
    )
    inline_file = st.file_uploader(
        f"上传 {target_ingester} 报表（.xls / .xlsx）",
        type=["xls", "xlsx"],
        key=f"__inline_upload_{target_ingester}",
    )
    if inline_file is not None:
        from data_warehouse.ingest.xls_ingest import INGESTOR_REGISTRY
        from shared.db import INPUTS_DIR
        from datetime import datetime as _dt
        INPUTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        save_path = INPUTS_DIR / f"{ts}_{inline_file.name}"
        save_path.write_bytes(inline_file.getvalue())
        try:
            result = INGESTOR_REGISTRY[target_ingester](
                save_path, conn, source_name=inline_file.name
            )
            st.success(
                f"✅ 已导入 `{inline_file.name}` → {target_ingester} · "
                f"总 {result['total']:,} · 入库 {result['inserted']:,} · "
                f"错误 {result['errors']:,}"
            )
            if result["inserted"] > 0:
                st.balloons()
                if st.button("🔄 刷新页面查看数据"):
                    st.rerun()
            else:
                st.error("❌ 入库 0 行——文件格式可能不对，把文件第 1-3 行截图给我")
        except Exception as e:
            st.error(f"❌ 导入失败：{e}")
    st.stop()
sel_period = st.selectbox(
    t("期间"),
    period_choices,
    format_func=lambda p: f"{p[0]} ~ {p[1]}",
)

# 加载明细（不再硬过滤 store IS NOT NULL；店铺识别失败的行用占位符兜底）
df = pd.DataFrame([dict(r) for r in conn.execute(
    f"""
    SELECT COALESCE(NULLIF(TRIM(COALESCE(store, '')), ''), '（未识别店铺）') AS store,
           item_code, display_name, qty_sold, revenue,
           defined_cost, gross_profit, gross_margin, rank
    FROM sales_line
    WHERE source IN ({src_placeholders}) AND period_start = ? AND period_end = ?
    """,
    (*allowed_srcs, sel_period[0], sel_period[1]),
).fetchall()])

if df.empty:
    st.info(t("此条件下无数据。"))
    st.stop()

# 加 market 列（基于 store）
df = add_market_column(df, store_col="store")

# 市场过滤
mk_choices = [t("全部市场")] + ALL_MARKETS
mk_pick = st.selectbox(t("市场"), mk_choices, index=0)
if mk_pick != t("全部市场"):
    df = df[df["market"] == mk_pick]
if df.empty:
    st.info(t("此市场下无数据。"))
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
c1.metric(t("总销量"), f"{total_qty:,}")
c2.metric(t("总售价（¥）"), f"{total_rev:,.0f}")
c3.metric(t("总成本（¥）"), f"{total_cost:,.0f}")
c4.metric(t("毛利（¥）"), f"{total_gp:,.0f}")
c5.metric(t("毛利率"), f"{total_margin:.2f}%")

st.divider()

tab_market, tab_store, tab_top_skus = st.tabs(
    [t("🌐 按市场聚合"), t("📊 按店铺聚合"), t("🏆 TOP SKU 贡献")]
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
    n_top = st.slider(t("Top N"), 10, 100, 30, 10)
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
st.caption(f"{t('维度')}：{sel_dim} · {t('期间')}：{sel_period[0]} ~ {sel_period[1]}")
