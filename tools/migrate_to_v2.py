"""v2 数据模型一键 ETL · 把旧表（12 张）合并到 v2 新表（10 张）

Boss 决策（2026-05-09）：
- Q1=A: jan 强制必填，无效 JAN 跳过
- Q2=C: market_segment 粗 + shop 细 两层
- Q3=A: purchase_* 三表合并按 source 区分
- Q4=C: 保留 benten_stock / warehouse_stock；废弃 store_profit_*

用法：
    # 命令行
    python -m tools.migrate_to_v2 --all
    python -m tools.migrate_to_v2 --step item_v2
    python -m tools.migrate_to_v2 --step shop_sales

    # 在 page 99 Tab 6 一键调用
    from tools.migrate_to_v2 import run_all, RUN_STEPS
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ──────── 允许从 repo 根目录跑 ────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.db import get_connection


# ============================================================
# 工具
# ============================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_jan(jan: Any) -> bool:
    """JAN 必须是 8-13 位数字字符串。"""
    if not jan:
        return False
    s = str(jan).strip()
    return s.isdigit() and 8 <= len(s) <= 13


def _safe(v: Any) -> Any:
    """None / 空字符串 → None，数字字符串保留为字符串。"""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _record_run(conn, step: str, source: str, read: int, written: int, errors: int, notes: str = "") -> None:
    conn.execute(
        """
        INSERT INTO _v2_migration_runs
            (step, source_table, rows_read, rows_written, errors, ran_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (step, source, read, written, errors, _now(), notes[:1000]),
    )
    conn.commit()


def _table_exists(conn, name: str) -> bool:
    """SQLite + Postgres 通用：探测表是否存在（错就当没有）。"""
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _table_count(conn, name: str) -> int:
    if not _table_exists(conn, name):
        return 0
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
    except Exception:
        return 0


