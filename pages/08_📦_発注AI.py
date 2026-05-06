"""模块 #8 発注AI · 自动订货建议（JD 库存基线）

Port from order-management-app · order_ai mode + JDモード仕入先別下载

业务：
- 输入: sales / purchase_data / item_master / warehouse_stock / purchase_history / benten_stock
- 输出: orders_available_based.csv（全量） + orders_{supplier}.csv（按供应商）
- 算法:
    A/B 档: 在庫+発注済 < ⌈sold×1.2⌉ 触发 → 发注数 = ⌈sold×1.7⌉，按 lot 向上取整
    C/TEST: 在庫+発注済 > ⌊sold×0.7⌋ 不触发；不足分 = ⌈sold×倍率⌉ - 在庫 - 発注済
- 直近(today/yesterday)发注的 SKU 跳过
- 「上海」memo 的发注济量从 item_master 中扣减
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("発注AI"), page_icon="📦", layout="wide")
from shared.auth import require_password
require_password()
lang_selector()
conn = get_connection()

st.title(t("📦 発注AI"))
st.caption(t("✅ JD-千叶仓库库存为基线·自动算 need_qty + 最优 lot 选择"))


def _normalize_jan(x):
    s = str(x).strip() if x is not None else ""
    if re.fullmatch(r"\d+(\.0+)?", s):
        return str(int(float(s)))
    return s


def _normalize_rank_base(rank: str) -> str:
    """ランク -> A/B/C/'' (TEST/NEW 归 '')."""
    if not rank:
        return ""
    r = str(rank)
    for k in ("A", "B", "C"):
        if k in r:
            return k
    return ""


def _df(sql: str) -> pd.DataFrame:
    return pd.DataFrame([dict(r) for r in conn.execute(sql).fetchall()])


if st.button(t("🤖 开始计算"), type="primary"):
    with st.spinner(t("📦 数据加载中...")):
        df_sales = _df("SELECT * FROM sales_line")
        df_purchase = _df("SELECT * FROM purchase_data")
        df_master = _df("SELECT * FROM item_master")
        df_warehouse = _df("SELECT * FROM warehouse_stock")
        df_history = _df("SELECT * FROM purchase_history")
        df_benten = _df("SELECT * FROM benten_stock")

    if df_sales.empty or df_purchase.empty or df_master.empty:
        st.warning(t("必要数据不足（需要 sales / purchase_data / item_master）"))
        st.stop()
    if df_warehouse.empty:
        st.warning(t("warehouse_stock 数据不足"))
        st.stop()

    # 字段统一（兼容我们 schema 的列名）
    sales_jan_col = "jan" if "jan" in df_sales.columns else "internal_id"
    sold_col = "quantity_sold" if "quantity_sold" in df_sales.columns else "qty"
    df_sales["jan"] = df_sales[sales_jan_col].apply(_normalize_jan)
    df_sales["quantity_sold"] = pd.to_numeric(df_sales[sold_col], errors="coerce").fillna(0).astype(int)
    if "stock_available" not in df_sales.columns:
        df_sales["stock_available"] = 0
    df_sales["stock_available"] = pd.to_numeric(df_sales["stock_available"], errors="coerce").fillna(0).astype(int)

    df_purchase["jan"] = df_purchase["jan"].apply(_normalize_jan)
    df_purchase["order_lot"] = pd.to_numeric(df_purchase["order_lot"], errors="coerce").fillna(0).astype(int)
    df_purchase["price"] = pd.to_numeric(df_purchase["price"], errors="coerce")

    df_master["jan"] = df_master["jan"].apply(_normalize_jan)

    df_warehouse["product_code"] = df_warehouse["product_code"].apply(_normalize_jan)
    df_warehouse["stock_available"] = pd.to_numeric(df_warehouse["stock_available"], errors="coerce").fillna(0).astype(int)

    if df_history.empty:
        df_history = pd.DataFrame(columns=["jan", "quantity", "memo", "order_date"])
    df_history["jan"] = df_history["jan"].apply(_normalize_jan)
    df_history["quantity"] = pd.to_numeric(df_history["quantity"], errors="coerce").fillna(0).astype(int)
    df_history["memo"] = df_history["memo"].astype(str).fillna("")

    # 「上海」分从 item_master 発注済 中扣减
    df_shanghai = df_history[df_history["memo"].str.contains("上海", na=False)]
    df_shanghai_grouped = df_shanghai.groupby("jan")["quantity"].sum().reset_index(name="shanghai_quantity")
    if "発注済" not in df_master.columns:
        df_master["発注済"] = 0
    df_master = df_master.merge(df_shanghai_grouped, on="jan", how="left")
    df_master["shanghai_quantity"] = df_master["shanghai_quantity"].fillna(0).astype(int)
    df_master["発注済_修正後"] = (
        pd.to_numeric(df_master["発注済"], errors="coerce").fillna(0) - df_master["shanghai_quantity"]
    ).clip(lower=0)

    df_sales.drop(columns=["発注済"], errors="ignore", inplace=True)
    df_sales = df_sales.merge(df_master[["jan", "発注済_修正後"]], on="jan", how="left")
    df_sales["発注済"] = df_sales["発注済_修正後"].fillna(0).astype(int)

    rank_multiplier = {"Cランク": 1.0, "TEST": 1.5, "NEW": 1.5}

    # 直近发注（今日 / 昨日）跳过
    df_history["order_date_dt"] = pd.to_datetime(df_history["order_date"], errors="coerce").dt.date
    today = date.today()
    yesterday = today - timedelta(days=1)
    recent_jans = set(
        df_history[df_history["order_date_dt"].isin([today, yesterday])]["jan"]
        .dropna().astype(str).apply(_normalize_jan).unique().tolist()
    )

    results = []
    with st.spinner(t("🤖 計算中...")):
        for _, row in df_sales.iterrows():
            jan = row["jan"]
            sold = int(row["quantity_sold"])
            ordered = int(row["発注済"])

            stock_row = df_warehouse[df_warehouse["product_code"] == jan]
            stock = int(stock_row["stock_available"].values[0]) if not stock_row.empty else 0

            rank_row = df_master[df_master["jan"] == jan]
            rank = ""
            if not rank_row.empty and ("ランク" in df_master.columns):
                rk = rank_row.iloc[0]["ランク"]
                rank = str(rk) if pd.notna(rk) else ""
            base_rank = _normalize_rank_base(rank)

            if jan in recent_jans:
                continue

            current_total = stock + ordered

            # 发注点判定
            if base_rank in ("A", "B"):
                reorder_point = max(math.ceil(sold * 1.2), 1)
                if current_total >= reorder_point:
                    continue
            else:
                reorder_point = max(math.floor(sold * 0.7), 1)
                if current_total > reorder_point:
                    continue

            # 发注数基准
            if base_rank in ("A", "B"):
                base_needed = max(math.ceil(sold * 1.7), 0)
                if stock <= 1 and sold >= 1 and base_needed <= 0:
                    base_needed = 1
            else:
                m = rank_multiplier.get(rank, 1.0)
                need_raw = math.ceil(sold * m) - stock - ordered
                base_needed = 1 if (stock <= 1 and sold >= 1 and need_raw <= 0) else max(need_raw, 0)
                if base_needed <= 0:
                    continue

            # 仕入候选
            options_all = df_purchase[df_purchase["jan"] == jan].copy()
            valid = pd.DataFrame()
            if not options_all.empty:
                lots_pos = options_all[options_all["order_lot"] > 0].copy()
                valid = lots_pos[lots_pos["price"].notna() & (lots_pos["price"] > 0)].copy()

            # 价格无 → 空白行输出
            if valid.empty:
                results.append({
                    "jan": jan, "販売実績": sold, "在庫": stock, "発注済": ordered,
                    "理論必要数": base_needed,
                    "発注数": "", "ロット": "", "数量": "", "単価": "", "総額": "",
                    "仕入先": "", "ランク": rank,
                })
                continue

            options = valid.copy()
            need_for_lot = base_needed
            if base_rank in ("A", "B"):
                bigger_lots = options[options["order_lot"] >= need_for_lot]
                if not bigger_lots.empty:
                    best = bigger_lots.sort_values("order_lot").iloc[0]
                else:
                    best = options.sort_values("order_lot", ascending=False).iloc[0]
            else:
                options["diff"] = (options["order_lot"] - need_for_lot).abs()
                smaller = options[options["order_lot"] <= need_for_lot]
                if not smaller.empty:
                    best = smaller.loc[smaller["diff"].idxmin()]
                else:
                    near = options[
                        (options["order_lot"] > need_for_lot)
                        & (options["order_lot"] <= need_for_lot * 1.5)
                        & (options["order_lot"] != 1)
                    ]
                    if not near.empty:
                        best = near.loc[near["diff"].idxmin()]
                    else:
                        one = options[options["order_lot"] == 1]
                        best = one.iloc[0] if not one.empty else options.sort_values("order_lot").iloc[0]

            lot = int(best["order_lot"])
            sets = math.ceil(need_for_lot / lot)
            qty = sets * lot
            total_cost = qty * float(best["price"])

            results.append({
                "jan": jan, "販売実績": sold, "在庫": stock, "発注済": ordered,
                "理論必要数": base_needed,
                "発注数": int(qty), "ロット": lot, "数量": int(sets),
                "単価": int(best["price"]), "総額": int(total_cost),
                "仕入先": best.get("supplier", "不明") or "不明",
                "ランク": rank,
            })

    if not results:
        st.info(t("当前没有需要订货的商品。"))
        st.stop()

    result_df = pd.DataFrame(results)

    # 商品名・取扱区分 join
    if "商品コード" in df_master.columns:
        df_master["商品コード"] = df_master["商品コード"].astype(str).str.strip()
        result_df["jan"] = result_df["jan"].astype(str).str.strip()
        m = df_master[["商品コード", "商品名", "取扱区分"]].copy().rename(columns={"商品コード": "jan"})
        result_df = result_df.merge(m, on="jan", how="left")

    # 弁天库存
    if not df_benten.empty:
        bn = df_benten[["jan", "stock"]].copy().rename(columns={"stock": "弁天在庫"})
        bn["jan"] = bn["jan"].apply(_normalize_jan)
        result_df = result_df.merge(bn, on="jan", how="left")
        result_df["弁天在庫"] = result_df["弁天在庫"].fillna(0).astype(int)

    result_df.rename(columns={"在庫": "JD在庫"}, inplace=True)

    if "商品名" in result_df.columns:
        result_df = result_df[result_df["商品名"].notna()]
    if "取扱区分" in result_df.columns:
        result_df = result_df[result_df["取扱区分"] != "取扱中止"]
    else:
        st.warning(t("⚠️『取扱区分』列不存在,无法过滤已停售。"))

    column_order = [
        "jan", "商品名", "ランク", "販売実績", "JD在庫", "弁天在庫", "発注済",
        "理論必要数", "発注数", "ロット", "数量", "単価", "総額", "仕入先",
    ]
    result_df = result_df[[c for c in column_order if c in result_df.columns]]

    st.success(t(f"✅ 订货对象: {len(result_df)} 件"))
    st.dataframe(result_df, use_container_width=True)

    csv = result_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(t("📥 订货 CSV 下载"), data=csv, file_name="orders_available_based.csv", mime="text/csv")

    st.markdown("---")
    st.subheader(t("📦 按供应商分组下载"))
    if "仕入先" in result_df.columns:
        groups = result_df[result_df["仕入先"].notna() & (result_df["仕入先"] != "")].groupby("仕入先")
        for supplier, group in groups:
            sup_csv = group.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label=f"📥 {supplier}",
                data=sup_csv,
                file_name=f"orders_{supplier}.csv",
                mime="text/csv",
                key=f"sup_{supplier}",
            )
    else:
        st.info(t("仕入先列不存在。"))
