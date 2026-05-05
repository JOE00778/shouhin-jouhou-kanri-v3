"""模块 ④ Shopee 財務 v3 · 按周 × 店铺/市场汇总.

业务节奏: Shopee 拨款以「周」为单位 → 对账粒度也按周

数据源:
- shopee_orders_raw  ← 订单导出.xlsx (订单号 / 店铺 / 平台 / SKU)
- shopee_income_lines ← ph.mtkshop.ph.income.已拨款.xlsx (拨款扣费, 含 seller_account)

聚合维度:
- 周 (payout_date 所在 ISO 周, 用周一日期表示)
- 店铺 (shop_name, 来自订单导出)
- 市场 (从 seller_account 末段或 shop_name 后缀提取, 如 mtkshop.ph → PH)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from shared.db import get_connection
from shared.forex import FX_TO_JPY
from shared.i18n import lang_selector, t

st.set_page_config(page_title=t("Shopee 財務"), page_icon="💱", layout="wide")
lang_selector()
conn = get_connection()

st.title(t("💱 Shopee 財務"))
st.caption(t(
    "按周 × 店铺/市场汇总 · 数据源: 订单导出.xlsx + ph.mtkshop.ph.income.已拨款.xlsx"
))


def _df(sql: str, params=None) -> pd.DataFrame:
    rs = conn.execute(sql, params or {}).fetchall()
    return pd.DataFrame([dict(r) for r in rs])


def _week_start(d) -> str | None:
    """ISO 周开始日 (周一)。返回 'YYYY-MM-DD' 或 None."""
    if d is None or pd.isna(d):
        return None
    try:
        dt = pd.to_datetime(d).date()
    except Exception:
        return None
    monday = dt - timedelta(days=dt.weekday())
    return monday.isoformat()


def _market_region(_label: str | None = None) -> str:
    """市场维度:
    当前阶段 Shopee + Lazada 都归属东南亚。Coupang(韩国) 后置.
    一律返回「东南亚」直到将来扩展。"""
    return t("东南亚")


def _country_from_shop(shop: str | None) -> str:
    """国家代码 (sub-market): TW/PH/MY/SG/ID/VN/TH/BR/...
    - 'Smikie Japan.ph' → 'PH'
    - '官方旗艦店（日本直郵）' → 'TW' (跨境到台湾)
    """
    if not shop:
        return "?"
    s = str(shop).lower()
    m = re.search(r"\.([a-z]{2,3})\b", s)
    if m:
        return m.group(1).upper()
    s_orig = str(shop)
    if "日本直" in s_orig or "直郵" in s_orig or "旗艦" in s_orig or "台灣" in s_orig:
        return "TW"
    return "OTHER"


def _country_from_seller(seller: str | None) -> str:
    """seller_account 'mtkshop.ph' → 'PH'."""
    if not seller:
        return "?"
    s = str(seller).lower()
    m = re.search(r"\.([a-z]{2,3})$", s)
    return m.group(1).upper() if m else s.upper()


# ============================================================
# 数据加载
# ============================================================
df_orders = _df("SELECT * FROM shopee_orders_raw")
df_income = _df("SELECT * FROM shopee_income_lines")

if df_orders.empty and df_income.empty:
    st.warning(t(
        "⚠️ 数据为空。请到「⚙️ 数据导入与设置」上传:\n"
        "1) 订单导出-*.xlsx (订单 ID + SKU + 店铺)\n"
        "2) ph.mtkshop.ph.income.已拨款.*.xlsx (拨款扣费)"
    ))
    st.stop()

# 数值化
fee_cols = [
    "gross_price", "product_discount", "refund_amount", "shopee_rebate",
    "seller_voucher", "buyer_shipping", "shopee_shipping_subsidy",
    "seller_shipping", "commission", "service_fee",
    "transaction_fee", "fbs_fee", "payout_amount",
]
if not df_income.empty:
    for c in fee_cols:
        if c in df_income.columns:
            df_income[c] = pd.to_numeric(df_income[c], errors="coerce").fillna(0)
    # 周 / 月 / 市场
    df_income["week"] = df_income["payout_date"].apply(_week_start)
    df_income["month"] = df_income["payout_date"].apply(
        lambda d: pd.to_datetime(d).strftime("%Y-%m") if pd.notna(d) else None
    )
    df_income["country"] = df_income["seller_account"].apply(_country_from_seller)
    df_income["market"] = df_income["seller_account"].apply(_market_region)
    if "platform" not in df_income.columns:
        df_income["platform"] = "Shopee"  # 拨款表都来自 Shopee 平台

    # 汇率系数列(用于后续生成 jpy 副本; 现在还不换算)
    df_income["_jpy_rate"] = df_income["country"].map(FX_TO_JPY).fillna(1.0)

# 订单导出: 给每个订单算周(用 payout_date 不可得 → 用 income 表 join)
# 简化: 仅做 shop_name → market 映射
if not df_orders.empty:
    df_orders["country"] = df_orders["shop_name"].apply(_country_from_shop)
    df_orders["market"] = df_orders["shop_name"].apply(_market_region)
    # platform 字段在订单导出原表里就有 (F 列)
    if "platform" not in df_orders.columns:
        df_orders["platform"] = "Shopee"
    if not df_income.empty:
        # join 拿订单的 week / month (来自 income.payout_date)
        df_orders = df_orders.merge(
            df_income[["order_no", "week", "month"]].drop_duplicates("order_no"),
            on="order_no", how="left",
        )

# ============================================================
# 期间筛选 (粒度切换 + 期间 + 市场)
# ============================================================
GRANULARITY_LABELS = {t("按周"): "week", t("按月"): "month"}

c0, c1, c2 = st.columns([1, 1.5, 1.5])
with c0:
    gran_label = st.radio(t("粒度"), list(GRANULARITY_LABELS.keys()), horizontal=False)
    gran_col = GRANULARITY_LABELS[gran_label]  # "week" or "month"

periods = []
if not df_income.empty and gran_col in df_income.columns:
    periods = sorted(df_income[gran_col].dropna().unique().tolist(), reverse=True)

with c1:
    period_label = t("拨款周（周一）") if gran_col == "week" else t("拨款月（YYYY-MM）")
    sel_period = st.selectbox(period_label, [t("全部")] + periods)
with c2:
    platforms_in_data = []
    if not df_orders.empty:
        platforms_in_data += df_orders["platform"].dropna().unique().tolist()
    if not df_income.empty:
        platforms_in_data += df_income["platform"].dropna().unique().tolist()
    platforms_in_data = sorted(set(platforms_in_data))
    sel_platform = st.selectbox(t("平台"), [t("全部")] + platforms_in_data)

# 应用筛选
if sel_period != t("全部") and not df_income.empty:
    df_income = df_income[df_income[gran_col] == sel_period]
    if not df_orders.empty and gran_col in df_orders.columns:
        df_orders = df_orders[(df_orders[gran_col] == sel_period) | df_orders[gran_col].isna()]
if sel_platform != t("全部"):
    if not df_income.empty and "platform" in df_income.columns:
        df_income = df_income[df_income["platform"] == sel_platform]
    if not df_orders.empty:
        df_orders = df_orders[df_orders["platform"] == sel_platform]

# ============================================================
# 筛选后生成 JPY 副本（仅给 KPI / 聚合 tab 用，原始 tab 仍 PHP）
# ============================================================
if not df_income.empty:
    df_income_jpy = df_income.copy()
    for c in fee_cols:
        if c in df_income_jpy.columns:
            df_income_jpy[c] = df_income_jpy[c] * df_income_jpy["_jpy_rate"]
else:
    df_income_jpy = df_income

# ============================================================
# KPI (JPY 视角)
# ============================================================
n_orders = len(df_orders) if not df_orders.empty else 0
if not df_income_jpy.empty:
    total_gross = float(df_income_jpy.get("gross_price", pd.Series([0])).sum())
    total_payout = float(df_income_jpy.get("payout_amount", pd.Series([0])).sum())
    total_deduct = total_gross - total_payout
    n_periods = df_income_jpy[gran_col].nunique()
else:
    total_gross = total_payout = total_deduct = 0.0
    n_periods = 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("覆盖期数"), f"{n_periods:,}")
c2.metric(t("订单数"), f"{n_orders:,}")
c3.metric(t("商品原价合计 (¥)"), f"¥{total_gross:,.0f}")
c4.metric(t("总扣费 (¥)"), f"¥{total_deduct:,.0f}")
c5.metric(t("拨款金额合计 (¥)"), f"¥{total_payout:,.0f}")

# 市场提示 (当前唯一)
st.info(t(
    "📍 市场: 东南亚（Shopee + Lazada）· Coupang 等其他市场后置 · "
    "💴 所有 tab (KPI / 聚合 / 原始) 都按 country × 公司固定汇率换算为日元 · "
    "PHP=2.4 / TWD=4.57 / MYR=36.48 / SGD=113.44 / USD=145 等 (详见首页折叠区)"
))
st.divider()

# ============================================================
# Tab: 平台 / 店铺 / 市场×粒度 + 原始数据
# ============================================================
period_col_label = t("周") if gran_col == "week" else t("月")

tab_platform, tab_shop, tab_market, tab_raw_o, tab_raw_i = st.tabs([
    f"📱 {period_col_label} × {t('平台')}",
    f"🏪 {period_col_label} × {t('店铺')}",
    f"🌐 {period_col_label} × {t('市场')}",
    t("📦 订单导出（原始）"),
    t("💰 拨款明细（原始）"),
])


def _fmt_money(x):
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return x


def _agg_dim(df, gran_col, dim_col):
    """通用聚合: by 期间 × dim_col."""
    return df.groupby([gran_col, dim_col], as_index=False).agg(
        gross_price=("gross_price", "sum"),
        commission=("commission", "sum"),
        service_fee=("service_fee", "sum"),
        transaction_fee=("transaction_fee", "sum"),
        buyer_shipping=("buyer_shipping", "sum"),
        seller_shipping=("seller_shipping", "sum"),
        payout_amount=("payout_amount", "sum"),
        n_orders=("order_no", "nunique"),
    )


def _format_agg(agg, gran_col, dim_col, dim_label, period_col_label):
    """格式化聚合表 → display df + raw csv."""
    agg = agg.copy()
    agg["net_deduct"] = agg["gross_price"] - agg["payout_amount"]
    show = agg.rename(columns={
        gran_col: period_col_label,
        dim_col: dim_label,
        "gross_price": t("商品原价"),
        "commission": t("佣金"),
        "service_fee": t("服务费"),
        "transaction_fee": t("交易手续费"),
        "buyer_shipping": t("买家运费"),
        "seller_shipping": t("卖家运费"),
        "payout_amount": t("拨款金额"),
        "n_orders": t("订单数"),
        "net_deduct": t("净扣费"),
    }).copy()
    for col in (t("商品原价"), t("佣金"), t("服务费"), t("交易手续费"),
                t("买家运费"), t("卖家运费"), t("拨款金额"), t("净扣费")):
        if col in show.columns:
            show[col] = show[col].map(_fmt_money)
    return show, agg


with tab_platform:
    if df_income_jpy.empty:
        st.info(t("拨款明细未上传, 无法按平台聚合。"))
    else:
        agg = _agg_dim(df_income_jpy, gran_col, "platform")
        show, raw = _format_agg(agg, gran_col, "platform", t("平台"), period_col_label)
        show = show.sort_values([period_col_label, t("平台")], ascending=[False, True])
        st.dataframe(show, use_container_width=True, hide_index=True, height=460)
        st.caption(t(f"共 {len(agg):,} 行 ({period_col_label} × 平台)"))
        st.download_button(
            f"📥 {period_col_label}×{t('平台')} CSV",
            data=raw.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"shopee_{gran_col}_platform.csv",
            mime="text/csv",
            key="dl_platform",
        )

with tab_market:
    if df_income_jpy.empty:
        st.info(t("拨款明细未上传, 无法按市场聚合。"))
    else:
        agg = df_income_jpy.groupby([gran_col, "market"], as_index=False).agg(
            gross_price=("gross_price", "sum"),
            commission=("commission", "sum"),
            service_fee=("service_fee", "sum"),
            transaction_fee=("transaction_fee", "sum"),
            buyer_shipping=("buyer_shipping", "sum"),
            seller_shipping=("seller_shipping", "sum"),
            payout_amount=("payout_amount", "sum"),
            n_orders=("order_no", "nunique"),
        )
        agg["net_deduct"] = agg["gross_price"] - agg["payout_amount"]
        show = agg.rename(columns={
            gran_col: period_col_label,
            "market": t("市场"),
            "gross_price": t("商品原价"),
            "commission": t("佣金"),
            "service_fee": t("服务费"),
            "transaction_fee": t("交易手续费"),
            "buyer_shipping": t("买家运费"),
            "seller_shipping": t("卖家运费"),
            "payout_amount": t("拨款金额"),
            "n_orders": t("订单数"),
            "net_deduct": t("净扣费"),
        }).copy()
        show = show.sort_values([period_col_label, t("市场")], ascending=[False, True])
        for col in (t("商品原价"), t("佣金"), t("服务费"), t("交易手续费"),
                    t("买家运费"), t("卖家运费"), t("拨款金额"), t("净扣费")):
            if col in show.columns:
                show[col] = show[col].map(_fmt_money)
        st.dataframe(show, use_container_width=True, hide_index=True, height=460)
        st.caption(t(f"共 {len(agg):,} 行 ({period_col_label} × 市场)"))
        csv = agg.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            f"📥 {period_col_label}×{t('市场')} CSV",
            data=csv,
            file_name=f"shopee_{gran_col}_market.csv",
            mime="text/csv",
            key="dl_market",
        )

with tab_shop:
    if df_income_jpy.empty or df_orders.empty:
        st.info(t("订单导出 + 拨款明细 都需上传后才能按店铺聚合 (店铺信息来自订单导出)。"))
    else:
        # join 订单 → 拨款 (按订单号), 把 shop_name 带过来
        joined = df_income_jpy.merge(
            df_orders[["order_no", "shop_name"]].drop_duplicates("order_no"),
            on="order_no", how="left",
        )
        joined["shop_name"] = joined["shop_name"].fillna(t("(无店铺信息)"))

        agg = joined.groupby([gran_col, "shop_name"], as_index=False).agg(
            gross_price=("gross_price", "sum"),
            commission=("commission", "sum"),
            service_fee=("service_fee", "sum"),
            transaction_fee=("transaction_fee", "sum"),
            buyer_shipping=("buyer_shipping", "sum"),
            seller_shipping=("seller_shipping", "sum"),
            payout_amount=("payout_amount", "sum"),
            n_orders=("order_no", "nunique"),
        )
        agg["net_deduct"] = agg["gross_price"] - agg["payout_amount"]
        show = agg.rename(columns={
            gran_col: period_col_label,
            "shop_name": t("店铺"),
            "gross_price": t("商品原价"),
            "commission": t("佣金"),
            "service_fee": t("服务费"),
            "transaction_fee": t("交易手续费"),
            "buyer_shipping": t("买家运费"),
            "seller_shipping": t("卖家运费"),
            "payout_amount": t("拨款金额"),
            "n_orders": t("订单数"),
            "net_deduct": t("净扣费"),
        }).copy()
        show = show.sort_values([period_col_label, t("拨款金额")], ascending=[False, False])
        for col in (t("商品原价"), t("佣金"), t("服务费"), t("交易手续费"),
                    t("买家运费"), t("卖家运费"), t("拨款金额"), t("净扣费")):
            if col in show.columns:
                show[col] = show[col].map(_fmt_money)
        st.dataframe(show, use_container_width=True, hide_index=True, height=460)
        st.caption(t(f"共 {len(agg):,} 行 ({period_col_label} × 店铺)"))
        csv = agg.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            f"📥 {period_col_label}×{t('店铺')} CSV",
            data=csv,
            file_name=f"shopee_{gran_col}_shop.csv",
            mime="text/csv",
            key="dl_shop",
        )

with tab_raw_o:
    if df_orders.empty:
        st.info(t("订单导出.xlsx 未上传。"))
    else:
        cols = ["order_no", "platform", "shop_name", "market",
                "currency", "local_sku", "unit_price", "ship_qty", "payment_amount"]
        cols = [c for c in cols if c in df_orders.columns]
        st.dataframe(df_orders[cols], use_container_width=True, hide_index=True, height=460)
        st.caption(t(f"共 {len(df_orders):,} 条订单"))
        csv = df_orders[cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("📥 订单导出 CSV"), data=csv,
            file_name="shopee_orders_raw.csv", mime="text/csv", key="dl_o",
        )

with tab_raw_i:
    if df_income_jpy.empty:
        st.info(t("拨款明细.xlsx 未上传。"))
    else:
        st.caption(t(
            "💴 所有金额已按 country (国家货币) × 公司固定汇率换算为日元 · "
            "country=PHP×2.4 / TWD×4.57 / MYR×36.48 / SGD×113.44 / USD×145 等"
        ))
        cols = [
            "week", "month", "market", "country", "_jpy_rate",
            "seller_account", "payout_date",
            "order_no", "buyer_account", "order_created_at",
            "gross_price", "product_discount", "refund_amount",
            "commission", "service_fee", "transaction_fee",
            "buyer_shipping", "seller_shipping",
            "payout_amount",
        ]
        cols = [c for c in cols if c in df_income_jpy.columns]
        st.dataframe(df_income_jpy[cols], use_container_width=True, hide_index=True, height=460)
        st.caption(t(f"共 {len(df_income_jpy):,} 行拨款 (金额已换为 JPY)"))
        csv = df_income_jpy[cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("📥 拨款明细 CSV (JPY)"), data=csv,
            file_name="shopee_income_lines_jpy.csv", mime="text/csv", key="dl_i",
        )
