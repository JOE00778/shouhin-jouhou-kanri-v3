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
            store, item_code, upc, display_name, handling_status, maker, rank,
            qty_sold, unit_purchase_price, revenue, defined_cost, gross_profit, gross_margin,
            period_start, period_end, source, source_file, imported_at
        ) VALUES (
            :store, :item_code, :upc, :display_name, :handling_status, :maker, :rank,
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
                "maker": _to_str(raw.get("メーカー名")),  # K 列（ASEAN 集計専用 R7 第 11 列）
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
# Ingestor 7：shopee_orders_raw（订单导出.xlsx Sheet0）
# ============================================================
def ingest_shopee_orders_raw(
    path, conn, *, source_name: str | None = None
) -> dict:
    """订单导出 .xlsx Sheet0 (8 列): 支付币种/单价/发货数量/本地SKU/支付金额/平台/订单号/店铺."""
    import openpyxl
    path = Path(path)
    source_name = source_name or path.name
    run_id = _start_run(conn, "shopee_orders_raw", source_name)

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = list(ws.iter_rows(values_only=True))
    if len(rows_iter) < 2:
        _finalize_run(conn, run_id, total=0, inserted=0, errors=0)
        return {"run_id": run_id, "total": 0, "inserted": 0, "errors": 0,
                "period_start": None, "period_end": None}

    header = [str(h).strip() if h is not None else "" for h in rows_iter[0]]
    # 字段映射 (中文表头 → DB 字段)
    name_map = {
        "支付币种": "currency", "单价": "unit_price", "发货数量": "ship_qty",
        "本地SKU": "local_sku", "支付金额": "payment_amount",
        "平台": "platform", "订单号": "order_no", "店铺": "shop_name",
    }
    col_to_field = {i: name_map[h] for i, h in enumerate(header) if h in name_map}

    sql = """
        INSERT OR REPLACE INTO shopee_orders_raw (
            currency, unit_price, ship_qty, local_sku, payment_amount,
            platform, order_no, shop_name, source_file, imported_at
        ) VALUES (
            :currency, :unit_price, :ship_qty, :local_sku, :payment_amount,
            :platform, :order_no, :shop_name, :source_file, :imported_at
        )
    """
    now = _now_iso()
    inserted = errors = 0
    total = len(rows_iter) - 1
    for n, row in enumerate(rows_iter[1:], start=1):
        try:
            payload = {
                "currency": None, "unit_price": None, "ship_qty": None,
                "local_sku": None, "payment_amount": None,
                "platform": None, "order_no": None, "shop_name": None,
                "source_file": source_name, "imported_at": now,
            }
            for i, field in col_to_field.items():
                if i < len(row):
                    v = row[i]
                    if field == "payment_amount":
                        payload[field] = _to_float(v)
                    else:
                        payload[field] = _to_str(v)
            if not payload["order_no"]:
                continue  # 跳过空行
            conn.execute(sql, payload)
            inserted += 1
        except Exception as e:
            errors += 1
            _record_error(conn, run_id, n, str(e), {"row": str(row)[:200]})

    conn.commit()
    _finalize_run(conn, run_id, total=total, inserted=inserted, errors=errors)
    return {"run_id": run_id, "total": total, "inserted": inserted,
            "errors": errors, "period_start": None, "period_end": None}


# ============================================================
# Ingestor 8：shopee_income_lines（ph.mtkshop.*.income.已拨款.xlsx Income sheet）
# ============================================================
def ingest_shopee_income(
    path, conn, *, source_name: str | None = None
) -> dict:
    """ph.*.income.已拨款.xlsx Income sheet (R6 表头, 46 列)."""
    import openpyxl
    path = Path(path)
    source_name = source_name or path.name
    run_id = _start_run(conn, "shopee_income", source_name)

    wb = openpyxl.load_workbook(str(path), data_only=True)
    if "Income" not in wb.sheetnames:
        _finalize_run(conn, run_id, total=0, inserted=0, errors=0)
        return {"run_id": run_id, "total": 0, "inserted": 0, "errors": 0,
                "period_start": None, "period_end": None}
    ws = wb["Income"]

    # R1: 卖家帐号/付款ID/收款渠道/拨款时间 (header)
    # R2: 值 (mtkshop.ph / 2026-04-01 等)
    # R6: 主表头
    # R7+: detail
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 7:
        _finalize_run(conn, run_id, total=0, inserted=0, errors=0)
        return {"run_id": run_id, "total": 0, "inserted": 0, "errors": 0,
                "period_start": None, "period_end": None}

    # 顶部 meta (R2)
    seller_account = _to_str(rows[1][0]) if len(rows[1]) > 0 else None
    payout_date_top = _to_str(rows[1][3]) if len(rows[1]) > 3 else None
    if payout_date_top:
        # 'YYYY-MM-DD HH:MM:SS' 或 'YYYY-MM-DD'
        payout_date_top = payout_date_top.split(" ")[0].split("T")[0]

    # 表头映射 (中文 → DB 字段)
    header = [str(h).strip() if h is not None else "" for h in rows[5]]  # R6 (0-indexed=5)
    NAME_MAP = {
        "编号": "seq", "订单编号": "order_no", "退款ID": "refund_id",
        "买家帐号": "buyer_account", "订单成立时间": "order_created_at",
        "买家付款方式": "payment_method", "Hot Listing": "hot_listing",
        "买家付款方式详情_1": "payment_method_detail",
        "分期付款计划 （如适用）": "installment_plan",
        "installment rate": "installment_rate",
        "拨款完成日期": "payout_completed_at",
        "商品原价": "gross_price", "商品折扣": "product_discount",
        "退款金額": "refund_amount", "Shopee回扣金额": "shopee_rebate",
        "卖家赞助的优惠券": "seller_voucher",
        "卖家赞助的合资优惠券": "seller_voucher_jv",
        "卖家赞助的 Shopee 币回扣": "seller_shopee_coin",
        "卖家赞助的合资 Shopee 币回扣": "seller_shopee_coin_jv",
        "买家支付运费": "buyer_shipping",
        "Shopee运费补贴": "shopee_shipping_subsidy",
        "卖家支付运费": "seller_shipping",
        "退货运费": "return_shipping",
        "退货给卖家的运费": "return_to_seller_ship",
        "通过运费险计划节省下的运费总额": "shipping_insurance_save",
        "联盟营销方案佣金": "affiliate_commission",
        "佣金": "commission",
        "物流+：海外免退服务-派送失败场景服务费": "fbs_overseas_fail",
        "物流+：海外免退服务-退货退款场景服务费": "fbs_overseas_return",
        "服务费": "service_fee",
        "运费险计划活动服务费": "shipping_insurance_fee",
        "交易手续费": "transaction_fee",
        "FBS Fee": "fbs_fee",
        "拨款金额 (₱)": "payout_amount",
        "优惠码": "promo_code",
        "损失赔偿": "loss_compensation",
        "每个订单的实际总重量": "actual_weight",
        "卖家提供的运费促销": "seller_shipping_promo",
        "物流承运商": "logistics_carrier",
        "物流名称": "logistics_name",
        "退款给买家的现金金额": "refund_cash",
        "退货/退款商品的按比例Shopee币抵消": "prorated_shopee_coin",
        "退货商品的按比例Shopee优惠券抵消": "prorated_shopee_voucher",
        "Pro-rated Bank Payment Channel Promotion  for return refund Items": "prorated_bank_promo",
        "Pro-rated Shopee Payment Channel Promotion  for return refund Items": "prorated_payment_promo",
    }
    col_to_field = {i: NAME_MAP[h] for i, h in enumerate(header) if h in NAME_MAP}
    NUMERIC_FIELDS = {
        "gross_price", "product_discount", "refund_amount", "shopee_rebate",
        "seller_voucher", "seller_voucher_jv", "seller_shopee_coin",
        "seller_shopee_coin_jv", "buyer_shipping", "shopee_shipping_subsidy",
        "seller_shipping", "return_shipping", "return_to_seller_ship",
        "shipping_insurance_save", "affiliate_commission", "commission",
        "fbs_overseas_fail", "fbs_overseas_return", "service_fee",
        "shipping_insurance_fee", "transaction_fee", "fbs_fee",
        "payout_amount", "loss_compensation", "actual_weight",
        "seller_shipping_promo", "refund_cash",
        "prorated_shopee_coin", "prorated_shopee_voucher",
        "prorated_bank_promo", "prorated_payment_promo",
    }
    INT_FIELDS = {"seq"}

    # 所有 DB 字段（按 schema 顺序）
    DB_FIELDS = [
        "seq", "order_no", "refund_id", "buyer_account", "order_created_at",
        "payment_method", "hot_listing", "payment_method_detail",
        "installment_plan", "installment_rate", "payout_completed_at",
        "gross_price", "product_discount", "refund_amount", "shopee_rebate",
        "seller_voucher", "seller_voucher_jv", "seller_shopee_coin",
        "seller_shopee_coin_jv", "buyer_shipping", "shopee_shipping_subsidy",
        "seller_shipping", "return_shipping", "return_to_seller_ship",
        "shipping_insurance_save", "affiliate_commission", "commission",
        "fbs_overseas_fail", "fbs_overseas_return", "service_fee",
        "shipping_insurance_fee", "transaction_fee", "fbs_fee",
        "payout_amount", "promo_code", "loss_compensation", "actual_weight",
        "seller_shipping_promo", "logistics_carrier", "logistics_name",
        "refund_cash", "prorated_shopee_coin", "prorated_shopee_voucher",
        "prorated_bank_promo", "prorated_payment_promo",
        "seller_account", "payout_date", "source_file", "imported_at",
    ]
    placeholders = ",".join(f":{f}" for f in DB_FIELDS)
    cols = ",".join(DB_FIELDS)
    sql = f"INSERT OR REPLACE INTO shopee_income_lines ({cols}) VALUES ({placeholders})"

    now = _now_iso()
    inserted = errors = 0
    detail_rows = rows[6:]  # R7+ (0-indexed=6)
    total = 0
    for n, row in enumerate(detail_rows, start=1):
        try:
            if not row or all(v is None for v in row):
                continue
            payload = {f: None for f in DB_FIELDS}
            payload["seller_account"] = seller_account
            payload["payout_date"] = payout_date_top
            payload["source_file"] = source_name
            payload["imported_at"] = now

            for i, field in col_to_field.items():
                if i < len(row):
                    v = row[i]
                    if field in NUMERIC_FIELDS:
                        payload[field] = _to_float(v)
                    elif field in INT_FIELDS:
                        try:
                            payload[field] = int(v) if v is not None else None
                        except (ValueError, TypeError):
                            payload[field] = None
                    else:
                        payload[field] = _to_str(v)

            if not payload.get("order_no"):
                continue  # 跳过空行 / 小计行
            total += 1
            conn.execute(sql, payload)
            inserted += 1
        except Exception as e:
            errors += 1
            _record_error(conn, run_id, n, str(e), {"row": str(row)[:200]})

    conn.commit()
    _finalize_run(conn, run_id, total=total, inserted=inserted, errors=errors)
    return {"run_id": run_id, "total": total, "inserted": inserted,
            "errors": errors, "period_start": payout_date_top, "period_end": payout_date_top}


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
    "shopee_orders": ingest_shopee_orders_raw,
    "shopee_income": ingest_shopee_income,
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
    # Shopee 财务两份原表
    if "订单导出" in n or "订单导出" in n.lower():
        return "shopee_orders"
    if "income" in n.lower() and ("已拨款" in n or "拨款" in n):
        return "shopee_income"
    if "mtkshop" in n.lower() and "income" in n.lower():
        return "shopee_income"
    return None