# ============================================================
# Step 1 · item_v2（合并 4 张商品主表）
# ============================================================
def step_item_v2(conn) -> dict:
    """合并 item_master_netsuite + nst_item_summary + item_master + supply_cycle → item_v2.

    优先级：item_master_netsuite (NST 主) > nst_item_summary > item_master > supply_cycle
    JAN 来源：item_master_netsuite.upc 或 item_master.jan
    """
    read = written = errors = 0
    skipped_no_jan = 0

    # 1. 先收集所有可能的 JAN（按表优先级）
    jan_data: dict[str, dict] = {}

    # 1a. item_master_netsuite (NST · 主权威)
    if _table_exists(conn, "item_master_netsuite"):
        for row in conn.execute(
            "SELECT internal_id, upc, display_name, avg_cost, std_cost, "
            "department, rank, sku_id, created_at, maker FROM item_master_netsuite"
        ).fetchall():
            r = dict(row)
            jan = r.get("upc")
            read += 1
            if not _is_valid_jan(jan):
                skipped_no_jan += 1
                continue
            jan = str(jan).strip()
            jan_data[jan] = {
                "jan": jan,
                "internal_id": _safe(r.get("internal_id")),
                "upc": jan,
                "display_name": _safe(r.get("display_name")),
                "maker": _safe(r.get("maker")),
                "rank": _safe(r.get("rank")),
                "std_cost": _safe(r.get("std_cost")),
                "avg_cost": _safe(r.get("avg_cost")),
                "source_priority": "nst",
            }

    # 1b. nst_item_summary (覆盖 avg_cost / std_cost / handling_status，按 item_code 关联)
    if _table_exists(conn, "nst_item_summary"):
        # 通过 item_master_netsuite 反查 jan：先建 item_code → jan 索引
        code_to_jan: dict[str, str] = {}
        if _table_exists(conn, "item_master_netsuite"):
            for r in conn.execute(
                "SELECT internal_id, upc FROM item_master_netsuite WHERE upc IS NOT NULL"
            ).fetchall():
                # nst_item_summary.item_code 是 NetSuite 的 アイテム编号
                # 但 item_master_netsuite 没存 item_code 列（只有 internal_id + upc）
                # 退而求其次，用 upc 直接 join nst_item_summary.upc
                pass

        # 直接 join: nst_item_summary.upc ↔ jan
        for row in conn.execute(
            "SELECT item_code, upc, display_name, handling_status, "
            "std_cost, avg_cost FROM nst_item_summary WHERE upc IS NOT NULL"
        ).fetchall():
            r = dict(row)
            jan = r.get("upc")
            read += 1
            if not _is_valid_jan(jan):
                continue
            jan = str(jan).strip()
            existing = jan_data.get(jan, {"jan": jan, "upc": jan, "source_priority": "nst"})
            # 用 nst_item_summary 的字段补充
            if not existing.get("display_name"):
                existing["display_name"] = _safe(r.get("display_name"))
            if not existing.get("std_cost") and r.get("std_cost") is not None:
                existing["std_cost"] = _safe(r.get("std_cost"))
            if not existing.get("avg_cost") and r.get("avg_cost") is not None:
                existing["avg_cost"] = _safe(r.get("avg_cost"))
            existing["handling_status"] = _safe(r.get("handling_status"))
            existing["item_code"] = _safe(r.get("item_code"))
            jan_data[jan] = existing

    # 1c. item_master (PK = jan，含 maker / actual_cost / min_cost / case_qty / weight)
    if _table_exists(conn, "item_master"):
        for row in conn.execute(
            "SELECT jan, item_code, rank, maker, display_name, handling_status, "
            "actual_cost, min_cost, case_qty, order_lot, weight FROM item_master"
        ).fetchall():
            r = dict(row)
            jan = r.get("jan")
            read += 1
            if not _is_valid_jan(jan):
                skipped_no_jan += 1
                continue
            jan = str(jan).strip()
            existing = jan_data.get(jan, {"jan": jan, "upc": jan, "source_priority": "supplier"})
            # item_master 优先级低于 NST，仅补缺
            if not existing.get("item_code"):
                existing["item_code"] = _safe(r.get("item_code"))
            if not existing.get("display_name"):
                existing["display_name"] = _safe(r.get("display_name"))
            if not existing.get("maker"):
                existing["maker"] = _safe(r.get("maker"))
            if not existing.get("rank"):
                existing["rank"] = _safe(r.get("rank"))
            if not existing.get("handling_status"):
                existing["handling_status"] = _safe(r.get("handling_status"))
            existing["actual_cost"] = _safe(r.get("actual_cost"))
            existing["min_cost"] = _safe(r.get("min_cost"))
            existing["case_qty"] = _safe(r.get("case_qty"))
            existing["order_lot"] = _safe(r.get("order_lot"))
            existing["weight"] = _safe(r.get("weight"))
            jan_data[jan] = existing

    # 1d. supply_cycle (jan PK，提供 supply_cycle_days / bucket)
    if _table_exists(conn, "supply_cycle"):
        for row in conn.execute(
            "SELECT jan, lead_time_days, bucket FROM supply_cycle"
        ).fetchall():
            r = dict(row)
            jan = r.get("jan")
            if not _is_valid_jan(jan):
                continue
            jan = str(jan).strip()
            if jan in jan_data:
                jan_data[jan]["supply_cycle_days"] = _safe(r.get("lead_time_days"))
                jan_data[jan]["bucket"] = _safe(r.get("bucket"))

    # 1e. nst_inventory_snapshot 汇总当前库存（on_hand_total）
    if _table_exists(conn, "nst_inventory_snapshot"):
        rows = conn.execute(
            "SELECT upc, SUM(qty_on_hand) AS total_qty "
            "FROM nst_inventory_snapshot WHERE upc IS NOT NULL "
            "GROUP BY upc"
        ).fetchall()
        for r in rows:
            jan = (r["upc"] or "").strip()
            if jan in jan_data:
                jan_data[jan]["on_hand_total"] = r["total_qty"] or 0

    # 2. 写入 item_v2
    now = _now()
    for jan, payload in jan_data.items():
        try:
            cols = [
                "jan", "item_code", "internal_id", "upc",
                "display_name", "maker", "rank", "handling_status",
                "std_cost", "avg_cost", "actual_cost", "min_cost",
                "case_qty", "order_lot", "weight", "supplier_default",
                "supply_cycle_days", "bucket",
                "on_hand_total", "on_order_total",
                "source_priority", "imported_at", "updated_at",
            ]
            row = {c: payload.get(c) for c in cols}
            row["imported_at"] = now
            row["updated_at"] = now
            placeholders = ", ".join(f":{c}" for c in cols)
            col_list = ", ".join(cols)
            conn.execute(
                f"INSERT OR REPLACE INTO item_v2 ({col_list}) VALUES ({placeholders})",
                row,
            )
            written += 1
        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"[item_v2] error on {jan}: {e}")

    conn.commit()
    notes = f"merged_jan_count={len(jan_data)}, skipped_no_jan={skipped_no_jan}"
    _record_run(conn, "item_v2", "item_master_netsuite+nst_item_summary+item_master+supply_cycle",
                read, written, errors, notes)
    return {"step": "item_v2", "read": read, "written": written, "errors": errors,
            "notes": notes}


