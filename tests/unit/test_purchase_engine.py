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


# ---- 品牌集約 (Boss 2026-05-12) ----

def _conn_no_inv():
    """在庫テーブル無し版（在庫差引なし → 全 SKU が不足扱いになりやすい）。"""
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
    return c


def _add_sku(c, jan, maker, sales=(20, 20, 20)):
    c.execute("INSERT INTO item_v2 VALUES (?,?,?,?,?)", (jan, f"商品{jan[-2:]}", maker, "Bランク", "取扱中"))
    for i, v in enumerate(sales):
        ym = ["2026-02-01", "2026-03-01", "2026-04-01"][i]
        c.execute("INSERT INTO shop_sales VALUES (?,?,?,?, 'export_item','monthly')", (jan, ym, ym, v))


def _q(c, sup, jan, price, zone="JD_DIRECT", zr=1, lot=1):
    c.execute("INSERT INTO supplier_quote VALUES (?,?,?,?,?,NULL,0,'掛','2週間',?,?,?)",
              (sup, jan, "x", price, lot, zone, zr, "NST"))


def test_brand_consolidation_concentrates_to_max_coverage_supplier():
    c = _conn_no_inv()
    jans = [f"49000000000{i:02d}" for i in range(8)]
    for j in jans:
        _add_sku(c, j, "ブランドX")
    # 甲: 全 8 SKU を報価 (やや高め 110)。乙: 0-2 (安 100)。丙: 3-5 (安 100)。丁: 6-7 (安 100)。
    for j in jans:
        _q(c, "甲", j, 110)
    for j in jans[0:3]:
        _q(c, "乙", j, 100)
    for j in jans[3:6]:
        _q(c, "丙", j, 100)
    for j in jans[6:8]:
        _q(c, "丁", j, 100)
    # 集約なし: 0-2→乙, 3-5→丙, 6-7→丁 = 3 仕入先（甲は最安でないので選ばれない）
    df0 = compute_recommendations(c, months=3, safety_months=1.0, use_inventory=False, consolidate_by_brand=False, small_brand_skip=4)
    assert df0["supplier_name"].nunique() == 3
    # 集約あり: 甲が全 8 をカバー → 甲 1 社に集約 (110 ≤ 100×1.5 なので移動 OK)
    df = compute_recommendations(c, months=3, safety_months=1.0, use_inventory=False, consolidate_by_brand=True, small_brand_skip=4)
    assert df["supplier_name"].nunique() == 1
    assert set(df["supplier_name"]) == {"甲"}
    assert df["consolidated"].all()
    assert df.attrs["n_consolidated"] == 8


def test_consolidation_never_worsens_zone():
    c = _conn_no_inv()
    jans = [f"49000000010{i:02d}" for i in range(8)]
    for j in jans:
        _add_sku(c, j, "ブランドY")
    # JD直送「甲」が 0-6 を報価。SKU 7 は JD では「乙」のみ、弁天「丙」も報価。
    for j in jans[0:7]:
        _q(c, "甲", j, 100)
    _q(c, "乙", jans[7], 100, zone="JD_DIRECT", zr=1)
    # 弁天「丙」が 全 8 を報価 (安) → カバー数だけ見ると 丙 が最強。だが zone tier=1 を先に処理するので
    # SKU 0-7 は JD アンカー(甲)に行く。SKU 7 は 甲 が報価無し → 乙(JD) のまま。丙(弁天) には行かない。
    for j in jans:
        _q(c, "丙", j, 80, zone="BENTEN_TRANSIT", zr=2)
    df = compute_recommendations(c, months=3, safety_months=1.0, use_inventory=False, consolidate_by_brand=True, small_brand_skip=4)
    # 全 SKU が JD直送 (丙=弁天 には 1 件も行っていない)
    assert (df["zone"] == "JD_DIRECT").all()
    assert "丙" not in set(df["supplier_name"])
    # SKU 7 は 乙 のまま
    assert df.loc[df["jan"] == jans[7], "supplier_name"].iloc[0] == "乙"


def test_small_brand_not_consolidated():
    c = _conn_no_inv()
    jans = [f"49000000020{i:02d}" for i in range(4)]   # 4 SKU ≤ small_brand_skip(5)
    for j in jans:
        _add_sku(c, j, "ミニ品牌")
    _q(c, "甲", jans[0], 100); _q(c, "甲", jans[1], 100); _q(c, "甲", jans[2], 100); _q(c, "甲", jans[3], 100)
    _q(c, "乙", jans[0], 90); _q(c, "乙", jans[1], 90)   # 0,1 は 乙 が安い
    df = compute_recommendations(c, months=3, safety_months=1.0, use_inventory=False, consolidate_by_brand=True, small_brand_skip=5)
    # 小品牌なので集約しない → 0,1→乙 / 2,3→甲 の 2 社のまま
    assert df["supplier_name"].nunique() == 2
    assert df.attrs["n_consolidated"] == 0


