"""模块 #17 价格改善 · 当前进价 vs 最低进价 找改善空间.

业务:
- 对每个 SKU 算 need_qty = max(sold - stock + ⌈sold×0.5⌉ - ordered, 0)
- 在 purchase_data 中按 lot 接近度选「当前会用的进价」
- 跟同 JAN 全行的 min(price) 对比,差额>0 → 改善对象
"""
from __future__ import annotations

import math
import re

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("价格改善"), page_icon="💰", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("💰 价格改善"))
st.caption(t("当前进价 vs 同 JAN 最低进价 · 找改善空间"))


def _normalize_jan(x):
    s = str(x).strip() if x is not None else ""
    if re.fullmatch(r"\d+(\.0+)?", s):
        return str(int(float(s)))
    return s


def _df(sql: str) -> pd.DataFrame:
    return pd.DataFrame([dict(r) for r in conn.execute(sql).fetchall()])


with st.spinner(t("📊 数据加载中...")):
    df_sales = _df("SELECT * FROM sales_line")
    df_purchase = _df("SELECT * FROM purchase_data")
    df_item = _df("SELECT * FROM item_master")

if df_sales.empty or df_purchase.empty or df_item.empty:
    st.warning(t("必要数据不足（需要 sales / purchase_data / item_master）"))
    st.stop()

# 字段统一
sales_jan_col = "jan" if "jan" in df_sales.columns else "internal_id"
df_sales["jan"] = df_sales[sales_jan_col].apply(_normalize_jan)
df_sales["quantity_sold"] = pd.to_numeric(
    df_sales.get("quantity_sold", df_sales.get("qty", 0)), errors="coerce"
).fillna(0).astype(int)
if "stock_available" not in df_sales.columns:
    df_sales["stock_available"] = 0
if "stock_ordered" not in df_sales.columns:
    df_sales["stock_ordered"] = 0
df_sales["stock_available"] = pd.to_numeric(df_sales["stock_available"], errors="coerce").fillna(0).astype(int)
df_sales["stock_ordered"] = pd.to_numeric(df_sales["stock_ordered"], errors="coerce").fillna(0).astype(int)

df_purchase["jan"] = df_purchase["jan"].apply(_normalize_jan)
df_purchase["price"] = pd.to_numeric(df_purchase["price"], errors="coerce").fillna(0)
df_purchase["order_lot"] = pd.to_numeric(df_purchase["order_lot"], errors="coerce").fillna(0).astype(int)
df_item["jan"] = df_item["jan"].apply(_normalize_jan)

current_prices: dict[str, float] = {}
for _, row in df_sales.iterrows():
    jan = row["jan"]
    sold = int(row["quantity_sold"])
    stock = int(row["stock_available"])
    ordered = int(row["stock_ordered"])
    options = df_purchase[df_purchase["jan"] == jan].copy()
    if options.empty:
        continue

    if stock >= sold:
        need_qty = 0
    else:
        need_qty = sold - stock + math.ceil(sold * 0.5) - ordered
        need_qty = max(need_qty, 0)
    if need_qty <= 0:
        continue

    options = options[options["order_lot"] > 0]
    if options.empty:
        continue
    options["diff"] = (options["order_lot"] - need_qty).abs()

    smaller = options[options["order_lot"] <= need_qty]
    if not smaller.empty:
        best = smaller.loc[smaller["diff"].idxmin()]
    else:
        near = options[
            (options["order_lot"] > need_qty)
            & (options["order_lot"] <= need_qty * 1.5)
            & (options["order_lot"] != 1)
        ]
        if not near.empty:
            best = near.loc[near["diff"].idxmin()]
        else:
            one = options[options["order_lot"] == 1]
            best = one.iloc[0] if not one.empty else options.sort_values("order_lot").iloc[0]

    current_prices[jan] = float(best["price"])

min_prices = df_purchase.groupby("jan")["price"].min().to_dict()

rows = []
for jan, cur_price in current_prices.items():
    if jan in min_prices and min_prices[jan] < cur_price:
        item = df_item[df_item["jan"] == jan].head(1)
        if not item.empty:
            rows.append({
                "商品コード": item.iloc[0].get("item_code", ""),
                "JAN": jan,
                "メーカー名": item.iloc[0].get("brand", item.iloc[0].get("maker", "")),
                "現在の仕入価格": cur_price,
                "最安値の仕入価格": min_prices[jan],
                "差分": round(min_prices[jan] - cur_price, 2),
            })

if not rows:
    st.info(t("没找到可改善的商品。"))
    st.stop()

df_result = pd.DataFrame(rows).sort_values("差分")

st.success(t(f"✅ 改善对象: {len(df_result)} 件"))
st.dataframe(df_result, use_container_width=True)

csv = df_result.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    t("📥 改善清单 CSV 下载"),
    data=csv,
    file_name="price_improvement_list.csv",
    mime="text/csv",
    key="price_improve_dl",
)
