"""模块 #25 発注AI v2 · 多仕入先決定版.

ロジック (Boss 2026-05-11):
  1. 月販トレンド (【ASEAN】店舗別売上 集計専用 = shop_sales source='asean_monthly')
     → 直近 N ヶ月 → avg/最新 + トレンド (↑↓→)
  2. 発注数 = base_monthly × トレンド係数 × 発注月数 (納期から自動補正), ロット丸め
  3. 仕入先選定 = zone 優先 (JD直送>弁天経由>応急>前払い) → 同 zone 内 単価最安
     · 弁天経由 (共和/大木/若竹園/森フォレスト) は中継費 +3% で比価
  4. 仕入先単位合算 → 注文最低金額チェック
  5. 出力 = 仕入先別 発注清单 (品牌別小計+総額) + NST 発注 CSV (page 10 同形式)

タブ:
  📊 発注計算   — 仕入先表アップロード + パラメータ + 推奨清单 + CSV 出力
  🚚 仕入先ビュー — 仕入先選択 → 報価単 / 発注条件 / 現在の発注金額
  🏷️ 商品ビュー  — JAN 入力 → 各仕入先の報価比較 + 選定理由
  🏭 品牌ビュー  — メーカー選択 → SKU × 仕入先 分布
"""
from __future__ import annotations

import io
from datetime import date, datetime

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t
from shared.purchase_engine import compute_recommendations, DEFAULT_TREND_FACTORS

st.set_page_config(page_title=t("発注AI v2"), page_icon="📦", layout="wide")
from shared.auth import require_admin
require_admin()
from shared.theme import inject_theme
inject_theme()
lang_selector()
conn = get_connection()

st.title(t("📦 発注AI v2 — 多仕入先決定版"))
st.caption(t(
    "月販トレンド + 価格 + ロット + 注文最低金額 + 仕入先 zone (JD直送/弁天経由+3%/応急/前払い) "
    "→ どの商品をどの仕入先から何個 → 仕入先別発注清单 + NST 発注 CSV"
))


# ============================================================
# zone ラベル
# ============================================================
ZONE_LABEL = {
    "JD_DIRECT": "🟢 JD直送", "BENTEN_TRANSIT": "🟡 弁天経由(+3%)",
    "EMERGENCY": "🟠 応急", "PREPAID": "🔴 前払い", "OTHER": "⚪ 他",
}
TREND_LABEL = {"up": "📈 上昇", "flat": "➡️ 横ばい", "down": "📉 下降"}

# NST 発注 CSV メタ (page 10 と同じ選択肢)
NST_SUPPLIERS = [
    "0020 エンパイヤ自動車株式会社（KONNGU'S）", "0025 株式会社オンダ", "0073 株式会社　エィチ・ケイ",
    "0077 大分共和株式会社", "0085 中央物産株式会社", "0197 大木化粧品株式会社", "0201 現金仕入れ",
    "0202 トラスコ中山株式会社", "0256 株式会社 グランジェ", "0258 株式会社 ファイン",
    "0343 株式会社森フォレスト", "0376 菅野株式会社", "0402 ハリマ共和物産株式会社",
    "0411 株式会社ラクーンコマース（スーパーデリバリー）", "0435 株式会社 流久商事", "0444 ハナモンワークス 合同会社",
    "0445 富森商事 株式会社", "0457 カネイシ株式会社", "0468 王子国際貿易株式会社", "0469 株式会社 新日配薬品",
    "0474 株式会社　五洲", "0476 カード仕入れ", "0479 スケーター株式会社", "0482 風雲商事株式会社",
    "0486 Maple International株式会社", "0490 NEW WIND株式会社", "0491 アプライド株式会社",
    "0504 京浜商事株式会社", "C000510 太田物産 株式会社",
]
EMPLOYEES = ["079 隋艶偉", "005 川崎里子", "037 米澤和敏", "043 徐越"]
DEPARTMENTS = ["輸出事業", "輸出事業 : 輸出（中国）"]


