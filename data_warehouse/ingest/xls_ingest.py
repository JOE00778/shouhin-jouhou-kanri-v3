"""6 个 NetSuite 导出 .xls 的 ingestor（函数式，每个文件一个函数）。

设计：
- 每个函数接受 (path, conn)，返回 {run_id, total, inserted, errors}
- 用 shared.xml_xls 解析 SpreadsheetML
- 共用 _start_run / _finalize_run / _record_error 落审计

涉及的表：
- inventory_snapshot — 库存数据（含 std_cost + avg_cost，喂 #1 成本同步）
- sales_line — 销售明细（4 类销售导出共用，source 字段区分）
- inventory_turnover — 库存周转率
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from shared.filters import ALLOWED_INVENTORY_LOCATIONS
from shared.xml_xls import iter_rows, parse_to_dicts


# ============================================================
# 通用工具
# ============================================================
def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _to_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Audit helpers（共用）
# ============================================================
def _start_run(conn: sqlite3.Connection, ingestor: str, source_file: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO _ingest_runs (ingestor, source_file, total_rows, inserted, updated, errors, run_at)
        VALUES (?, ?, 0, 0, 0, 0, ?)
        """,
        (ingestor, source_file, _now_iso()),
    )
    rid = cursor.lastrowid
    if rid is None:
        raise RuntimeError("无法获取 run_id")
    return rid


def _finalize_run(
    conn: sqlite3.Connection, run_id: int, *, total: int, inserted: int, errors: int
) -> None:
    conn.execute(
        "UPDATE _ingest_runs SET total_rows=?, inserted=?, errors=? WHERE run_id=?",
        (total, inserted, errors, run_id),
    )
    conn.commit()


def _record_error(
    conn: sqlite3.Connection, run_id: int, row_number: int, message: str, raw_row: dict
) -> None:
    conn.execute(
        "INSERT INTO _ingest_errors (run_id, row_number, error_message, raw_row) VALUES (?, ?, ?, ?)",
        (run_id, row_number, message, json.dumps(raw_row, ensure_ascii=False)),
    )


# ============================================================
# Period 解析（从 NetSuite 报表第 3 行 "2026年04月01日 - 2026年04月30日"）
# ============================================================
_PERIOD_PATTERN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*-\s*(\d{4})年(\d{1,2})月(\d{1,2})日")


def _extract_period(path: Path) -> tuple[str, str]:
    """从 NetSuite 报表 preamble 第 3 行提取期间。失败返回 ('', '')."""
    rows = list(iter_rows(path))
    for row in rows[:8]:
        for cell in row:
            if cell:
                m = _PERIOD_PATTERN.search(str(cell))
                if m:
                    y1, m1, d1, y2, m2, d2 = m.groups()
                    return (
                        f"{y1}-{int(m1):02d}-{int(d1):02d}",
                        f"{y2}-{int(m2):02d}-{int(d2):02d}",
                    )
    return ("", "")


# ============================================================
# Ingestor 1：inventory_snapshot（FB全倉庫通常在庫数残数検索結果）
# ============================================================
def ingest_inventory_snapshot(
    path: Path, conn: sqlite3.Connection, *, source_name: str | None = None
) -> dict:
    """喂 inventory_snapshot 表。每次导入用「文件 mtime」作为 snapshot_at。"""
    path = Path(path)
    source_name = source_name or path.name
    snapshot_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()

    run_id = _start_run(conn, "inventory_snapshot", source_name)
    inserted = 0
    errors = 0

    rows = parse_to_dicts(path, header_row=0)
    sql = """
        INSERT OR REPLACE INTO inventory_snapshot (
            internal_id, item_code, upc, display_name, status, bin_number, location,
            handling_status, qty_on_hand, qty_committed, qty_backorder,
            std_cost, total_amount, avg_cost, owner, department,
            snapshot_at, source_file, imported_at
        ) VALUES (
            :internal_id, :item_code, :upc, :display_name, :status, :bin_number, :location,
            :handling_status, :qty_on_hand, :qty_committed, :qty_backorder,
            :std_cost, :total_amount, :avg_cost, :owner, :department,
            :snapshot_at, :source_file, :imported_at
        )
    """
    now = _now_iso()
    skipped_other_loc = 0
    for n, raw in enumerate(rows, start=1):
        try:
            payload = {
                "internal_id": _to_str(raw.get("内部ID")),
                "item_code": _to_str(raw.get("アイテム")),
                "upc": _to_str(raw.get("UPCコード")),
                "display_name": _to_str(raw.get("表示名")),
                "status": _to_str(raw.get("ステータス")),
                "bin_number": _to_str(raw.get("保管棚番号")),
                "location": _to_str(raw.get("場所")),
                "handling_status": _to_str(raw.get("取扱区分")),
                "qty_on_hand": _to_float(raw.get("手持合計")),
                "qty_committed": _to_float(raw.get("確保済合計")),
                "qty_backorder": _to_float(raw.get("バック・オーダー合計")),
                "std_cost": _to_float(raw.get("アイテム定義原価")),
                "total_amount": _to_float(raw.get("合計金額")),
                "avg_cost": _to_float(raw.get("平均原価合計")),
                "owner": _to_str(raw.get("商品担当者")),
                "department": _to_str(raw.get("部門")),
                "snapshot_at": snapshot_at,
                "source_file": source_name,
                "imported_at": now,
            }
            if not payload["internal_id"] or not payload["item_code"]:
                continue  # 空行 或 「総合計」/「合計」等汇总行
            if payload["internal_id"] in ("総合計", "合計"):
                continue
            # 仓库白名单：只保留 JD-物流-千葉 + 弁天倉庫
            if payload["location"] not in ALLOWED_INVENTORY_LOCATIONS:
                skipped_other_loc += 1
                continue
            conn.execute(sql, payload)
            inserted += 1
        except Exception as e:
            errors += 1
            _record_error(conn, run_id, n, str(e), raw)

    _finalize_run(conn, run_id, total=len(rows), inserted=inserted, errors=errors)
    return {"run_id": run_id, "total": len(rows), "inserted": inserted, "errors": errors}


