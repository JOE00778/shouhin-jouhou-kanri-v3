"""_PostgresAdapter SQL 改写测试（不需要真实 Postgres）。

验证：
- INSERT OR REPLACE INTO X (cols) VALUES (...) → ON CONFLICT (pk) DO UPDATE SET ...=EXCLUDED.*
- INSERT OR IGNORE INTO X (cols) VALUES (...) → ON CONFLICT DO NOTHING
- ? 占位符 → %s
- 普通 SELECT / INSERT 不动
- 未登记的表抛 RuntimeError
"""
from __future__ import annotations

import pytest

from shared.db import _PostgresAdapter


def test_insert_or_replace_basic():
    sql = "INSERT OR REPLACE INTO shopee_payouts (payout_id, total_payout) VALUES (?, ?)"
    out = _PostgresAdapter._adapt_sql(sql)
    assert "INSERT INTO shopee_payouts" in out
    assert "ON CONFLICT (payout_id) DO UPDATE SET" in out
    assert "total_payout=EXCLUDED.total_payout" in out
    assert "%s" in out and "?" not in out


def test_insert_or_replace_composite_pk():
    sql = (
        "INSERT OR REPLACE INTO inventory_snapshot "
        "(internal_id, location, bin_number, snapshot_at, qty_on_hand) "
        "VALUES (:a, :b, :c, :d, :e)"
    )
    out = _PostgresAdapter._adapt_sql(sql)
    assert "ON CONFLICT (internal_id, location, bin_number, snapshot_at)" in out
    assert "qty_on_hand=EXCLUDED.qty_on_hand" in out
    # PK 列不应出现在 SET 子句
    assert "internal_id=EXCLUDED.internal_id" not in out
    assert "snapshot_at=EXCLUDED.snapshot_at" not in out


def test_insert_or_replace_multiline():
    sql = """
        INSERT OR REPLACE INTO inventory_turnover (
            item_code, description, cost,
            period_start, period_end
        ) VALUES (
            :item_code, :description, :cost,
            :period_start, :period_end
        )
    """
    out = _PostgresAdapter._adapt_sql(sql)
    assert "INSERT INTO inventory_turnover" in out
    assert "ON CONFLICT (item_code, period_start, period_end)" in out
    assert "description=EXCLUDED.description" in out
    assert "cost=EXCLUDED.cost" in out


def test_insert_or_ignore():
    sql = "INSERT OR IGNORE INTO _schema_version (version, applied_at) VALUES (?, ?)"
    out = _PostgresAdapter._adapt_sql(sql)
    assert "INSERT INTO _schema_version" in out
    assert "ON CONFLICT DO NOTHING" in out
    assert "OR IGNORE" not in out


def test_insert_or_ignore_store_monthly():
    sql = """
        INSERT OR IGNORE INTO store_monthly (
            year_month, market, store_id, revenue
        ) VALUES (?, ?, ?, ?)
    """
    out = _PostgresAdapter._adapt_sql(sql)
    assert "INSERT INTO store_monthly" in out
    assert "ON CONFLICT DO NOTHING" in out


def test_unknown_table_raises():
    sql = "INSERT OR REPLACE INTO unknown_table (a, b) VALUES (?, ?)"
    with pytest.raises(RuntimeError, match="未登记表"):
        _PostgresAdapter._adapt_sql(sql)


def test_plain_select_unchanged():
    sql = "SELECT * FROM item WHERE jan = ?"
    out = _PostgresAdapter._adapt_sql(sql)
    assert out == "SELECT * FROM item WHERE jan = %s"


def test_plain_insert_unchanged():
    sql = "INSERT INTO _ingest_runs (ingestor, source_file) VALUES (?, ?)"
    out = _PostgresAdapter._adapt_sql(sql)
    assert "ON CONFLICT" not in out
    assert "INSERT INTO _ingest_runs" in out
    assert "%s" in out


def test_all_known_ingest_tables_register():
    """确保所有 ingest 链路上会写入的表都登记了 conflict 列。"""
    expected_tables = {
        "shopee_payouts", "inventory_snapshot", "inventory_turnover",
        "shopee_orders_raw", "shopee_income_lines", "shopee_orders",
        "supplier_cost", "supply_cycle", "supplier_jan_list",
        "item", "item_master", "item_master_netsuite", "store_monthly",
        "nst_turnover", "nst_store_sales", "nst_inventory_snapshot",
        "nst_item_summary",
        "operation_advice_monthly", "stock_sales_ratio_monthly",
        "cross_ratio_monthly", "health_grade_monthly", "rank_history",
        "_schema_version",
    }
    registered = set(_PostgresAdapter._UPSERT_CONFLICT.keys())
    missing = expected_tables - registered
    assert not missing, f"未登记的表：{missing}"