def _has_supplier_quotes() -> bool:
    try:
        return conn.execute("SELECT COUNT(*) FROM supplier_quote").fetchone()[0] > 0
    except Exception:
        return False


def _ingest_supplier_file(file_bytes: bytes, filename: str) -> dict:
    import tempfile, os
    from data_warehouse.ingest.supplier_ingest import ingest_supplier_master
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    try:
        return ingest_supplier_master(path, conn)
    finally:
        os.unlink(path)


def _build_nst_csv(df_sup: pd.DataFrame, *, nst_supplier: str, order_date: date,
                   employee: str, department: str, memo: str) -> pd.DataFrame:
    """page 10 と同形式の NST 発注 CSV (13 列)。1 行 = 1 SKU。"""
    eid = datetime.now().strftime("%Y%m%d%H%M%S")
    rows = []
    for _, r in df_sup.iterrows():
        qty = int(r["suggested_qty"])
        price = int(r["unit_price"])
        amount = qty * price
        rows.append({
            "外部ID": eid, "仕入先": nst_supplier, "日付": order_date.strftime("%Y/%m/%d"),
            "従業員": employee, "部門": department, "メモ": memo, "場所": "JD-物流-千葉",
            "アイテム": f"{r['jan']} {r['display_name']}".strip(),
            "数量": qty, "単価/率": price, "金額": amount, "税額": 0, "総額": amount,
        })
    return pd.DataFrame(rows, columns=[
        "外部ID", "仕入先", "日付", "従業員", "部門", "メモ", "場所",
        "アイテム", "数量", "単価/率", "金額", "税額", "総額",
    ])


# ============================================================
# Tabs
# ============================================================
tab_calc, tab_sup, tab_item, tab_maker = st.tabs([
    t("📊 発注計算"), t("🚚 仕入先ビュー"), t("🏷️ 商品ビュー"), t("🏭 品牌ビュー"),
])

