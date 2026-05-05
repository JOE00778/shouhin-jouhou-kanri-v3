"""Tests for NetSuite XML ingestors."""
import pytest
import sqlite3
import tempfile
from pathlib import Path
from data_warehouse.db.migrations import init_db
from data_warehouse.ingest.xml_netsuite import (
    TurnoverIngestor,
    StoreSalesIngestor,
    InventoryIngestor,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


class TestTurnoverIngestor:
    """Tests for TurnoverIngestor."""

    def test_schema_exists(self, temp_db):
        """Test that nst_turnover table exists."""
        cursor = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nst_turnover'"
        )
        assert cursor.fetchone() is not None

    def test_parse_row_valid(self):
        """Test parsing a valid row."""
        ingestor = TurnoverIngestor()
        row = {
            "department": "輸出事業",
            "item_code": "0719812031491",
            "handling_status": "取扱中",
            "cost": "100.0",
            "avg_value": "50.5",
            "turnover_rate": "2.5",
            "avg_days_on_hand": "30.0",
        }
        result = ingestor.parse_row(row)
        assert result is not None
        assert result["item_code"] == "0719812031491"
        assert result["cost"] == 100.0
        assert result["avg_value"] == 50.5

    def test_parse_row_skip_empty_item(self):
        """Test that rows without item_code are skipped."""
        ingestor = TurnoverIngestor()
        row = {
            "department": "輸出事業",
            "item_code": "",
            "handling_status": "取扱中",
        }
        result = ingestor.parse_row(row)
        assert result is None

    def test_parse_row_handles_missing_fields(self):
        """Test that missing fields default to None."""
        ingestor = TurnoverIngestor()
        row = {
            "item_code": "test",
        }
        result = ingestor.parse_row(row)
        assert result is not None
        assert result["item_code"] == "test"
        assert result["department"] == ""
        assert result["cost"] is None


class TestStoreSalesIngestor:
    """Tests for StoreSalesIngestor."""

    def test_schema_exists(self, temp_db):
        """Test that nst_store_sales table exists."""
        cursor = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nst_store_sales'"
        )
        assert cursor.fetchone() is not None

    def test_parse_row_valid(self):
        """Test parsing a valid row."""
        ingestor = StoreSalesIngestor()
        row = {
            "fb_store": "店舗A",
            "item_code": "001",
            "upc": "123456",
            "handling_status": "取扱中",
            "display_name": "商品A",
            "qty_sold": "10.0",
            "revenue": "1000.0",
            "defined_cost": "500.0",
            "gross_profit": "500.0",
            "gross_margin": "50.0",
            "rank": "A",
        }
        result = ingestor.parse_row(row)
        assert result is not None
        assert result["fb_store"] == "店舗A"
        assert result["qty_sold"] == 10.0
        assert result["revenue"] == 1000.0

    def test_parse_row_skip_incomplete(self):
        """Test that rows without fb_store or item_code are skipped."""
        ingestor = StoreSalesIngestor()
        row = {
            "fb_store": "",
            "item_code": "001",
        }
        result = ingestor.parse_row(row)
        assert result is None

    def test_parse_row_invalid_float(self):
        """Test that invalid floats are converted to None."""
        ingestor = StoreSalesIngestor()
        row = {
            "fb_store": "店舗",
            "item_code": "001",
            "qty_sold": "invalid",
        }
        result = ingestor.parse_row(row)
        assert result is not None
        assert result["qty_sold"] is None


