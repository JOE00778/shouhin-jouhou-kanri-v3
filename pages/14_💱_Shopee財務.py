"""模块 ④ Shopee 財務 v2 · 数据源对齐 Boss 提供的两份原表.

数据源:
1. `shopee_orders_raw`
   ← 来自 订单导出-*.xlsx Sheet0 (8 列)
   A=支付币种 B=单价 C=发货数量 D=本地SKU E=支付金额 F=平台 G=订单号 H=店铺
   订单维度，提供 订单号 + SKU + 售价 + 平台/店铺

2. `shopee_income_lines`
   ← 来自 ph.mtkshop.ph.income.已拨款.*.xlsx Income sheet (R6 表头, 46 列)
   拨款维度，提供各项扣费 + 拨款金额

业务: 按 订单号 join → 订单级对账（商品原价 → 各项扣费 → 净拨款）
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("Shopee 財務"), page_icon="💱", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("💱 Shopee 財務"))
st.caption(t(
    "数据源: 订单导出.xlsx (订单+SKU) + ph.mtkshop.ph.income.已拨款.xlsx (拨款扣费) · "
    "按订单号对账"
))


def _df(sql: str, params=None) -> pd.DataFrame:
    rs = conn.execute(sql, params or {}).fetchall()
    return pd.DataFrame([dict(r) for r in rs])


# ============================================================
# 数据加载
# ============================================================
df_orders = _df("SELECT * FROM shopee_orders_raw")
df_income = _df("SELECT * FROM shopee_income_lines")

if df_orders.empty and df_income.empty:
    st.warning(t(
        "⚠️ 数据为空。请到「⚙️ 数据导入与设置」上传:\n"
        "1) 订单导出-*.xlsx (订单 ID + SKU)\n"
        "2) ph.mtkshop.ph.income.已拨款.*.xlsx (拨款扣费)"
    ))
    st.stop()

# ============================================================
# 期间筛选 (按拨款时间)
# ============================================================
periods = []
if not df_income.empty and "payout_date" in df_income.columns:
    periods = sorted(df_income["payout_date"].dropna().unique().tolist(), reverse=True)

c1, c2, c3 = st.columns([1.5, 1.5, 1])
with c1:
    if periods:
        sel_period = st.selectbox(t("拨款日期"), [t("全部")] + periods)
        if sel_period != t("全部"):
            df_income = df_income[df_income["payout_date"] == sel_period]
    else:
        sel_period = t("全部")
with c2:
    keyword = st.text_input(t("订单号搜索"), "")
with c3:
    shops = []
    if not df_orders.empty and "shop_name" in df_orders.columns:
        shops = sorted(df_orders["shop_name"].dropna().unique().tolist())
    if shops:
        sel_shop = st.selectbox(t("店铺"), [t("全部")] + shops)
        if sel_shop != t("全部"):
            df_orders = df_orders[df_orders["shop_name"] == sel_shop]

if keyword.strip():
    kw = keyword.strip()
    if not df_orders.empty:
        df_orders = df_orders[df_orders["order_no"].astype(str).str.contains(kw, na=False)]
    if not df_income.empty:
        df_income = df_income[df_income["order_no"].astype(str).str.contains(kw, na=False)]

# ============================================================
# 数值化
# ============================================================
fee_cols = [
    "gross_price", "product_discount", "refund_amount", "shopee_rebate",
    "seller_voucher", "seller_voucher_jv", "seller_shopee_coin", "seller_shopee_coin_jv",
    "buyer_shipping", "shopee_shipping_subsidy", "seller_shipping",
    "return_shipping", "return_to_seller_ship", "shipping_insurance_save",
    "affiliate_commission", "commission",
    "fbs_overseas_fail", "fbs_overseas_return", "service_fee",
    "shipping_insurance_fee", "transaction_fee", "fbs_fee",
    "payout_amount",
]
if not df_income.empty:
    for c in fee_cols:
        if c in df_income.columns:
            df_income[c] = pd.to_numeric(df_income[c], errors="coerce").fillna(0)

# ============================================================
# KPI
# ============================================================
n_orders = len(df_orders) if not df_orders.empty else 0
n_income = len(df_income) if not df_income.empty else 0

if not df_income.empty:
    total_gross = float(df_income["gross_price"].sum()) if "gross_price" in df_income else 0.0
    total_payout = float(df_income["payout_amount"].sum()) if "payout_amount" in df_income else 0.0
    # 总扣费 = 商品原价 - 拨款金额 (粗略口径)
    total_deduct = total_gross - total_payout
    n_refund = int((df_income["refund_amount"] < 0).sum()) if "refund_amount" in df_income else 0
else:
    total_gross = total_payout = total_deduct = 0.0
    n_refund = 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("订单数"), f"{n_orders:,}")
c2.metric(t("拨款行数"), f"{n_income:,}")
c3.metric(t("商品原价合计"), f"₱{total_gross:,.0f}")
c4.metric(t("总扣费"), f"₱{total_deduct:,.0f}")
c5.metric(t("拨款金额合计"), f"₱{total_payout:,.0f}")

st.divider()

# ============================================================
# Tab 视图
# ============================================================
tab_recon, tab_orders, tab_income = st.tabs([
    t("📋 订单级对账（join）"),
    t("📦 订单导出（原始）"),
    t("💰 拨款明细（原始）"),
])

with tab_recon:
    if df_orders.empty or df_income.empty:
        st.info(t("订单导出 + 拨款明细 都需上传后才能对账。"))
    else:
        # 按订单号 join
        merged = df_orders.merge(
            df_income[[
                "order_no", "buyer_account", "order_created_at", "payout_completed_at",
                "gross_price", "product_discount", "refund_amount",
                "commission", "service_fee", "transaction_fee",
                "buyer_shipping", "seller_shipping",
                "payout_amount", "payout_date",
            ]],
            on="order_no",
            how="outer",
            suffixes=("_o", "_i"),
        )
        # 计算净到账（= 拨款金额 if 有, 否则 = payment_amount - 各项扣费）
        merged["净到账"] = pd.to_numeric(merged.get("payout_amount"), errors="coerce").fillna(0)

        show_cols = [
            "order_no", "platform", "shop_name", "local_sku",
            "currency", "payment_amount",
            "buyer_account", "order_created_at",
            "gross_price", "product_discount", "refund_amount",
            "commission", "service_fee", "transaction_fee",
            "buyer_shipping", "seller_shipping",
            "payout_amount", "payout_date", "净到账",
        ]
        show_cols = [c for c in show_cols if c in merged.columns]
        merged = merged[show_cols].sort_values("order_no")
        st.dataframe(merged, use_container_width=True, hide_index=True, height=500)
        st.caption(t(f"共 {len(merged):,} 行 (订单 ⊕ 拨款 outer join)"))
        csv = merged.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("📥 对账明细 CSV"),
            data=csv,
            file_name=f"shopee_recon_{sel_period}.csv",
            mime="text/csv",
        )

with tab_orders:
    if df_orders.empty:
        st.info(t("订单导出.xlsx 未上传。"))
    else:
        cols = ["order_no", "platform", "shop_name", "currency",
                "local_sku", "unit_price", "ship_qty", "payment_amount"]
        cols = [c for c in cols if c in df_orders.columns]
        st.dataframe(df_orders[cols], use_container_width=True, hide_index=True, height=500)
        st.caption(t(f"共 {len(df_orders):,} 条订单"))
        csv = df_orders[cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("📥 订单导出 CSV"),
            data=csv,
            file_name="shopee_orders_raw.csv",
            mime="text/csv",
            key="dl_orders",
        )

with tab_income:
    if df_income.empty:
        st.info(t("拨款明细.xlsx 未上传。"))
    else:
        cols = [
            "seq", "order_no", "buyer_account", "order_created_at",
            "payout_completed_at", "payout_date",
            "gross_price", "product_discount", "refund_amount",
            "commission", "service_fee", "transaction_fee",
            "buyer_shipping", "shopee_shipping_subsidy", "seller_shipping",
            "payout_amount",
        ]
        cols = [c for c in cols if c in df_income.columns]
        st.dataframe(df_income[cols], use_container_width=True, hide_index=True, height=500)
        st.caption(t(f"共 {len(df_income):,} 行拨款"))
        csv = df_income[cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("📥 拨款明细 CSV"),
            data=csv,
            file_name="shopee_income_lines.csv",
            mime="text/csv",
            key="dl_income",
        )