# ============================================================
# Ingestor 2-5：sales_line（4 个销售导出，source 不同）
# ============================================================
_STORE_PREFIXES = ("Shopee", "Lazada", "Tokopedia")
_STORE_KEYWORDS = ("COUPANG", "Coupang", "coupang")


def _is_store_group_header(item_code: str | None, display_name: str | None) -> bool:
    """判断一行是不是「店铺分组标题」（item_code 是店铺名，display_name 空）。"""
    if not item_code or display_name:
        return False
    s = item_code.strip()
    if s.startswith(_STORE_PREFIXES):
        return True
    return any(k in s for k in _STORE_KEYWORDS)


def _ingest_sales(
    path: Path,
    conn: sqlite3.Connection,
    *,
    source: str,
    has_store_column: bool,        # CSV 列里直接有 FB_店舗
    has_store_groups: bool,         # CSV 用「店铺标题行 + SKU 明细行」分组结构
    has_rank: bool,
    has_purchase_price: bool,
    source_name: str | None = None,
) -> dict:
    """通用销售导入。

    支持 3 种 store 形态：
    - has_store_column=True  : 每行直接有 FB_店舗 列（asean_monthly / export_store）
    - has_store_groups=True  : NetSuite 分组报表，需有状态地从分组标题行提取 store（asean_daily）
    - 都 False                : 纯 SKU 维度，无店铺信息（export_item）
    """
    path = Path(path)
    source_name = source_name or path.name
    period_start, period_end = _extract_period(path)

    run_id = _start_run(conn, f"sales_line.{source}", source_name)
    inserted = 0
    errors = 0

    rows = parse_to_dicts(path, header_row=6)
    sql = """
        INSERT INTO sales_line (
            store, item_code, upc, display_name, handling_status, rank,
            qty_sold, unit_purchase_price, revenue, defined_cost, gross_profit, gross_margin,
            period_start, period_end, source, source_file, imported_at
        ) VALUES (
            :store, :item_code, :upc, :display_name, :handling_status, :rank,
            :qty_sold, :unit_purchase_price, :revenue, :defined_cost, :gross_profit, :gross_margin,
            :period_start, :period_end, :source, :source_file, :imported_at
        )
    """
    now = _now_iso()
    current_store: str | None = None  # for stateful group parsing

    for n, raw in enumerate(rows, start=1):
        try:
            item_code = _to_str(raw.get("アイテム"))
            display_name = _to_str(raw.get("表示名"))

            # 处理店铺分组标题
            if _is_store_group_header(item_code, display_name):
                if has_store_groups:
                    current_store = item_code
                continue  # 不论哪种模式，分组标题行都不入库

            if not item_code:
                continue

            # 决定 store 值
            if has_store_column:
                store = _to_str(raw.get("FB_店舗"))
            elif has_store_groups:
                store = current_store
            else:
                store = None

            payload = {
                "store": store,
                "item_code": item_code,
                "upc": _to_str(raw.get("UPCコード")) or item_code,
                "display_name": display_name,
                "handling_status": _to_str(
                    raw.get("取扱区分") or raw.get("取扱区分: 名前") or raw.get("商品取扱区分: 名前")
                ),
                "rank": _to_str(raw.get("商品ランク")) if has_rank else None,
                "qty_sold": _to_float(raw.get("販売数量")),
                "unit_purchase_price": _to_float(raw.get("購入価格（単価）")) if has_purchase_price else None,
                "revenue": _to_float(raw.get("総収益")),
                "defined_cost": _to_float(raw.get("定義原価")),
                "gross_profit": _to_float(raw.get("粗利")),
                "gross_margin": _to_float(raw.get("粗利率")),
                "period_start": period_start,
                "period_end": period_end,
                "source": source,
                "source_file": source_name,
                "imported_at": now,
            }
            conn.execute(sql, payload)
            inserted += 1
        except Exception as e:
            errors += 1
            _record_error(conn, run_id, n, str(e), raw)

    _finalize_run(conn, run_id, total=len(rows), inserted=inserted, errors=errors)
    return {
        "run_id": run_id,
        "total": len(rows),
        "inserted": inserted,
        "errors": errors,
        "period_start": period_start,
        "period_end": period_end,
    }


