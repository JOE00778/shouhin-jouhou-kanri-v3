"""运营建议批量生成（基于 cross_ratio_monthly + rank_classifier 输出）"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from .rules import advise


def generate_advice(year_month: str = "2026-04",
                    db_path: str = "data_warehouse/warehouse.db",
                    persist: bool = True) -> List[dict]:
    """跑全 SKU（B/C 档）出运营建议清单"""
    from shared.db import get_connection
    conn = get_connection()

    try:
        # 1. 拉等级（仅 B/C 档需要建议，A/停售跳过）
        from modules.rank_classifier.proposal import generate_proposal
        proposals = generate_proposal('2026-Q1', str(db_path))
        rank_map = {p['sku']: p['new_rank'] for p in proposals}

        # 2. 拉双指标
        rows = conn.execute("""
            SELECT c.sku, c.gross_margin AS margin_pct, c.turnover AS m_turn,
                   c.cross_ratio,
                   MIN(i.display_name) AS name,
                   SUM(i.qty_on_hand) AS qty,
                   AVG(i.std_cost) AS std_cost
            FROM cross_ratio_monthly c
            LEFT JOIN nst_inventory_snapshot i
                ON c.sku = i.item_code AND i.location = 'JD-物流-千葉'
            WHERE c.year_month = ?
            GROUP BY c.sku
        """, (year_month,)).fetchall()

        results = []
        for r in rows:
            sku = r["sku"]
            rank = rank_map.get(sku, "C")
            adv = advise(rank, r["margin_pct"] or 0, r["m_turn"] or 0)

            # 仅输出有建议（含 ✅ 维持）的 B/C 档
            if adv["advice"] == "—":
                continue

            inventory_value = (r["qty"] or 0) * (r["std_cost"] or 0)
            results.append({
                "sku": sku,
                "name": r["name"] or sku,
                "rank": rank,
                "margin_pct": round(r["margin_pct"] or 0, 1),
                "monthly_turnover": round(r["m_turn"] or 0, 3),
                "cross_ratio": round(r["cross_ratio"] or 0, 2),
                "inventory_value": round(inventory_value, 0),
                **adv,
            })

        # 按"重点"优先 + 库存价值降序
        priority_order = {
            "🔥 重点提价": 0, "🔥 重点降价": 0,
            "⬆️ 提价候选": 1, "⚠️ 降价候选": 1,
            "⬇️ 降级候选": 2, "✅ 维持": 3,
        }
        results.sort(key=lambda x: (priority_order.get(x["advice"], 9), -x["inventory_value"]))

        # 持久化到 operation_advice_monthly
        if persist and results:
            now = datetime.now(timezone.utc).isoformat()
            conn.executemany("""
                INSERT OR REPLACE INTO operation_advice_monthly
                (sku, year_month, rank, margin_pct, monthly_turnover, cross_ratio,
                 margin_lv, turnover_lv, advice, reason, inventory_value, calculated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (r["sku"], year_month, r["rank"], r["margin_pct"], r["monthly_turnover"],
                 r["cross_ratio"], r["margin_lv"], r["turnover_lv"], r["advice"],
                 r["reason"], r["inventory_value"], now)
                for r in results
            ])
            conn.commit()

        return results

    finally:
        conn.close()