# ============================================================
# Step 2 · market_segment（静态字典）
# ============================================================
def step_market_segment(conn) -> dict:
    markets = [
        ("TW", "台湾", "TWD"),
        ("SG", "Singapore", "SGD"),
        ("MY", "Malaysia", "MYR"),
        ("PH", "Philippines", "PHP"),
        ("TH", "Thailand", "THB"),
        ("VN", "Vietnam", "VND"),
        ("ID", "Indonesia", "IDR"),
        ("JP", "Japan", "JPY"),
        ("US", "United States", "USD"),
        ("CN", "China", "CNY"),
        ("KR", "Korea", "KRW"),
        ("BR", "Brazil", "BRL"),
    ]
    written = 0
    for m in markets:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO market_segment (market_id, display_name, currency, active) "
                "VALUES (?, ?, ?, 1)",
                m,
            )
            written += 1
        except Exception:
            pass
    conn.commit()
    _record_run(conn, "market_segment", "(static)", len(markets), written, 0, "12 markets seeded")
    return {"step": "market_segment", "read": len(markets), "written": written, "errors": 0}


# ============================================================
# Step 3 · shop（从 sales_line + store_monthly + nst_store_sales 提取 distinct 店铺）
# ============================================================
def _infer_market_platform(store_id: str, source: str) -> tuple[str, str]:
    """从 store_id 名字推断 (market_id, platform)。"""
    s = (store_id or "").lower()
    # 平台
    if "shopee" in s or s.startswith("sp_") or "smkj" in s:
        platform = "shopee"
    elif "lazada" in s or "lzd" in s:
        platform = "lazada"
    elif "amazon" in s or "amzn" in s:
        platform = "amazon"
    elif "coupang" in s:
        platform = "coupang"
    elif "netsuite" in s or "fb_" in s or source == "nst_store_sales":
        platform = "netsuite"
    else:
        platform = "unknown"

    # 市场
    for code in ["tw", "sg", "my", "ph", "th", "vn", "id", "jp", "us", "cn", "kr", "br"]:
        if f"_{code}" in s or f"-{code}" in s or s.endswith(code):
            return code.upper(), platform
    return "JP", platform   # 默认 JP


def step_shop(conn) -> dict:
    read = written = errors = 0
    discovered: dict[str, dict] = {}

    # 从 sales_line.store（实际列名，不是 store_id）
    if _table_exists(conn, "sales_line"):
        try:
            for r in conn.execute(
                "SELECT DISTINCT store, source FROM sales_line WHERE store IS NOT NULL"
            ).fetchall():
                read += 1
                sid = (r["store"] or "").strip()
                if not sid:
                    continue
                market, platform = _infer_market_platform(sid, r["source"] or "")
                discovered[sid] = {
                    "shop_id": sid, "market_id": market, "platform": platform,
                    "display_name": sid,
                }
        except Exception:
            pass

    # 从 store_monthly.store_id
    if _table_exists(conn, "store_monthly"):
        for r in conn.execute(
            "SELECT DISTINCT store_id, market FROM store_monthly WHERE store_id IS NOT NULL"
        ).fetchall():
            read += 1
            sid = (r["store_id"] or "").strip()
            if not sid:
                continue
            market = (r["market"] or "").upper() or "JP"
            discovered.setdefault(sid, {
                "shop_id": sid, "market_id": market,
                "platform": "shopee", "display_name": sid,
            })

    # 从 nst_store_sales.fb_store
    if _table_exists(conn, "nst_store_sales"):
        for r in conn.execute(
            "SELECT DISTINCT fb_store FROM nst_store_sales WHERE fb_store IS NOT NULL"
        ).fetchall():
            read += 1
            sid = (r["fb_store"] or "").strip()
            if not sid:
                continue
            sid_full = f"netsuite_{sid}"
            market, _ = _infer_market_platform(sid, "nst_store_sales")
            discovered.setdefault(sid_full, {
                "shop_id": sid_full, "market_id": market,
                "platform": "netsuite", "display_name": sid,
            })

    now = _now()
    for sid, payload in discovered.items():
        try:
            conn.execute(
                "INSERT OR REPLACE INTO shop "
                "(shop_id, market_id, platform, display_name, currency, owner, active, created_at) "
                "VALUES (:shop_id, :market_id, :platform, :display_name, NULL, NULL, 1, :ts)",
                {**payload, "ts": now},
            )
            written += 1
        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"[shop] error on {sid}: {e}")

    conn.commit()
    _record_run(conn, "shop", "sales_line+store_monthly+nst_store_sales",
                read, written, errors, f"unique_shops={len(discovered)}")
    return {"step": "shop", "read": read, "written": written, "errors": errors,
            "notes": f"discovered={len(discovered)}"}


