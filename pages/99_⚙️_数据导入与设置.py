"""数据导入与设置页。

支持两类输入：
- 6 种 NetSuite 标准导出 .xls（自动识别文件名）
- 兜底 CSV（item_master_cleaned.csv 旧路径）

设计：用户可以一次拖多个文件，工具自动识别每个文件类型并入对应表。
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import streamlit as st
from shared.i18n import t, lang_selector

from data_warehouse.ingest.items import LocalItemMasterIngestor
from data_warehouse.ingest.xls_ingest import (
    INGESTOR_REGISTRY,
    detect_ingestor,
)
from shared.db import INPUTS_DIR, get_connection

st.set_page_config(page_title=t("数据导入与设置"), page_icon="⚙️", layout="wide")
from shared.auth import require_admin, require_extra_password
require_admin()
require_extra_password("page99", "PAGE99_PASSWORD")
lang_selector()
conn = get_connection()

st.title(t("⚙️ 数据导入与设置"))
st.caption(t("把 NetSuite 标准导出 .xls 拖到这里，工具自动识别类型并入库"))

tab_import, tab_status, tab_logs, tab_legacy, tab_lark = st.tabs(
    [
        t("📤 一键导入（NetSuite .xls）"),
        t("📊 数据现状"),
        t("📜 导入历史"),
        t("🛠 旧 CSV（item_master）"),
        t("🔔 飞书集成"),
    ]
)


# ============================================================
# Tab 1：一键多文件导入
# ============================================================
with tab_import:
    st.subheader(t("拖拽 NetSuite + Shopee 导出文件"))
    with st.expander(t("📖 支持的文件类型"), expanded=False):
        st.markdown(
            """
            | 文件名包含 | 入到哪张表 | 内容 |
            |---|---|---|
            | `在庫数残数` 或 `通常在庫` | `inventory_snapshot` | 多仓库库存快照（含 std/avg cost） |
            | `在庫回転率` | `inventory_turnover` | 库存周转率 + 平均手持日数 |
            | `ASEAN` + `前日` | `sales_line` (asean_daily) | ASEAN 日销售 |
            | `ASEAN` + `店舗別` | `sales_line` (asean_monthly) | ASEAN 月销售（按店铺） |
            | `輸出` + `アイテム別` | `sales_line` (export_item) | 出口销售（SKU 维度，带 rank） |
            | `輸出` + `店舗別` | `sales_line` (export_store) | 出口销售（店铺×SKU 维度） |
            | `订单导出` | `shopee_orders_raw` | Shopee 订单导出（订单号+SKU+店铺） |
            | `mtkshop`/`income`/`已拨款` | `shopee_income_lines` | Shopee 拨款明细（含各项扣费） |
            | `アイテム` 开头 | `nst_item_summary` | NetSuite アイテム概要（page 03 平均原価源・H 列） |

            一次可以拖多个文件，挨个识别 + 导入。识别不到的文件会显示警告但不影响其他文件。
            """
        )

    uploaded_files = st.file_uploader(
        t("拖拽或选择 .xls 文件（可多选）"),
        type=["xls", "xlsx"],
        accept_multiple_files=True,
        key="bulk_xls_uploader",
    )

    if uploaded_files:
        # 预扫描：识别每个文件，允许手动覆盖 ingester
        st.write(t("### 文件识别结果（可手动指定 ingester）"))
        INGESTOR_LABELS = {
            "inventory": "📦 在庫快照 (輸出通常在庫数残数)",
            "turnover": "🔄 在庫回転率",
            "asean_monthly": "🏪 ASEAN 月度（店舗別 集計専用）",
            "asean_daily": "📊 ASEAN 前日（店舗別売上 前日）",
            "export_item": "🇯🇵 輸出 SKU 維度（アイテム別）",
            "export_store": "🇯🇵 輸出 店舗別売上",
            "shopee_orders": "🛒 Shopee 订单导出",
            "shopee_income": "💱 Shopee 拨款明细",
            "item_summary": "📋 NetSuite アイテム概要",
        }
        plan: list[tuple[str, str, object]] = []
        all_keys = list(INGESTOR_LABELS.keys())
        for f in uploaded_files:
            auto_key = detect_ingestor(f.name)
            cols = st.columns([3, 3])
            with cols[0]:
                if auto_key:
                    st.write(f"📄 `{f.name}` ({f.size:,} bytes)")
                    st.caption(f"🤖 自动识别：**{INGESTOR_LABELS.get(auto_key, auto_key)}**")
                else:
                    st.warning(f"📄 `{f.name}` ({f.size:,} bytes) — 文件名未识别，**必须手动选**")
            with cols[1]:
                default_idx = all_keys.index(auto_key) if auto_key in all_keys else 0
                chosen_key = st.selectbox(
                    f"ingester for {f.name}",
                    all_keys,
                    format_func=lambda k: INGESTOR_LABELS[k],
                    index=default_idx,
                    key=f"__ingester_{f.name}",
                    label_visibility="collapsed",
                )
            plan.append((f.name, chosen_key, f))

        if plan and st.button(t(f"🚀 开始导入 {len(plan)} 个文件"), type="primary"):
            INPUTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            results = []
            progress = st.progress(0)
            status = st.empty()

            for i, (name, key, f) in enumerate(plan):
                status.write(f"⏳ 处理中：{name}")
                # 落盘到 inputs/ 留存（有时间戳前缀避免覆盖）
                save_path = INPUTS_DIR / f"{ts}_{name}"
                save_path.write_bytes(f.getvalue())

                try:
                    ingestor_fn = INGESTOR_REGISTRY[key]
                    result = ingestor_fn(save_path, conn, source_name=name)
                    results.append((name, key, result, None))
                except Exception as e:
                    results.append((name, key, None, str(e)))

                progress.progress((i + 1) / len(plan))

            status.write(t("✅ 全部处理完成"))

            # 汇总结果
            st.write(t("### 导入汇总"))
            for name, key, result, err in results:
                if err:
                    st.error(f"❌ `{name}` ({key}): {err}")
                else:
                    period = ""
                    if result.get("period_start"):
                        period = f" · 期间 {result['period_start']} ~ {result['period_end']}"
                    msg = (
                        f"✅ `{name}` ({key}) — "
                        f"总 {result['total']:,} · "
                        f"入库 {result['inserted']:,} · "
                        f"错误 {result['errors']:,}{period}"
                    )
                    st.success(msg)


# ============================================================
# Tab 2：数据现状
# ============================================================
with tab_status:
    st.subheader(t("各表当前数据量"))

    def _count(t: str) -> int:
        try:
            return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        except Exception:
            return 0

    cols = st.columns(4)
    cols[0].metric(t("商品（item）"), f"{_count('item'):,}")
    cols[1].metric(t("库存快照"), f"{_count('inventory_snapshot'):,}")
    cols[2].metric(t("销售明细"), f"{_count('sales_line'):,}")
    cols[3].metric(t("库存周转"), f"{_count('inventory_turnover'):,}")

    st.divider()

    # 按 source 拆分销售
    st.write(t("**销售数据按来源拆分：**"))
    rows = conn.execute(
        """
        SELECT source, COUNT(*) AS rows, MIN(period_start) AS p_from, MAX(period_end) AS p_to
        FROM sales_line GROUP BY source
        """
    ).fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows], use_container_width=True, hide_index=True)
    else:
        st.info(t("还没有销售数据。"))

    st.write(t("**库存数据按 location 拆分：**"))
    rows = conn.execute(
        """
        SELECT location, COUNT(*) AS rows,
               SUM(qty_on_hand) AS total_qty,
               COUNT(DISTINCT internal_id) AS unique_skus
        FROM inventory_snapshot GROUP BY location ORDER BY rows DESC
        """
    ).fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows], use_container_width=True, hide_index=True)
    else:
        st.info(t("还没有库存数据。"))


# ============================================================
# Tab 3：导入历史
# ============================================================
with tab_logs:
    st.subheader(t("最近导入记录"))
    runs = conn.execute(
        """
        SELECT run_id, ingestor, source_file, total_rows, inserted, errors, run_at
        FROM _ingest_runs ORDER BY run_id DESC LIMIT 50
        """
    ).fetchall()
    if not runs:
        st.info(t("还没有任何导入记录。"))
    else:
        st.dataframe([dict(r) for r in runs], use_container_width=True, hide_index=True)
        run_ids = [r["run_id"] for r in runs]
        sel = st.selectbox(
            t("查看错误明细："),
            run_ids,
            format_func=lambda i: f"#{i} · "
            + next(r["ingestor"] for r in runs if r["run_id"] == i)
            + " · "
            + next(r["source_file"] for r in runs if r["run_id"] == i),
        )
        if sel:
            errs = conn.execute(
                "SELECT row_number, error_message, raw_row FROM _ingest_errors WHERE run_id=? ORDER BY row_number",
                (sel,),
            ).fetchall()
            if errs:
                st.warning(t(f"⚠️ {len(errs)} 个失败行："))
                st.dataframe([dict(r) for r in errs], use_container_width=True, hide_index=True)
            else:
                st.success(t("✅ 这次导入无失败行。"))


# ============================================================
# Tab 4：兼容旧 CSV
# ============================================================
with tab_legacy:
    st.subheader(t("旧版 item_master CSV 导入"))
    st.caption(t("如果还需要导入 `/Users/joe/CC/item_master_cleaned.csv` 风格的本地表"))

    item_count = conn.execute("SELECT COUNT(*) AS c FROM item").fetchone()["c"]
    st.metric(t("当前 item 表行数"), f"{item_count:,}")

    uploaded = st.file_uploader(t("旧 item_master CSV"), type=["csv"], key="legacy_item_uploader")
    if uploaded and st.button(t("导入")):
        try:
            summary = LocalItemMasterIngestor().run(
                io.BytesIO(uploaded.getvalue()), conn, source_name=uploaded.name
            )
            st.success(
                f"✅ 总 {summary['total_rows']:,} · "
                f"入 {summary['inserted']:,} · "
                f"错误 {summary['errors']:,}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"❌ {e}")
            st.exception(e)


# ============================================================
# Tab 5: 飞书集成（群机器人 + 自建应用 OpenAPI 自检 + 测试）
# ============================================================
with tab_lark:
    from shared import lark_notify, lark_openapi
    import os as _os

    st.subheader(t("🔔 飞书通知集成"))
    st.caption(t("两套机制并行：群机器人 webhook（推消息）+ 自建应用 OpenAPI（写表格 / 文档）"))

    # ─────────────────── 1. 群机器人 webhook ───────────────────
    st.markdown("### 1. 群机器人 Webhook")
    st.caption("飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook URL")

    routes = lark_notify.list_configured_routes()
    if any(routes.values()):
        st.success(t("✅ 已配置至少一个 webhook"))
        st.dataframe(
            [{"路由": k, "URL（前 60 字符）": v} for k, v in routes.items() if v],
            use_container_width=True, hide_index=True,
        )
    else:
        st.warning(t("⚠️ 还没配置任何群机器人 webhook"))

    with st.expander(t("📖 怎么配（环境变量 / .streamlit/secrets.toml）"), expanded=False):
        st.code(
            """# 默认群（必填，作为兜底）
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 按业务模块分群（可选，命中则覆盖默认）
LARK_WEBHOOK_URL_SHOPEE=https://...      # shopee_mass_upload + image_gen
LARK_WEBHOOK_URL_DISCONTINUE=https://... # discontinue_confirm
LARK_WEBHOOK_URL_NST=https://...         # nst_order
LARK_WEBHOOK_URL_ERROR=https://...       # 任何 status='error' 的兜底群

