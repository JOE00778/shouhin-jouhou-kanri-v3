"""健康度计算模块的测试。

覆盖 10+ 测试：
- get_bucket：有/无 supply_cycle entry
- calc_stock_sales_ratio：正常 / 0 销量 / 0 库存
- calc_cross_ratio：正常 / 缺数据
- calc_health_grade：3 桶 × 4 档边界
- 死钱金额计算
- batch_calc：≥1 SKU 跑通
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from modules.inventory_health.metrics import (
    calc_cross_ratio,
    calc_health_grade,
    calc_stock_sales_ratio,
    batch_calc,
    get_bucket,
    THRESHOLD,
)


@pytest.fixture
def test_db():
    """创建临时测试数据库。"""
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        # 初始化 schema
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # 创建必要的表
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS supply_cycle (
                jan TEXT PRIMARY KEY,
                lead_time_days INTEGER,
                bucket TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS nst_turnover (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department TEXT,
                item_code TEXT NOT NULL,
                handling_status TEXT,
                cost REAL,
                avg_value REAL,
                turnover_rate REAL,
                avg_days_on_hand REAL,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(item_code, department)
            );

            CREATE TABLE IF NOT EXISTS nst_store_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fb_store TEXT,
                item_code TEXT NOT NULL,
                upc TEXT,
                handling_status TEXT,
                display_name TEXT,
                qty_sold REAL,
                unit_price REAL,
                revenue REAL,
                defined_cost REAL,
                gross_profit REAL,
                gross_margin REAL,
                rank TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(fb_store, item_code)
            );

            CREATE TABLE IF NOT EXISTS nst_inventory_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                internal_id TEXT NOT NULL,
                item_code TEXT NOT NULL,
                upc TEXT,
                display_name TEXT,
                status TEXT,
                bin_number TEXT,
                location TEXT,
                handling_status TEXT,
                qty_on_hand REAL,
                qty_committed REAL,
                qty_backorder REAL,
                std_cost REAL,
                total_amount REAL,
                avg_cost REAL,
                owner TEXT,
                department TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(internal_id, location, bin_number)
            );

            CREATE TABLE IF NOT EXISTS stock_sales_ratio_monthly (
                sku TEXT NOT NULL,
                year_month TEXT NOT NULL,
                end_inventory REAL,
                monthly_sales REAL,
                ratio_months REAL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sku, year_month)
            );

            CREATE TABLE IF NOT EXISTS cross_ratio_monthly (
                sku TEXT NOT NULL,
                year_month TEXT NOT NULL,
                gross_margin REAL,
                turnover REAL,
                cross_ratio REAL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sku, year_month)
            );

            CREATE TABLE IF NOT EXISTS health_grade_monthly (
                sku TEXT NOT NULL,
                year_month TEXT NOT NULL,
                bucket TEXT,
                threshold REAL,
                cross_ratio REAL,
                grade TEXT,
                dead_money_jpy REAL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sku, year_month)
            );
            """
        )
        conn.commit()
        yield (conn, str(db_path))
        conn.close()


# ============================================================
# get_bucket 测试（2 个）
# ============================================================


def test_get_bucket_exists(test_db):
    """测试：supply_cycle 中存在记录时返回 bucket。"""
    conn, db_path = test_db
    sku = "SKU-001"

    # 插入数据
    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "short"),
    )
    conn.commit()

    # 测试
    bucket = get_bucket(sku, conn)
    assert bucket == "short"


def test_get_bucket_missing(test_db):
    """测试：supply_cycle 中不存在记录时返回默认 'normal'。"""
    conn, db_path = test_db
    sku = "SKU-999"

    bucket = get_bucket(sku, conn)
    assert bucket == "normal"


# ============================================================
# calc_stock_sales_ratio 测试（3 个）
# ============================================================


def test_calc_stock_sales_ratio_normal(test_db):
    """测试：正常情况下正确计算存销比。"""
    conn, db_path = test_db
    sku = "SKU-100"

    # 插入库存
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-100-1", sku, 100.0, 1000.0),
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-100-2", sku, 50.0, 1000.0),
    )

    # 插入销售
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 30.0, 0.5),
    )
    conn.commit()

    # 测试
    result = calc_stock_sales_ratio(sku, "2026-04", conn)
    assert result.end_inventory == 150.0
    assert result.monthly_sales == 30.0
    assert result.ratio_months == 5.0  # 150 / 30