# ============================================================
# Step 4 · shop_monthly（直接从 store_monthly 复制）
# ============================================================
def step_shop_monthly(conn) -> dict:
    if not _table_exists(conn, "store_monthly"):
        return {"step": "shop_monthly", "read": 0, "written": 0, "errors": 0,
                "notes": "no store_monthly"}
    read = written = errors = 0
    now = _now()
    for r in conn.execute(
        "SELECT year_month, market, store_id, online_products, revenue, profit, "
        "margin_rate, profit_contrib, store_rating, deduction_total, order_count "
        "FROM store_monthly"
    ).fetchall():
        read += 1
        rd = dict(r)
        if not rd.get("store_id"):
            continue
        try:
            conn.execute(
                "INSERT OR REPLACE INTO shop_monthly "
                "(shop_id, year_month, gmv, profit, margin_rate, profit_contrib, "
                "deduction_total, order_count, store_rating, online_products, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rd["store_id"], rd["year_month"], rd["revenue"], rd["profit"],
                 rd["margin_rate"], rd["profit_contrib"], rd["deduction_total"],
                 rd["order_count"], rd["store_rating"], rd["online_products"], now),
            )
            written += 1
        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"[shop_monthly] error: {e}")
    conn.commit()
    _record_run(conn, "shop_monthly", "store_monthly", read, written, errors)
    return {"step": "shop_monthly", "read": read, "written": written, "errors": errors}


# ============================================================
# Step 5 · shop_sales（从 nst_store_sales + sales_line 拆出 jan 销售）
# ============================================================
def step_shop_sales(conn) -> dict:
    read = written = errors = 0
    now = _now()
    skipped_no_jan = 0

    # 5a. nst_store_sales · item_code 在实际数据中 99% 是 13 位 JAN，直接当 jan 用
    #     （nst_item_summary 通常空表，不能依赖映射）
    if _table_exists(conn, "nst_store_sales"):
        # 优先建 item_code → jan 映射（如果 nst_item_summary 有数据）
        code_to_jan: dict[str, str] = {}
        if _table_exists(conn, "nst_item_summary"):
            for r in conn.execute(
                "SELECT item_code, upc FROM nst_item_summary "
                "WHERE upc IS NOT NULL AND item_code IS NOT NULL"
            ).fetchall():
                jan = (r["upc"] or "").strip()
                if _is_valid_jan(jan):
                    code_to_jan[r["item_code"]] = jan

        for r in conn.execute(
            "SELECT fb_store, item_code, qty_sold, unit_price, revenue, "
            "defined_cost, gross_profit, gross_margin, rank FROM nst_store_sales"
        ).fetchall():
            read += 1
            ic = (r["item_code"] or "").strip()
            # 优先映射，fallback 到 item_code 自身（如果 13 位数字）
            jan = code_to_jan.get(ic) or (ic if _is_valid_jan(ic) else None)
            if not jan:
                skipped_no_jan += 1
                continue
            shop_id = f"netsuite_{r['fb_store']}" if r["fb_store"] else "netsuite_unknown"
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO shop_sales "
                    "(shop_id, jan, period_start, period_end, qty_sold, revenue, revenue_jpy, "
                    "cost, gross_profit, gross_margin, rank, source, imported_at) "
                    "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (shop_id, jan, r["qty_sold"], r["revenue"], r["revenue"],
                     r["defined_cost"], r["gross_profit"], r["gross_margin"], r["rank"],
                     "nst_store_sales", now),
                )
                written += 1
            except Exception as e:
                errors += 1

    # 5b. sales_line（实际表用 store 列名，不是 store_id）— 优先 upc 当 jan，fallback item_code
    if _table_exists(conn, "sales_line"):
        try:
            for r in conn.execute(
                "SELECT store, item_code, upc, period_start, period_end, "
                "qty_sold, revenue, defined_cost, gross_profit, gross_margin, "
                "source FROM sales_line"
            ).fetchall():
                read += 1
                upc = (r["upc"] or "").strip()
                ic = (r["item_code"] or "").strip()
                jan = upc if _is_valid_jan(upc) else (ic if _is_valid_jan(ic) else None)
                if not jan:
                    skipped_no_jan += 1
                    continue
                shop_id = (r["store"] or "unknown").strip()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO shop_sales "
                        "(shop_id, jan, period_start, period_end, qty_sold, revenue, "
                        "cost, gross_profit, gross_margin, source, imported_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (shop_id, jan, r["period_start"], r["period_end"],
                         r["qty_sold"], r["revenue"], r["defined_cost"],
                         r["gross_profit"], r["gross_margin"], r["source"], now),
                    )
                    written += 1
                except Exception:
                    errors += 1
        except Exception as e:
            errors += 1
            print(f"[shop_sales] sales_line query error: {e}")

    conn.commit()
    _record_run(conn, "shop_sales", "nst_store_sales+sales_line",
                read, written, errors, f"skipped_no_jan={skipped_no_jan}")
    return {"step": "shop_sales", "read": read, "written": written, "errors": errors,
            "notes": f"skipped_no_jan={skipped_no_jan}"}


