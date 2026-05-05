"""模块 ④ Shopee 財務 · 静态版（API 后置）。

支持：
- 月度选择器（2026-04 有数据）
- 站点切换 tab（PH / 全平台）
- KPI 卡片：总收入 / 总扣费 / 净到账 / 订单数 / 退款数
- 拨款汇总
- 订单级对账（含 fee 分列）
- 站点对比图表
"""
from __future__ import annotations

import pandas as pd
import sqlite3
import streamlit as st
from shared.i18n import t, lang_selector
import plotly.express as px
from pathlib import Path

st.set_page_config(page_title=t("Shopee 財務"), page_icon="💱", layout="wide")
st.title(t("💱 Shopee 財務"))
st.caption("Shopee 拨款 → 各项扣费 → 净到账 全链路对账（4 月份数据）")

from shared.db import get_connection, DB_PATH
DB = DB_PATH
conn = get_connection()
conn.row_factory = sqlite3.Row

# 月度选择器（默认 2026-04）
ym = st.selectbox("月度", ['2026-04'], index=0)

# 检查数据
order_count = conn.execute("SELECT COUNT(*) FROM shopee_orders").fetchone()[0]
if order_count == 0:
    st.warning("⚠️ 无数据。请在「⚙️ 数据导入与设置」上传 Shopee 拨款 + 订单 EXCEL。")
    st.stop()

# 获取平台列表
platforms = pd.read_sql_query(
    "SELECT DISTINCT platform FROM shopee_orders WHERE platform IS NOT NULL",
    conn
)
platform_list = sorted(platforms['platform'].tolist()) if not platforms.empty else ['Shopee']

# 站点切换 tab
tab_labels = platform_list + ['全平台']
tabs = st.tabs(tab_labels)

for tab_idx, (tab, plat) in enumerate(zip(tabs, platform_list + [None])):
    with tab:
        # SQL 过滤条件
        if plat is None:
            plat_filter = ""
            plat_where = ""
        else:
            plat_filter = f"AND platform = '{plat}'"
            plat_where = f"WHERE platform = '{plat}'"

        # KPI 卡片
        c1, c2, c3, c4, c5 = st.columns(5)

        # 总收入（payment_amount > 0）
        gross = pd.read_sql_query(
            f"SELECT COALESCE(SUM(payment_amount), 0) AS s, COUNT(*) AS n FROM shopee_orders WHERE payment_amount > 0 {plat_filter}",
            conn
        ).iloc[0]

        # 总扣费（费用表中的金额）
        fees_query = f"""
            SELECT COALESCE(SUM(f.amount), 0) AS s FROM shopee_fees f
            JOIN shopee_orders o ON f.order_no = o.order_no
            WHERE f.amount < 0 {plat_filter}
        """
        fees = pd.read_sql_query(fees_query, conn).iloc[0]

        # 净到账
        net_amount = gross['s'] + fees['s']

        # 退款数
        refund_count = conn.execute(
            f"SELECT COUNT(*) FROM shopee_orders WHERE payment_amount < 0 {plat_filter}"
        ).fetchone()[0]

        c1.metric("总收入", f"¥{gross['s']:,.0f}" if gross['s'] else "¥0")
        c2.metric("总扣费", f"¥{abs(fees['s']):,.0f}" if fees['s'] else "¥0")
        c3.metric("净到账", f"¥{net_amount:,.0f}")
        c4.metric("订单数", int(gross['n']))
        c5.metric("退款数", int(refund_count))

        # 拨款汇总
        st.subheader("📥 拨款汇总")
        st.info("📌 4 月拨款数据已录入系统。详细拨款明细表后续通过 Shopee API 同步。")

        # 订单级对账
        st.subheader("📋 订单级对账")

        orders = pd.read_sql_query(
            f"""
            SELECT o.order_no, o.sku_or_jan, o.unit_price, o.qty, o.payment_amount,
                   o.currency, o.platform, o.shop_name
            FROM shopee_orders o
            {plat_where}
            ORDER BY o.order_no
            """,
            conn
        )

        if not orders.empty:
            # 关联费用表，按 fee_type 分列
            order_nos_list = orders['order_no'].astype(str).tolist()
            if order_nos_list:
                placeholders = ','.join(f"'{no}'" for no in order_nos_list)
                fees_pivot = pd.read_sql_query(
                    f"""
                    SELECT order_no, fee_type, SUM(amount) as amount
                    FROM shopee_fees
                    WHERE order_no IN ({placeholders})
                    GROUP BY order_no, fee_type
                    """,
                    conn
                )

                if not fees_pivot.empty:
                    # 创建费用分列表
                    fees_wide = fees_pivot.pivot_table(
                        index='order_no',
                        columns='fee_type',
                        values='amount',
                        aggfunc='sum',
                        fill_value=0
                    ).reset_index()

                    # 合并订单和费用
                    merged = orders.merge(fees_wide, on='order_no', how='left').fillna(0)

                    # 计算净金额
                    fee_cols = [col for col in merged.columns if col not in
                               ['order_no', 'sku_or_jan', 'unit_price', 'qty', 'payment_amount', 'currency', 'platform', 'shop_name']]
                    if fee_cols:
                        merged['净金额'] = merged['payment_amount'] + merged[fee_cols].sum(axis=1)
                    else:
                        merged['净金额'] = merged['payment_amount']

                    # 重新排列列
                    display_cols = ['order_no', 'sku_or_jan', 'unit_price', 'qty', 'payment_amount']
                    display_cols.extend(sorted(fee_cols))
                    display_cols.extend(['currency', 'platform', 'shop_name', '净金额'])
                    display_cols = [col for col in display_cols if col in merged.columns]
                    merged_display = merged[display_cols]

                    st.dataframe(merged_display, use_container_width=True, height=400)
                else:
                    st.dataframe(orders, use_container_width=True, height=400)
            else:
                st.dataframe(orders, use_container_width=True, height=400)

            st.caption(f"显示 {len(orders):,} 条订单")
        else:
            st.info("该平台无订单")

        # 全平台对比图
        if plat is None:
            st.subheader("📊 站点对比 · 净到账")
            site_data = pd.read_sql_query(
                """
                SELECT
                    o.platform AS 站点,
                    COALESCE(SUM(o.payment_amount), 0) + COALESCE(SUM(f.amount), 0) AS 净到账
                FROM shopee_orders o
                LEFT JOIN shopee_fees f ON f.order_no = o.order_no
                GROUP BY o.platform
                ORDER BY 净到账 DESC
                """,
                conn
            )
            if not site_data.empty:
                fig = px.bar(
                    site_data,
                    x='站点',
                    y='净到账',
                    color='站点',
                    text='净到账',
                    title="各站点净到账金额"
                )
                fig.update_traces(textposition='outside', texttemplate='¥%{text:,.0f}')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("无多站点数据")

conn.close()
