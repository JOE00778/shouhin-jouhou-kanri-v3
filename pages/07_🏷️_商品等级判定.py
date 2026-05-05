import streamlit as st
from shared.i18n import t
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
from modules.rank_classifier.proposal import generate_proposal, export_csv

st.set_page_config(page_title=t("商品等级判定"), page_icon="🏷️", layout="wide")
st.title(t("🏷️ 商品等级判定（季度·Boss-only）"))
st.caption("基于销售前 80% × 利润率 ≥59% 的 4 档判定 (A/B/C/停售) · 仅 Boss 可确认变更")

DB = Path(__file__).parent.parent / "data_warehouse" / "warehouse.db"

# 日期映射：Q → year_month（用于 operation_advice_monthly JOIN）
QUARTER_TO_MONTH = {'2026-Q1': '2026-04', '2026-Q2': '2026-07'}

# 季度选择器
q = st.selectbox("季度", ['2026-Q1', '2026-Q2'], index=0)

# Tab 1: 生成新建议  Tab 2: 历史回看
tab1, tab2 = st.tabs(['🆕 新建议', '📜 历史回看'])

with tab1:
    if 'proposal_data' not in st.session_state:
        st.session_state.proposal_data = None

    if st.button("🔄 生成等级建议"):
        with st.spinner("跑 generate_proposal..."):
            data = generate_proposal(q, str(DB))
            st.session_state.proposal_data = data
        st.success(f"✓ 已生成 {len(data)} 条建议")

    if st.session_state.proposal_data:
        df = pd.DataFrame(st.session_state.proposal_data)

        # 增强：JOIN operation_advice_monthly
        if 'sku' in df.columns:
            year_month = QUARTER_TO_MONTH.get(q, '2026-04')
            try:
                conn = sqlite3.connect(str(DB))
                adv = pd.read_sql_query(
                    "SELECT sku, advice FROM operation_advice_monthly WHERE year_month=?",
                    conn, params=[year_month])
                conn.close()
                df = df.merge(adv, on='sku', how='left').fillna({'advice': '—'})
            except Exception:
                df['advice'] = '—'

        # KPI 卡片
        c1, c2, c3, c4, c5 = st.columns(5)
        c6, c7, c8 = st.columns(3)

        if 'new_rank' in df.columns:
            counts = df['new_rank'].value_counts()
            c1.metric("A", int(counts.get('A', 0)))
            c2.metric("B", int(counts.get('B', 0)))
            c3.metric("C", int(counts.get('C', 0)))
            c4.metric("停售", int(counts.get('停售', 0)))
            change_n = (df['old_rank'] != df['new_rank']).sum() if 'old_rank' in df.columns else 0
            c5.metric("有变化", int(change_n))

        # 趋势计数
        if 'trend' in df.columns:
            trend_counts = df['trend'].value_counts()
            c6.metric("⬆️ 升级", int(trend_counts.get('⬆️ 升级', 0)))
            c7.metric("⬇️ 降级", int(trend_counts.get('⬇️ 降级', 0)))
            c8.metric("➡️ 维持", int(trend_counts.get('➡️ 维持', 0)))

        # 过滤区块
        st.markdown("### 过滤")
        f1, f2, f3, f4 = st.columns(4)
        rank_filter = f1.multiselect(
            "新档",
            options=sorted(df['new_rank'].unique()) if 'new_rank' in df.columns else [],
            default=[]
        )
        trend_filter = f2.multiselect(
            "趋势",
            options=sorted(df['trend'].unique()) if 'trend' in df.columns else [],
            default=[]
        )
        change_only = f3.checkbox("只看有变化", value=True)
        sample = f4.number_input("显示行数", min_value=10, max_value=10000, value=100, step=50)

        view = df.copy()
        if rank_filter:
            view = view[view['new_rank'].isin(rank_filter)]
        if trend_filter:
            view = view[view['trend'].isin(trend_filter)]
        if change_only and 'old_rank' in view.columns:
            view = view[view['old_rank'] != view['new_rank']]

        st.markdown(f"### 建议清单（{len(view)} 条）")

        # 显示表（选择显示列）
        view_display = view.head(int(sample)).copy()
        display_cols = ['sku', 'name', 'old_rank', 'new_rank', 'trend', 'advice', 'sales', 'margin', 'rank_pct']
        display_cols = [c for c in display_cols if c in view_display.columns]
        st.dataframe(view_display[display_cols], use_container_width=True, height=400)

        # 确认操作区块
        st.markdown("### ✅ 确认变更")
        n_to_confirm = len(view)
        st.warning(f"⚠️ 即将变更 {n_to_confirm} 个 SKU 的等级")

        # 二次确认：checkbox + button
        confirmed = st.checkbox("我已审阅并确认")
        if confirmed:
            if st.button("✅ 写入 rank_history + 导出 CSV"):
                # 找出有变化的行
                rows_to_insert = []
                for _, row in view.iterrows():
                    if row.get('old_rank') != row.get('new_rank'):
                        rows_to_insert.append((
                            row.get('sku'),
                            q,
                            row.get('old_rank'),
                            str(row.get('new_rank')),
                            'BOSS',
                            datetime.now().isoformat()
                        ))

                # 写入 rank_history
                conn = sqlite3.connect(str(DB))
                if rows_to_insert:
                    conn.executemany(
                        """INSERT OR REPLACE INTO rank_history
                           (sku, quarter, old_rank, new_rank, changed_by, changed_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        rows_to_insert
                    )
                    conn.commit()
                conn.close()

                # 导出 CSV
                csv_path = Path(f'/tmp/rank_update_{q}.csv')
                export_csv(st.session_state.proposal_data, str(csv_path))
                st.success(f"✅ 写入 rank_history {len(rows_to_insert)} 条 + 导出 {csv_path.name}")

                # 提供下载
                if csv_path.exists():
                    with open(csv_path, 'rb') as f:
                        st.download_button(
                            "📥 下载 rank_update.csv",
                            f.read(),
                            file_name=csv_path.name,
                            mime='text/csv'
                        )

with tab2:
    conn = sqlite3.connect(str(DB))
    history = pd.read_sql_query(
        "SELECT * FROM rank_history ORDER BY changed_at DESC",
        conn
    )
    conn.close()

    if history.empty:
        st.info("暂无历史变更")
    else:
        # 按季度过滤
        quarters = sorted(history['quarter'].unique().tolist(), reverse=True)
        sel_q = st.multiselect("季度筛选", options=quarters, default=quarters)
        view2 = history[history['quarter'].isin(sel_q)] if sel_q else history
        st.dataframe(view2, use_container_width=True, height=500)