# ============================================================
# Step 6 · item_sales_history（jan × period × channel 聚合）
# ============================================================
def step_item_sales_history(conn) -> dict:
    """从 shop_sales 按 jan + period + channel 聚合（避开重复 ETL，
    直接 GROUP BY shop_sales 然后写入）。"""
    read = written = errors = 0
    now = _now()

    if not _table_exists(conn, "shop_sales"):
        return {"step": "item_sales_history", "read": 0, "written": 0, "errors": 0,
                "notes": "shop_sales not yet"}

    # 关联 shop 拿 platform
    rows = conn.execute(
        """
        SELECT ss.jan, ss.period_start, ss.period_end,
               COALESCE(s.platform, 'unknown') || '_' || COALESCE(s.market_id, 'XX') AS channel,
               SUM(ss.qty_sold) AS qty_sold,
               SUM(ss.revenue) AS revenue,
               SUM(ss.cost) AS cost,
               SUM(ss.gross_profit) AS gross_profit,
               AVG(ss.gross_margin) AS gross_margin,
               ss.source
        FROM shop_sales ss
        LEFT JOIN shop s ON s.shop_id = ss.shop_id
        GROUP BY ss.jan, ss.period_start, ss.period_end, channel, ss.source
        """
    ).fetchall()

    for r in rows:
        read += 1
        try:
            conn.execute(
                "INSERT OR REPLACE INTO item_sales_history "
                "(jan, period_start, period_end, channel, qty_sold, revenue, cost, "
                "gross_profit, gross_margin, source, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["jan"], r["period_start"], r["period_end"], r["channel"],
                 r["qty_sold"], r["revenue"], r["cost"], r["gross_profit"],
                 r["gross_margin"], r["source"], now),
            )
            written += 1
        except Exception:
            errors += 1
    conn.commit()
    _record_run(conn, "item_sales_history", "shop_sales (aggregated)",
                read, written, errors)
    return {"step": "item_sales_history", "read": read, "written": written, "errors": errors}


# ============================================================
# Step 7 · item_inventory_snapshot_v2（从 nst_inventory_snapshot 转换）
# ============================================================
def step_item_inventory(conn) -> dict:
    if not _table_exists(conn, "nst_inventory_snapshot"):
        return {"step": "item_inventory_snapshot_v2", "read": 0, "written": 0, "errors": 0,
                "notes": "nst_inventory_snapshot not exist"}
    read = written = errors = skipped = 0
    now = _now()
    for r in conn.execute(
        "SELECT upc, location, bin_number, qty_on_hand, qty_committed, qty_backorder, "
        "std_cost, avg_cost FROM nst_inventory_snapshot"
    ).fetchall():
        read += 1
        jan = (r["upc"] or "").strip()
        if not _is_valid_jan(jan):
            skipped += 1
            continue
        try:
            conn.execute(
                "INSERT OR REPLACE INTO item_inventory_snapshot_v2 "
                "(jan, location, bin_number, snapshot_at, qty_on_hand, qty_committed, "
                "qty_backorder, std_cost, avg_cost, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (jan, r["location"], r["bin_number"], now,
                 r["qty_on_hand"], r["qty_committed"], r["qty_backorder"],
                 r["std_cost"], r["avg_cost"], now),
            )
            written += 1
        except Exception:
            errors += 1
    conn.commit()
    _record_run(conn, "item_inventory_snapshot_v2", "nst_inventory_snapshot",
                read, written, errors, f"skipped_no_jan={skipped}")
    return {"step": "item_inventory_snapshot_v2", "read": read, "written": written,
            "errors": errors, "notes": f"skipped_no_jan={skipped}"}