# 或者完全自定义路由表（JSON，优先级最高）
LARK_WEBHOOK_ROUTES={"shopee_mass_upload": "https://...", "image_gen": "..."}""",
            language="bash",
        )

    # 测试按钮
    cc1, cc2, cc3 = st.columns(3)
    if cc1.button(t("发测试消息（默认群）"), key="lark_test_default"):
        ok = lark_notify.notify_card(
            title="🧪 CMS 飞书集成测试",
            rows=[("来源", "page 99 测试按钮"), ("状态", "OK")],
            status="info",
        )
        st.success("✅ 已发送，去飞书群看") if ok else st.error("❌ 失败（检查 webhook URL）")
    if cc2.button(t("发测试卡片（success 绿）"), key="lark_test_succ"):
        ok = lark_notify.notify_card(
            title="✅ 测试 · success 卡片",
            rows=[("市场", "TW"), ("成功", "12"), ("失败", "0")],
            status="success", module="shopee_mass_upload",
        )
        st.success("✅ 已发送") if ok else st.error("❌ 失败")
    if cc3.button(t("发测试卡片（error 红）"), key="lark_test_err"):
        ok = lark_notify.notify_card(
            title="🔴 测试 · error 卡片",
            body="模拟一个失败场景",
            rows=[("error", "ConnectionError")],
            status="error",
        )
        st.success("✅ 已发送") if ok else st.error("❌ 失败")

    st.divider()

    # ─────────────────── 2. 飞书自建应用 OpenAPI ───────────────────
    st.markdown("### 2. 飞书自建应用 OpenAPI（写表格 / 文档）")
    st.caption("适用 stock_monitor 写改廃监控表格、把月度报告写飞书文档等场景")

    health = lark_openapi.health_check()
    if health["configured"]:
        st.success(f"✅ App ID 已配置 · `{health['app_id']}`")
        if health["token_ok"]:
            mins = health["token_expires_in"] // 60
            st.success(f"✅ tenant_access_token 拉取成功（{mins} 分钟后过期，会自动刷新）")
        else:
            st.error(f"❌ token 拉取失败：{health['error']}")
    else:
        st.warning(t("⚠️ LARK_APP_ID / LARK_APP_SECRET 未配置"))

    with st.expander(t("📖 怎么注册自建应用 + 申请权限"), expanded=False):
        st.markdown(
            """