class TestInventoryIngestor:
    """Tests for InventoryIngestor."""

    def test_schema_exists(self, temp_db):
        """Test that nst_inventory_snapshot table exists."""
        cursor = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nst_inventory_snapshot'"
        )
        assert cursor.fetchone() is not None

    def test_parse_row_valid(self):
        """Test parsing a valid row."""
        ingestor = InventoryIngestor()
        row = {
            "internal_id": "51206",
            "item_code": "01-0641-134",
            "upc": "7611160093868",
            "display_name": "WENGER",
            "status": "通常在庫",
            "bin_number": "- None -",
            "location": "本社サンプル倉庫",
            "handling_status": "取扱中止",
            "qty_on_hand": "1.0",
            "qty_committed": "0.0",
            "qty_backorder": "0.0",
            "std_cost": "20300.0",
            "total_amount": "20300.0",
            "avg_cost": "20300.0",
            "owner": "010 祁王傑",
            "department": "輸出事業",
        }
        result = ingestor.parse_row(row)
        assert result is not None
        assert result["internal_id"] == "51206"
        assert result["department"] == "輸出事業"
        assert result["qty_on_hand"] == 1.0

    def test_parse_row_skip_no_internal_id(self):
        """Test that rows without internal_id are skipped."""
        ingestor = InventoryIngestor()
        row = {
            "internal_id": "",
            "item_code": "001",
        }
        result = ingestor.parse_row(row)
        assert result is None

    def test_dept_filter_active(self):
        """Test that department filter is set."""
        ingestor = InventoryIngestor()
        assert ingestor.DEPT_FILTER == "輸出事業"


class TestIntegration:
    """Integration tests with actual files."""

    def test_turnover_file_exists(self):
        """Test that turnover file can be found."""
        path = Path("【ASEAN】在庫回転率699.xls")
        assert path.exists(), f"File {path} not found"

    def test_store_sales_file_exists(self):
        """Test that store sales file can be found."""
        path = Path("【ASEAN】店舗別売上　集計専用-694.xls")
        assert path.exists(), f"File {path} not found"

    def test_inventory_file_exists(self):
        """Test that inventory file can be found."""
        path = Path("輸出通常在庫数残数検索結果480.xls")
        assert path.exists(), f"File {path} not found"

    def test_turnover_parse_rows(self):
        """Test parsing actual turnover file."""
        ingestor = TurnoverIngestor()
        rows = ingestor.parse_rows("【ASEAN】在庫回転率699.xls")
        assert len(rows) > 0
        # Verify structure
        assert all("item_code" in r for r in rows)

    def test_store_sales_parse_rows(self):
        """Test parsing actual store sales file."""
        ingestor = StoreSalesIngestor()
        rows = ingestor.parse_rows("【ASEAN】店舗別売上　集計専用-694.xls")
        assert len(rows) > 0
        assert all("item_code" in r for r in rows)

    def test_inventory_parse_rows_with_dept_filter(self):
        """Test parsing actual inventory file with department filter."""
        ingestor = InventoryIngestor()
        rows = ingestor.parse_rows("輸出通常在庫数残数検索結果480.xls")
        assert len(rows) == 6805, f"Expected 6805 filtered rows, got {len(rows)}"
        # Verify all rows have '輸出事業' prefix in department
        for row in rows:
            dept = row.get("department", "")
            assert dept.startswith("輸出事業"), f"Unexpected dept: {dept}"

    def test_end_to_end_inventory(self, temp_db):
        """End-to-end test: parse and ingest inventory."""
        ingestor = InventoryIngestor()
        result = ingestor.run(
            "輸出通常在庫数残数検索結果480.xls",
            temp_db,
            source_name="test_inventory.xls",
        )
        # Parse result contains 6805 rows (before UNIQUE constraint dedup)
        assert result["total_rows"] == 6805
        assert result["inserted"] > 0
        assert result["errors"] == 0

        # Verify count in database (6800 after UNIQUE constraint dedup)
        cursor = temp_db.execute(
            "SELECT COUNT(*) FROM nst_inventory_snapshot"
        )
        count = cursor.fetchone()[0]
        assert count == 6800, f"Expected 6800 rows in DB after dedup, got {count}"

        # Verify department filter was applied
        cursor = temp_db.execute(
            "SELECT COUNT(*) FROM nst_inventory_snapshot WHERE department NOT LIKE '輸出事業%'"
        )
        non_yuushutu = cursor.fetchone()[0]
        assert (
            non_yuushutu == 0
        ), f"Found {non_yuushutu} rows that should be filtered out"