# ------------------------------------------------------------
# Tab 1: 発注計算
# ------------------------------------------------------------
with tab_calc:
    st.subheader(t("① 仕入先管理リスト"))
    c_up, c_st = st.columns([2, 1])
    with c_up:
        up = st.file_uploader(t("仕入先管理リスト.xlsx をアップロード"), type=["xlsx"], key="sup_upload")
        if up is not None:
            with st.spinner(t("解析中…")):
                res = _ingest_supplier_file(up.getvalue(), up.name)
            st.success(t(
                f"✅ {res['sheets_processed']} 仕入先 sheet · {res['rows_inserted']:,} 件報価入库 "
                f"(JAN無効スキップ {res['skipped_no_jan']:,})"
            ))
            if res["sheets_skipped"]:
                st.warning(t("スキップ sheet: " + ", ".join(res["sheets_skipped"])))
            if res["warnings"]:
                with st.expander(t("⚠️ warnings")):
                    for w in res["warnings"]:
                        st.text(w)
    with c_st:
        if _has_supplier_quotes():
            n = conn.execute("SELECT COUNT(*) FROM supplier_quote").fetchone()[0]
            ns = conn.execute("SELECT COUNT(DISTINCT supplier_name) FROM supplier_quote").fetchone()[0]
            st.metric(t("登録済み報価"), f"{n:,}", f"{ns} 仕入先")
        else:
            st.info(t("← 先に仕入先管理リストをアップロード"))

    st.divider()
    st.subheader(t("② パラメータ"))
    st.caption(t("📅 月販: 【輸出】アイテム別売上（概要）_JO (export_item) ｜ 在庫: 【輸出】在庫のスナップショット (手持 − 確保済 + 注文済)"))
    st.caption(t("発注数 = max(0, 目標在庫 − 有効在庫) を ロット倍数に切り上げ ｜ 目標在庫 = max(平均月販,直近月販) × トレンド係数 × (納期カバー月数 + 安全在庫月数)"))
    st.caption(t("仕入先選定: zone優先(JD直送>弁天>応急>前払い)→同zoneは最安。各SKUに主力+備用1〜2。さらにメーカー単位で1〜3仕入先に集約(zone劣化なし)"))
    sales_source = "export_item"

    p1, p2, p3, p4 = st.columns(4)
    with p1:
        months = st.number_input(t("月販トレンド期間 (ヶ月)"), 1, 12, 3)
        use_inv = st.checkbox(t("現在庫を差し引く"), value=True)
    with p2:
        safety = st.number_input(t("安全在庫 (ヶ月分)"), 0.0, 6.0, 1.0, 0.5)
        incl_disc = st.checkbox(t("取扱中止品も含める"), value=False)
    with p3:
        use_fixed = st.checkbox(t("発注月数を固定 (納期補正しない)"))
        fixed_months = st.number_input(t("固定発注月数"), 0.5, 6.0, 1.0, 0.5, disabled=not use_fixed)
    with p4:
        st.caption(t("トレンド係数"))
        f_up = st.number_input("📈", 1.0, 3.0, DEFAULT_TREND_FACTORS["up"], 0.1, key="f_up")
        f_dn = st.number_input("📉", 0.1, 1.0, DEFAULT_TREND_FACTORS["down"], 0.1, key="f_dn")

    q0, q1, q2, q3 = st.columns([1.4, 1, 1, 1.3])
    with q0:
        opt_label = st.radio(
            t("発注先の選び方"),
            [t("zone優先(JD直送>弁天>応急>前払い)"), t("発注金額(line_cost)が最小")],
            help=t("『発注金額最小』はロット丸め・納期込みで一番安い仕入先を選ぶ。zone劣化(JD直送→応急/前払い)が起きる場合あり"),
        )
        optimize = "line_cost" if "金額" in opt_label else "zone"
    with q1:
        consol = st.checkbox(t("メーカー単位で仕入先を集約"), value=True)
    with q2:
        max_sup_brand = st.number_input(t("1メーカーの上限仕入先数"), 1, 5, 3, disabled=not consol)
    with q3:
        small_brand = st.number_input(t("これ以下のSKU数のメーカーは集約しない"), 1, 20, 5, disabled=not consol)

    r0, r1 = st.columns([1.6, 1.4])
    with r0:
        rank_sel = st.multiselect(
            t("対象ランク (空 = 全部)"), ["Aランク", "Bランク", "Cランク", "NEW", "取扱中止"],
            default=["Aランク", "Bランク"],
            help=t("まず A/B 等級だけ見たい等。空にすると全ランク(取扱中止は別途除外)"),
        )
    with r1:
        cap_on = st.checkbox(t("発注後の在庫月数に上限を設ける"), value=False,
                             help=t("ロット起定量で買い過ぎになる SKU を『保留』にする。例: 上限4ヶ月なら発注後在庫が4ヶ月超の SKU は発注しない"))
        max_stock = st.number_input(t("在庫月数の上限 (ヶ月)"), 1.0, 12.0, 4.0, 0.5, disabled=not cap_on)

    if st.button(t("🔍 発注計算実行"), type="primary", disabled=not _has_supplier_quotes()):
        with st.spinner(t("計算中…")):
            df = compute_recommendations(
                conn, months=int(months), safety_months=float(safety),
                trend_factors={"up": f_up, "flat": 1.0, "down": f_dn},
                fixed_order_months=float(fixed_months) if use_fixed else None,
                sales_source=sales_source,
                include_discontinued=bool(incl_disc),
                use_inventory=bool(use_inv),
                consolidate_by_brand=bool(consol),
                max_suppliers_per_brand=int(max_sup_brand),
                small_brand_skip=int(small_brand),
                optimize=optimize,
                ranks=rank_sel or None,
                max_stock_months=float(max_stock) if cap_on else None,
            )
        st.session_state["po_reco"] = df

    df = st.session_state.get("po_reco")
    if df is None:
        st.info(t("「🔍 発注計算実行」を押すと推奨清单が出ます"))
    elif df.empty:
        st.warning(t(
            "⚠️ 発注対象 0 件 — shop_sales に export_item データがないか、supplier_quote に報価がありません。"
            "page 99 で【輸出】アイテム別売上（概要）_JO をアップロードしてください。"
        ))
    else:
        periods = df.attrs.get("periods", [])
        n_disc_ex = df.attrs.get("n_discontinued_excluded", 0)
        n_rank_ex = df.attrs.get("n_rank_excluded", 0)
        inv_loaded = df.attrs.get("inventory_loaded", False)
        n_consol = df.attrs.get("n_consolidated", 0)
        n_over = df.attrs.get("n_overstock", 0)
        cap = df.attrs.get("max_stock_months", None)
        st.caption(t(
            f"📅 月販期間: {', '.join(periods) if periods else '(なし)'} ｜ "
            f"在庫差引: {'✅ 適用' if inv_loaded else '⚠️ 在庫データなし(全量発注)'} ｜ "
            f"取扱中止 除外: {n_disc_ex} SKU ｜ ランク対象外 除外: {n_rank_ex} SKU ｜ "
            f"品牌集約で発注先変更: {n_consol} SKU ｜ 在庫過多で保留: {n_over} SKU"
            + (f" (上限{cap}ヶ月)" if cap else "")
        ))
        df_rec = df[df["status"] == "recommended"]
        df_hold = df[df["status"] != "recommended"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(t("発注 SKU (推奨)"), f"{len(df_rec):,}")
        m2.metric(t("発注コスト (推奨)"), f"¥{int(df_rec['line_cost'].sum()):,}")
        m3.metric(t("仕入先数"), f"{df_rec['supplier_name'].nunique()}")
        m4.metric(t("保留 SKU (在庫過多/最低受注未達)"), f"{len(df_hold)}", delta=None)

        if len(df_hold):
            with st.expander(t(f"⚠️ 保留 SKU {len(df_hold)} 件 (発注しない — 在庫過多 / 最低受注額未達)"), expanded=False):
                st.dataframe(
                    df_hold[["jan", "display_name", "maker", "rank", "status", "on_hand", "suggested_qty",
                             "lot_size", "stock_months_after", "supplier_name", "zone", "unit_price",
                             "line_cost", "reason"]]
                    .rename(columns={"rank": "ランク", "status": "状態", "on_hand": "手持在庫",
                                     "suggested_qty": "発注数(参考)", "lot_size": "ロット",
                                     "stock_months_after": "発注後在庫月数", "supplier_name": "仕入先",
                                     "unit_price": "単価", "line_cost": "金額(参考)", "reason": "理由"})
                    .sort_values("発注後在庫月数", ascending=False),
                    use_container_width=True, hide_index=True,
                )

        # 仕入先別 → 品牌別 サマリ (推奨分のみ)
        st.markdown(t("#### 仕入先別 → 品牌別 発注清单 (推奨分)"))
        for sup in df_rec.groupby("supplier_name")["line_cost"].sum().sort_values(ascending=False).index:
            sub = df_rec[df_rec["supplier_name"] == sup]
            zone = sub["zone"].iloc[0]
            sup_total = int(sub["line_cost"].sum())
            moq = int(sub["min_order_amount"].iloc[0])
            ok = sub["meets_min_order"].iloc[0]
            badge = "✅" if ok else f"⚠️ 最低¥{moq:,}未達"
            nst_code = sub["nst_supplier_code"].iloc[0] or "(NST コード未設定)"
            with st.expander(
                f"{ZONE_LABEL.get(zone, zone)}  {sup}  —  ¥{sup_total:,}  {badge}  ({len(sub)} SKU)  · {nst_code}",
                expanded=False,
            ):
                by_maker = (
                    sub.groupby("maker")
                    .agg(SKU数=("jan", "count"), 発注数=("suggested_qty", "sum"), コスト=("line_cost", "sum"))
                    .reset_index().sort_values("コスト", ascending=False)
                )
                st.dataframe(by_maker, use_container_width=True, hide_index=True)
                st.dataframe(
                    sub[["jan", "display_name", "maker", "rank", "avg_monthly", "latest_monthly", "trend",
                         "trend_factor", "order_months", "on_hand", "on_order", "target_stock", "shortfall",
                         "lot_size", "suggested_qty", "stock_months_after", "unit_price", "line_cost", "lead_time_text",
                         "consolidated", "supplier_backup1", "backup1_price", "supplier_backup2", "backup2_price",
                         "alt_suppliers", "reason"]]
                    .rename(columns={
                        "rank": "ランク", "avg_monthly": "平均月販", "latest_monthly": "直近月販", "trend": "傾向",
                        "trend_factor": "係数", "order_months": "発注月数",
                        "on_hand": "手持在庫", "on_order": "注文済", "target_stock": "目標在庫", "shortfall": "不足",
                        "lot_size": "ロット", "suggested_qty": "発注数", "stock_months_after": "発注後在庫月数",
                        "unit_price": "単価", "line_cost": "金額",
                        "lead_time_text": "納期", "consolidated": "集約",
                        "supplier_backup1": "備用①", "backup1_price": "備①単価",
                        "supplier_backup2": "備用②", "backup2_price": "備②単価",
                        "alt_suppliers": "全候補", "reason": "理由",
                    }),
                    use_container_width=True, hide_index=True,
                )

        st.divider()
        # NST 発注 CSV 出力
        st.markdown(t("#### ③ NST 発注 CSV 出力 (仕入先別)"))
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            sel_sup = st.selectbox(t("仕入先を選択"), sorted(df_rec["supplier_name"].unique()))
        with cc2:
            od = st.date_input(t("発注日"), value=date.today())
            emp = st.selectbox(t("従業員"), EMPLOYEES)
        with cc3:
            dept = st.selectbox(t("部門"), DEPARTMENTS)
            memo = st.text_input(t("メモ"), value=f"自動発注 {date.today():%Y-%m}")
        sub_sel = df_rec[df_rec["supplier_name"] == sel_sup]
        # NST コードの推定 (報価の nst_supplier_code → 一致するものを default に)
        guess_code = sub_sel["nst_supplier_code"].iloc[0]
        nst_idx = NST_SUPPLIERS.index(guess_code) if guess_code in NST_SUPPLIERS else 0
        nst_sup = st.selectbox(t("NST 仕入先コード"), NST_SUPPLIERS, index=nst_idx)
        df_csv = _build_nst_csv(sub_sel, nst_supplier=nst_sup, order_date=od, employee=emp, department=dept, memo=memo)
        st.dataframe(df_csv, use_container_width=True, hide_index=True, height=300)
        st.download_button(
            t(f"⬇️ {sel_sup} 発注 CSV ダウンロード"),
            data=df_csv.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"発注書_{sel_sup}_{datetime.now():%Y%m%d}.csv",
            mime="text/csv", type="primary",
        )
        # 全件まとめ
        st.download_button(
            t("⬇️ 全 SKU 推奨清单 CSV (全仕入先)"),
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"発注推奨_全件_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
        )

# ------------------------------------------------------------
# Tab 2: 仕入先ビュー
# ------------------------------------------------------------
with tab_sup:
    if not _has_supplier_quotes():
        st.info(t("先に「📊 発注計算」タブで仕入先管理リストをアップロード"))
    else:
        sups = [r[0] for r in conn.execute(
            "SELECT DISTINCT supplier_name FROM supplier_quote ORDER BY zone_rank, supplier_name"
        ).fetchall()]
        sel = st.selectbox(t("仕入先"), sups, key="sup_view_sel")
        rows = conn.execute(
            "SELECT jan, display_name, unit_price, lot_size, case_qty, min_order_amount, "
            "order_condition, lead_time_text, zone, zone_rank, nst_supplier_code "
            "FROM supplier_quote WHERE supplier_name = ? ORDER BY display_name", (sel,)
        ).fetchall()
        df_q = pd.DataFrame([dict(r) for r in rows])
        zone = df_q["zone"].iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(t("zone"), ZONE_LABEL.get(zone, zone))
        c2.metric(t("取扱 SKU 数"), f"{len(df_q):,}")
        moq_vals = df_q["min_order_amount"].dropna()
        c3.metric(t("注文最低金額"), f"¥{int(moq_vals.max()):,}" if len(moq_vals) and moq_vals.max() else t("制限なし"))
        c4.metric(t("NST コード"), df_q["nst_supplier_code"].iloc[0] or "-")
        # 発注条件の分布
        oc = df_q["order_condition"].dropna().value_counts()
        if len(oc):
            st.caption(t("発注条件: " + " / ".join(f"{k}({v})" for k, v in oc.items())))
        lt = df_q["lead_time_text"].dropna().value_counts()
        if len(lt):
            st.caption(t("納期: " + " / ".join(f"{k}({v})" for k, v in lt.items())))

        # 現在の発注金額 (発注計算済みなら)
        df_reco = st.session_state.get("po_reco")
        if df_reco is not None and not df_reco.empty:
            cur = df_reco[df_reco["supplier_name"] == sel]
            if not cur.empty:
                st.success(t(
                    f"📦 現在の発注推奨: {len(cur)} SKU · ¥{int(cur['line_cost'].sum()):,}"
                    + ("" if cur["meets_min_order"].iloc[0] else " · ⚠️ 最低受注額未達")
                ))

        st.dataframe(
            df_q[["jan", "display_name", "unit_price", "lot_size", "case_qty",
                  "min_order_amount", "order_condition", "lead_time_text"]]
            .rename(columns={"unit_price": "単価", "lot_size": "ロット", "case_qty": "入数",
                             "min_order_amount": "注文最低金額", "order_condition": "発注条件",
                             "lead_time_text": "納期"}),
            use_container_width=True, hide_index=True, height=480,
        )
        st.download_button(
            t(f"⬇️ {sel} 報価単 CSV"),
            data=df_q.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"報価単_{sel}.csv", mime="text/csv",
        )

# ------------------------------------------------------------
# Tab 3: 商品ビュー
# ------------------------------------------------------------
with tab_item:
    if not _has_supplier_quotes():
        st.info(t("先に「📊 発注計算」タブで仕入先管理リストをアップロード"))
    else:
        jan_in = st.text_input(t("JAN コードを入力"), placeholder="4987241173242")
        if jan_in.strip():
            jan = jan_in.strip()
            rows = conn.execute(
                "SELECT supplier_name, display_name, unit_price, lot_size, min_order_amount, "
                "order_condition, lead_time_text, zone, zone_rank "
                "FROM supplier_quote WHERE jan = ? ORDER BY zone_rank, unit_price", (jan,)
            ).fetchall()
            if not rows:
                st.warning(t(f"❌ JAN {jan} はどの仕入先 sheet にもありません (新商品 or SD/ハリマ に询价が必要)"))
            else:
                from shared.purchase_engine import ZONE_MARKUP
                df_c = pd.DataFrame([dict(r) for r in rows])
                df_c["実質単価(比価)"] = df_c.apply(lambda r: round(r["unit_price"] * ZONE_MARKUP.get(r["zone"], 1.0)), axis=1)
                df_c["zone"] = df_c["zone"].map(lambda z: ZONE_LABEL.get(z, z))
                name = df_c["display_name"].dropna().iloc[0] if df_c["display_name"].notna().any() else "(名称不明)"
                st.markdown(f"**{name}** — {len(df_c)} 仕入先で取扱")
                # 月販 (輸出概要)
                ms = conn.execute(
                    "SELECT period_start, SUM(qty_sold) q FROM shop_sales "
                    "WHERE jan = ? AND source = 'export_item' GROUP BY period_start ORDER BY period_start", (jan,)
                ).fetchall()
                if ms:
                    st.caption(t("月販 (輸出概要): " + " / ".join(f"{r[0]}:{int(r[1] or 0)}" for r in ms)))
                # 推奨先 highlight
                best = df_c.sort_values(["zone_rank", "実質単価(比価)"]).iloc[0]
                st.success(t(f"→ 推奨仕入先: {best['supplier_name']} ({best['zone']}) · 単価 ¥{int(best['unit_price'])} · ロット {int(best['lot_size'] or 1)}"))
                st.dataframe(
                    df_c[["supplier_name", "zone", "unit_price", "実質単価(比価)", "lot_size",
                          "min_order_amount", "order_condition", "lead_time_text"]]
                    .rename(columns={"supplier_name": "仕入先", "unit_price": "単価", "lot_size": "ロット",
                                     "min_order_amount": "注文最低金額", "order_condition": "発注条件",
                                     "lead_time_text": "納期"}),
                    use_container_width=True, hide_index=True,
                )

# ------------------------------------------------------------
# Tab 4: 品牌ビュー
# ------------------------------------------------------------
with tab_maker:
    if not _has_supplier_quotes():
        st.info(t("先に「📊 発注計算」タブで仕入先管理リストをアップロード"))
    else:
        # item_v2 から maker → jan のマップ; supplier_quote と JOIN
        makers = [r[0] for r in conn.execute(
            "SELECT DISTINCT maker FROM item_v2 WHERE maker IS NOT NULL AND maker <> '' ORDER BY maker"
        ).fetchall()]
        if not makers:
            st.warning(t("item_v2 に maker データがありません (商品主档を先にインポート)"))
        else:
            sel_mk = st.selectbox(t("メーカー"), makers, key="maker_view_sel")
            rows = conn.execute(
                "SELECT i.jan, i.display_name, q.supplier_name, q.unit_price, q.lot_size, "
                "q.zone, q.zone_rank, q.min_order_amount "
                "FROM item_v2 i JOIN supplier_quote q ON q.jan = i.jan "
                "WHERE i.maker = ? ORDER BY i.jan, q.zone_rank, q.unit_price", (sel_mk,)
            ).fetchall()
            if not rows:
                st.info(t(f"{sel_mk} の SKU は仕入先 sheet に見つかりません"))
            else:
                df_m = pd.DataFrame([dict(r) for r in rows])
                n_sku = df_m["jan"].nunique()
                n_sup = df_m["supplier_name"].nunique()
                c1, c2 = st.columns(2)
                c1.metric(t("SKU 数 (報価あり)"), f"{n_sku}")
                c2.metric(t("関与仕入先数"), f"{n_sup}")
                # SKU 毎の最安仕入先
                best_per_sku = (
                    df_m.sort_values(["jan", "zone_rank", "unit_price"])
                    .groupby("jan").first().reset_index()
                )
                best_per_sku["zone"] = best_per_sku["zone"].map(lambda z: ZONE_LABEL.get(z, z))
                st.markdown(t("#### SKU 別 推奨仕入先 (zone 優先 → 単価最安)"))
                st.dataframe(
                    best_per_sku[["jan", "display_name", "supplier_name", "zone", "unit_price", "lot_size"]]
                    .rename(columns={"supplier_name": "推奨仕入先", "unit_price": "単価", "lot_size": "ロット"}),
                    use_container_width=True, hide_index=True, height=400,
                )
                # 仕入先別 SKU 数
                st.markdown(t("#### 仕入先別カバレッジ"))
                cov = df_m.groupby(["zone", "supplier_name"])["jan"].nunique().reset_index().rename(columns={"jan": "SKU数"})
                cov["zone"] = cov["zone"].map(lambda z: ZONE_LABEL.get(z, z))
                st.dataframe(cov.sort_values("SKU数", ascending=False), use_container_width=True, hide_index=True)