def ingest_sales_asean_monthly(path, conn, **kw):
    """ASEAN 店舗別売上 集計専用（每行有 FB_店舗 列）"""
    return _ingest_sales(
        path, conn, source="asean_monthly",
        has_store_column=True, has_store_groups=False,
        has_rank=False, has_purchase_price=False, **kw,
    )


def ingest_sales_asean_daily(path, conn, **kw):
    """ASEAN 店舗別売上（前日）（NetSuite 分组报表，店铺标题行 + SKU 明细行）"""
    return _ingest_sales(
        path, conn, source="asean_daily",
        has_store_column=False, has_store_groups=True,
        has_rank=False, has_purchase_price=False, **kw,
    )


def ingest_sales_export_item(path, conn, **kw):
    """輸出 アイテム別売上（概要）（纯 SKU 维度，带 rank + 单价，无店铺）"""
    return _ingest_sales(
        path, conn, source="export_item",
        has_store_column=False, has_store_groups=False,
        has_rank=True, has_purchase_price=True, **kw,
    )


def ingest_sales_export_store(path, conn, **kw):
    """輸出 店舗別売上（每行有 FB_店舗 列 + 商品ランク）"""
    return _ingest_sales(
        path, conn, source="export_store",
        has_store_column=True, has_store_groups=False,
        has_rank=True, has_purchase_price=False, **kw,
    )


# ============================================================
# Ingestor 6：inventory_turnover（在庫回転率）
# ============================================================
def ingest_inventory_turnover(
    path, conn, *, source_name: str | None = None
) -> dict:
    """在庫回転率：(item_code, period) UPSERT。"""
    path = Path(path)
    source_name = source_name or path.name
    period_start, period_end = _extract_period(path)

    run_id = _start_run(conn, "inventory_turnover", source_name)
    inserted = 0
    errors = 0

    rows = parse_to_dicts(path, header_row=6)
    sql = """
        INSERT OR REPLACE INTO inventory_turnover (
            item_code, description, cost, avg_value, turnover_rate, avg_days_on_hand,
            period_start, period_end, source_file, imported_at
        ) VALUES (
            :item_code, :description, :cost, :avg_value, :turnover_rate, :avg_days_on_hand,
            :period_start, :period_end, :source_file, :imported_at
        )
    """
    now = _now_iso()
    for n, raw in enumerate(rows, start=1):
        try:
            item_code = _to_str(raw.get("アイテム"))
            if not item_code or item_code in ("在庫アイテム", "合計", "総合計"):
                continue  # 跳过分组标题行
            payload = {
                "item_code": item_code,
                "description": _to_str(raw.get("説明")),
                "cost": _to_float(raw.get("原価")),
                "avg_value": _to_float(raw.get("平均値")),
                "turnover_rate": _to_float(raw.get("回転率")),
                "avg_days_on_hand": _to_float(raw.get("平均手持日数")),
                "period_start": period_start,
                "period_end": period_end,
                "source_file": source_name,
                "imported_at": now,
            }
            conn.execute(sql, payload)
            inserted += 1
        except Exception as e:
            errors += 1
            _record_error(conn, run_id, n, str(e), raw)

    _finalize_run(conn, run_id, total=len(rows), inserted=inserted, errors=errors)
    return {
        "run_id": run_id,
        "total": len(rows),
        "inserted": inserted,
        "errors": errors,
        "period_start": period_start,
        "period_end": period_end,
    }


# ============================================================
# 自动派发：根据文件名启发式选择 ingestor
# ============================================================
INGESTOR_REGISTRY: dict[str, callable] = {
    "inventory": ingest_inventory_snapshot,
    "asean_monthly": ingest_sales_asean_monthly,
    "asean_daily": ingest_sales_asean_daily,
    "export_item": ingest_sales_export_item,
    "export_store": ingest_sales_export_store,
    "turnover": ingest_inventory_turnover,
}


def detect_ingestor(filename: str) -> str | None:
    """根据文件名启发式判断使用哪个 ingestor。"""
    n = filename
    if "在庫数残数" in n or "通常在庫" in n:
        return "inventory"
    if "在庫回転率" in n or "回転率" in n:
        return "turnover"
    if "ASEAN" in n and "前日" in n:
        return "asean_daily"
    if "ASEAN" in n and "店舗別" in n:
        return "asean_monthly"
    if "輸出" in n and "アイテム別" in n:
        return "export_item"
    if "輸出" in n and "店舗別" in n:
        return "export_store"
    return None