# ============================================================
# Step 8 · item_purchase_history（合并 purchase + purchase_data + purchase_history）
# ============================================================
def step_item_purchase_history(conn) -> dict:
    read = written = errors = skipped = 0
    now = _now()

    # 8a. purchase（NetSuite PO 明细）
    if _table_exists(conn, "purchase"):
        try:
            for r in conn.execute(
                "SELECT * FROM purchase"
            ).fetchall():
                read += 1
                rd = dict(r)
                # 假设 purchase 有 internal_id / qty / ordered_at / po_number 等
                # 通过 item_master_netsuite.internal_id 找 upc=jan
                # 实际字段以 schema 为准 — 这里做尽量保留
                jan = rd.get("upc") or rd.get("jan")
                if not _is_valid_jan(jan):
                    skipped += 1
                    continue
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO item_purchase_history "
                        "(jan, po_number, supplier, qty, unit_cost, total_cost, "
                        "ordered_at, received_at, source, imported_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (jan, rd.get("po_number"), rd.get("supplier"),
                         rd.get("qty") or rd.get("quantity"),
                         rd.get("unit_cost") or rd.get("cost"),
                         rd.get("total_cost"),
                         rd.get("ordered_at") or rd.get("order_date"),
                         rd.get("received_at") or rd.get("receive_date"),
                         "netsuite_po", now),
                    )
                    written += 1
                except Exception:
                    errors += 1
        except Exception as e:
            print(f"[purchase] table query error: {e}")

    # 8b. purchase_data（采购预测 / 计划）
    if _table_exists(conn, "purchase_data"):
        try:
            for r in conn.execute("SELECT * FROM purchase_data").fetchall():
                read += 1
                rd = dict(r)
                jan = rd.get("jan")
                if not _is_valid_jan(jan):
                    skipped += 1
                    continue
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO item_purchase_history "
                        "(jan, po_number, supplier, qty, unit_cost, total_cost, "
                        "ordered_at, source, imported_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (jan, rd.get("po_number") or f"PRED-{rd.get('id', '')}",
                         rd.get("supplier"),
                         rd.get("order_lot") or rd.get("qty"),
                         rd.get("unit_cost"),
                         rd.get("total_cost"),
                         rd.get("predicted_at") or rd.get("created_at"),
                         "predict", now),
                    )
                    written += 1
                except Exception:
                    errors += 1
        except Exception as e:
            print(f"[purchase_data] error: {e}")

    # 8c. purchase_history（历史入库）
    if _table_exists(conn, "purchase_history"):
        try:
            for r in conn.execute("SELECT * FROM purchase_history").fetchall():
                read += 1
                rd = dict(r)
                jan = rd.get("jan")
                if not _is_valid_jan(jan):
                    skipped += 1
                    continue
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO item_purchase_history "
                        "(jan, po_number, supplier, qty, unit_cost, total_cost, "
                        "ordered_at, received_at, source, imported_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (jan, rd.get("po_number"), rd.get("supplier"),
                         rd.get("qty"), rd.get("unit_cost"), rd.get("total_cost"),
                         rd.get("ordered_at"), rd.get("received_at"),
                         "history", now),
                    )
                    written += 1
                except Exception:
                    errors += 1
        except Exception as e:
            print(f"[purchase_history] error: {e}")

    conn.commit()
    _record_run(conn, "item_purchase_history",
                "purchase+purchase_data+purchase_history",
                read, written, errors, f"skipped_no_jan={skipped}")
    return {"step": "item_purchase_history", "read": read, "written": written,
            "errors": errors, "notes": f"skipped_no_jan={skipped}"}


