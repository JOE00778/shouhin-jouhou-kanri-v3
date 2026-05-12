"""shared/purchase_engine.compute_recommendations の最小スモークテスト。"""
import math
import sqlite3

import pytest

from shared.purchase_engine import compute_recommendations


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE shop_sales (jan TEXT, period_start TEXT, period_end TEXT,
            qty_sold REAL, source TEXT, granularity TEXT);
        CREATE TABLE item_v2 (jan TEXT, display_name TEXT, maker TEXT, rank TEXT, handling_status TEXT);
        CREATE TABLE supplier_quote (supplier_name TEXT, jan TEXT, display_name TEXT, unit_price INTEGER,
            lot_size INTEGER, case_qty INTEGER, min_order_amount INTEGER, order_condition TEXT,
            lead_time_text TEXT, zone TEXT, zone_rank INTEGER, nst_supplier_code TEXT);
        CREATE TABLE item_inventory_snapshot_v2 (jan TEXT, qty_on_hand REAL, qty_committed REAL,
            qty_on_order REAL, qty_in_transit REAL);
        """
    )
    return c


def _seed_sales(c, jan, vals):
    for i, v in enumerate(vals):
        ym = ["2026-02-01", "2026-03-01", "2026-04-01"][i]
        c.execute("INSERT INTO shop_sales(jan,period_start,period_end,qty_sold,source,granularity) VALUES (?,?,?,?, 'export_item','monthly')",
                  (jan, ym, ym, v))


def test_inventory_deduction_and_lot_rounding():
    c = _conn()
    # JAN A: 月販 100/100/100 → base 100, flat ×1.0, 納期2週→order_months=ceil(14/30)+1=2 → 目標=200
    _seed_sales(c, "4900000000001", [100, 100, 100])
    c.execute("INSERT INTO item_v2 VALUES ('4900000000001','商品A','メーカーX','Aランク','取扱中')")
    c.execute("INSERT INTO supplier_quote VALUES ('仕入先甲','4900000000001','商品A',100,50,NULL,0,'掛','2週間','JD_DIRECT',1,'NST1')")
    # 手持 30, 確保 0, 注文済 20 → 有効在庫 50 → 不足 150 → lot 50 切り上げ = 150
    c.execute("INSERT INTO item_inventory_snapshot_v2 VALUES ('4900000000001',30,0,20,999)")  # 輸送中999は無視

    df = compute_recommendations(c, months=3, safety_months=1.0)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["target_stock"] == pytest.approx(200.0)
    assert r["eff_stock"] == pytest.approx(50.0)        # 30 - 0 + 20、輸送中は入らない
    assert r["shortfall"] == pytest.approx(150.0)
    assert r["suggested_qty"] == 150                     # 既に 50 倍数
    assert r["line_cost"] == 150 * 100
    assert df.attrs["inventory_loaded"] is True


def test_skip_when_enough_stock():
    c = _conn()
    _seed_sales(c, "4900000000002", [10, 10, 10])
    c.execute("INSERT INTO item_v2 VALUES ('4900000000002','商品B','メーカーX',NULL,'取扱中')")
    c.execute("INSERT INTO supplier_quote VALUES ('仕入先甲','4900000000002','商品B',100,1,NULL,0,'掛','1週間','JD_DIRECT',1,'NST1')")
    c.execute("INSERT INTO item_inventory_snapshot_v2 VALUES ('4900000000002',9999,0,0,0)")  # 在庫過多
    df = compute_recommendations(c, months=3, safety_months=1.0)
    assert df.empty


def test_discontinued_excluded_unless_opted_in():
    c = _conn()
    _seed_sales(c, "4900000000003", [50, 50, 50])
    c.execute("INSERT INTO item_v2 VALUES ('4900000000003','商品C','メーカーX','取扱中止','メーカー取扱中止')")
    c.execute("INSERT INTO supplier_quote VALUES ('仕入先甲','4900000000003','商品C',200,10,NULL,0,'掛','2週間','JD_DIRECT',1,'NST1')")
    c.execute("INSERT INTO item_inventory_snapshot_v2 VALUES ('4900000000003',0,0,0,0)")
    df = compute_recommendations(c, months=3, safety_months=1.0)
    assert df.empty
    assert df.attrs["n_discontinued_excluded"] == 1
    df2 = compute_recommendations(c, months=3, safety_months=1.0, include_discontinued=True)
    assert len(df2) == 1


def test_zone_priority_then_cheapest():
    c = _conn()
    _seed_sales(c, "4900000000004", [40, 40, 40])
    c.execute("INSERT INTO item_v2 VALUES ('4900000000004','商品D','メーカーX','Bランク','取扱中')")
    # 弁天が単価安いが zone_rank 後 → JD直送(zone_rank1)が選ばれる（弁天 markup 撤廃でも zone 優先は維持）
    c.execute("INSERT INTO supplier_quote VALUES ('JD系A','4900000000004','商品D',120,1,NULL,0,'掛','2週間','JD_DIRECT',1,'NSTJD')")
    c.execute("INSERT INTO supplier_quote VALUES ('弁天系B','4900000000004','商品D',100,1,NULL,0,'掛','2週間','BENTEN_TRANSIT',2,'NSTBT')")
    c.execute("INSERT INTO item_inventory_snapshot_v2 VALUES ('4900000000004',0,0,0,0)")
    df = compute_recommendations(c, months=3, safety_months=1.0)
    assert len(df) == 1
    assert df.iloc[0]["supplier_name"] == "JD系A"
    assert df.iloc[0]["zone"] == "JD_DIRECT"
    # 弁天が選ばれた場合の検証: JD候補を消す
    c.execute("DELETE FROM supplier_quote WHERE supplier_name='JD系A'")
    df2 = compute_recommendations(c, months=3, safety_months=1.0)
    assert df2.iloc[0]["supplier_name"] == "弁天系B"
    assert df2.iloc[0]["effective_price"] == 100   # markup 1.00 → 単価そのまま


def test_runs_without_inventory_table():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE shop_sales (jan TEXT, period_start TEXT, period_end TEXT, qty_sold REAL, source TEXT, granularity TEXT);
        CREATE TABLE item_v2 (jan TEXT, display_name TEXT, maker TEXT, rank TEXT, handling_status TEXT);
        CREATE TABLE supplier_quote (supplier_name TEXT, jan TEXT, display_name TEXT, unit_price INTEGER,
            lot_size INTEGER, case_qty INTEGER, min_order_amount INTEGER, order_condition TEXT,
            lead_time_text TEXT, zone TEXT, zone_rank INTEGER, nst_supplier_code TEXT);
        """
    )
    _seed_sales(c, "4900000000005", [30, 30, 30])
    c.execute("INSERT INTO item_v2 VALUES ('4900000000005','商品E','メーカーX','Cランク','取扱中')")
    c.execute("INSERT INTO supplier_quote VALUES ('仕入先甲','4900000000005','商品E',50,1,NULL,0,'掛','2週間','JD_DIRECT',1,'NST1')")
    df = compute_recommendations(c, months=3, safety_months=1.0)
    assert len(df) == 1
    assert df.attrs["inventory_loaded"] is False
    assert df.iloc[0]["eff_stock"] == 0.0           # 在庫なし → 0 扱い
    assert df.iloc[0]["suggested_qty"] == math.ceil(30 * 1.0 * 2)
