"""自動発注 計算エンジン.

入力:
  - supplier_quote テーブル (仕入先 × JAN の報価, zone 分類付き)
  - shop_sales テーブル (source='export_item' = 【輸出】アイテム別売上（概要）= 月次 SKU 販売数量)
  - item_v2 (display_name / maker / rank / handling_status 補完用)
  - item_inventory_snapshot_v2 (現在庫: 手持 / 確保済 / 注文済)  ← Boss 2026-05-12

出力: 発注推奨 DataFrame
  jan / display_name / maker / rank / m1..mN(月販) / avg_monthly / latest_monthly / trend / trend_factor
  / on_hand / qty_committed / on_order / eff_stock / target_stock / shortfall
  / suggested_qty / supplier_name / zone / zone_rank / nst_supplier_code / unit_price / effective_price
  / lot_size / order_months / lead_time_text / line_cost / supplier_total / meets_min_order
  / status / reason

発注ロジック (Boss 2026-05-12):
  目標在庫 = max(平均月販, 直近月販) × トレンド係数(1.2/1.0/0.7) × (納期カバー月数 + 安全在庫月数)
  有効在庫 = 手持 − 確保済 + 注文済        ← 「注文済」= 発注済で未入荷, 在途扱い (輸送中 列は無視)
  不足 = max(0, 目標在庫 − 有効在庫)
  発注数 = 不足 を lot 倍数に切り上げ
  → 発注数 ≤ 0 なら推奨に出さない

仕入先選定: zone 優先 (JD_DIRECT > BENTEN_TRANSIT > EMERGENCY > PREPAID > OTHER) → 同 zone 内は単価最安
  ※ 弁天倉庫は自社倉庫 → 中継費なし (markup 撤廃, Boss 2026-05-12)

除外: 取扱中止 / メーカー取扱中止 の SKU は発注対象外。
"""
from __future__ import annotations

import math
import re

import pandas as pd

# 弁天は自社倉庫 → 中継費なし (Boss 2026-05-12)。運費モデルは現状なし。
ZONE_MARKUP = {
    "JD_DIRECT": 1.00,
    "BENTEN_TRANSIT": 1.00,
    "EMERGENCY": 1.00,
    "PREPAID": 1.00,
    "OTHER": 1.00,
}
DEFAULT_TREND_FACTORS = {"up": 1.2, "flat": 1.0, "down": 0.7}

# 取扱中止扱いの値 (item_v2.handling_status / rank)
DISCONTINUED_VALUES = {"取扱中止", "メーカー取扱中止", "取扱停止", "廃番", "生産終了"}


def _parse_lead_days(text: str | None) -> int:
    """納期テキスト → 日数 (おおよそ)。'2週間'→14, '4/5週間'→35, '1週間'→7。"""
    if not text:
        return 14
    s = str(text)
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
    if all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1)) and last >= avg * 1.2:
        return "up"
    if all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)) and last <= avg * 0.8:
        return "down"
    return "flat"


# 月販データソース (Boss 2026-05-11):
#   'export_item' = 【輸出】アイテム別売上（概要）_JO  ← 綜合性 (全輸出商品, 全渠道月販)
SALES_SOURCE_PRIORITY = ["export_item"]


def _load_monthly_sales(conn, months: int = 3, sales_source: str = "auto") -> tuple[pd.DataFrame, str]:
    """shop_sales から JAN × period_start の月販を取得 (最新 `months` 期間分)。"""
    candidates = SALES_SOURCE_PRIORITY if sales_source == "auto" else [sales_source]
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