**Step 1**：飞书开发者后台 → https://open.feishu.cn/app → 创建企业自建应用

**Step 2**：左侧「凭证与基础信息」复制 **App ID** + **App Secret**，填到 `.env`：
```
LARK_APP_ID=cli_xxxxxxxx
LARK_APP_SECRET=xxxxxxxx
```

**Step 3**：左侧「权限管理」→ 申请以下权限（再点页面顶部「发布版本」让管理员审核）：
| 权限 | 用途 |
|---|---|
| `sheets:spreadsheet` | 电子表格读写（stock_monitor 改廃报告写飞书表）|
| `docs:document` | 云文档读写（月度自动报告写飞书 doc）|
| `im:message:send_as_bot` | 给指定用户/群发卡片消息（双向交互）|
| `contact:user.id:readonly` | 用户基础信息（按 union_id 查邮箱）|

**Step 4**：左侧「应用发布」→「创建版本」→ 提交审核 → 管理员通过后生效。

**Step 5**：把要写的表格 / 文档**共享给这个 App**（在表格右上角分享 → 添加协作者 → 输入应用名）。
"""
        )

    if health["configured"] and health["token_ok"]:
        st.markdown("**测试 OpenAPI · 表格写入**")
        col_a, col_b = st.columns(2)
        with col_a:
            test_token = st.text_input(
                "电子表格 token（URL 中 /sheets/<token>）",
                key="lark_sheet_token",
            )
        with col_b:
            test_sheet_id = st.text_input(
                "子表 ID（URL 中 ?sheet=xxx）",
                key="lark_sheet_id",
            )
        if st.button(t("📋 追加一行测试数据"), key="lark_test_sheet"):
            if not (test_token and test_sheet_id):
                st.warning(t("先填表格 token 和 sheet_id"))
            else:
                from datetime import datetime as _dt
                try:
                    n = lark_openapi.sheet_append_rows(
                        test_token, test_sheet_id,
                        [["CMS 测试", _dt.now().isoformat(timespec="seconds"), "OK"]],
                        column_range="A:C",
                    )
                    st.success(f"✅ 已追加 {n} 行（去飞书表格刷新看）")
                except Exception as e:
                    st.error(f"❌ {e}")
