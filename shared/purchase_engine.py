"""自動発注 計算エンジン.

入力:
  - supplier_quote テーブル (仕入先 × JAN の報価, zone 分類付き)
  - shop_sales テーブル (source='asean_monthly' = 【ASEAN】店舗別売上 集計専用 = 月次 SKU 販売数量)
  - item_v2 (display_name / maker / rank 補完用)

出力: 発注推奨 DataFrame
  jan / display_name / maker / rank / m1..m3(月販) / avg_monthly / trend / trend_factor
  / suggested_qty / supplier_name / zone / zone_rank / unit_price / effective_price
  / lot_size / order_months / lead_time_text / line_cost / supplier_total / meets_min_order
  / status / reason

zone 優先 (Boss 2026-05-11):
  JD_DIRECT(1) > BENTEN_TRANSIT(2, +3%) > EMERGENCY(3) > PREPAID(4) > OTHER(9)
"""
from __future__ import annotations

import math
import re

import pandas as pd

# 弁天経由 = JD への中継費を +3% で原価に上乗せ (比価のみ; 実発注額は原単価)
ZONE_MARKUP = {
    "JD_DIRECT": 1.00,
    "BENTEN_TRANSIT": 1.03,
    "EMERGENCY": 1.00,
    "PREPAID": 1.00,
    "OTHER": 1.00,
}
DEFAULT_TREND_FACTORS = {"up": 1.2, "flat": 1.0, "down": 0.7}


def _parse_lead_days(text: str | None) -> int:
    """納期テキスト → 日数 (おおよそ)。'2週間'→14, '4/5週間'→35, '1週間'→7。"""
    if not text:
        return 14
    s = str(text)
    # 「N週間」「N/M週間」(後者は大きい方)
    weeks = [int(x) for x in re.findall(r"(\d+)\s*週", s)]
    if weeks:
        return max(weeks) * 7
    days = [int(x) for x in re.findall(r"(\d+)\s*日", s)]
    if days:
        return max(days)
    months = [int(x) for x in re.findall(r"(\d+)\s*[ヶカか]?月", s)]
    if months:
        return max(months) * 30
    return 14


def _order_months_from_lead(lead_days: int, safety_months: float = 1.0) -> float:
    """納期(日) → 何ヶ月分まとめて発注するか。納期カバー + 安全在庫。"""
    return math.ceil(lead_days / 30) + safety_months


def _classify_trend(months: list[float]) -> str:
    """直近の月販リスト(古→新) からトレンド判定。"""
    vals = [v for v in months if v is not None]
    if len(vals) < 2:
        return "flat"
    avg = sum(vals) / len(vals)
    last = vals[-1]
    # 単調増加 + 直近が平均の 1.2 倍以上
    if all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1)) and last >= avg * 1.2:
        return "up"
    if all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)) and last <= avg * 0.8:
        return "down"
    return "flat"


# 月販データソース優先順位 (Boss 2026-05-11):
#   1. 'export_item'  = 【輸出】アイテム別売上（概要）_JO  ← 綜合性 (全輸出商品, 全渠道月販)
#   2. 'asean_monthly' = 【ASEAN】店舗別売上 集計専用       ← ASEAN 局部
SALES_SOURCE_PRIORITY = ["export_item", "asean_monthly"]


def _load_monthly_sales(conn, months: int = 3, sales_source: str = "auto") -> tuple[pd.DataFrame, str]:
    """shop_sales から JAN × period_start の月販を取得。

    Args:
        sales_source: 'auto' = export_item を優先, なければ asean_monthly /
                      または 'export_item' / 'asean_monthly' を明示指定
    戻り値: (DataFrame[jan, period_start, qty], 実際に使った source)
    最新 `months` 期間分のみ。
    """
    if sales_source == "auto":
        candidates = SALES_SOURCE_PRIORITY
    else:
        candidates = [sales_source]
    for src in candidates:
        rows = conn.execute(
            "SELECT jan, period_start, SUM(qty_sold) AS qty "
            "FROM shop_sales WHERE source = ? AND qty_sold IS NOT NULL "
            "GROUP BY jan, period_start", (src,)
        ).fetchall()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            periods = sorted(df["period_start"].dropna().unique())[-months:]
            df = df[df["period_start"].isin(periods)].copy()
            df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
            return df, src
    return pd.DataFrame(columns=["jan", "period_start", "qty"]), (candidates[0] if candidates else "")


