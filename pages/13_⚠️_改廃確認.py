"""改廃確認 (Page 13) — T-318 N8N 化重写 v2.0

变更：handle_action() 不再直写 SQLite，而是 POST N8N webhook，
        N8N → cms-api /v1/kaihai/confirm → PG update + 联动停售 + 飞书 + audit。

紧急回退：env LEGACY_KAIHAI=true → 走老 SQLite 直写逻辑（旧 v1.x 行为，
                                   T-318 完工前如 N8N 故障可救场）。

env 依赖：
    N8N_WEBHOOK_KAIHAI   — N8N webhook URL（默认 http://n8n:5678/webhook/cms-kaihai-confirm）
    LEGACY_KAIHAI=true   — 走老路径（默认 false）
"""
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from shared.i18n import t, lang_selector
from shared.i18n_columns import localize_df

st.set_page_config(page_title=t("改廃確認"), page_icon="⚠️", layout="wide")
from shared.auth import require_password
require_password()
from shared.theme import inject_theme
inject_theme()
lang_selector()

# ============================================================================
# 配置
# ============================================================================

N8N_WEBHOOK_KAIHAI = os.environ.get(
    "N8N_WEBHOOK_KAIHAI",
    "http://n8n:5678/webhook/cms-kaihai-confirm",
)
LEGACY_KAIHAI = os.environ.get("LEGACY_KAIHAI", "false").lower() == "true"
WEBHOOK_TIMEOUT = float(os.environ.get("N8N_WEBHOOK_TIMEOUT", "10"))


# ============================================================================
# 处理函数（新：webhook · 旧：SQLite 直写）
# ============================================================================

def handle_action_webhook(row, action):
    """新路径：POST N8N webhook，N8N 异步处理 PG + 联动 + 飞书 + audit。"""
    run_id = f"kaihai_{row['jan']}_{int(time.time())}"
    payload = {
        "run_id": run_id,
        "jan": row["jan"],
        "source": row["source"],
        "signal_type": row["signal_type"],
        "detected_at": row["detected_at"],
        "action": action,
        "operator": "BOSS",
    }
    try:
        resp = requests.post(N8N_WEBHOOK_KAIHAI, json=payload, timeout=WEBHOOK_TIMEOUT)
    except requests.RequestException as e:
        st.error(t(f"❌ N8N webhook 调用失败: {e}"))
        st.caption(t(f"webhook url: {N8N_WEBHOOK_KAIHAI}"))
        st.caption(t("救场：设 LEGACY_KAIHAI=true 切回老路径，或检查 N8N 容器"))
        return False
    if resp.status_code not in (200, 202):
        st.warning(t(f"⚠️ N8N 响应非 2xx: {resp.status_code} body={resp.text[:200]}"))
        return False
    st.toast(t(f"✅ {action} 已入队 · run_id={run_id}"), icon="🚀")
    return True


def handle_action_legacy(conn, row, action):
    """老路径（v1.x）：Streamlit 直写 SQLite + 联动 + 飞书。LEGACY_KAIHAI=true 时启用。"""
    ts = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE discontinue_alerts
        SET acknowledged_by = ?, acknowledged_at = ?, action = ?
        WHERE jan = ? AND source = ? AND signal_type = ? AND detected_at = ?
        """,
        ("BOSS", ts, action, row["jan"], row["source"], row["signal_type"], row["detected_at"]),
    )
    if action == "取扱中止":
        conn.execute("UPDATE item_master SET rank = ? WHERE jan = ?", ("停売", row["jan"]))
    conn.commit()
    try:
        msg = f"⚠️ 改廃確認 [LEGACY] · JAN={row['jan']} · 操作={action} · By BOSS"
        subprocess.run(
            ["bash", "/Users/joe/CC/.tasks/lark-notify.sh", msg],
            check=False, timeout=10, capture_output=True,
        )
    except Exception:
        pass
    st.toast(t(f"✅ [LEGACY] {action} 已写入"), icon="📝")
    return True


def handle_action(conn, row, action):
    if LEGACY_KAIHAI:
        return handle_action_legacy(conn, row, action)
    return handle_action_webhook(row, action)


# ============================================================================
# 页面布局
# ============================================================================

st.title(t("⚠️ 改廃確認（Boss-only）"))
st.caption(t("月度改廃信号审核 · 三按钮：取扱中止 / 継続 / 代替品調査 · 取扱中止自动联动等级=停売"))

if LEGACY_KAIHAI:
    st.warning(t("⚠️ LEGACY 模式：直写 SQLite（紧急回退）· 解除回退请 unset LEGACY_KAIHAI 并重启容器"))
else:
    st.caption(t(f"🚀 N8N 模式 · webhook → cms-api → PG · `{N8N_WEBHOOK_KAIHAI}`"))

from shared.db import get_connection, DB_PATH
DB = DB_PATH
conn = get_connection()

tab1, tab2 = st.tabs([t("🆕 待確認"), t("📜 历史回看")])

# ============================================================================
# Tab 1: 待確認
# ============================================================================

with tab1:
    _rows_pending = conn.execute(
        """
        SELECT
            a.*,
            COALESCE(i.display_name, '-') AS name,
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
        """
    ).fetchall()
    pending = pd.DataFrame([dict(r) for r in _rows_pending])

    if pending.empty:
        st.success(t("✅ 暂无待確認改廃信号"))
    else:
        st.markdown(t(f"### 待確認 {len(pending)} 条"))

        sources = sorted(pending["source"].unique().tolist())
        sel_src = st.multiselect(t("来源筛选"), options=sources, default=sources)
        view = pending[pending["source"].isin(sel_src)] if sel_src else pending

        for idx, row in view.iterrows():
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 1, 1, 2])

                c1.markdown(
                    f"""
**JAN**: `{row['jan']}` · **{row.get('name', '?')}**

来源: {row['source']} · 信号: {row['signal_type']} · 库存: {row.get('qty', '?')}

检测时间: {row['detected_at']}
                """
                )

                if c2.button(t("🚫 取扱中止"), key=f"halt-{idx}"):
                    if handle_action(conn, row, "取扱中止"):
                        st.rerun()

                if c3.button(t("✅ 継続"), key=f"keep-{idx}"):
                    if handle_action(conn, row, "継続"):
                        st.rerun()

                if c4.button(t("🔍 代替品調査"), key=f"investigate-{idx}"):
                    if handle_action(conn, row, "代替品調査"):
                        st.rerun()


# ============================================================================
# Tab 2: 历史回看
# ============================================================================

with tab2:
    _rows_hist = conn.execute(
        """
        SELECT * FROM discontinue_alerts
        WHERE acknowledged_by IS NOT NULL
        ORDER BY acknowledged_at DESC
        LIMIT 500
        """
    ).fetchall()
    history = pd.DataFrame([dict(r) for r in _rows_hist])

    if history.empty:
        st.info(t("暂无历史确认记录"))
    else:
        history["month"] = pd.to_datetime(history["acknowledged_at"], format="mixed", errors="coerce").dt.strftime("%Y-%m")
        months = sorted(history["month"].unique().tolist(), reverse=True)
        sel_m = st.multiselect(t("月份筛选"), options=months, default=months)

        if sel_m:
            mask = history["month"].isin(sel_m)
            view_history = history[mask].drop(columns=["month"])
        else:
            view_history = history.drop(columns=["month"])

        st.dataframe(localize_df(view_history), use_container_width=True, height=500)

conn.close()