def _load_inventory(conn) -> dict[str, dict]:
    """item_inventory_snapshot_v2 を JAN 単位に集計。

    戻り値: {jan: {'on_hand', 'committed', 'on_order'}}
      on_order = 「注文済」(発注済で未入荷, 在途扱い)。「輸送中」は Boss 指示で無視。
    テーブルが無い / 空でも空 dict を返す (在庫情報なし扱い)。
    """
    inv: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT jan, COALESCE(SUM(qty_on_hand),0) AS oh, "
            "COALESCE(SUM(qty_committed),0) AS cm, COALESCE(SUM(qty_on_order),0) AS oo "
            "FROM item_inventory_snapshot_v2 WHERE jan IS NOT NULL GROUP BY jan"
        ).fetchall()
    except Exception:
        return inv
    for r in rows:
        inv[r["jan"]] = {
            "on_hand": float(r["oh"] or 0),
            "committed": float(r["cm"] or 0),
            "on_order": float(r["oo"] or 0),
        }
    return inv


def _load_items(conn) -> dict[str, dict]:
    """item_v2 → {jan: {display_name, maker, rank, handling_status, discontinued(bool)}}"""
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT jan, display_name, maker, rank, handling_status FROM item_v2 WHERE jan IS NOT NULL"
    ).fetchall():
        hs = (r["handling_status"] or "").strip()
        rk = (r["rank"] or "").strip()
        out[r["jan"]] = {
            "display_name": r["display_name"],
            "maker": r["maker"] or "(不明)",
            "rank": r["rank"],
            "handling_status": r["handling_status"],
            "discontinued": (hs in DISCONTINUED_VALUES) or (rk in DISCONTINUED_VALUES),
        }
    return out


