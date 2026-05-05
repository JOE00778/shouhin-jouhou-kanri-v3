import streamlit as st
from shared.i18n import t, lang_selector
import pandas as pd
import sqlite3
from pathlib import Path
import plotly.graph_objects as go

st.set_page_config(page_title=t("等级历史趋势"), page_icon="📈", layout="wide")

from shared.db import get_connection, DB_PATH
DB = DB_PATH
conn = get_connection()

st.title(t("📈 等级历史趋势"))
st.caption(t("跨季度等级变化跟踪 · 升级/降级/稳定 SKU 分析"))

# 季度选择
quarters = pd.read_sql_query(
    "SELECT DISTINCT quarter FROM rank_history ORDER BY quarter DESC", conn
)['quarter'].tolist()

if not quarters:
    st.info(t("暂无历史数据。请先在「🏷️ 商品等级判定」page 确认变更。"))
    st.stop()

sel_q = st.multiselect(t("季度筛选"), quarters, default=quarters[:3] if len(quarters) >= 3 else quarters)

if not sel_q:
    st.warning(t("请至少选择一个季度。"))
    st.stop()

placeholders = ','.join(['?' for _ in sel_q])
df = pd.read_sql_query(f"""
    SELECT * FROM rank_history WHERE quarter IN ({placeholders})
    ORDER BY changed_at DESC
""", conn, params=sel_q)

if df.empty:
    st.info(t("选定季度内无变更记录。"))
    st.stop()

# 等级评分映射
rank_score = {
    'A': 4, 'Aランク': 4,
    'B': 3, 'Bランク': 3,
    'C': 2, 'Cランク': 2,
    'NEW': 1, '新商品': 1,
    '停售': 0, '取扱中止': 0
}

df['old_score'] = df['old_rank'].map(rank_score).fillna(1.5)
df['new_score'] = df['new_rank'].map(rank_score).fillna(1.5)

up_count = (df['new_score'] > df['old_score']).sum()
down_count = (df['new_score'] < df['old_score']).sum()
stable_count = (df['new_score'] == df['old_score']).sum()

# KPI 卡片
c1, c2, c3, c4 = st.columns(4)
c1.metric(t("总变化"), len(df))
c2.metric(t("⬆️ 升级"), int(up_count))
c3.metric(t("⬇️ 降级"), int(down_count))
c4.metric(t("➡️ 稳定"), int(stable_count))

st.divider()

# 桑基图（流向）
st.subheader(t("等级流向（Sankey）"))
flow = df.groupby(['old_rank', 'new_rank']).size().reset_index(name='count')

if not flow.empty:
    labels = sorted(list(set(flow['old_rank'].tolist() + flow['new_rank'].tolist())))
    label_idx = {l: i for i, l in enumerate(labels)}

    fig = go.Figure(go.Sankey(
        node=dict(label=labels, color=['#1f77b4'] * len(labels)),
        link=dict(
            source=[label_idx[r] for r in flow['old_rank']],
            target=[label_idx[r] for r in flow['new_rank']],
            value=flow['count'].tolist(),
        )
    ))
    fig.update_layout(height=400, margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# 历史变化表
st.subheader(t("变更明细"))
display_df = df[['sku', 'quarter', 'old_rank', 'new_rank', 'changed_by', 'changed_at']].copy()
display_df = display_df.sort_values('changed_at', ascending=False)

st.dataframe(
    display_df.head(500),
    use_container_width=True,
    height=400,
    hide_index=True
)
st.caption(t(f"显示前 500 / 共 {len(df)} 条记录"))

st.divider()

# SKU 详情下钻
st.subheader(t("🔍 单 SKU 历史下钻"))
unique_skus = sorted(df['sku'].unique().tolist())

if unique_skus:
    sel_sku = st.selectbox(t("选 SKU"), unique_skus, key="sku_select")
    sku_history = df[df['sku'] == sel_sku].sort_values('changed_at', ascending=False)

    st.dataframe(
        sku_history[['quarter', 'old_rank', 'new_rank', 'changed_by', 'changed_at']],
        use_container_width=True,
        hide_index=True
    )

    # 统计信息
    score_changes = sku_history['new_score'].iloc[0] - sku_history['old_score'].iloc[-1] if len(sku_history) > 1 else 0
    st.caption(t(f"总体变化：{score_changes:+.1f} 等级分（{sku_history['old_rank'].iloc[-1]} → {sku_history['new_rank'].iloc[0]}）"))

conn.close()