def test_consolidation_price_guard():
    c = _conn_no_inv()
    jans = [f"49000000030{i:02d}" for i in range(8)]
    for j in jans:
        _add_sku(c, j, "ブランドZ")
    # 甲: 全 8 を報価だが激高 (1000)。乙: 0-3 (安 100)。丙: 4-7 (安 100)。
    for j in jans:
        _q(c, "甲", j, 1000)
    for j in jans[0:4]:
        _q(c, "乙", j, 100)
    for j in jans[4:8]:
        _q(c, "丙", j, 100)
    df = compute_recommendations(c, months=3, safety_months=1.0, use_inventory=False, consolidate_by_brand=True, small_brand_skip=4)
    # 甲は 1000 > 100×1.5 なので誰も移らない → 乙(4) + 丙(4) の 2 社のまま, 甲 は使われない
    assert "甲" not in set(df["supplier_name"])
    assert df["supplier_name"].nunique() == 2


# ---- optimize モード (Boss 2026-05-12: 最小支出シナリオ) ----

def test_optimize_line_cost_picks_lowest_total_not_lowest_unit_price():
    c = _conn_no_inv()
    _add_sku(c, "4900000040001", "ブランドQ", sales=(25, 25, 25))   # base 25, flat ×1.0
    # 甲: 単価100 だが ロット100 → 25 必要でも 100 個 = ¥10,000
    _q(c, "甲", "4900000040001", 100, lot=100)
    # 乙: 単価120 (甲より高い!) だが ロット10 → 30 個 (ceil(25/10*?)...) 実際 order_months=2 で 50必要→ lot10→50個=¥6,000
    _q(c, "乙", "4900000040001", 120, lot=10)
    # 注: order_months = ceil(14/30)+1 = 2 → target = 25*1*2 = 50
    df_cost = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False, optimize="cost")
    df_lc = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False, optimize="line_cost")
    # 'cost' は単価最安 → 甲 (100<120) → 100個×100 = 10000
    assert df_cost.iloc[0]["supplier_name"] == "甲"
    assert df_cost.iloc[0]["line_cost"] == 100 * 100
    # 'line_cost' は発注金額最安 → 乙 (50個×120=6000 < 10000)
    assert df_lc.iloc[0]["supplier_name"] == "乙"
    assert df_lc.iloc[0]["line_cost"] == 50 * 120
    assert df_lc.attrs["optimize"] == "line_cost"


# ---- ランクフィルタ / 在庫月数上限 (Boss 2026-05-12) ----

def test_rank_filter():
    c = _conn_no_inv()
    _add_sku(c, "4900000050001", "M", sales=(20, 20, 20))
    c.execute("UPDATE item_v2 SET rank='Aランク' WHERE jan='4900000050001'")
    _add_sku(c, "4900000050002", "M", sales=(20, 20, 20))
    c.execute("UPDATE item_v2 SET rank='Cランク' WHERE jan='4900000050002'")
    _q(c, "甲", "4900000050001", 100); _q(c, "甲", "4900000050002", 100)
    df = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False, ranks=("Aランク", "Bランク"))
    assert len(df) == 1
    assert df.iloc[0]["jan"] == "4900000050001"
    assert df.attrs["n_rank_excluded"] == 1
    df_all = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False)
    assert len(df_all) == 2


def test_max_stock_months_defers_overstock():
    c = _conn_no_inv()
    # 月販 1/1/1 (base 1), lot 100 → order_months=2 → target=2 → shortfall=2 → ceil(2/100)*100=100
    # 発注後在庫 = 0 + 100 = 100 ヶ月分 >>> 上限
    _add_sku(c, "4900000060001", "M", sales=(1, 1, 1))
    _q(c, "甲", "4900000060001", 50, lot=100)
    df_nocap = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False)
    assert df_nocap.iloc[0]["status"] == "recommended"
    assert df_nocap.iloc[0]["stock_months_after"] == 100.0
    df_cap = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False, max_stock_months=4.0)
    assert df_cap.iloc[0]["status"] == "deferred_overstock"
    assert df_cap.iloc[0]["overstock"] is True or df_cap.iloc[0]["overstock"] == 1
    assert df_cap.attrs["n_overstock"] == 1
    # 上限内の SKU は recommended のまま
    _add_sku(c, "4900000060002", "M", sales=(100, 100, 100))   # base 100, lot 100 → target 200 → ceil(200/100)*100=200 → 在庫 200/100=2ヶ月
    _q(c, "甲", "4900000060002", 50, lot=100)
    df2 = compute_recommendations(c, use_inventory=False, consolidate_by_brand=False, max_stock_months=4.0)
    rec = df2[df2["jan"] == "4900000060002"].iloc[0]
    assert rec["status"] == "recommended"
    assert rec["stock_months_after"] == 2.0