def test_calc_stock_sales_ratio_zero_sales(test_db):
    """测试：销售为 0 时比值为 0。"""
    conn, db_path = test_db
    sku = "SKU-101"

    # 插入库存，无销售
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-101-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_stock_sales_ratio(sku, "2026-04", conn)
    assert result.end_inventory == 100.0
    assert result.monthly_sales == 0.0
    assert result.ratio_months == 0.0


def test_calc_stock_sales_ratio_zero_inventory(test_db):
    """测试：库存为 0 时比值为 0。"""
    conn, db_path = test_db
    sku = "SKU-102"

    # 插入销售，无库存
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 50.0, 0.5),
    )
    conn.commit()

    result = calc_stock_sales_ratio(sku, "2026-04", conn)
    assert result.end_inventory == 0.0
    assert result.monthly_sales == 50.0
    assert result.ratio_months == 0.0


# ============================================================
# calc_cross_ratio 测试（2 个）
# ============================================================


def test_calc_cross_ratio_normal(test_db):
    """测试：正常情况下正确计算交叉比率。"""
    conn, db_path = test_db
    sku = "SKU-200"

    # 插入毛利率
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) "
        "VALUES (?, ?, ?)",
        (sku, 10.0, 0.4),
    )

    # 插入回转率
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 3.0),
    )
    conn.commit()

    result = calc_cross_ratio(sku, "2026-04", conn)
    assert result.gross_margin == 0.4
    assert result.turnover == 3.0
    assert abs(result.cross_ratio - 1.2) < 1e-9  # 0.4 * 3.0


def test_calc_cross_ratio_missing_data(test_db):
    """测试：缺少数据时交叉比率为 0。"""
    conn, db_path = test_db
    sku = "SKU-201"

    # 不插入任何数据

    result = calc_cross_ratio(sku, "2026-04", conn)
    assert result.gross_margin == 0.0
    assert result.turnover == 0.0
    assert result.cross_ratio == 0.0


# ============================================================
# calc_health_grade 测试（12 个：3 桶 × 4 档）
# ============================================================


def test_health_grade_short_excellent(test_db):
    """测试：short bucket，优秀（≥ 18）。"""
    conn, db_path = test_db
    sku = "SKU-300"

    # 设置 short bucket (T=12)
    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "short"),
    )

    # 插入数据使 cross_ratio >= 12 * 1.5 = 18
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.6),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.6 * 30 = 18
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-300-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.bucket == "short"
    assert result.threshold == 12
    assert result.cross_ratio == 18.0
    assert result.grade == "🟢 优秀"


def test_health_grade_short_healthy(test_db):
    """测试：short bucket，健康（12 ≤ cross_ratio < 18）。"""
    conn, db_path = test_db
    sku = "SKU-301"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "short"),
    )
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.4),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.4 * 30 = 12
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-301-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🟡 健康"


def test_health_grade_short_attention(test_db):
    """测试：short bucket，注意（6 ≤ cross_ratio < 12）。"""
    conn, db_path = test_db
    sku = "SKU-302"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "short"),
    )
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.2),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.2 * 30 = 6
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-302-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🟠 注意"


def test_health_grade_short_deadmoney(test_db):
    """测试：short bucket，死钱（< 6）且计算金额。"""
    conn, db_path = test_db
    sku = "SKU-303"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "short"),
    )
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.1),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.1 * 30 = 3
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-303-1", sku, 50.0, 100.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🔴 死钱"
    assert result.dead_money_jpy == 5000.0  # 50 * 100


def test_health_grade_normal_excellent(test_db):
    """测试：normal bucket，优秀（≥ 9）。"""
    conn, db_path = test_db
    sku = "SKU-304"

    # normal 默认，T=6
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.3),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.3 * 30 = 9
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-304-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.bucket == "normal"
    assert result.threshold == 6
    assert result.grade == "🟢 优秀"


