"""Tests for excel_unified ingestor."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from data_warehouse.db.migrations import init_db
from data_warehouse.ingest.excel_unified import (
    ItemMasterIngestor,
    AllItem0405Ingestor,
    StoreMonthlyIngestor,
)


@pytest.fixture
def temp_db():
    """Create temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


def test_schema_creation(temp_db):
    """Test that all 4 new tables are created."""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}

    assert "item_master" in tables
    assert "item_master_netsuite" in tables
    assert "store_monthly" in tables
    assert "dead_inventory_monthly" in tables


def test_item_master_ingestor_parse_row():
    """Test ItemMasterIngestor.parse_row()."""
    ingestor = ItemMasterIngestor()

    # Valid row
    raw = {
        "jan": "4901234567890",
        "商品コード": "ABC123",
        "ランク": "A",
        "メーカー名": "メーカー1",
        "商品名": "テスト商品",
        "取扱区分": "通常",
        "在庫": "100",
        "発注済": "50",
        "実績原価": "1000.5",
        "最安原価": "900.0",
        "ケース入数": "20",
        "発注ロット": "10",
        "重量": "5.5",
    }
    ingestor._current_source = "test.xlsx"

    result = ingestor.parse_row(raw)

    assert result is not None
    assert result["jan"] == "4901234567890"
    assert result["item_code"] == "ABC123"
    assert result["rank"] == "A"
    assert result["maker"] == "メーカー1"
    assert result["on_hand"] == 100
    assert result["on_order"] == 50
    assert result["actual_cost"] == 1000.5
    assert result["case_qty"] == 20


def test_item_master_ingestor_empty_jan():
    """Test that rows without JAN are skipped."""
    ingestor = ItemMasterIngestor()

    raw = {
        "jan": "",
        "商品コード": "ABC123",
        "商品名": "テスト商品",
    }

    result = ingestor.parse_row(raw)
    assert result is None  # Should be skipped


def test_item_master_ingestor_missing_jan():
    """Test that rows without JAN key are skipped."""
    ingestor = ItemMasterIngestor()

    raw = {
        "商品コード": "ABC123",
        "商品名": "テスト商品",
    }

    result = ingestor.parse_row(raw)
    assert result is None  # Should be skipped


def test_item_master_upsert_with_real_data(temp_db):
    """Test upsert into item_master table."""
    ingestor = ItemMasterIngestor()

    import io
    csv_text = """jan,商品コード,ランク,メーカー名,商品名,取扱区分,在庫,発注済,実績原価,最安原価,ケース入数,発注ロット,重量
4901234567890,ABC123,A,メーカー1,テスト1,通常,100,50,1000.5,900.0,20,10,5.5
4901234567891,ABC124,B,メーカー2,テスト2,廃番,0,0,500.0,450.0,10,5,2.5
"""

    result = ingestor.run(
        io.StringIO(csv_text),
        temp_db,
        source_name="test.csv",
    )

    # Check summary
    assert result["total_rows"] == 2
    assert result["inserted"] == 2
    assert result["errors"] == 0

    # Check data in DB
    cursor = temp_db.execute(
        "SELECT COUNT(*) FROM item_master WHERE jan='4901234567890'"
    )
    count = cursor.fetchone()[0]
    assert count == 1

    # Check specific columns
    cursor = temp_db.execute(
        "SELECT item_code, on_hand FROM item_master WHERE jan='4901234567890'"
    )
    row = cursor.fetchone()
    assert row[0] == "ABC123"
    assert row[1] == 100


