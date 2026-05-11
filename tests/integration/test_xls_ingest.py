"""测试 6 个 xls ingestor 都能正确入库 Boss 的真实数据。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data_warehouse.db.migrations import init_db
from data_warehouse.ingest.xls_ingest import (
    detect_ingestor,
    ingest_inventory_snapshot,
    ingest_inventory_turnover,
    ingest_sales_asean_daily,
    ingest_sales_asean_monthly,
    ingest_sales_export_item,
    ingest_sales_export_store,
)

DATA_DIR = Path("/Users/joe/CC/data")
INVENTORY_FILE = DATA_DIR / "FB全倉庫通常在庫数残数検索結果362.xls"
SALES_MONTHLY_FILE = DATA_DIR / "【ASEAN】店舗別売上　集計専用705.xls"
SALES_DAILY_FILE = DATA_DIR / "【ASEAN】店舗別売上（前日）-646.xls"
EXPORT_ITEM_FILE = DATA_DIR / "【輸出】アイテム別売上（概要）_JO-14.xls"
EXPORT_STORE_FILE = DATA_DIR / "【輸出】店舗別売上_JO-800.xls"
TURNOVER_FILE = DATA_DIR / "在庫回転率-959.xls"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return init_db(tmp_path / "test.db")


def _skip_if_missing(p: Path):
    if not p.exists():
        pytest.skip(f"missing: {p}")


# ============================================================
# Inventory snapshot
# ============================================================
class TestInventoryIngest:
    def test_imports_thousands(self, conn):
        _skip_if_missing(INVENTORY_FILE)
        r = ingest_inventory_snapshot(INVENTORY_FILE, conn)
        assert r["errors"] == 0
        assert r["inserted"] > 5000  # 只有 JD + 弁天，约 6,500
        count = conn.execute("SELECT COUNT(*) AS c FROM inventory_snapshot").fetchone()["c"]
        assert count == r["inserted"]

    def test_only_jd_and_benten_warehouses(self, conn):
        """Boss 规定：只保留 JD-物流-千葉 + 弁天倉庫，其他仓库丢弃。"""
        _skip_if_missing(INVENTORY_FILE)
        ingest_inventory_snapshot(INVENTORY_FILE, conn)
        locations = {r["location"] for r in conn.execute(
            "SELECT DISTINCT location FROM inventory_snapshot"
        ).fetchall()}
        assert locations == {"JD-物流-千葉", "弁天倉庫"}

    def test_avg_and_std_cost_populated(self, conn):
        _skip_if_missing(INVENTORY_FILE)
        ingest_inventory_snapshot(INVENTORY_FILE, conn)
        # 至少绝大多数行 std_cost 和 avg_cost 都有值
        with_costs = conn.execute(
            "SELECT COUNT(*) AS c FROM inventory_snapshot WHERE std_cost IS NOT NULL AND avg_cost IS NOT NULL"
        ).fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) AS c FROM inventory_snapshot").fetchone()["c"]
        assert with_costs / total > 0.5  # 至少一半行有完整成本数据

    def test_idempotent_on_same_snapshot_at(self, conn):
        _skip_if_missing(INVENTORY_FILE)
        ingest_inventory_snapshot(INVENTORY_FILE, conn)
        before = conn.execute("SELECT COUNT(*) AS c FROM inventory_snapshot").fetchone()["c"]
        ingest_inventory_snapshot(INVENTORY_FILE, conn)
        after = conn.execute("SELECT COUNT(*) AS c FROM inventory_snapshot").fetchone()["c"]
        assert before == after  # 同 snapshot_at + internal_id + location 唯一


# ============================================================
# Sales line（4 类）
# ============================================================
class TestSalesIngest:
    def test_asean_monthly(self, conn):
        _skip_if_missing(SALES_MONTHLY_FILE)
        r = ingest_sales_asean_monthly(SALES_MONTHLY_FILE, conn)
        assert r["errors"] == 0
        assert r["inserted"] > 4000
        # period 解析正确
        assert r["period_start"].startswith("2026-")
        # 全部行 source = asean_monthly
        sources = conn.execute(
            "SELECT DISTINCT source FROM sales_line"
        ).fetchall()
        assert [s["source"] for s in sources] == ["asean_monthly"]

    def test_asean_daily(self, conn):
        _skip_if_missing(SALES_DAILY_FILE)
        r = ingest_sales_asean_daily(SALES_DAILY_FILE, conn)
        assert r["errors"] == 0
        assert r["inserted"] > 100

    def test_asean_daily_extracts_store_from_groups(self, conn):
        """asean_daily 是 NetSuite 分组报表（店铺标题行 + SKU 明细行）。
        验证我们能从分组结构里正确提取 store。"""
        _skip_if_missing(SALES_DAILY_FILE)
        ingest_sales_asean_daily(SALES_DAILY_FILE, conn)
        # 应该有多个不同的店铺
        stores = conn.execute(
            "SELECT DISTINCT store FROM sales_line WHERE source='asean_daily' AND store IS NOT NULL"
        ).fetchall()
        assert len(stores) >= 5
        # 店铺组标题行（如 'Shopee PH'）不应作为 SKU 入库
        leak = conn.execute(
            """
            SELECT COUNT(*) AS c FROM sales_line
            WHERE source='asean_daily' AND item_code LIKE 'Shopee%'
            """
        ).fetchone()["c"]
        assert leak == 0

    def test_export_item_has_rank_and_jan_from_item_column(self, conn):
        _skip_if_missing(EXPORT_ITEM_FILE)
        r = ingest_sales_export_item(EXPORT_ITEM_FILE, conn)
        assert r["errors"] == 0
        assert r["inserted"] > 1000  # この報表は JAN を「アイテム」列に持つ → has_upc=False で取れる
        # 商品ランク列が入っていること
        has_rank = conn.execute(
            "SELECT COUNT(*) AS c FROM shop_sales WHERE source='export_item' AND rank IS NOT NULL"
        ).fetchone()["c"]
        # 全行に jan が入っていること（item_code フォールバックの検証 → NULL jan は 0 件）
        rows_total = conn.execute(
            "SELECT COUNT(*) AS c FROM shop_sales WHERE source='export_item'"
        ).fetchone()["c"]
        null_jan = conn.execute(
            "SELECT COUNT(*) AS c FROM shop_sales WHERE source='export_item' AND jan IS NULL"
        ).fetchone()["c"]
        assert has_rank > 0
        assert rows_total > 1000
        assert null_jan == 0

    def test_export_store(self, conn):
        _skip_if_missing(EXPORT_STORE_FILE)
        r = ingest_sales_export_store(EXPORT_STORE_FILE, conn)
        assert r["errors"] == 0
        assert r["inserted"] > 5000
        # store + rank 都有
        has_store = conn.execute(
            "SELECT COUNT(*) AS c FROM sales_line WHERE store IS NOT NULL"
        ).fetchone()["c"]
        assert has_store > 0


# ============================================================
# Inventory turnover
# ============================================================
class TestTurnoverIngest:
    def test_imports_skus(self, conn):
        _skip_if_missing(TURNOVER_FILE)
        r = ingest_inventory_turnover(TURNOVER_FILE, conn)
        assert r["errors"] == 0
        # 在庫回転率 文件 16,592 行，含分组标题，实际 SKU 行应该不少
        assert r["inserted"] > 5000
        assert r["period_start"].startswith("2026-")

    def test_grouping_header_skipped(self, conn):
        _skip_if_missing(TURNOVER_FILE)
        ingest_inventory_turnover(TURNOVER_FILE, conn)
        # 「在庫アイテム」分组标题不应该入库
        leak = conn.execute(
            "SELECT COUNT(*) AS c FROM inventory_turnover WHERE item_code = '在庫アイテム'"
        ).fetchone()["c"]
        assert leak == 0


# ============================================================
# Auto-detect
# ============================================================
class TestDetectIngestor:
    def test_detect_inventory(self):
        assert detect_ingestor("FB全倉庫通常在庫数残数検索結果362.xls") == "inventory"

    def test_detect_turnover(self):
        assert detect_ingestor("在庫回転率-959.xls") == "turnover"

    def test_detect_asean_daily(self):
        assert detect_ingestor("【ASEAN】店舗別売上（前日）-646.xls") == "asean_daily"

    def test_detect_asean_monthly(self):
        assert detect_ingestor("【ASEAN】店舗別売上　集計専用705.xls") == "asean_monthly"

    def test_detect_export_item(self):
        assert detect_ingestor("【輸出】アイテム別売上（概要）_JO-14.xls") == "export_item"

    def test_detect_export_store(self):
        assert detect_ingestor("【輸出】店舗別売上_JO-800.xls") == "export_store"

    def test_unknown_returns_none(self):
        assert detect_ingestor("random.xls") is None