def compute_recommendations(
    conn,
    *,
    months: int = 3,
    safety_months: float = 1.0,
    trend_factors: dict | None = None,
    fixed_order_months: float | None = None,
    sales_source: str = "auto",
    include_discontinued: bool = False,
    use_inventory: bool = True,
) -> pd.DataFrame:
    """発注推奨を計算。

    Args:
        months: 月販トレンドに使う直近期間数
        safety_months: 納期カバーに上乗せする安全在庫(月)
        trend_factors: {'up':1.2,'flat':1.0,'down':0.7}
        fixed_order_months: 指定すると納期補正を無視し全 SKU この月数で発注
        sales_source: 'auto' / 'export_item'
        include_discontinued: True なら 取扱中止 品も含める (既定 False = 除外)
        use_inventory: True なら現在庫を差し引いて不足分のみ発注 (既定 True)

    DataFrame.attrs に sales_source / periods / n_discontinued_excluded / inventory_loaded を入れる。
    """
    tf = trend_factors or DEFAULT_TREND_FACTORS

    sales, used_source = _load_monthly_sales(conn, months=months, sales_source=sales_source)
    periods = sorted(sales["period_start"].dropna().unique()) if not sales.empty else []
    sales_pivot: dict[str, dict[str, float]] = {}
    for _, r in sales.iterrows():
        sales_pivot.setdefault(r["jan"], {})[r["period_start"]] = float(r["qty"])

    item_map = _load_items(conn)
    inv_map = _load_inventory(conn) if use_inventory else {}
    inventory_loaded = bool(inv_map)

    quotes: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT supplier_name, jan, display_name, unit_price, lot_size, case_qty, "
        "min_order_amount, order_condition, lead_time_text, zone, zone_rank, nst_supplier_code "
        "FROM supplier_quote WHERE unit_price IS NOT NULL AND unit_price > 0"
    ).fetchall():
        quotes.setdefault(r["jan"], []).append(dict(r))

    n_discontinued_excluded = 0
    rows: list[dict] = []
    for jan, qlist in quotes.items():
        meta = item_map.get(jan, {})

        # --- 取扱中止 除外 ---
        if meta.get("discontinued") and not include_discontinued:
            n_discontinued_excluded += 1
            continue

        msales = sales_pivot.get(jan, {})
        m_seq = [msales.get(p, 0.0) for p in periods]   # 古→新
        avg_monthly = (sum(m_seq) / len(m_seq)) if m_seq else 0.0
        latest_monthly = m_seq[-1] if m_seq else 0.0
        if avg_monthly <= 0 and latest_monthly <= 0:
            continue
        base_monthly = max(avg_monthly, latest_monthly)
        trend = _classify_trend(m_seq)
        tfac = tf.get(trend, 1.0)

        # zone 優先 → 単価最安
        for q in qlist:
            q["_eff"] = q["unit_price"] * ZONE_MARKUP.get(q["zone"], 1.0)
        qlist_sorted = sorted(qlist, key=lambda q: (q["zone_rank"], q["_eff"]))
        best = qlist_sorted[0]
        lot = best["lot_size"] or 1
        lead_days = _parse_lead_days(best["lead_time_text"])
        order_months = fixed_order_months if fixed_order_months else _order_months_from_lead(lead_days, safety_months)

        target_stock = base_monthly * tfac * order_months

        # --- 現在庫差し引き ---
        iv = inv_map.get(jan, {})
        on_hand = iv.get("on_hand", 0.0)
        committed = iv.get("committed", 0.0)
        on_order = iv.get("on_order", 0.0)   # 注文済 = 在途
        eff_stock = on_hand - committed + on_order
        shortfall = target_stock - eff_stock
        if shortfall <= 0:
            continue
        suggested_qty = math.ceil(shortfall / lot) * lot
        if suggested_qty <= 0:
            continue
        line_cost = round(suggested_qty * best["unit_price"])

        rows.append({
            "jan": jan,
            "display_name": meta.get("display_name") or best.get("display_name") or "",
            "maker": meta.get("maker", "(不明)"),
            "rank": meta.get("rank"),
            "handling_status": meta.get("handling_status"),
            **{f"m{i+1}": m_seq[i] if i < len(m_seq) else None for i in range(months)},
            "avg_monthly": round(avg_monthly, 1),
            "latest_monthly": latest_monthly,
            "trend": trend,
            "trend_factor": tfac,
            "order_months": order_months,
            "lead_time_text": best["lead_time_text"],
            "on_hand": on_hand,
            "qty_committed": committed,
            "on_order": on_order,
            "eff_stock": eff_stock,
            "target_stock": round(target_stock, 1),
            "shortfall": round(shortfall, 1),
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
        df.attrs.update(sales_source=used_source, periods=periods,
                        n_discontinued_excluded=n_discontinued_excluded,
                        inventory_loaded=inventory_loaded)
        return df

    sup_total = df.groupby("supplier_name")["line_cost"].sum()
    df["supplier_total"] = df["supplier_name"].map(sup_total)
    df["meets_min_order"] = df.apply(
        lambda r: (r["min_order_amount"] == 0) or (r["supplier_total"] >= r["min_order_amount"]),
        axis=1,
    )
    df["status"] = df["meets_min_order"].map({True: "recommended", False: "deferred_min_order"})

    def _reason(r):
        z = {"JD_DIRECT": "JD直送", "BENTEN_TRANSIT": "弁天経由", "EMERGENCY": "応急",
             "PREPAID": "前払い", "OTHER": "他"}.get(r["zone"], r["zone"])
        base = (f"{z}・単価{r['unit_price']}・{r['trend']}×{r['trend_factor']}・{r['order_months']:.0f}ヶ月分"
                f"｜目標{r['target_stock']:.0f}−在庫{r['eff_stock']:.0f}(手持{r['on_hand']:.0f}"
                f"+注文済{r['on_order']:.0f}−確保{r['qty_committed']:.0f})＝不足{r['shortfall']:.0f}")
        if not r["meets_min_order"]:
            base += f"・⚠️最低受注¥{r['min_order_amount']:,}未達(現¥{r['supplier_total']:,.0f})"
        return base
    df["reason"] = df.apply(_reason, axis=1)

    df = df.sort_values(["zone_rank", "supplier_name", "line_cost"], ascending=[True, True, False]).reset_index(drop=True)
    df.attrs.update(sales_source=used_source, periods=periods,
                    n_discontinued_excluded=n_discontinued_excluded,
                    inventory_loaded=inventory_loaded)
    return df
