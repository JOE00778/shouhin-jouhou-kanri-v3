"""模块 #1 定義原価編集（原「成本同步」· 2026-05-05 改名）· Streamlit 页面（v2 — 基于 inventory_snapshot 表）。

业务定位：NetSuite Standard Cost = 定義原価 字段的统一管理入口。
本质是修改 NetSuite Standard Cost（= 定義原価）。两种触发场景共用同一流程：
  ① 数据驱动：上传 NetSuite 在库 .xls → 系统检测 avg_cost 偏差 → 提议更新 std_cost
  ② Boss 决策：Boss 跟供应商谈了新价 / 政策调整 → 在结果表内手动覆盖 std_cost_new → 生成 CSV
两种场景输出**同一份 cost_update.csv**，由 Boss 上传 NetSuite Item Import。

数据来源：用户在「⚙️ 数据导入与设置」页上传过的 NetSuite 在库数据 .xls
        → 入到 inventory_snapshot 表（含 std_cost + avg_cost）

流程：
  1. 选择快照（默认最新）+ 过滤条件（場所 / 取扱区分 / 担当者 / 部門）
  2. 按 internal_id 聚合：
     - std_cost / avg_cost 取首个非空（同 SKU 在不同 location 应该一致）
     - qty_on_hand 求和（用于显示）
  3. 应用业务规则（5 类 SKIP + 阈值 + ceil）
  4. 预览三 Tab（更新 / 跳过 / 异常）
  5. 确认 → 生成 NetSuite CSV Import 文件
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from shared.i18n import t, lang_selector

from data_warehouse.exports.cost_update import CostUpdateExporter
from modules.cost_sync.rules import (
    THRESHOLD_PCT,
    THRESHOLD_YEN,
    decide_action,
)
from shared.db import OUTPUTS_DIR, get_connection

st.set_page_config(page_title=t("定義原価編集"), page_icon="💰", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("💰 定義原価編集"))
st.caption(
    f"NetSuite Standard Cost（定義原価）統一編集口 · "
    f"自動判定阈值 |Δ|≥{THRESHOLD_YEN:.0f}¥ 或 |Δ%|≥{THRESHOLD_PCT:.0%} · "
    f"新值 = ⌈avg⌉ · 也支持 Boss 手动覆盖 std_cost_new"
)

# ============================================================
# 0. 检查数据
# ============================================================
inv_count = conn.execute("SELECT COUNT(*) AS c FROM inventory_snapshot").fetchone()["c"]
if inv_count == 0:
    st.warning(
        t("⚠️ `inventory_snapshot` 表为空。请先到「⚙️ 数据导入与设置」上传 "
        "`FB全倉庫通常在庫数残数検索結果.xls`。")
    )
    st.stop()

# ============================================================
# Session state
# ============================================================
if "cs_step" not in st.session_state:
    st.session_state.cs_step = 1
if "cs_decisions" not in st.session_state:
    st.session_state.cs_decisions = None
if "cs_output_path" not in st.session_state:
    st.session_state.cs_output_path = None


def _reset() -> None:
    st.session_state.cs_step = 1
    st.session_state.cs_decisions = None
    st.session_state.cs_output_path = None


# ============================================================
# 进度条
# ============================================================
step = st.session_state.cs_step
prog_cols = st.columns(3)
for i, label in enumerate([t("1️⃣ 选择数据 + 过滤"), t("2️⃣ 预览结果"), t("3️⃣ 下载输出")], 1):
    with prog_cols[i - 1]:
        if i == step:
            st.info(f"**{label}**")
        elif i < step:
            st.success(f"{label} ✓")
        else:
            st.caption(label)

st.divider()


# ============================================================
# 步骤 1：选择数据范围
# ============================================================
if step == 1:
    st.subheader(t("📋 步骤 1 / 3：选择数据范围"))

    # 快照选项
    snapshots = conn.execute(
        "SELECT DISTINCT snapshot_at FROM inventory_snapshot ORDER BY snapshot_at DESC"
    ).fetchall()
    snapshot_choices = [r["snapshot_at"] for r in snapshots]
    sel_snapshot = st.selectbox(
        t("在库数据快照（默认最新）"), snapshot_choices, index=0
    )

    # 过滤条件（部门固定为含「輸出」的，不在 UI 暴露；仓库限定 JD + 弁天，ingest 时已过滤）
    DEPT_KEYWORD = "輸出"
    LOC_BOTH = "JD + 弁天（默认）"
    HANDLE_PRESET_ALL = "全部"

    # 自动锁定 部門：含「輸出」的全部
    sel_dept = [
        r["department"] for r in conn.execute(
            "SELECT DISTINCT department FROM inventory_snapshot WHERE snapshot_at=?",
            (sel_snapshot,)
        ).fetchall()
        if r["department"] and DEPT_KEYWORD in r["department"]
    ]

    # 数据源
    loc_all = [r["location"] for r in conn.execute(
        "SELECT DISTINCT location FROM inventory_snapshot WHERE snapshot_at=? ORDER BY location",
        (sel_snapshot,)
    ).fetchall() if r["location"]]
    handle_all = [r["handling_status"] for r in conn.execute(
        "SELECT DISTINCT handling_status FROM inventory_snapshot WHERE snapshot_at=? ORDER BY handling_status",
        (sel_snapshot,)
    ).fetchall() if r["handling_status"]]

    loc_choices = [LOC_BOTH] + loc_all
    handle_choices = [HANDLE_PRESET_ALL] + handle_all

    c1, c2 = st.columns(2)
    with c1:
        loc_pick = st.selectbox(t("場所（仓库）"), loc_choices, index=0)
    with c2:
        handle_pick = st.selectbox(t("取扱区分"), handle_choices, index=0)

    sel_locs = loc_all if loc_pick == LOC_BOTH else [loc_pick]
    sel_handle = handle_all if handle_pick == HANDLE_PRESET_ALL else [handle_pick]

    st.caption(
        f"📌 已选场所：{', '.join(sel_locs)} ｜ "
        f"取扱区分：{', '.join(sel_handle)} ｜ "
        f"部門（自动锁定）：{', '.join(sel_dept) or '（无）'}"
    )

    # 预览过滤后的 SKU 数
    where = ["snapshot_at = :snap"]
    params: dict = {"snap": sel_snapshot}
    if sel_locs:
        placeholders = ",".join(f":loc{i}" for i in range(len(sel_locs)))
        where.append(f"location IN ({placeholders})")
        params.update({f"loc{i}": v for i, v in enumerate(sel_locs)})
    if sel_handle:
        placeholders = ",".join(f":h{i}" for i in range(len(sel_handle)))
        where.append(f"handling_status IN ({placeholders})")
        params.update({f"h{i}": v for i, v in enumerate(sel_handle)})
    if sel_dept:
        placeholders = ",".join(f":d{i}" for i in range(len(sel_dept)))
        where.append(f"department IN ({placeholders})")
        params.update({f"d{i}": v for i, v in enumerate(sel_dept)})

    where_sql = " AND ".join(where)
    sku_count = conn.execute(
        f"SELECT COUNT(DISTINCT internal_id) AS c FROM inventory_snapshot WHERE {where_sql}",
        params,
    ).fetchone()["c"]

    st.metric(t("过滤后唯一 SKU 数"), f"{sku_count:,}")

    if sku_count == 0:
        st.warning(t("当前过滤条件下没有 SKU。请调整。"))
        st.stop()

    if st.button(t("🚀 计算并预览"), type="primary"):
        # 按 internal_id 聚合：avg/std 取 MAX（同 SKU 各 location 应一致；MAX 兜底）
        # qty 求和（仅展示用）
        agg_sql = f"""
            SELECT
                internal_id,
                MAX(item_code) AS item_code,
                MAX(display_name) AS display_name,
                MAX(handling_status) AS handling_status,
                MAX(avg_cost) AS avg_cost,
                MAX(std_cost) AS std_cost,
                SUM(qty_on_hand) AS total_qty
            FROM inventory_snapshot
            WHERE {where_sql}
            GROUP BY internal_id
        """
        rows = conn.execute(agg_sql, params).fetchall()

        # 跑业务规则
        decisions = []
        for r in rows:
            row = {
                "internal_id": r["internal_id"],
                "item_code": r["item_code"],
                "display_name": r["display_name"],
                "avg_cost": r["avg_cost"],
                "std_cost_old": r["std_cost"],
            }
            # master 用本行自身（同源数据，handling_status 已经在过滤里了）
            master = {
                "handling_status": r["handling_status"],
                "display_name": r["display_name"],
            }
            d = decide_action(row, master)
            d["total_qty"] = r["total_qty"]  # 附带库存量给预览
            decisions.append(d)

        st.session_state.cs_decisions = decisions
        st.session_state.cs_step = 2
        st.rerun()


# ============================================================
# 步骤 2：预览
# ============================================================
elif step == 2:
    st.subheader(t("🔍 步骤 2 / 3：预览结果"))

    decisions = st.session_state.cs_decisions or []
    df_all = pd.DataFrame(decisions)

    total = len(df_all)
    n_update = (df_all["action"] == "UPDATE").sum() if total else 0
    n_skip = total - n_update
    n_red = (df_all.get("severity") == "RED").sum() if total else 0
    n_yellow = (df_all.get("severity") == "YELLOW").sum() if total else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("候选 SKU 总数"), f"{total:,}")
    c2.metric(t("✅ 触发更新"), f"{n_update:,}")
    c3.metric(t("⏭️ 跳过"), f"{n_skip:,}")
    c4.metric(t("⚠️ 异常 R+Y"), f"{n_red + n_yellow:,}")

    if n_skip > 0:
        skip_breakdown = (
            df_all[df_all["action"] != "UPDATE"]["action"]
            .value_counts()
            .to_dict()
        )
        st.caption(t("跳过原因分布：") + " · ".join(f"{k}: {v}" for k, v in skip_breakdown.items()))

    st.divider()

    tab_u, tab_s, tab_a = st.tabs([
        t(f"✅ 更新清单 ({n_update})"),
        t(f"⏭️ 跳过清单 ({n_skip})"),
        t(f"⚠️ 异常告警 ({n_red + n_yellow})"),
    ])

    with tab_u:
        df_u = df_all[df_all["action"] == "UPDATE"].copy()
        if df_u.empty:
            st.info(t("本次没有 SKU 触发更新。"))
        else:
            df_show = df_u[["internal_id", "item_code", "display_name", "total_qty",
                            "std_cost_old", "std_cost_new", "diff", "diff_pct", "severity"]]
            df_show = df_show.copy()
            df_show["diff_pct"] = df_show["diff_pct"].apply(
                lambda x: f"{x:+.2%}" if pd.notna(x) else ""
            )
            st.dataframe(df_show, use_container_width=True, hide_index=True)

    with tab_s:
        df_s = df_all[df_all["action"] != "UPDATE"].copy()
        if df_s.empty:
            st.info(t("没有任何 SKU 被跳过。"))
        else:
            df_show = df_s[["internal_id", "item_code", "display_name", "total_qty",
                            "avg_cost", "std_cost_old", "action", "skip_reason"]]
            st.dataframe(df_show, use_container_width=True, hide_index=True)

    with tab_a:
        df_a = df_all[df_all.get("severity").isin(["RED", "YELLOW"])].copy()
        if df_a.empty:
            st.success(t("✅ 无异常告警。"))
        else:
            df_a = df_a.sort_values(
                by=["severity", "diff_pct"],
                key=lambda s: s.map({"RED": 0, "YELLOW": 1}) if s.name == "severity" else s.abs(),
                ascending=[True, False],
            )
            df_show = df_a[["severity", "internal_id", "item_code", "display_name",
                            "std_cost_old", "avg_cost", "std_cost_new",
                            "diff", "diff_pct", "action"]].copy()
            df_show["diff_pct"] = df_show["diff_pct"].apply(
                lambda x: f"{x:+.2%}" if pd.notna(x) else ""
            )
            st.dataframe(df_show, use_container_width=True, hide_index=True)

    st.divider()
    btn_back, btn_next = st.columns(2)
    with btn_back:
        if st.button(t("← 重新选择数据"), use_container_width=True):
            _reset()
            st.rerun()
    with btn_next:
        if n_update == 0:
            st.button(
                t("确认并生成 CSV →"), type="primary", disabled=True, use_container_width=True
            )
            st.caption(t("没有需要更新的 SKU"))
        else:
            if st.button(
                t(f"确认并生成 CSV ({n_update} 行) →"), type="primary", use_container_width=True
            ):
                rows = CostUpdateExporter.build_rows(decisions)
                file_path, _ = CostUpdateExporter().export(
                    rows, OUTPUTS_DIR, conn,
                    notes=f"snapshot={st.session_state.cs_decisions[0].get('snapshot_at', '?') if decisions else '?'}",
                )
                st.session_state.cs_output_path = file_path
                st.session_state.cs_step = 3
                st.rerun()


# ============================================================
# 步骤 3：下载
# ============================================================
elif step == 3:
    st.subheader(t("✅ 步骤 3 / 3：完成"))

    file_path = st.session_state.cs_output_path
    if file_path and file_path.exists():
        with file_path.open("rb") as f:
            data = f.read()
        st.success(t(f"已生成更新 CSV：`{file_path.name}`"))
        st.download_button(
            t("📥 下载更新 CSV"),
            data=data,
            file_name=file_path.name,
            mime="text/csv",
            type="primary",
            use_container_width=True,
        )
        st.divider()
        st.markdown(
            """
            ### 📋 上传到 NetSuite 的步骤

            1. NetSuite → **Setup → Import/Export → Import CSV Records**
            2. **Import Type**: `Items` · **Record Type**: `Inventory Item` · **Import**: `Update`
            3. 上传刚下载的 CSV
            4. **Field Mapping**：CSV `Internal ID` → NetSuite `Internal ID` · CSV `Standard Cost` → NetSuite `Standard Cost`
            5. 第一次配完保存为 `Cost_Sync_Update` 映射，下次秒上传
            """
        )
    else:
        st.error(t("⚠️ 输出文件丢失，重来一次"))

    if st.button(t("🔄 再做一次"), type="primary"):
        _reset()
        st.rerun()
