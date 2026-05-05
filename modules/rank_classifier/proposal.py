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


def generate_proposal(
    quarter: str = '2026-Q1',
    db_path: str = 'data_warehouse/warehouse.db',
    *,
    period_start: str | None = None,
    period_end: str | None = None,
) -> List[dict]:
    """
    生成等级判定建议清单（proposal）

    Args:
        quarter: 仅作为 metadata 标识 (e.g. 'FY2026-Q1' / '2026-04')
        db_path: SQLite DB path
        period_start / period_end: 'YYYY-MM-DD' 期间过滤
            - 月度: 都传单期间 (例 '2026-04-01' ~ '2026-04-30')
              SQL 用 = 精确匹配 sales_line.period_start/period_end
            - 季度: 传 Q 范围 (例 '2026-03-01' ~ '2026-05-31')
              SQL 用 >= / <= 范围,聚合 3 个月度报表
            - 都不传: 聚合全部 asean_monthly (不分期间, 兼容旧调用)

    Returns:
        list of dicts (含 sku/old_rank/new_rank/sales/margin/rank_pct 等)
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
        # 期间过滤:
        #   - 月度: period_start = X AND period_end = Y (精确匹配单月)
        #   - 季度: period_start >= Q_start AND period_end <= Q_end (3 个月聚合)
        #   - 不传: 聚合所有 asean_monthly (旧行为)
        # ========================================================
        if period_start and period_end:
            # 自动判断单月度 vs 季度: 期间跨度 ≤ 35 天 → 单月精确匹配; 否则范围
            from datetime import date as _date
            try:
                _ds = _date.fromisoformat(period_start)
                _de = _date.fromisoformat(period_end)
                _delta_days = (_de - _ds).days
            except Exception:
                _delta_days = 999
            if _delta_days <= 35:
                # 单月度精确匹配
                where_clause = "WHERE source = 'asean_monthly' AND period_start = ? AND period_end = ?"
                params = (period_start, period_end)
            else:
                # 季度范围 (3 个月聚合)
                where_clause = (
                    "WHERE source = 'asean_monthly' "
                    "AND period_start >= ? AND period_end <= ?"
                )
                params = (period_start, period_end)
        else:
            where_clause = "WHERE source = 'asean_monthly'"
            params = ()
        sales_data = conn.execute(f"""
            SELECT
                item_code,
                MIN(display_name) as display_name,
                MIN(handling_status) as handling_status,
                COALESCE(SUM(revenue), 0) as total_revenue,
                COALESCE(AVG(gross_margin), 0) as avg_margin,
                COALESCE(SUM(qty_sold), 0) as total_qty
            FROM sales_line
            {where_clause}
            GROUP BY item_code
        """, params).fetchall()

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

        # 5b. 3 个月无动销标记 (Boss 新增规则)
        # 窗口 = period_end 前推 3 个月; 若没传 period_end, 默认用 sales_line 中最大 period_end
        no_sales_3m_set: set[str] = set()
        try:
            from datetime import date as _date
            if period_end:
                _ref_end = _date.fromisoformat(period_end)
            else:
                _row = conn.execute(
                    "SELECT MAX(period_end) AS m FROM sales_line WHERE source='asean_monthly'"
                ).fetchone()
                _ref_end = _date.fromisoformat(_row['m']) if _row and _row['m'] else None

            if _ref_end:
                # 前推约 3 个月 (90 天) 起点
                from datetime import timedelta as _td
                _ref_start = (_ref_end - _td(days=90)).isoformat()
                _ref_end_iso = _ref_end.isoformat()
                # 该窗口内有过销售的 SKU
                _active_skus = {
                    r['item_code']
                    for r in conn.execute(
                        """
                        SELECT item_code
                        FROM sales_line
                        WHERE source='asean_monthly'
                          AND period_start >= ? AND period_end <= ?
                        GROUP BY item_code
                        HAVING COALESCE(SUM(qty_sold), 0) > 0
                        """,
                        (_ref_start, _ref_end_iso),
                    ).fetchall()
                }
                # 全 SKU 减去窗口活跃集 = 3 个月无动销
                no_sales_3m_set = {row['item_code'] for row in sales_data} - _active_skus
        except Exception:
            no_sales_3m_set = set()

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
            no_sales_3m = item_code in no_sales_3m_set

            new_rank = classify_rank({
                'netsuite_status': netsuite_status,
                'acknowledged_action': None,
                'sales_amount_rank_pct': rank_pct,
                'gross_margin_rate': margin,
                'no_sales_3m': no_sales_3m,
            })
            old_rank = old_rank_map.get(item_code, 'NEW')

            # 等级波动标记（升 / 降 / 维持）
            rank_order = {'A': 4, 'Aランク': 4, 'Bランク': 3, 'B': 3,
                          'Cランク': 2, 'C': 2, 'NEW': 1,
                          '停售/处理': 0, '停售': 0,
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
                'no_sales_3m': no_sales_3m,
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
