"""测试 LocalItemMasterIngestor 对 item_master_cleaned.csv 的导入。"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from data_warehouse.db.migrations import init_db
from data_warehouse.ingest.items import LocalItemMasterIngestor


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return init_db(tmp_path / "test.db")


SAMPLE_CSV = """商品コード,jan,ランク,メーカー名,商品名,取扱区分,在庫,発注済,実績原価,最安原価,ケース入数,発注ロット,重量
4901111310490,4901111310490,NEW,AGF,AGF ブレンディ カフェラトリー スティック 濃厚キャラメルマキアート7本,取扱中,15,0,286,286,24,24,0
4901111310506,4901111310506,NEW,AGF,AGF ブレンディ カフェラトリー スティック 濃厚ビターカフェラテ8本,取扱中,15,0,286,286,24,24,0
4900000999999,,A,テストメーカー,テスト商品,廃番,,,,,,,
"""


def test_imports_three_rows(conn):
    summary = LocalItemMasterIngestor().run(
        io.StringIO(SAMPLE_CSV), conn, source_name="sample.csv"
    )
    assert summary["total_rows"] == 3
    assert summary["errors"] == 0
    assert summary["inserted"] == 3
    rows = conn.execute("SELECT COUNT(*) AS c FROM item").fetchone()
    assert rows["c"] == 3


def test_inactive_flag_set_for_廃番(conn):
    LocalItemMasterIngestor().run(
        io.StringIO(SAMPLE_CSV), conn, source_name="sample.csv"
    )
    row = conn.execute(
        "SELECT inactive_flag, handling_status FROM item WHERE item_code='4900000999999'"
    ).fetchone()
    assert row["inactive_flag"] == 1
    assert row["handling_status"] == "廃番"


def test_inactive_flag_zero_for_取扱中(conn):
    LocalItemMasterIngestor().run(
        io.StringIO(SAMPLE_CSV), conn, source_name="sample.csv"
    )
    row = conn.execute(
        "SELECT inactive_flag FROM item WHERE item_code='4901111310490'"
    ).fetchone()
    assert row["inactive_flag"] == 0


def test_internal_id_falls_back_to_item_code(conn):
    """本地 CSV 没有 NS Internal ID，应该用 item_code 兜底。"""
    LocalItemMasterIngestor().run(
        io.StringIO(SAMPLE_CSV), conn, source_name="sample.csv"
    )
    row = conn.execute(
        "SELECT internal_id, item_code FROM item WHERE item_code='4901111310490'"
    ).fetchone()
    assert row["internal_id"] == row["item_code"]


def test_idempotent(conn):
    LocalItemMasterIngestor().run(
        io.StringIO(SAMPLE_CSV), conn, source_name="sample.csv"
    )
    LocalItemMasterIngestor().run(
        io.StringIO(SAMPLE_CSV), conn, source_name="sample.csv"
    )
    count = conn.execute("SELECT COUNT(*) AS c FROM item").fetchone()["c"]
    assert count == 3


def test_full_real_csv_imports_all_unique(conn):
    """端到端：导入 Boss 实际的 item_master_cleaned.csv。

    实测 CSV 含 5,865 数据行 + 3 个重复 商品コード（已被 UPSERT 正确合并），
    应得到 5,862 个唯一商品行。NetSuite 端总 SKU 数（7,403）需通过 NS Saved Search
    导入补齐 —— 本地 CSV 只是子集。
    """
    real_csv = Path("/Users/joe/CC/item_master_cleaned.csv")
    if not real_csv.exists():
        pytest.skip("real CSV not present")
    summary = LocalItemMasterIngestor().run(
        str(real_csv), conn, source_name="item_master_cleaned.csv"
    )
    count = conn.execute("SELECT COUNT(*) AS c FROM item").fetchone()["c"]
    assert summary["errors"] == 0
    # 5,865 数据行 - 3 个重复 = 5,862 个唯一 item
    assert count == 5862
    # 总行数与 CSV 一致
    assert summary["total_rows"] == 5865


def test_real_csv_廃番_count_matches_handling_status(conn):
    """端到端：导入后 inactive_flag=1 的数量应等于 handling_status='廃番' 的数量。"""
    real_csv = Path("/Users/joe/CC/item_master_cleaned.csv")
    if not real_csv.exists():
        pytest.skip("real CSV not present")
    LocalItemMasterIngestor().run(
        str(real_csv), conn, source_name="item_master_cleaned.csv"
    )
    by_flag = conn.execute(
        "SELECT COUNT(*) AS c FROM item WHERE inactive_flag=1"
    ).fetchone()["c"]
    by_status = conn.execute(
        "SELECT COUNT(*) AS c FROM item WHERE handling_status='廃番'"
    ).fetchone()["c"]
    assert by_flag == by_status
