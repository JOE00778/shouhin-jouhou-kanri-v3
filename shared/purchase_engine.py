"""自動発注 計算エンジン.

入力:
  - supplier_quote テーブル (仕入先 × JAN の報価, zone 分類付き)
  - shop_sales テーブル (source='export_item' = 【輸出】アイテム別売上（概要）= 月次 SKU 販売数量)
  - item_v2 (display_name / maker / rank / handling_status 補完用)
  - item_inventory_snapshot_v2 (現在庫: 手持 / 確保済 / 注文済)  ← Boss 2026-05-12

発注ロジック (Boss 2026-05-12):
  目標在庫 = max(平均月販, 直近月販) × トレンド係数(1.2/1.0/0.7) × (納期カバー月数 + 安全在庫月数)
  有効在庫 = 手持 − 確保済 + 注文済        ← 「注文済」= 発注済で未入荷, 在途扱い (輸送中 列は無視)
  発注数 = max(0, 目標在庫 − 有効在庫) を lot 倍数に切り上げ ; ≤0 なら推奨に出さない

仕入先選定 (Boss 2026-05-12):
  1) 各 SKU は候補仕入先を zone 優先 (JD_DIRECT > BENTEN_TRANSIT > EMERGENCY > PREPAID > OTHER)
     → 同 zone は単価最安 でランク付け。主力 = 1位, 備用 = 2〜3位。
  2) メーカー単位で「なるべく 1〜3 仕入先に集約」(品牌集中)。
     1 仕入先がそのメーカーの SKU を数個しか持たない散らばりを避ける。
     ただしメーカー全体が数 SKU しか無い場合はこのルールを無視。
  3) 弁天倉庫は自社倉庫 → 中継費なし (markup 撤廃)。

除外: 取扱中止 / メーカー取扱中止 の SKU は発注対象外。
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

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
DISCONTINUED_VALUES = {"取扱中止", "メーカー取扱中止", "取扱停止", "廃番", "生産終了"}
ZONE_LABEL_JA = {"JD_DIRECT": "JD直送", "BENTEN_TRANSIT": "弁天経由", "EMERGENCY": "応急",
                 "PREPAID": "前払い", "OTHER": "他"}


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
    return math.ceil(lead_days / 30) + safety_months


def _classify_trend(months: list[float]) -> str:
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


SALES_SOURCE_PRIORITY = ["export_item"]


def _load_monthly_sales(conn, months: int = 3, sales_source: str = "auto") -> tuple[pd.DataFrame, str]:
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
    """item_inventory_snapshot_v2 を JAN 単位に集計。on_order='注文済'。輸送中は無視。"""
    inv: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT jan, COALESCE(SUM(qty_on_hand),0) AS oh, COALESCE(SUM(qty_committed),0) AS cm, "
            "COALESCE(SUM(qty_on_order),0) AS oo FROM item_inventory_snapshot_v2 "
            "WHERE jan IS NOT NULL GROUP BY jan"
        ).fetchall()
    except Exception:
        return inv
    for r in rows:
        inv[r["jan"]] = {"on_hand": float(r["oh"] or 0), "committed": float(r["cm"] or 0),
                         "on_order": float(r["oo"] or 0)}
    return inv


def _load_items(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT jan, display_name, maker, rank, handling_status FROM item_v2 WHERE jan IS NOT NULL"
    ).fetchall():
        hs = (r["handling_status"] or "").strip()
        rk = (r["rank"] or "").strip()
        out[r["jan"]] = {
            "display_name": r["display_name"], "maker": r["maker"] or "(不明)", "rank": r["rank"],
            "handling_status": r["handling_status"],
            "discontinued": (hs in DISCONTINUED_VALUES) or (rk in DISCONTINUED_VALUES),
        }
    return out


def _supplier_label(q: dict) -> str:
    return f"{q['supplier_name']}({ZONE_LABEL_JA.get(q['zone'], q['zone'])})·¥{q['unit_price']}"


# 集約のアンカーに使ってよい zone tier。応急(3)/前払い(4) は絶対にアンカーにしない
#  → 品牌集約のために JD直送 の SKU を 応急/前払い に移すことはない。
ANCHOR_ZONE_TIERS = (1, 2)   # JD_DIRECT, BENTEN_TRANSIT
# 集約で発注先を移すときの単価許容比。元の最安より これ以上 高くなる移動はしない。
MAX_CONSOLIDATION_PRICE_RATIO = 1.5


def _consolidate_brand(jan_to_candidates: dict[str, list[dict]], maker_jans: dict[str, list[str]],
                       *, max_suppliers_per_brand: int, small_brand_skip: int) -> dict[str, str]:
    """メーカー単位で発注先を集約。戻り値: {jan: 採用 supplier_name}。

    既定 = 各 JAN の候補1位 (zone優先→最安)。
    メーカーの SKU 数 > small_brand_skip のとき:
      zone tier ごと (JD直送 → 弁天) に「そのメーカーの SKU を最も多くカバーできる仕入先」を貪欲に
      合計 max_suppliers_per_brand 社まで選ぶ (アンカー)。
      各 SKU は「その SKU の最良 zone と同じ zone のアンカー」が報価を持つなら、カバー数最大のアンカーへ。
      → ⚠️ zone tier をまたいだ移動は絶対にしない (JD直送→弁天/応急 等にはならない)。
        最良 zone が 応急/前払い の SKU はそのまま (選択肢が無いので)。
      タイブレーク: カバー数 → 平均単価安い。
    """
    chosen: dict[str, str] = {jan: c[0]["supplier_name"] for jan, c in jan_to_candidates.items()}

    for jans in maker_jans.values():
        if len(jans) <= small_brand_skip:
            continue  # 小品牌は集約しない
        jan_best_zr = {jan: jan_to_candidates[jan][0]["zone_rank"] for jan in jans}
        sup_zone: dict[str, int] = {}
        sup_jans: dict[str, set] = defaultdict(set)
        sup_prices: dict[str, list] = defaultdict(list)
        for jan in jans:
            for q in jan_to_candidates[jan]:
                sn = q["supplier_name"]
                sup_zone[sn] = q["zone_rank"]   # 1 仕入先 = 1 zone (sheet 由来) なので上書きで OK
                sup_jans[sn].add(jan)
                sup_prices[sn].append(q["unit_price"])

        anchors: list[str] = []
        anchor_set: set[str] = set()
        for tier in ANCHOR_ZONE_TIERS:
            if len(anchors) >= max_suppliers_per_brand:
                break
            tier_sups = [s for s in sup_zone if sup_zone[s] == tier and s not in anchor_set]
            need = {jan for jan in jans if jan_best_zr[jan] == tier}   # この tier が最良 zone の SKU
            while need and tier_sups and len(anchors) < max_suppliers_per_brand:
                def _key(s: str):
                    gain = len(sup_jans[s] & need)
                    avg_p = sum(sup_prices[s]) / len(sup_prices[s])
                    return (gain, -avg_p)
                best_s = max(tier_sups, key=_key)
                if len(sup_jans[best_s] & need) == 0:
                    break
                anchors.append(best_s)
                anchor_set.add(best_s)
                need -= sup_jans[best_s]
                tier_sups.remove(best_s)
        if not anchor_set:
            continue
        anchor_rank = {s: i for i, s in enumerate(anchors)}  # 早い = カバー数多い
        for jan in jans:
            bzr = jan_best_zr[jan]
            orig_price = jan_to_candidates[jan][0]["unit_price"]
            # その SKU の最良 zone と同じ zone のアンカーで, 報価があり, 単価が許容比内のもの
            cands = [q for q in jan_to_candidates[jan]
                     if q["supplier_name"] in anchor_set and q["zone_rank"] == bzr
                     and q["unit_price"] <= orig_price * MAX_CONSOLIDATION_PRICE_RATIO]
            if cands:
                cands.sort(key=lambda q: anchor_rank[q["supplier_name"]])
                chosen[jan] = cands[0]["supplier_name"]
            # else: 既定 (アンカー無し / 応急・前払い / 単価が高すぎ) のまま
    return chosen


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
    consolidate_by_brand: bool = True,
    max_suppliers_per_brand: int = 3,
    small_brand_skip: int = 5,
    optimize: str = "zone",
    ranks: list[str] | tuple[str, ...] | None = None,
    max_stock_months: float | None = None,
) -> pd.DataFrame:
    """発注推奨を計算。

    主な引数:
        months / safety_months / trend_factors / fixed_order_months / sales_source
        include_discontinued: True なら 取扱中止 品も含める (既定 False)
        use_inventory: True なら現在庫を差し引く (既定 True)
        consolidate_by_brand: True ならメーカー単位で 1〜3 仕入先に集約 (既定 True)
        max_suppliers_per_brand: 1 メーカーを集約する上限仕入先数 (既定 3)
        small_brand_skip: SKU 数がこれ以下のメーカーは集約しない (既定 5)
        optimize: 'zone' = zone優先→同zone最安 (既定) /
                  'line_cost' = 発注金額(line_cost)最小 — ロット丸め・納期込みで一番安い仕入先 (= 最小支出プラン) /
                  'cost' = zone無視で純粋に最安単価 (ロット無視, 比較用)
                  ※ 'line_cost'/'cost' は consolidate_by_brand=False と併用推奨
        ranks: 指定すると item_v2.rank がこのリストに含まれる SKU のみ対象 (例 ('Aランク','Bランク'))。None=全ランク。
        max_stock_months: 指定すると「発注後の在庫月数 (= (有効在庫+発注数)/月販)」がこれを超える SKU を
                          status='deferred_overstock' にする (ロット起定量で買い過ぎになるケースの健全性ガード)。
                          None=チェックなし。

    DataFrame.attrs: sales_source / periods / n_discontinued_excluded / n_rank_excluded / inventory_loaded
                     / n_consolidated / optimize / n_overstock / max_stock_months
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

    # ---- Phase 1: SKU ごとの候補リスト + 基礎指標 (発注先はまだ決めない) ----
    n_discontinued_excluded = 0
    n_rank_excluded = 0
    rank_set = set(ranks) if ranks else None
    sku: dict[str, dict] = {}          # jan -> {candidates, meta, m_seq, base_monthly, trend, tfac, on_hand, committed, on_order, eff_stock}
    jan_to_candidates: dict[str, list[dict]] = {}
    for jan, qlist in quotes.items():
        meta = item_map.get(jan, {})
        if meta.get("discontinued") and not include_discontinued:
            n_discontinued_excluded += 1
            continue
        if rank_set is not None and (meta.get("rank") not in rank_set):
            n_rank_excluded += 1
            continue
        msales = sales_pivot.get(jan, {})
        m_seq = [msales.get(p, 0.0) for p in periods]
        avg_monthly = (sum(m_seq) / len(m_seq)) if m_seq else 0.0
        latest_monthly = m_seq[-1] if m_seq else 0.0
        if avg_monthly <= 0 and latest_monthly <= 0:
            continue
        for q in qlist:
            q["_eff"] = q["unit_price"] * ZONE_MARKUP.get(q["zone"], 1.0)
        iv = inv_map.get(jan, {})
        on_hand, committed, on_order = iv.get("on_hand", 0.0), iv.get("committed", 0.0), iv.get("on_order", 0.0)
        eff_stock = on_hand - committed + on_order
        base_monthly = max(avg_monthly, latest_monthly)
        trend = _classify_trend(m_seq)
        tfac = tf.get(trend, 1.0)

        def _est_line_cost(q: dict) -> int:
            """その仕入先で発注した場合の line_cost 見積り (ロット丸め・納期込み)。≤0 なら 0。"""
            lot_ = q["lot_size"] or 1
            om = fixed_order_months if fixed_order_months else _order_months_from_lead(
                _parse_lead_days(q["lead_time_text"]), safety_months)
            sf = base_monthly * tfac * om - eff_stock
            if sf <= 0:
                return 0
            return math.ceil(sf / lot_) * lot_ * q["unit_price"]

        if optimize == "cost":
            # 純粋に最安単価 (ロット・納期は無視) → 比較用シナリオ
            candidates = sorted(qlist, key=lambda q: (q["_eff"], q["zone_rank"], q["supplier_name"]))
        elif optimize == "line_cost":
            # 発注金額 (line_cost) 最小 — ロット丸め・納期も込みで一番安い仕入先を採用
            candidates = sorted(qlist, key=lambda q: (_est_line_cost(q), q["zone_rank"], q["supplier_name"]))
        else:
            # 既定: zone 優先 → 同 zone は最安単価
            candidates = sorted(qlist, key=lambda q: (q["zone_rank"], q["_eff"], q["supplier_name"]))

        sku[jan] = {
            "candidates": candidates, "meta": meta, "m_seq": m_seq,
            "avg_monthly": avg_monthly, "latest_monthly": latest_monthly,
            "base_monthly": base_monthly,
            "trend": trend,
            "on_hand": on_hand, "committed": committed, "on_order": on_order,
            "eff_stock": eff_stock,
        }
        jan_to_candidates[jan] = candidates

    # ---- Phase 2: メーカー単位の集約 → 発注先決定 ----
    if consolidate_by_brand:
        maker_jans: dict[str, list[str]] = defaultdict(list)
        for jan, d in sku.items():
            maker_jans[d["meta"].get("maker", "(不明)")].append(jan)
        final_supplier = _consolidate_brand(
            jan_to_candidates, maker_jans,
            max_suppliers_per_brand=max_suppliers_per_brand, small_brand_skip=small_brand_skip,
        )
    else:
        final_supplier = {jan: d["candidates"][0]["supplier_name"] for jan, d in sku.items()}

    # ---- Phase 3: 発注先確定後に数量・金額を確定 ----
    n_consolidated = 0
    rows: list[dict] = []
    for jan, d in sku.items():
        cands = d["candidates"]
        fs = final_supplier.get(jan, cands[0]["supplier_name"])
        best = next((q for q in cands if q["supplier_name"] == fs), cands[0])
        moved = fs != cands[0]["supplier_name"]
        if moved:
            n_consolidated += 1
        tfac = tf.get(d["trend"], 1.0)
        lot = best["lot_size"] or 1
        lead_days = _parse_lead_days(best["lead_time_text"])
        order_months = fixed_order_months if fixed_order_months else _order_months_from_lead(lead_days, safety_months)
        target_stock = d["base_monthly"] * tfac * order_months
        eff_stock = d["eff_stock"]
        shortfall = target_stock - eff_stock
        if shortfall <= 0:
            continue
        suggested_qty = math.ceil(shortfall / lot) * lot
        if suggested_qty <= 0:
            continue
        line_cost = round(suggested_qty * best["unit_price"])
        # 発注後の在庫月数 (= 在庫回転の目安)。ロット起定量のために多めに買う場合の健全性チェック用。
        stock_months_after = (eff_stock + suggested_qty) / max(d["base_monthly"], 1e-6)
        overstock = (max_stock_months is not None) and (stock_months_after > max_stock_months)

        backups = [q for q in cands if q["supplier_name"] != fs][:2]
        b1 = backups[0] if len(backups) >= 1 else None
        b2 = backups[1] if len(backups) >= 2 else None
        meta = d["meta"]
        rows.append({
            "jan": jan,
            "display_name": meta.get("display_name") or best.get("display_name") or "",
            "maker": meta.get("maker", "(不明)"),
            "rank": meta.get("rank"),
            "handling_status": meta.get("handling_status"),
            **{f"m{i+1}": d["m_seq"][i] if i < len(d["m_seq"]) else None for i in range(months)},
            "avg_monthly": round(d["avg_monthly"], 1),
            "latest_monthly": d["latest_monthly"],
            "trend": d["trend"],
            "trend_factor": tfac,
            "order_months": order_months,
            "lead_time_text": best["lead_time_text"],
            "on_hand": d["on_hand"],
            "qty_committed": d["committed"],
            "on_order": d["on_order"],
            "eff_stock": eff_stock,
            "target_stock": round(target_stock, 1),
            "shortfall": round(shortfall, 1),
            # --- 発注先 (主力) + 備用 ---
            "supplier_name": fs,                # = 主力 (互換のため supplier_name 維持)
            "supplier_primary": fs,
            "zone": best["zone"],
            "zone_rank": best["zone_rank"],
            "nst_supplier_code": best["nst_supplier_code"],
            "unit_price": best["unit_price"],
            "effective_price": round(best["_eff"]),
            "lot_size": lot,
            "consolidated": moved,              # 品牌集約で主力が最安以外に変わったか
            "supplier_backup1": b1["supplier_name"] if b1 else None,
            "backup1_zone": b1["zone"] if b1 else None,
            "backup1_price": b1["unit_price"] if b1 else None,
            "supplier_backup2": b2["supplier_name"] if b2 else None,
            "backup2_zone": b2["zone"] if b2 else None,
            "backup2_price": b2["unit_price"] if b2 else None,
            # --- 数量・金額 ---
            "suggested_qty": suggested_qty,
            "stock_months_after": round(stock_months_after, 1),
            "overstock": overstock,
            "line_cost": line_cost,
            "min_order_amount": best["min_order_amount"] or 0,
            "order_condition": best["order_condition"],
            "n_alt_suppliers": len(cands),
            "alt_suppliers": " / ".join(_supplier_label(q) for q in cands[:5]),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        df.attrs.update(sales_source=used_source, periods=periods,
                        n_discontinued_excluded=n_discontinued_excluded, n_rank_excluded=n_rank_excluded,
                        inventory_loaded=inventory_loaded, n_consolidated=0, optimize=optimize,
                        n_overstock=0, max_stock_months=max_stock_months)
        return df

    sup_total = df.groupby("supplier_name")["line_cost"].sum()
    df["supplier_total"] = df["supplier_name"].map(sup_total)
    df["meets_min_order"] = df.apply(
        lambda r: (r["min_order_amount"] == 0) or (r["supplier_total"] >= r["min_order_amount"]),
        axis=1,
    )

    def _status(r):
        if r["overstock"]:
            return "deferred_overstock"
        if not r["meets_min_order"]:
            return "deferred_min_order"
        return "recommended"
    df["status"] = df.apply(_status, axis=1)

    def _reason(r):
        z = ZONE_LABEL_JA.get(r["zone"], r["zone"])
        base = (f"{z}・単価{r['unit_price']}・{r['trend']}×{r['trend_factor']}・{r['order_months']:.0f}ヶ月分"
                f"｜目標{r['target_stock']:.0f}−在庫{r['eff_stock']:.0f}(手持{r['on_hand']:.0f}"
                f"+注文済{r['on_order']:.0f}−確保{r['qty_committed']:.0f})＝不足{r['shortfall']:.0f}"
                f"→発注{r['suggested_qty']}(発注後在庫{r['stock_months_after']:.1f}ヶ月)")
        if r["consolidated"]:
            base += "・🔗品牌集約"
        if r["overstock"]:
            base += f"・⚠️起定量で在庫{r['stock_months_after']:.1f}ヶ月>上限{max_stock_months}→保留"
        if not r["meets_min_order"]:
            base += f"・⚠️最低受注¥{r['min_order_amount']:,}未達(現¥{r['supplier_total']:,.0f})"
        return base
    df["reason"] = df.apply(_reason, axis=1)

    df = df.sort_values(["zone_rank", "supplier_name", "maker", "line_cost"],
                        ascending=[True, True, True, False]).reset_index(drop=True)
    df.attrs.update(sales_source=used_source, periods=periods,
                    n_discontinued_excluded=n_discontinued_excluded, n_rank_excluded=n_rank_excluded,
                    inventory_loaded=inventory_loaded, n_consolidated=n_consolidated, optimize=optimize,
                    n_overstock=int(df["overstock"].sum()), max_stock_months=max_stock_months)
    return df