def compute_recommendations(
    conn,
    *,
    months: int = 3,
    safety_months: float = 1.0,
    trend_factors: dict | None = None,
    fixed_order_months: float | None = None,
    sales_source: str = "auto",
) -> pd.DataFrame:
    """発注推奨を計算。

    Args:
        months: 月販トレンドに使う直近期間数
        safety_months: 納期カバーに上乗せする安全在庫(月)
        trend_factors: {'up':1.2,'flat':1.0,'down':0.7}
        fixed_order_months: 指定すると納期補正を無視し全 SKU この月数で発注
        sales_source: 'auto' / 'export_item' / 'asean_monthly'

    DataFrame.attrs['sales_source'] に実際使った source 名を入れる。
    """
    tf = trend_factors or DEFAULT_TREND_FACTORS

    # --- 月販 ---
    sales, used_source = _load_monthly_sales(conn, months=months, sales_source=sales_source)
    periods = sorted(sales["period_start"].dropna().unique()) if not sales.empty else []
    sales_pivot: dict[str, dict[str, float]] = {}
    for _, r in sales.iterrows():
        sales_pivot.setdefault(r["jan"], {})[r["period_start"]] = float(r["qty"])

    # --- 商品マスタ (display_name / maker / rank) ---
    item_map: dict[str, dict] = {}
    for r in conn.execute("SELECT jan, display_name, maker, rank FROM item_v2 WHERE jan IS NOT NULL").fetchall():
        item_map[r["jan"]] = {"display_name": r["display_name"], "maker": r["maker"] or "(不明)", "rank": r["rank"]}

    # --- 報価 ---
    quotes: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT supplier_name, jan, display_name, unit_price, lot_size, case_qty, "
        "min_order_amount, order_condition, lead_time_text, zone, zone_rank, nst_supplier_code "
        "FROM supplier_quote WHERE unit_price IS NOT NULL AND unit_price > 0"
    ).fetchall():
        quotes.setdefault(r["jan"], []).append(dict(r))

    # --- SKU 毎に暫定割り当て (zone 優先 → effective_price 最安) ---
    rows: list[dict] = []
    for jan, qlist in quotes.items():
        msales = sales_pivot.get(jan, {})
        m_seq = [msales.get(p, 0.0) for p in periods]   # 古→新
        avg_monthly = (sum(m_seq) / len(m_seq)) if m_seq else 0.0
        latest_monthly = m_seq[-1] if m_seq else 0.0
        # 直近の販売がゼロなら発注しない
        if avg_monthly <= 0 and latest_monthly <= 0:
            continue
        base_monthly = max(avg_monthly, latest_monthly)  # 保守的に大きい方
        trend = _classify_trend(m_seq)
        tfac = tf.get(trend, 1.0)

        # zone 優先 → effective_price でソート
        for q in qlist:
            q["_eff"] = q["unit_price"] * ZONE_MARKUP.get(q["zone"], 1.0)
        qlist_sorted = sorted(qlist, key=lambda q: (q["zone_rank"], q["_eff"]))
        best = qlist_sorted[0]
        lot = best["lot_size"] or 1
        lead_days = _parse_lead_days(best["lead_time_text"])
        order_months = fixed_order_months if fixed_order_months else _order_months_from_lead(lead_days, safety_months)
        raw_qty = base_monthly * tfac * order_months
        suggested_qty = math.ceil(raw_qty / lot) * lot if raw_qty > 0 else 0
        if suggested_qty <= 0:
            continue
        markup = ZONE_MARKUP.get(best["zone"], 1.0)
        line_cost = round(suggested_qty * best["unit_price"] * markup)

        meta = item_map.get(jan, {})
        rows.append({
            "jan": jan,
            "display_name": meta.get("display_name") or best.get("display_name") or "",
            "maker": meta.get("maker", "(不明)"),
            "rank": meta.get("rank"),
            **{f"m{i+1}": m_seq[i] if i < len(m_seq) else None for i in range(months)},
            "avg_monthly": round(avg_monthly, 1),
            "latest_monthly": latest_monthly,
            "trend": trend,
            "trend_factor": tfac,
            "order_months": order_months,
            "lead_time_text": best["lead_time_text"],
            "supplier_name": best["supplier_name"],
            "zone": best["zone"],
            "zone_rank": best["zone_rank"],
            "nst_supplier_code": best["nst_supplier_code"],
            "unit_price": best["unit_price"],
            "effective_price": round(best["_eff"]),
            "lot_size": lot,
            "suggested_qty": suggested_qty,
            "line_cost": line_cost,
            "min_order_amount": best["min_order_amount"] or 0,
            "order_condition": best["order_condition"],
            "n_alt_suppliers": len(qlist),
            "alt_suppliers": " / ".join(f"{q['supplier_name']}:{q['unit_price']}" for q in qlist_sorted[1:6]),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        df.attrs["sales_source"] = used_source
        df.attrs["periods"] = periods
        return df

    # --- 仕入先単位の合算 → 最低受注額判定 ---
    sup_total = df.groupby("supplier_name")["line_cost"].sum()
    df["supplier_total"] = df["supplier_name"].map(sup_total)
    df["meets_min_order"] = df.apply(
        lambda r: (r["min_order_amount"] == 0) or (r["supplier_total"] >= r["min_order_amount"]),
        axis=1,
    )
    df["status"] = df["meets_min_order"].map({True: "recommended", False: "deferred_min_order"})

    def _reason(r):
        z = {"JD_DIRECT": "JD直送", "BENTEN_TRANSIT": "弁天経由+3%", "EMERGENCY": "応急", "PREPAID": "前払い", "OTHER": "他"}.get(r["zone"], r["zone"])
        base = f"{z}・単価{r['unit_price']}・{r['trend']}×{r['trend_factor']}・{r['order_months']:.0f}ヶ月分"
        if not r["meets_min_order"]:
            base += f"・⚠️最低受注¥{r['min_order_amount']:,}未達(現¥{r['supplier_total']:,.0f})"
        return base
    df["reason"] = df.apply(_reason, axis=1)

    df = df.sort_values(["zone_rank", "supplier_name", "line_cost"], ascending=[True, True, False]).reset_index(drop=True)
    df.attrs["sales_source"] = used_source
    df.attrs["periods"] = periods
    return df
