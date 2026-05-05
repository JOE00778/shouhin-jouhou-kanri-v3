"""跨页面共享的过滤常量与辅助。

设计原则：所有商品类页面默认只显示「輸出」部门商品，
通过 SQL JOIN 限制（INNER JOIN inventory_snapshot WHERE department LIKE '%輸出%'）。
"""
from __future__ import annotations

import sqlite3

# 输出部门关键字（含「輸出事業」「輸出事業 : 輸出（中国）」等所有变体）
EXPORT_DEPT_KEYWORD = "輸出"

# 仅保留的库存仓库（其他仓库数据在 ingest 时丢弃）
ALLOWED_INVENTORY_LOCATIONS = {"JD-物流-千葉", "弁天倉庫"}


def export_item_codes(conn: sqlite3.Connection) -> set[str]:
    """返回属于「輸出」部门的全部 item_code（基于最新 inventory_snapshot）。

    用于 inventory_turnover、sales_line 等没有 department 字段的表做软过滤。
    """
    rows = conn.execute(
        """
        SELECT DISTINCT item_code
        FROM inventory_snapshot
        WHERE department LIKE ?
        """,
        (f"%{EXPORT_DEPT_KEYWORD}%",),
    ).fetchall()
    return {r["item_code"] for r in rows}


def export_internal_ids(conn: sqlite3.Connection) -> set[str]:
    """返回属于「輸出」部门的全部 internal_id。"""
    rows = conn.execute(
        """
        SELECT DISTINCT internal_id
        FROM inventory_snapshot
        WHERE department LIKE ?
        """,
        (f"%{EXPORT_DEPT_KEYWORD}%",),
    ).fetchall()
    return {r["internal_id"] for r in rows}