def test_ingest_unified_reads_excel_sheet():
    """Test that ingest_excel_unified can read 一元くん sheet from actual file."""
    from data_warehouse.ingest.excel_unified import ingest_excel_unified

    excel_path = Path.home() / "CC" / "商品信息管理" / "SKU一元管理表格.xlsx"

    if not excel_path.exists():
        pytest.skip(f"Excel file not found at {excel_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        try:
            results = ingest_excel_unified(str(excel_path), conn)

            # Check 一元くん was processed
            assert "一元くん" in results
            result = results["一元くん"]

            # Should have > 0 rows from the sheet
            assert result["total_rows"] > 0

            # Check data was actually inserted
            assert result["inserted"] > 0 or result["skipped"] > 0
        finally:
            conn.close()


def test_all_item_0405_ingestor_parse_row():
    """Test AllItem0405Ingestor.parse_row()."""
    ingestor = AllItem0405Ingestor()

    raw = {
        "内部ID": "NS-12345",
        "UPCコード": "4901234567890",
        "表示名": "テスト商品NS",
        "平均原価": "2000.5",
        "アイテム定義原価": "1800.0",
        "前回購入価格": "2100.0",
        "手持": "500",
        "利用可能": "450",
        "注文済": "100",
        "部門": "部門A",
        "商品ランク": "S",
        "skuID": "SKU-NS-001",
        "作成日": "2024-01-15",
        "メーカー名": "メーカーB",
    }
    ingestor._current_source = "test.xlsx"

    result = ingestor.parse_row(raw)

    assert result is not None
    assert result["internal_id"] == "NS-12345"
    assert result["upc"] == "4901234567890"
    assert result["display_name"] == "テスト商品NS"
    assert result["avg_cost"] == 2000.5
    assert result["std_cost"] == 1800.0
    assert result["on_hand"] == 500.0
    assert result["department"] == "部門A"


def test_all_item_0405_ingestor_upsert(temp_db):
    """Test upsert into item_master_netsuite table."""
    ingestor = AllItem0405Ingestor()

    import io

    csv_text = """内部ID,UPCコード,表示名,平均原価,アイテム定義原価,前回購入価格,手持,利用可能,注文済,部門,商品ランク,skuID,作成日,メーカー名
NS-001,4901234567890,商品1,2000.5,1800.0,2100.0,500,450,100,部門A,S,SKU-001,2024-01-15,メーカーA
NS-002,4901234567891,商品2,1500.0,1300.0,1600.0,300,280,50,部門B,A,SKU-002,2024-02-20,メーカーB
"""

    result = ingestor.run(
        io.StringIO(csv_text),
        temp_db,
        source_name="test_all_item.csv",
    )

    assert result["total_rows"] == 2
    assert result["inserted"] == 2
    assert result["errors"] == 0

    # Verify data in DB
    cursor = temp_db.execute(
        "SELECT COUNT(*) FROM item_master_netsuite WHERE internal_id='NS-001'"
    )
    count = cursor.fetchone()[0]
    assert count == 1

    cursor = temp_db.execute(
        "SELECT avg_cost, department FROM item_master_netsuite WHERE internal_id='NS-001'"
    )
    row = cursor.fetchone()
    assert row[0] == 2000.5
    assert row[1] == "部門A"


def test_store_monthly_ingestor_parse_row():
    """Test StoreMonthlyIngestor.parse_row()."""
    ingestor = StoreMonthlyIngestor()

    raw = {
        "月": "2月",
        "市場": "ASEAN",
        "店铺ID": "Shopee PH",
        "在线产品数": "500",
        "营业额": "50000.0",
        "利润": "5000.0",
        "毛利率": "10.0",
        "利润贡献率": "5.0",
        "店铺评价": "4.5",
        "扣减合计": "1000.0",
        "訂單數": "150",
    }
    ingestor._current_source = "test.xlsx"

    result = ingestor.parse_row(raw)

    assert result is not None
    assert result["market"] == "ASEAN"
    assert result["store_id"] == "Shopee PH"
    assert result["online_products"] == 500
    assert result["revenue"] == 50000.0
    assert result["profit"] == 5000.0
    assert result["margin_rate"] == 10.0


def test_store_monthly_ingestor_upsert(temp_db):
    """Test upsert into store_monthly table."""
    ingestor = StoreMonthlyIngestor()

    import io

    csv_text = """月,市場,店铺ID,在线产品数,营业额,利润,毛利率,利润贡献率,店铺评价,扣减合计,訂單數
2月,ASEAN,Shopee PH,500,50000.0,5000.0,10.0,5.0,4.5,1000.0,150
2月,ASEAN,Shopee SG,450,45000.0,4500.0,10.0,4.5,4.3,900.0,140
"""

    result = ingestor.run(
        io.StringIO(csv_text),
        temp_db,
        source_name="test_store.csv",
    )

    assert result["total_rows"] == 2
    assert result["inserted"] == 2
    assert result["errors"] == 0

    # Verify data in DB
    cursor = temp_db.execute(
        "SELECT COUNT(*) FROM store_monthly WHERE store_id='Shopee PH'"
    )
    count = cursor.fetchone()[0]
    assert count == 1

    cursor = temp_db.execute(
        "SELECT revenue, market FROM store_monthly WHERE store_id='Shopee PH'"
    )
    row = cursor.fetchone()
    assert row[0] == 50000.0
    assert row[1] == "ASEAN"