def test_health_grade_normal_healthy(test_db):
    """测试：normal bucket，健康（6 ≤ cross_ratio < 9）。"""
    conn, db_path = test_db
    sku = "SKU-305"

    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.2),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.2 * 30 = 6
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-305-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🟡 健康"


def test_health_grade_normal_attention(test_db):
    """测试：normal bucket，注意（3 ≤ cross_ratio < 6）。"""
    conn, db_path = test_db
    sku = "SKU-306"

    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.1),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.1 * 30 = 3
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-306-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🟠 注意"


def test_health_grade_long_excellent(test_db):
    """测试：long bucket，优秀（≥ 6）。"""
    conn, db_path = test_db
    sku = "SKU-307"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "long"),
    )
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.2),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 30.0),  # 0.2 * 30 = 6，long T=4，6 >= 4*1.5=6
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-307-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.bucket == "long"
    assert result.threshold == 4
    assert result.grade == "🟢 优秀"


def test_health_grade_long_healthy(test_db):
    """测试：long bucket，健康（4 ≤ cross_ratio < 6）。"""
    conn, db_path = test_db
    sku = "SKU-308"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "long"),
    )
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.2),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 20.0),  # 0.2 * 20 = 4
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-308-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🟡 健康"


def test_health_grade_long_attention(test_db):
    """测试：long bucket，注意（2 ≤ cross_ratio < 4）。"""
    conn, db_path = test_db
    sku = "SKU-309"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "long"),
    )
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.1),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 20.0),  # 0.1 * 20 = 2
    )
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-309-1", sku, 100.0, 1000.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🟠 注意"


# ============================================================
# batch_calc 测试（1 个）
# ============================================================


def test_batch_calc_multi_skus(test_db):
    """测试：批量计算多个 SKU。"""
    conn, db_path = test_db

    # 插入 3 个 SKU
    for i in range(1, 4):
        sku = f"SKU-400-{i}"
        conn.execute(
            "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
            (sku, 10.0 + i, 0.1 + i * 0.1),
        )
        conn.execute(
            "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
            (sku, 10.0 + i),
        )
        conn.execute(
            "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
            "VALUES (?, ?, ?, ?)",
            (f"ID-400-{i}", sku, 100.0, 1000.0),
        )

    conn.commit()

    # 批量计算
    results = batch_calc("2026-04", db_path)

    # 检查结果
    assert len(results) == 3
    assert all(r.year_month == "2026-04" for r in results)
    assert all(r.grade in ["🟢 优秀", "🟡 健康", "🟠 注意", "🔴 死钱"] for r in results)

    # 检查写入数据库
    health_rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM health_grade_monthly WHERE year_month = '2026-04'"
    ).fetchone()
    assert health_rows["cnt"] == 3


# ============================================================
# 死钱计算测试（包含在 grade 测试中，此处独立验证）
# ============================================================


def test_deadmoney_calculation_precise(test_db):
    """测试：死钱金额精确计算。"""
    conn, db_path = test_db
    sku = "SKU-500"

    conn.execute(
        "INSERT INTO supply_cycle (jan, bucket) VALUES (?, ?)",
        (sku, "normal"),  # T=6
    )

    # 设置 cross_ratio < 3 (T/2)
    conn.execute(
        "INSERT INTO nst_store_sales (item_code, qty_sold, gross_margin) VALUES (?, ?, ?)",
        (sku, 10.0, 0.05),
    )
    conn.execute(
        "INSERT INTO nst_turnover (item_code, turnover_rate) VALUES (?, ?)",
        (sku, 50.0),  # 0.05 * 50 = 2.5，< 3
    )

    # 库存：75 件，单价 200 JPY
    conn.execute(
        "INSERT INTO nst_inventory_snapshot (internal_id, item_code, qty_on_hand, std_cost) "
        "VALUES (?, ?, ?, ?)",
        ("ID-500-1", sku, 75.0, 200.0),
    )
    conn.commit()

    result = calc_health_grade(sku, "2026-04", conn)
    assert result.grade == "🔴 死钱"
    assert result.dead_money_jpy == 15000.0  # 75 * 200
