"""商品信息管理平台 · Streamlit 主入口。"""
from __future__ import annotations

import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t
from shared.supabase_client import is_configured

# 页面配置
st.set_page_config(
    page_title=t("商品信息管理平台"),
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 全局语言切换器（侧边栏顶部）
lang_selector()

conn = get_connection()

# 首页内容
st.title(f"📊 {t('商品信息管理平台')}")
st.caption(t("SmikieJapan 综合商品分析与运营工具集 · Supabase 真源 + 本地缓存"))

# Supabase 连接状态
if is_configured():
    st.success(t("🟢 Supabase 已连接"))
else:
    st.warning(t("🟡 Supabase 未配置 —— 部分模块降级为本地数据"))


# 顶部 KPI
def _count(table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    except Exception:
        return 0


col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric(t("商品"), f"{_count('item'):,}")
col2.metric(t("供应商"), f"{_count('supplier'):,}")
col3.metric(t("销售记录"), f"{_count('sales'):,}")
col4.metric(t("库存快照"), f"{_count('inventory'):,}")
col5.metric(t("采购记录"), f"{_count('purchase'):,}")
col6.metric(t("批次"), f"{_count('lot'):,}")

st.divider()

# 模块导航总览
st.subheader(t("功能模块"))

modules = [
    ("🔍 商品情报检索", "Phase 1", "按多维度筛选 SKU，看完整商品信息"),
    ("💰 定義原価編集", "Phase 1", "NetSuite Standard Cost 统一编辑"),
    ("📊 销售数据查询", "Phase 2", "按时间/商品/店铺多维度查销售"),
    ("🏪 店铺别毛利", "Phase 2", "整月与单月对比"),
    ("📦 库存健康监控", "Phase 3", "ratio_months 4 档健康度判定"),
    ("🏷️ 商品等级判定", "Phase 3", "A/B/C/停售 4 档·季度·Boss-only"),
    ("💡 运营调整建议", "Phase 3", "毛利×周转 5 档矩阵建议"),
    ("⚠️ 改廃確認", "Phase 4", "Boss 三按钮 · 联动停售"),
    ("💱 Shopee財務", "Phase 4", "拨款 + 订单级对账"),
    ("📝 商品登录", "Phase 5", "新品工作流 → NetSuite Item Create CSV"),
    ("📈 等级历史趋势", "Phase 3", "Sankey 图跨季度等级流向"),
]

for i in range(0, len(modules), 2):
    cols = st.columns(2)
    for col, (name, phase, desc) in zip(cols, modules[i : i + 2]):
        with col:
            st.markdown(f"**{t(name)}**  ·  _{phase}_")
            st.caption(t(desc))

st.divider()

# ============================================================
# 固定汇率表（公司对日元，Boss 维护，非市场实时）
# ============================================================
import pandas as pd
from shared.forex import FX_TO_JPY, FX_SYMBOLS, FX_NAMES_JA

st.subheader(t("💱 公司对日元固定汇率"))
st.caption(t("数据源 NetSuite 為替レート (発効日 2026-04-30) · PHP 由 Boss 修正为 2.4 · 修改在 shared/forex.py"))

fx_df = pd.DataFrame([
    {
        t("货币代码"): code,
        t("ソース通貨"): FX_NAMES_JA.get(code, ""),
        t("符号"): FX_SYMBOLS.get(code, ""),
        t("1 单位 → JPY"): rate,
        t("示例 (1000 单位 → JPY)"): f"¥{rate * 1000:,.0f}",
    }
    for code, rate in FX_TO_JPY.items()
    if code != "JPY"
])
st.dataframe(fx_df, use_container_width=True, hide_index=True)
st.caption(t("📌 基準通貨: 日本円 · PHP=2.4 (Boss 修正) · 其他严格按 NetSuite 為替レート"))

st.divider()

# 最近导入
st.subheader(t("最近数据导入"))

try:
    runs = conn.execute(
        """
        SELECT ingestor, source_file, total_rows, inserted, errors, run_at
        FROM _ingest_runs
        ORDER BY run_id DESC
        LIMIT 10
        """
    ).fetchall()
    if runs:
        st.dataframe(
            [dict(r) for r in runs],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(t("还没有任何导入记录。请到「⚙️ 数据导入与设置」上传 CSV"))
except Exception as e:
    st.error(f"{t('读取导入记录失败')}: {e}")
