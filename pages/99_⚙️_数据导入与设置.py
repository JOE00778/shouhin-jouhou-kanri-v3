"""数据导入与设置页。

支持两类输入：
- 6 种 NetSuite 标准导出 .xls（自动识别文件名）
- 兜底 CSV（item_master_cleaned.csv 旧路径）

设计：用户可以一次拖多个文件，工具自动识别每个文件类型并入对应表。
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import streamlit as st
from shared.i18n import t

from data_warehouse.ingest.items import LocalItemMasterIngestor
from data_warehouse.ingest.xls_ingest import (
    INGESTOR_REGISTRY,
    detect_ingestor,
)
from shared.db import INPUTS_DIR, get_connection

st.set_page_config(page_title=t("数据导入与设置"), page_icon="⚙️", layout="wide")
conn = get_connection()

st.title(t("⚙️ 数据导入与设置"))
st.caption("把 NetSuite 标准导出 .xls 拖到这里，工具自动识别类型并入库")

tab_import, tab_status, tab_logs, tab_legacy = st.tabs(
    [
        "📤 一键导入（NetSuite .xls）",
        "📊 数据现状",
        "📜 导入历史",
        "🛠 旧 CSV（item_master）",
    ]
)


# ============================================================
# Tab 1：一键多文件导入
# ============================================================
with tab_import:
    st.subheader("拖拽 6 类 NetSuite 导出文件")
    with st.expander("📖 支持的文件类型", expanded=False):
        st.markdown(
            """
            | 文件名包含 | 入到哪张表 | 内容 |
            |---|---|---|
            | `在庫数残数` 或 `通常在庫` | `inventory_snapshot` | 多仓库库存快照（含 std/avg cost） |
            | `在庫回転率` | `inventory_turnover` | 库存周转率 + 平均手持日数 |
            | `ASEAN` + `前日` | `sales_line` (asean_daily) | ASEAN 日销售 |
            | `ASEAN` + `店舗別` | `sales_line` (asean_monthly) | ASEAN 月销售（按店铺） |
            | `輸出` + `アイテム別` | `sales_line` (export_item) | 出口销售（SKU 维度，带 rank） |
            | `輸出` + `店舗別` | `sales_line` (export_store) | 出口销售（店铺×SKU 维度） |

            一次可以拖多个文件，挨个识别 + 导入。识别不到的文件会显示警告但不影响其他文件。
            """
        )

    uploaded_files = st.file_uploader(
        "拖拽或选择 .xls 文件（可多选）",
        type=["xls", "xlsx"],
        accept_multiple_files=True,
        key="bulk_xls_uploader",
    )

    if uploaded_files:
        # 预扫描：识别每个文件
        st.write("### 文件识别结果")
        plan: list[tuple[str, str, object]] = []  # (filename, ingestor_key, file_obj)
        for f in uploaded_files:
            key = detect_ingestor(f.name)
            if key:
                st.success(f"✅ `{f.name}` → **{key}** ingestor ({f.size:,} bytes)")
                plan.append((f.name, key, f))
            else:
                st.error(f"❌ `{f.name}` 文件名无法识别类型，跳过")

        if plan and st.button(f"🚀 开始导入 {len(plan)} 个文件", type="primary"):
            INPUTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            results = []
            progress = st.progress(0)
            status = st.empty()

            for i, (name, key, f) in enumerate(plan):
                status.write(f"⏳ 处理中：{name}")
                # 落盘到 inputs/ 留存（有时间戳前缀避免覆盖）
                save_path = INPUTS_DIR / f"{ts}_{name}"
                save_path.write_bytes(f.getvalue())

                try:
                    ingestor_fn = INGESTOR_REGISTRY[key]
                    result = ingestor_fn(save_path, conn, source_name=name)
                    results.append((name, key, result, None))
                except Exception as e:
                    results.append((name, key, None, str(e)))

                progress.progress((i + 1) / len(plan))

            status.write("✅ 全部处理完成")

            # 汇总结果
            st.write("### 导入汇总")
            for name, key, result, err in results:
                if err:
                    st.error(f"❌ `{name}` ({key}): {err}")
                else:
                    period = ""
                    if result.get("period_start"):
                        period = f" · 期间 {result['period_start']} ~ {result['period_end']}"
                    msg = (
                        f"✅ `{name}` ({key}) — "
                        f"总 {result['total']:,} · "
                        f"入库 {result['inserted']:,} · "
                        f"错误 {result['errors']:,}{period}"
                    )
                    st.success(msg)


# ============================================================
# Tab 2：数据现状
# ============================================================
with tab_status:
    st.subheader("各表当前数据量")

    def _count(t: str) -> int:
        try:
            return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        except Exception:
            return 0

    cols = st.columns(4)
    cols[0].metric("商品（item）", f"{_count('item'):,}")
    cols[1].metric("库存快照", f"{_count('inventory_snapshot'):,}")
    cols[2].metric("销售明细", f"{_count('sales_line'):,}")
    cols[3].metric("库存周转", f"{_count('inventory_turnover'):,}")

    st.divider()

    # 按 source 拆分销售
    st.write("**销售数据按来源拆分：**")
    rows = conn.execute(
        """
        SELECT source, COUNT(*) AS rows, MIN(period_start) AS p_from, MAX(period_end) AS p_to
        FROM sales_line GROUP BY source
        """
    ).fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows], use_container_width=True, hide_index=True)
    else:
        st.info("还没有销售数据。")

    st.write("**库存数据按 location 拆分：**")
    rows = conn.execute(
        """
        SELECT location, COUNT(*) AS rows,
               SUM(qty_on_hand) AS total_qty,
               COUNT(DISTINCT internal_id) AS unique_skus
        FROM inventory_snapshot GROUP BY location ORDER BY rows DESC
        """
    ).fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows], use_container_width=True, hide_index=True)
    else:
        st.info("还没有库存数据。")


# ============================================================
# Tab 3：导入历史
# ============================================================
with tab_logs:
    st.subheader("最近导入记录")
    runs = conn.execute(
        """
        SELECT run_id, ingestor, source_file, total_rows, inserted, errors, run_at
        FROM _ingest_runs ORDER BY run_id DESC LIMIT 50
        """
    ).fetchall()
    if not runs:
        st.info("还没有任何导入记录。")
    else:
        st.dataframe([dict(r) for r in runs], use_container_width=True, hide_index=True)
        run_ids = [r["run_id"] for r in runs]
        sel = st.selectbox(
            "查看错误明细：",
            run_ids,
            format_func=lambda i: f"#{i} · "
            + next(r["ingestor"] for r in runs if r["run_id"] == i)
            + " · "
            + next(r["source_file"] for r in runs if r["run_id"] == i),
        )
        if sel:
            errs = conn.execute(
                "SELECT row_number, error_message, raw_row FROM _ingest_errors WHERE run_id=? ORDER BY row_number",
                (sel,),
            ).fetchall()
            if errs:
                st.warning(f"⚠️ {len(errs)} 个失败行：")
                st.dataframe([dict(r) for r in errs], use_container_width=True, hide_index=True)
            else:
                st.success("✅ 这次导入无失败行。")


# ============================================================
# Tab 4：兼容旧 CSV
# ============================================================
with tab_legacy:
    st.subheader("旧版 item_master CSV 导入")
    st.caption("如果还需要导入 `/Users/joe/CC/item_master_cleaned.csv` 风格的本地表")

    item_count = conn.execute("SELECT COUNT(*) AS c FROM item").fetchone()["c"]
    st.metric("当前 item 表行数", f"{item_count:,}")

    uploaded = st.file_uploader("旧 item_master CSV", type=["csv"], key="legacy_item_uploader")
    if uploaded and st.button("导入"):
        try:
            summary = LocalItemMasterIngestor().run(
                io.BytesIO(uploaded.getvalue()), conn, source_name=uploaded.name
            )
            st.success(
                f"✅ 总 {summary['total_rows']:,} · "
                f"入 {summary['inserted']:,} · "
                f"错误 {summary['errors']:,}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"❌ {e}")
            st.exception(e)
