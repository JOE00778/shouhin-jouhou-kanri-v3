"""等级判定提案生成（T-016）— generate_proposal + export_csv

v2 决策（2026-05-05）：
- 仓库限定 = JD-物流-千葉（其他仓库不参与等级 / 订货决策）
- 月周转率 = 订货决策核心指标（决定再订货点 + 下单量）
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import List
from datetime import datetime, timezone

from .rules import classify_rank, calc_sales_rank, Rank


# 仓库硬过滤（v2 决策 · 跟 modules/inventory_health/metrics.py 保持一致）
WAREHOUSE_FILTER = "JD-物流-千葉"

# 安全系数（按等级差异化订货）
SAFETY_FACTOR = {'A': 1.5, 'B': 1.0, 'C': 0.5, '停售': 0.0}


def calc_reorder(monthly_sales: float, lead_time_days: int, rank: str) -> dict:
    """订货决策（基于月销量 + 进货周期 + 等级安全系数）

    再订货点（库存低于此就要补货）
    建议下单量
    """
    safety = SAFETY_FACTOR.get(rank, 0.5)
    lead_time_months = (lead_time_days or 30) / 30.0

    reorder_point = monthly_sales * lead_time_months * safety
    suggested_order_qty = monthly_sales * min(lead_time_months, 3) * safety
    return {
        "reorder_point": round(reorder_point, 1),
        "suggested_order_qty": round(suggested_order_qty, 1),
        "safety_factor": safety,
        "lead_time_days": lead_time_days or 30,
    }


def generate_proposal(quarter: str = '2026-Q1', db_path: str = 'data_warehouse/warehouse.db') -> List[dict]:
    """
    生成等级判定建议清单（proposal）

    流程：
    1. 读 nst_store_sales 按 SKU 聚合（販売数量 × 単価 = 売上、平均粗利率）
    2. 读 nst_inventory_snapshot 取 取扱区分（handling_status）
    3. 跑 calc_sales_rank → 拿 rank_pct
    4. 每 SKU 调 classify_rank 拿 new_rank
    5. 跟现有 item_master_netsuite.rank（旧档）对比 → 输出建议清单

    Args:
        quarter: e.g. '2026-Q1'
        db_path: SQLite DB path

    Returns:
        [{
            'sku': str,
            'name': str,
            'old_rank': str,
            'new_rank': Rank,
            'sales': float,
            'margin': float,
            'rank_pct': float,
            'netsuite_status': str,
            'acknowledged_action': str | None
        }, ...]
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # ========================================================
        # SKU 集合 + 销售聚合 + 取扱区分 全部从 sales_line 拉
        # Boss 决定: 商品等级判定 与库存无关, 完全基于销售数据
        # ========================================================
        sales_data = conn.execute("""
            SELECT
                item_code,
                MIN(display_name) as display_name,
                MIN(handling_status) as handling_status,
                COALESCE(SUM(revenue), 0) as total_revenue,
                COALESCE(AVG(gross_margin), 0) as avg_margin,
                COALESCE(SUM(qty_sold), 0) as total_qty
            FROM sales_line
            WHERE source = 'asean_monthly'
            GROUP BY item_code
        """).fetchall()

        if not sales_data:
            return []
        sales_data = [dict(r) for r in sales_data]

        # 2. 构建 SKU -> 销售额映射 + 计算 rank_pct
        sku_to_sales = {row['item_code']: row['total_revenue'] for row in sales_data}
        rank_pcts = calc_sales_rank(sku_to_sales)

        # 3. status_map 直接来自销售表 handling_status (不再读库存表)
        status_map = {row['item_code']: row['handling_status'] or '取扱中' for row in sales_data}

        # 4. qty_map 库存量 (仅用于 reorder 订货建议字段, 等级判定不依赖)
        # 库存表可空 → qty 默认 0, 不影响等级判定
        qty_map = {}
        try:
            inv_data = conn.execute("""
                SELECT item_code, SUM(qty_on_hand) as qty
                FROM inventory_snapshot WHERE location = ?
                GROUP BY item_code
            """, (WAREHOUSE_FILTER,)).fetchall()
            if not inv_data:
                inv_data = conn.execute("""
                    SELECT item_code, SUM(qty_on_hand) as qty
                    FROM nst_inventory_snapshot WHERE location = ?
                    GROUP BY item_code
                """, (WAREHOUSE_FILTER,)).fetchall()
            qty_map = {row['item_code']: row['qty'] or 0 for row in inv_data}
        except Exception:
            pass

        # 4. 现有 rank（item_master_netsuite）
        old_rank_map = {row['item_code']: row['rank']
                        for row in conn.execute("SELECT upc as item_code, rank FROM item_master_netsuite").fetchall()}

        # 5. 进货周期（用于订货公式）
        lead_time_map = {row['jan']: row['lead_time_days']
                         for row in conn.execute("SELECT jan, lead_time_days FROM supply_cycle").fetchall()}

        # 6. 生成 proposal（含订货建议）
        proposals = []
        for row in sales_data:
            item_code = row['item_code']
            name = row['display_name'] or item_code
            sales = row['total_revenue']
            margin = row['avg_margin'] or 0
            monthly_qty_sold = row['total_qty'] or 0
            qty_on_hand = qty_map.get(item_code, 0)

            netsuite_status = status_map.get(item_code, '取扱中')
            rank_pct = rank_pcts.get(item_code, 1.0)

            new_rank = classify_rank({
                'netsuite_status': netsuite_status,
                'acknowledged_action': None,
                'sales_amount_rank_pct': rank_pct,
                'gross_margin_rate': margin,
            })
            old_rank = old_rank_map.get(item_code, 'NEW')

            # 等级波动标记（升 / 降 / 维持）
            rank_order = {'A': 4, 'Aランク': 4, 'Bランク': 3, 'B': 3,
                          'Cランク': 2, 'C': 2, 'NEW': 1, '停售': 0,
                          '取扱中止': 0, 'メーカー取扱中止': 0}
            old_score = rank_order.get(str(old_rank), 1)
            new_score = rank_order.get(new_rank, 2)
            if new_score > old_score:
                trend = "⬆️ 升级"
            elif new_score < old_score:
                trend = "⬇️ 降级"
            else:
                trend = "➡️ 维持"

            # 订货建议（基于月销量 × 进货周期 × 等级安全系数）
            lead_time_days = lead_time_map.get(item_code)
            reorder = calc_reorder(monthly_qty_sold, lead_time_days, new_rank)

            proposals.append({
                'sku': item_code,
                'name': name,
                'old_rank': old_rank,
                'new_rank': new_rank,
                'trend': trend,
                'sales': sales,
                'margin': margin,
                'rank_pct': rank_pct,
                'netsuite_status': netsuite_status,
                'monthly_qty_sold': monthly_qty_sold,
                'qty_on_hand': qty_on_hand,
                'reorder_point': reorder['reorder_point'],
                'suggested_order_qty': reorder['suggested_order_qty'],
                'lead_time_days': reorder['lead_time_days'],
                'need_reorder': qty_on_hand < reorder['reorder_point'],
                'acknowledged_action': None,
            })

        return proposals

    finally:
        conn.close()


def export_csv(proposals: List[dict], path: str | Path) -> None:
    """
    导出 NetSuite Item Import 格式 CSV

    Args:
        proposals: generate_proposal 的输出
        path: 输出文件路径
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        'item_code',
        'display_name',
        'old_rank',
        'new_rank',
        'total_revenue',
        'avg_margin_rate',
        'sales_rank_pct',
        'netsuite_status',
    ]

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for p in proposals:
            writer.writerow({
                'item_code': p['sku'],
                'display_name': p['name'],
                'old_rank': p['old_rank'],
                'new_rank': p['new_rank'],
                'total_revenue': round(p['sales'], 2),
                'avg_margin_rate': f"{p['margin']*100:.1f}%",
                'sales_rank_pct': f"{p['rank_pct']*100:.1f}%",
                'netsuite_status': p['netsuite_status'],
            })
