"""每月仪表盘 KPI snapshot, 用于 home 显示「上月对比」delta + sparkline.

设计:
- `kpi_monthly_history` 表幂等建表 (兼容 SQLite + Postgres)
- 每次访问 home 时 `take_snapshot()` 自动 UPSERT 当月快照
- `get_delta()` 拿当月 vs 上月 delta (sku/stock/revenue/margin)
- `get_history(field, n)` 拿过去 n 个月某字段历史 (sparkline 用)

字段:
- year_month       (主键, YYYYMM)
- sku_total        item_v2 行数
- stock_value_jpy  item_v2 SUM(total_amount)
- month_revenue_jpy shop_sales 当月 SUM(revenue_jpy)
- gross_margin     shop_sales 当月 gross_profit / revenue (0~1)
- snapshot_at      ISO timestamp

所有 SQL 都 try/except 防挂.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from shared.db import get_connection


def _ensure_table(conn) -> None:
    """幂等建表 (兼容 SQLite + Postgres)."""
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kpi_monthly_history (
                year_month TEXT PRIMARY KEY,
                sku_total INTEGER,
                stock_value_jpy REAL,
                month_revenue_jpy REAL,
                gross_margin REAL,
                snapshot_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.commit()
        except Exception:
            pass
    except Exception:
        pass


def _scalar(conn, sql, default=None):
    """跑 single-row single-col SQL, 失败/空返回 default."""
    try:
        r = conn.execute(sql).fetchone()
        if r is None:
            return default
        try:
            v = r[0]
        except Exception:
            try:
                v = list(dict(r).values())[0]
            except Exception:
                return default
        return v if v is not None else default
    except Exception:
        return default


def take_snapshot(conn=None) -> dict:
    """对当前数据快照, UPSERT 到 kpi_monthly_history.

    返回 {year_month, sku_total, stock_value_jpy, month_revenue_jpy, gross_margin}.
    """
    conn = conn or get_connection()
    _ensure_table(conn)

    ym = datetime.now().strftime("%Y%m")
    snap_at = datetime.now().isoformat()

    sku_total = _scalar(conn, "SELECT COUNT(*) FROM item_v2", 0) or 0
    stock_value = _scalar(
        conn,
        "SELECT SUM(total_amount) FROM item_v2 WHERE total_amount IS NOT NULL",
        0.0,
    ) or 0.0
    month_rev = _scalar(
        conn,
        f"SELECT SUM(revenue_jpy) FROM shop_sales "
        f"WHERE granularity='monthly' "
        f"AND substr(REPLACE(period_start,'-',''),1,6)='{ym}'",
        0.0,
    ) or 0.0
    revenue_for_margin = _scalar(
        conn,
        f"SELECT SUM(revenue) FROM shop_sales "
        f"WHERE granularity='monthly' "
        f"AND substr(REPLACE(period_start,'-',''),1,6)='{ym}'",
        0.0,
    ) or 0.0
    profit = _scalar(
        conn,
        f"SELECT SUM(gross_profit) FROM shop_sales "
        f"WHERE granularity='monthly' "
        f"AND substr(REPLACE(period_start,'-',''),1,6)='{ym}'",
        0.0,
    ) or 0.0
    margin = (profit / revenue_for_margin) if revenue_for_margin and revenue_for_margin > 0 else None

    payload = (
        ym,
        int(sku_total),
        float(stock_value),
        float(month_rev),
        margin,
        snap_at,
    )
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kpi_monthly_history "
            "(year_month, sku_total, stock_value_jpy, month_revenue_jpy, gross_margin, snapshot_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            payload,
        )
        try:
            conn.commit()
        except Exception:
            pass
    except Exception:
        # 兼容 Postgres ON CONFLICT 路径未登记 conflict 列时, 退回 INSERT-或-UPDATE 两步
        try:
            conn.execute(
                "DELETE FROM kpi_monthly_history WHERE year_month = ?",
                (ym,),
            )
            conn.execute(
                "INSERT INTO kpi_monthly_history "
                "(year_month, sku_total, stock_value_jpy, month_revenue_jpy, gross_margin, snapshot_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                payload,
            )
            try:
                conn.commit()
            except Exception:
                pass
        except Exception:
            pass

    return {
        "year_month": ym,
        "sku_total": sku_total,
        "stock_value_jpy": stock_value,
        "month_revenue_jpy": month_rev,
        "gross_margin": margin,
    }


def _row_get(row, key, idx):
    """row 兼容 SQLite Row / psycopg2 DictRow / tuple."""
    try:
        return row[key]
    except Exception:
        try:
            return row[idx]
        except Exception:
            return None


def get_delta(conn=None) -> dict:
    """返回当前 vs 上月 delta:
    {sku_delta, stock_delta_pct, revenue_delta_pct, margin_delta_pp}.
    历史不足 2 月返回 {}.
    """
    conn = conn or get_connection()
    try:
        rs = conn.execute(
            "SELECT year_month, sku_total, stock_value_jpy, month_revenue_jpy, gross_margin "
            "FROM kpi_monthly_history ORDER BY year_month DESC LIMIT 2"
        ).fetchall()
        if len(rs) < 2:
            return {}
        cur_row, prev_row = rs[0], rs[1]
        cur = {
            "sku_total": _row_get(cur_row, "sku_total", 1),
            "stock_value_jpy": _row_get(cur_row, "stock_value_jpy", 2),
            "month_revenue_jpy": _row_get(cur_row, "month_revenue_jpy", 3),
            "gross_margin": _row_get(cur_row, "gross_margin", 4),
        }
        prev = {
            "sku_total": _row_get(prev_row, "sku_total", 1),
            "stock_value_jpy": _row_get(prev_row, "stock_value_jpy", 2),
            "month_revenue_jpy": _row_get(prev_row, "month_revenue_jpy", 3),
            "gross_margin": _row_get(prev_row, "gross_margin", 4),
        }
        out = {}
        out["sku_delta"] = (cur["sku_total"] or 0) - (prev["sku_total"] or 0)
        if prev["stock_value_jpy"]:
            out["stock_delta_pct"] = (
                ((cur["stock_value_jpy"] or 0) - prev["stock_value_jpy"])
                / prev["stock_value_jpy"]
                * 100
            )
        if prev["month_revenue_jpy"]:
            out["revenue_delta_pct"] = (
                ((cur["month_revenue_jpy"] or 0) - prev["month_revenue_jpy"])
                / prev["month_revenue_jpy"]
                * 100
            )
        if cur.get("gross_margin") is not None and prev.get("gross_margin") is not None:
            out["margin_delta_pp"] = (cur["gross_margin"] - prev["gross_margin"]) * 100
        return out
    except Exception:
        return {}


def get_history(field: str, n: int = 6, conn=None) -> pd.DataFrame:
    """拿过去 n 个月某字段历史, 返回 DataFrame [ym, v].
    field ∈ {sku_total, stock_value_jpy, month_revenue_jpy, gross_margin}.
    失败/空返回空 DataFrame.
    """
    if field not in {"sku_total", "stock_value_jpy", "month_revenue_jpy", "gross_margin"}:
        return pd.DataFrame()
    conn = conn or get_connection()
    try:
        rs = conn.execute(
            f"SELECT year_month, {field} FROM kpi_monthly_history "
            f"ORDER BY year_month DESC LIMIT {int(n)}"
        ).fetchall()
        if not rs:
            return pd.DataFrame()
        rows = []
        for r in rs:
            ym = _row_get(r, "year_month", 0)
            v = _row_get(r, field, 1)
            if v is None:
                continue
            rows.append({"ym": ym, "v": float(v)})
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        return df.sort_values("ym")
    except Exception:
        return pd.DataFrame()


__all__ = ["take_snapshot", "get_delta", "get_history"]