# ============================================================
# Step 9 · item_cost_history（从 std_cost_history 转换）
# ============================================================
def step_item_cost_history(conn) -> dict:
    if not _table_exists(conn, "std_cost_history"):
        return {"step": "item_cost_history", "read": 0, "written": 0, "errors": 0,
                "notes": "std_cost_history not exist"}
    read = written = errors = skipped = 0
    for r in conn.execute("SELECT * FROM std_cost_history").fetchall():
        read += 1
        rd = dict(r)
        jan = rd.get("jan") or rd.get("upc")
        if not _is_valid_jan(jan):
            skipped += 1
            continue
        try:
            conn.execute(
                "INSERT INTO item_cost_history "
                "(jan, std_cost, avg_cost, changed_by, changed_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (jan, rd.get("std_cost"), rd.get("avg_cost"),
                 rd.get("changed_by"),
                 rd.get("changed_at") or rd.get("created_at") or _now(),
                 rd.get("reason")),
            )
            written += 1
        except Exception:
            errors += 1
    conn.commit()
    _record_run(conn, "item_cost_history", "std_cost_history",
                read, written, errors, f"skipped_no_jan={skipped}")
    return {"step": "item_cost_history", "read": read, "written": written,
            "errors": errors}


# ============================================================
# 编排
# ============================================================
RUN_STEPS: list[tuple[str, Callable[[Any], dict]]] = [
    ("market_segment",            step_market_segment),
    ("item_v2",                   step_item_v2),
    ("shop",                      step_shop),
    ("shop_monthly",              step_shop_monthly),
    ("shop_sales",                step_shop_sales),
    ("item_sales_history",        step_item_sales_history),
    ("item_inventory_snapshot_v2", step_item_inventory),
    ("item_purchase_history",     step_item_purchase_history),
    ("item_cost_history",         step_item_cost_history),
]


def run_all(conn=None, *, only: list[str] | None = None) -> list[dict]:
    """跑全套 ETL；only 可指定子集。"""
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    results = []
    for name, fn in RUN_STEPS:
        if only and name not in only:
            continue
        print(f"==> {name} ...")
        try:
            r = fn(conn)
            results.append(r)
            print(f"    read={r['read']} written={r['written']} errors={r['errors']} {r.get('notes','')}")
        except Exception as e:
            print(f"    ❌ {name} 失败: {e}")
            results.append({"step": name, "read": 0, "written": 0, "errors": 1,
                            "notes": f"FATAL: {e}"})
    if own_conn:
        try:
            conn.close()
        except Exception:
            pass
    return results


def overview(conn=None) -> dict:
    """对照 row count：旧表 vs 新表。"""
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    pairs = [
        ("item_master_netsuite + item_master + nst_item_summary", ["item_master_netsuite", "item_master", "nst_item_summary"], "item_v2"),
        ("nst_inventory_snapshot",   ["nst_inventory_snapshot"],     "item_inventory_snapshot_v2"),
        ("nst_store_sales+sales_line", ["nst_store_sales", "sales_line"], "shop_sales"),
        ("store_monthly",            ["store_monthly"],              "shop_monthly"),
        ("std_cost_history",         ["std_cost_history"],           "item_cost_history"),
    ]
    out = []
    for label, old_tables, new_table in pairs:
        old_total = sum(_table_count(conn, t) for t in old_tables)
        new_total = _table_count(conn, new_table)
        out.append({"pair": label, "old_count": old_total, "new_count": new_total,
                    "new_table": new_table})
    if own_conn:
        try:
            conn.close()
        except Exception:
            pass
    return {"pairs": out}


# ============================================================
# CLI 入口
# ============================================================
def main() -> int:
    p = argparse.ArgumentParser(description="v2 数据模型 ETL")
    p.add_argument("--all", action="store_true", help="跑全套")
    p.add_argument("--step", action="append",
                   help="只跑指定 step（可多次）；可选名见 RUN_STEPS")
    p.add_argument("--overview", action="store_true",
                   help="只对照 row count，不执行 ETL")
    args = p.parse_args()

    conn = get_connection()
    try:
        if args.overview:
            print(json.dumps(overview(conn), indent=2, ensure_ascii=False))
        elif args.step:
            results = run_all(conn, only=args.step)
            print(json.dumps(results, indent=2, ensure_ascii=False))
        elif args.all:
            results = run_all(conn)
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            p.print_help()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
