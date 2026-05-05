"""商品信息管理平台 · Streamlit 主入口。

启动命令：`uv run streamlit run 商品信息管理.py`

侧边栏由 `pages/` 目录下的 .py 文件按文件名前缀数字自动排序生成。
本文件只渲染首页。
"""
from __future__ import annotations

import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t
from shared.supabase_client import is_configured

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="商品信息管理平台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 全局语言切换器（侧边栏顶部）
lang_selector()

conn = get_connection()

# ============================================================
# 首页内容
# ============================================================
st.title(f"📊 {t('商品信息管理')}")
st.caption("SmikieJapan 综合商品分析与运营工具集 · Supabase 真源 + 本地缓存")

# Supabase 连接状态
if is_configured():
    st.success("🟢 Supabase 已连接")
else:
    st.warning(
        "🟡 Supabase 未配置 —— 部分模块降级为本地数据。"
        "复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 并填入凭证以启用全功能。"
    )

# ------------------------------------------------------------
# 顶部 KPI：核心数据现状
# ------------------------------------------------------------
def _count(table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    except Exception:
        return 0


col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("商品", f"{_count('item'):,}")
col2.metric("供应商", f"{_count('supplier'):,}")
col3.metric("销售记录", f"{_count('sales'):,}")
col4.metric("库存快照", f"{_count('inventory'):,}")
col5.metric("采购记录", f"{_count('purchase'):,}")
col6.metric("批次", f"{_count('lot'):,}")

st.divider()

# ------------------------------------------------------------
# 模块导航总览
# ------------------------------------------------------------
st.subheader("功能模块")

modules = [
    ("🔍 商品情报检索", "Phase 1", "按多维度筛选 SKU，看完整商品信息"),
    ("💰 成本同步", "Phase 1", "根据平均原价批量更新定义原价"),
    ("📊 销售数据查询", "Phase 2", "按时间/商品/店铺多维度查销售"),
    ("🏪 店铺别毛利", "Phase 2", "整月与单月对比"),
    ("📦 库存健康监控", "Phase 3", "周转率 + 交叉比率 + 红黄绿告警"),
    ("🏷️ 商品等级自动判定", "Phase 3", "基于销量/周转自动给 SKU 分级"),
    ("💹 进货价格波动", "Phase 4", "SKU 级进货单价历史 + 异常告警"),
    ("📅 赏味期限管理", "Phase 4", "90/60/30 天即将到期清单"),
    ("📝 商品登录", "Phase 5", "新品工作流 → 生成 NetSuite Item Create CSV"),
    ("🛒 自动订货", "Phase 5", "再订货点 + EOQ 算法生成 PO"),
]

for i in range(0, len(modules), 2):
    cols = st.columns(2)
    for col, (name, phase, desc) in zip(cols, modules[i : i + 2]):
        with col:
            st.markdown(f"**{name}**  ·  _{phase}_")
            st.caption(desc)

st.divider()

# ------------------------------------------------------------
# 最近导入
# ------------------------------------------------------------
st.subheader("最近数据导入")

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
        st.info("还没有任何导入记录。请到「⚙️ 数据导入与设置」上传 CSV。")
except Exception as e:
    st.error(f"读取导入记录失败：{e}")

st.divider()
st.caption(
    "📚 完整设计文档：`/Users/joe/.claude/plans/tidy-yawning-pony.md`"
)
