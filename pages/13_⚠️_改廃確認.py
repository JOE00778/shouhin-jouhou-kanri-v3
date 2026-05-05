import streamlit as st
from shared.i18n import t, lang_selector
import pandas as pd
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime

st.set_page_config(page_title=t("改廃確認"), page_icon="⚠️", layout="wide")
lang_selector()

# ============================================================================
# 函数定义（必须在使用前）
# ============================================================================

def handle_action(conn, row, action):
    """处理 Boss 三按钮点击"""
    ts = datetime.now().isoformat()

    # 1. 更新 discontinue_alerts
    conn.execute("""
        UPDATE discontinue_alerts
        SET acknowledged_by = ?, acknowledged_at = ?, action = ?
        WHERE jan = ? AND source = ? AND signal_type = ? AND detected_at = ?
    """, ('BOSS', ts, action, row['jan'], row['source'], row['signal_type'], row['detected_at']))

    # 2. 联动等级（仅取扱中止 → item_master.rank = '停売'）
    if action == '取扱中止':
        conn.execute("UPDATE item_master SET rank = ? WHERE jan = ?", ('停売', row['jan']))

    conn.commit()

    # 3. 飞书通知（best-effort）
    try:
        msg = f"⚠️ 改廃確認 · JAN={row['jan']} · 操作={action} · By BOSS"
        subprocess.run(
            ['bash', '/Users/joe/CC/.tasks/lark-notify.sh', msg],
            check=False, timeout=10, capture_output=True
        )
    except Exception:
        pass  # 飞书失败不阻塞


# ============================================================================
# 页面布局
# ============================================================================

st.title(t("⚠️ 改廃確認（Boss-only）"))
st.caption(t("月度改廃信号审核 · 三按钮：取扱中止 / 継続 / 代替品調査 · 取扱中止自动联动等级=停売"))

from shared.db import get_connection, DB_PATH
DB = DB_PATH
conn = get_connection()

# Tab 1: 待確認  Tab 2: 历史回看
tab1, tab2 = st.tabs([t('🆕 待確認'), t('📜 历史回看')])

# ============================================================================
# Tab 1: 待確認
# ============================================================================

with tab1:
    # v2 决策：只看 取扱区分 != 取扱中止 的 SKU（已停售的不需要再扫改廃）
    # 数据源用 nst_inventory_snapshot.handling_status（更权威，跟 NetSuite 同步）
    pending = pd.read_sql_query("""
        SELECT
            a.*,
            COALESCE(i.display_name, '?') AS name,
            i.qty_on_hand AS qty,
            i.handling_status AS netsuite_status
        FROM discontinue_alerts a
        LEFT JOIN (
            SELECT item_code, MIN(display_name) AS display_name,
                   SUM(qty_on_hand) AS qty_on_hand,
                   MIN(handling_status) AS handling_status
            FROM nst_inventory_snapshot
            GROUP BY item_code
        ) i ON a.jan = i.item_code
        WHERE a.acknowledged_by IS NULL
          AND (i.handling_status IS NULL OR i.handling_status NOT IN ('取扱中止', 'メーカー取扱中止'))
        ORDER BY a.detected_at DESC
    """, conn)

    if pending.empty:
        st.success(t("✅ 暂无待確認改廃信号"))
    else:
        st.markdown(t(f"### 待確認 {len(pending)} 条"))

        # 按 source 过滤
        sources = sorted(pending['source'].unique().tolist())
        sel_src = st.multiselect(t("来源筛选"), options=sources, default=sources)
        view = pending[pending['source'].isin(sel_src)] if sel_src else pending

        # 逐行展示 + 三按钮
        for idx, row in view.iterrows():
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 1, 1, 2])

                c1.markdown(f"""
**JAN**: `{row['jan']}` · **{row.get('name', '?')}**

来源: {row['source']} · 信号: {row['signal_type']} · 库存: {row.get('qty', '?')}

检测时间: {row['detected_at']}
                """)

                # 三按钮
                if c2.button(t("🚫 取扱中止"), key=f"halt-{idx}"):
                    handle_action(conn, row, '取扱中止')
                    st.rerun()

                if c3.button(t("✅ 継続"), key=f"keep-{idx}"):
                    handle_action(conn, row, '継続')
                    st.rerun()

                if c4.button(t("🔍 代替品調査"), key=f"investigate-{idx}"):
                    handle_action(conn, row, '代替品調査')
                    st.rerun()


# ============================================================================
# Tab 2: 历史回看
# ============================================================================

with tab2:
    history = pd.read_sql_query("""
        SELECT * FROM discontinue_alerts
        WHERE acknowledged_by IS NOT NULL
        ORDER BY acknowledged_at DESC
        LIMIT 500
    """, conn)

    if history.empty:
        st.info(t("暂无历史确认记录"))
    else:
        # 按月度过滤
        history['month'] = pd.to_datetime(history['acknowledged_at']).dt.strftime('%Y-%m')
        months = sorted(history['month'].unique().tolist(), reverse=True)
        sel_m = st.multiselect(t("月份筛选"), options=months, default=months)

        if sel_m:
            mask = history['month'].isin(sel_m)
            view_history = history[mask].drop(columns=['month'])
        else:
            view_history = history.drop(columns=['month'])

        st.dataframe(view_history, use_container_width=True, height=500)

conn.close()
