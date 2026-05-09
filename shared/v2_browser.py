"""v2 数据快查组件 · admin only · 嵌入式 expander，不影响 page 主逻辑。

用法（page 顶部一行）：
    from shared.v2_browser import render_v2_quickview
    render_v2_quickview(conn, key_prefix="page04_")

功能：
- 按品牌（maker）过滤 item_v2 → 显示 SKU 列表 + 关联 shop_sales 汇总
- 按 jan 直接查单个 SKU → item_v2 详情 + 销售/库存/进货记录
- 完全独立于现有 page 业务逻辑，仅作为 admin 数据探索工具
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from shared.auth import is_admin


def _table_exists(conn, name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def render_v2_quickview(conn: Any, *, key_prefix: str = "v2qv_") -> None:
    """嵌入 expander 展示 v2 数据快查。仅 admin 可见。"""
    if not is_admin():
        return
    if not _table_exists(conn, "item_v2"):
        # v2 表还没建（init 失败 / Boss 还没重启容器）— 静默跳过
        return

    with st.expander("🧬 v2 数据快查（admin · item_v2 + shop_sales）", expanded=False):
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM item_v2").fetchone()["c"]
        except Exception:
            total = 0

        if total == 0:
            st.info("v2 还没数据。去 page 99 → Tab 6「🧬 v2 数据迁移」点「🚀 开始全套 ETL」。")
            return

        st.caption(f"item_v2 当前 {total:,} 个 JAN")

        # 三个子查询 Tab
        tab_brand, tab_jan, tab_overview = st.tabs(
            ["按品牌查", "按 JAN 查", "整体概览"]
        )

        # ─ Tab 1: 按 maker 过滤 ─
        with tab_brand:
            try:
                makers = conn.execute(
                    "SELECT maker, COUNT(*) AS n FROM item_v2 "
                    "WHERE maker IS NOT NULL AND maker != '' "
                    "GROUP BY maker ORDER BY n DESC LIMIT 100"
                ).fetchall()
                maker_options = [f"{r['maker']} ({r['n']})" for r in makers]
                if not maker_options:
                    st.info("item_v2.maker 为空。先跑 ETL。")
                else:
                    sel = st.selectbox(
                        "品牌",
                        maker_options,
                        key=f"{key_prefix}maker_sel",
                    )
                    chosen_maker = sel.rsplit(" (", 1)[0]
                    rows = conn.execute(
                        "SELECT jan, item_code, display_name, rank, "
                        "handling_status, std_cost, avg_cost, on_hand_total "
                        "FROM item_v2 WHERE maker = ? "
                        "ORDER BY display_name LIMIT 200",
                        (chosen_maker,),
                    ).fetchall()
                    st.dataframe(
                        [dict(r) for r in rows],
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(f"{chosen_maker} · 显示前 200 个 SKU")
            except Exception as e:
                st.warning(f"按品牌查询失败：{e}")

        # ─ Tab 2: 按 jan 直查 ─
        with tab_jan:
            jan = st.text_input(
                "JAN（13 位）",
                key=f"{key_prefix}jan_input",
                placeholder="4901085196533",
            )
            if jan and jan.strip():
                jan = jan.strip()
                try:
                    item = conn.execute(
                        "SELECT * FROM item_v2 WHERE jan = ?",
                        (jan,),
                    ).fetchone()
                    if not item:
                        st.warning(f"JAN {jan} 在 item_v2 找不到")
                    else:
                        st.json({k: item[k] for k in item.keys() if item[k] is not None})

                        # 销售
                        if _table_exists(conn, "shop_sales"):
                            sales = conn.execute(
                                "SELECT shop_id, period_start, period_end, qty_sold, "
                                "revenue, gross_profit, source FROM shop_sales "
                                "WHERE jan = ? ORDER BY period_start DESC LIMIT 50",
                                (jan,),
                            ).fetchall()
                            if sales:
                                st.markdown("**销售（shop_sales 最近 50 行）**")
                                st.dataframe(
                                    [dict(r) for r in sales],
                                    use_container_width=True, hide_index=True,
                                )

                        # 库存
                        if _table_exists(conn, "item_inventory_snapshot_v2"):
                            inv = conn.execute(
                                "SELECT location, bin_number, qty_on_hand, std_cost, avg_cost "
                                "FROM item_inventory_snapshot_v2 WHERE jan = ?",
                                (jan,),
                            ).fetchall()
                            if inv:
                                st.markdown("**库存（item_inventory_snapshot_v2）**")
                                st.dataframe(
                                    [dict(r) for r in inv],
                                    use_container_width=True, hide_index=True,
                                )

                        # 进货
                        if _table_exists(conn, "item_purchase_history"):
                            purchase = conn.execute(
                                "SELECT supplier, qty, unit_cost, ordered_at, source "
                                "FROM item_purchase_history WHERE jan = ? "
                                "ORDER BY ordered_at DESC LIMIT 20",
                                (jan,),
                            ).fetchall()
                            if purchase:
                                st.markdown("**进货（item_purchase_history 最近 20）**")
                                st.dataframe(
                                    [dict(r) for r in purchase],
                                    use_container_width=True, hide_index=True,
                                )
                except Exception as e:
                    st.warning(f"查询失败：{e}")

        # ─ Tab 3: 整体概览 ─
        with tab_overview:
            try:
                cols = st.columns(4)
                cols[0].metric("item_v2", f"{total:,}")
                for tbl, lbl, idx in [
                    ("shop", "店铺", 1),
                    ("shop_sales", "店铺销售", 2),
                    ("item_inventory_snapshot_v2", "库存快照", 3),
                ]:
                    if _table_exists(conn, tbl):
                        try:
                            c = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
                            cols[idx].metric(lbl, f"{c:,}")
                        except Exception:
                            pass

                # 按 platform 分布
                if _table_exists(conn, "shop"):
                    by_p = conn.execute(
                        "SELECT platform, market_id, COUNT(*) AS n "
                        "FROM shop GROUP BY platform, market_id ORDER BY n DESC"
                    ).fetchall()
                    if by_p:
                        st.markdown("**店铺分布（platform × market）**")
                        st.dataframe(
                            [dict(r) for r in by_p],
                            use_container_width=True, hide_index=True,
                        )

                # 最近 ETL 历史
                if _table_exists(conn, "_v2_migration_runs"):
                    runs = conn.execute(
                        "SELECT step, rows_read, rows_written, errors, ran_at "
                        "FROM _v2_migration_runs ORDER BY id DESC LIMIT 10"
                    ).fetchall()
                    if runs:
                        st.markdown("**最近 ETL 历史**")
                        st.dataframe(
                            [dict(r) for r in runs],
                            use_container_width=True, hide_index=True,
                        )
            except Exception as e:
                st.warning(f"概览查询失败：{e}")
