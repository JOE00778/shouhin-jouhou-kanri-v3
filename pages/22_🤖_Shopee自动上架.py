"""模块 #22 Shopee 自动上架（N8N 自动化版）

业务定位：
    page 21 出 XLSX 是「半自动」（产物供 Boss 手工上传到 Shopee 后台）
    本页是「全自动」：上传 XLSX → 调 N8N webhook → N8N 调 Shopee API 创建商品
    → 飞书群通知结果。中间过程 CMS 用 automation_runs 表跟踪状态。

数据流：
    page 22 上传 mass-upload XLSX
        ↓ shared/n8n_client.trigger_workflow()
    automation_runs 落 pending → POST n8n.smikie-cms.cc/webhook/shopee-mass-upload
        ↓ N8N workflow（Webhook → Shopee API → 飞书）
    自动回写 automation_runs.status = completed/failed
        ↓
    page 22 轮询 automation_runs 显示进度

依赖：
    - shared/n8n_client.py · 触发 + 状态查询
    - shared/lark_notify.py · 失败兜底通知
    - schema 表 automation_runs
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime

import streamlit as st

from shared.auth import require_admin
from shared.db import get_connection
from shared.i18n import lang_selector, t
from shared.n8n_client import (
    get_run_status,
    list_recent_runs,
    trigger_workflow,
)

st.set_page_config(page_title="Shopee 自动上架", page_icon="🤖", layout="wide")
require_admin()
lang_selector()

conn = get_connection()

st.title("🤖 Shopee 自动上架（N8N 链路）")
st.caption(
    "上传 page 21 导出的 mass-upload XLSX → 自动调 N8N → "
    "Shopee Open API 创建商品 → 飞书群通知"
)

tab_run, tab_history = st.tabs(["🚀 触发新上架", "📜 历史运行"])


# ============================================================
# Tab 1：触发新上架
# ============================================================
with tab_run:
    st.subheader("Step 1 · 选目标市场")
    market = st.selectbox(
        "Shopee 站点",
        ["TW", "SG", "MY", "PH", "TH", "VN", "ID"],
        help="N8N 会用市场对应的 Shopee Partner API 鉴权",
    )

    st.subheader("Step 2 · 上传 mass-upload XLSX")
    st.caption(
        "通常是 page 21「方案 A · Shopee 直传」生成的 5 类目分文件 zip 解压后的 .xlsx；"
        "可一次拖多个，N8N 会按顺序处理"
    )
    files = st.file_uploader(
        "拖拽 / 选择 XLSX 文件（可多选）",
        type=["xlsx"],
        accept_multiple_files=True,
    )

    st.subheader("Step 3 · 触发")

    user_email = st.session_state.get("user_email", "admin")  # 实际登录场景按 auth 体系拿
    can_run = bool(files)
    if not can_run:
        st.info("请先上传至少一个 XLSX")

    if st.button(
        "🚀 触发 N8N 自动上架",
        type="primary",
        disabled=not can_run,
        use_container_width=True,
    ):
        # 把文件读入 → base64 进 payload（小于 10 MB）
        encoded = []
        total_size = 0
        for f in files:
            data = f.getvalue()
            total_size += len(data)
            encoded.append({
                "name": f.name,
                "size": len(data),
                "data_b64": base64.b64encode(data).decode("ascii"),
            })

        if total_size > 10 * 1024 * 1024:
            st.error(
                f"❌ 文件总大小 {total_size / 1024 / 1024:.1f} MB 超过 10 MB；"
                "请拆批触发或改为放共享目录的方式"
            )
            st.stop()

        payload = {
            "market": market,
            "files": encoded,
            "triggered_at": datetime.utcnow().isoformat() + "Z",
        }

        try:
            run_id = trigger_workflow(
                module="shopee_mass_upload",
                webhook_path="shopee-mass-upload",
                payload=payload,
                conn=conn,
                triggered_by=user_email,
            )
            st.session_state["last_run_id"] = run_id
            st.success(f"✅ 已触发，run_id = `{run_id}`")
        except Exception as e:
            st.error(f"❌ 触发失败：{e}")
            st.exception(e)

    # ---- 进度面板 ----
    last_id = st.session_state.get("last_run_id")
    if last_id:
        st.divider()
        st.subheader(f"实时进度 · `{last_id}`")
        placeholder = st.empty()
        # Streamlit 单次 rerun 内做有限轮询；超时让用户手动刷新
        for _ in range(20):  # 20 × 1.5s = 30s
            row = get_run_status(conn, last_id)
            if not row:
                placeholder.warning("查不到该 run（可能 DB 还没刷新）")
                break
            with placeholder.container():
                cols = st.columns(4)
                cols[0].metric("状态", row.get("status", "-"))
                cols[1].metric("模块", row.get("module", "-"))
                cols[2].metric("触发者", row.get("triggered_by", "-"))
                cols[3].metric("触发时间", str(row.get("triggered_at", "-"))[:19])
                if row.get("summary"):
                    st.json(row["summary"])
            if row.get("status") in ("completed", "failed"):
                break
            time.sleep(1.5)
        else:
            placeholder.info("仍在处理中，可切到「历史运行」Tab 或刷新页面继续观察")


# ============================================================
# Tab 2：历史运行
# ============================================================
with tab_history:
    st.subheader("最近 50 次自动化运行")
    runs = list_recent_runs(conn, limit=50)
    if not runs:
        st.info("还没有任何运行记录")
    else:
        rows = []
        for r in runs:
            rows.append({
                "run_id": r["run_id"][:8] + "...",
                "module": r["module"],
                "status": r["status"],
                "triggered_by": r["triggered_by"],
                "triggered_at": (r.get("triggered_at") or "")[:19],
                "completed_at": (r.get("completed_at") or "")[:19],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        # 抽样查看明细
        ids = [r["run_id"] for r in runs]
        sel = st.selectbox(
            "查看 payload + summary",
            ids,
            format_func=lambda x: f"{x[:8]}... · {next(r['module'] for r in runs if r['run_id']==x)} · "
            f"{next(r['status'] for r in runs if r['run_id']==x)}",
        )
        if sel:
            row = next(r for r in runs if r["run_id"] == sel)
            with st.expander("payload", expanded=False):
                try:
                    st.json(json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"])
                except Exception:
                    st.code(row.get("payload") or "(empty)")
            with st.expander("summary", expanded=True):
                try:
                    st.json(json.loads(row["summary"]) if isinstance(row["summary"], str) else row["summary"])
                except Exception:
                    st.code(row.get("summary") or "(empty)")
