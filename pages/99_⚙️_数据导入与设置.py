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
                    # Postgres: 单个 ingest 完成后显式 commit，避免事务跨 ingest 累积
                    try:
                        conn.commit()
                    except Exception:
                        pass
                    results.append((name, key, result, None))
                except Exception as e:
                    # Postgres: 出错后必须 rollback，否则下个 ingest 在 aborted 事务里全失败
                    try:
                        conn.rollback()
                    except Exception:
                        pass
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

            # ────────────────────────────────────────────────────────
            # Phase 4 · ingester 已直写 v2 → 无需中间 ETL 层
            # 旧表名通过 VIEW 透传 v2 数据，page SQL 不用改
            # ────────────────────────────────────────────────────────


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
# Tab 5: 飞书集成（一个 CMS App 启用机器人能力 → 推消息 + 写表格 + 写文档）
# ============================================================
with tab_lark:
    from shared import lark_notify, lark_openapi

    st.subheader(t("🔔 飞书集成 · 机器人通知"))
    st.caption(
        "用 CMS 自建应用启用「机器人」能力 → 推卡片通知到群 / 单人。"
        "（写飞书表格 API 代码保留备用，stock_monitor 仍在用；CMS 暂不需要）"
    )

    # ─────────────────── 当前状态总览 ───────────────────
    health = lark_openapi.health_check()
    routes = lark_notify.list_configured_routes()
    active_mode = routes["active_mode"]

    cols = st.columns(3)
    with cols[0]:
        if active_mode == "bot":
            st.success("📡 模式：**Bot App**")
        elif active_mode == "webhook":
            st.warning("📡 模式：**Webhook fallback**")
        else:
            st.error("📡 模式：**未配置**")
    with cols[1]:
        if health["configured"]:
            st.success(f"App ID `{health['app_id']}`")
        else:
            st.warning("App 未配置")
    with cols[2]:
        if health.get("token_ok"):
            mins = health["token_expires_in"] // 60
            st.success(f"Token OK · {mins} 分钟后刷新")
        elif health["configured"]:
            st.error(f"Token 失败：{health.get('error','')[:40]}")

    st.divider()

    # ─────────────────── 配置步骤 ───────────────────
    with st.expander(t("📖 配置步骤（Boss 操作清单 · 约 8 分钟）"), expanded=not health["configured"]):
        st.markdown(
            """
**Step 1**：打开 https://open.feishu.cn/app

- 如果 stock_monitor 已经在用某个 App → **直接复用同一个**（不要再新建）
- 没有就「**创建企业自建应用**」起名 `SmikieJapan CMS`

**Step 2**：「**凭证与基础信息**」复制 App ID + Secret 写进 `.env`：
```
LARK_APP_ID=cli_xxxxxxxx
LARK_APP_SECRET=xxxxxxxxxxxxxxxx
```

**Step 3**：左栏「**应用功能 → 机器人 → 启用**」⭐

**Step 4**：左栏「**权限管理**」→ 仅申请这 3 项核心权限即可：

| 权限 | 用途 |
|---|---|
| `im:message:send_as_bot` ⭐ | 给群 / 用户发卡片消息 |
| `im:chat` | 列出机器人加入的所有群（拉 chat_id 用） |
| `im:chat.members:read` | 读群成员（可选） |

> stock_monitor 仍在用 `sheets:spreadsheet` 写飞书表格 — 那个权限保持不动；
> CMS 端通知功能不需要写表格权限。

**Step 5**：「**版本管理与发布**」→ 创建版本 → 提审

**Step 6**：**把机器人加进群** — 群设置 → 添加机器人 → 搜索应用名 → 添加
（或群里直接 @机器人名字，首次会自动加入）

**Step 7**：用下面「📋 拉取群列表」按钮拿 chat_id

**Step 8**：写到 `.env`：
```
LARK_DEFAULT_CHAT_ID=oc_xxxxxxxxxxxxxxxxxxxxxxxx       # 默认群
LARK_CHAT_ROUTES={"_error": "oc_yyy", "shopee_mass_upload": "oc_zzz"}  # 可选
```
"""
        )

    if health["configured"] and health["token_ok"]:
        st.markdown("#### 🔍 列出机器人加入的所有群（拿 chat_id）")
        if st.button("📋 拉取群列表", key="lark_list_chats"):
            try:
                chats = lark_openapi.list_chats()
                if not chats:
                    st.warning("机器人还没加入任何群（去群里 @机器人 或在群设置加机器人）")
                else:
                    st.dataframe(
                        [{"chat_id": c.get("chat_id"), "群名": c.get("name"),
                          "类型": c.get("chat_type"), "描述": (c.get("description") or "")[:30]}
                         for c in chats],
                        use_container_width=True, hide_index=True,
                    )
                    st.caption("👆 复制 chat_id 填到 .env 的 LARK_DEFAULT_CHAT_ID")
            except Exception as e:
                st.error(f"❌ 拉取失败：{e}")
                st.caption("常见原因：缺 im:chat 权限 / 版本未发布 / token 拿不到")

        st.divider()
        st.markdown("#### 🧪 发测试消息到指定群")
        c1, c2 = st.columns([3, 2])
        with c1:
            test_chat_id = st.text_input(
                "chat_id（oc_xxx，留空则用 LARK_DEFAULT_CHAT_ID）",
                key="lark_test_chat_id",
            )
        with c2:
            test_status = st.selectbox(
                "卡片颜色",
                ["info", "success", "warning", "error"],
                key="lark_test_status",
            )
        if st.button("🚀 发测试卡片", key="lark_test_bot_send", type="primary"):
            kwargs = {"chat_id": test_chat_id} if test_chat_id else {"status": test_status}
            ok = lark_notify.notify_card(
                title=f"🧪 CMS 飞书 Bot 测试 · {test_status}",
                body=f"From CMS page 99 → Bot App 机器人模式",
                rows=[("时间", datetime.now().isoformat(timespec="seconds")),
                      ("模式", routes["active_mode"])],
                status=test_status,
                **kwargs,
            )
            st.success("✅ 已发送，去飞书群看") if ok else st.error(
                "❌ 失败（看上面状态：App 配置 / 权限 / 机器人是否在群里 / chat_id 是否对）")

    # ─────────────────── 当前路由配置 ───────────────────
    st.divider()
    st.markdown("### 📊 当前路由配置")
    bot_default = routes["bot"]["default_chat_id"]
    bot_routes = routes["bot"]["routes"]
    st.write("**Bot 模式**：")
    st.write(f"- 默认 chat_id: `{bot_default or '（未配置）'}`")
    if bot_routes:
        st.dataframe([{"module": k, "chat_id": v} for k, v in bot_routes.items()],
                     use_container_width=True, hide_index=True)

    if routes["webhook"]["default_url"] or routes["webhook"]["routes"]:
        st.write("**Webhook 模式（fallback / 备用）**：")
        st.write(f"- 默认 URL: `{routes['webhook']['default_url'] or '（未配置）'}`")
        if routes["webhook"]["routes"]:
            st.dataframe([{"module": k, "URL（脱敏）": v}
                          for k, v in routes["webhook"]["routes"].items()],
                         use_container_width=True, hide_index=True)

    # ─────────────────── 方案 B · 群机器人 webhook（备用） ───────────────────
    st.divider()
    st.markdown("### 🪝 方案 B · 群机器人 Webhook（备用 / 兼容旧配置）")
    st.caption("不需要自建应用，但功能弱（只能推消息，不能写表格 / 接收消息）。两套并存时优先走 A。")

    with st.expander("📖 群机器人 webhook 配置（需要时再开）", expanded=False):
        st.code(
            """LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
LARK_WEBHOOK_URL_SHOPEE=...
LARK_WEBHOOK_URL_DISCONTINUE=...
LARK_WEBHOOK_URL_NST=...
LARK_WEBHOOK_URL_ERROR=...
LARK_WEBHOOK_ROUTES={"shopee_mass_upload": "https://..."}""",
            language="bash",
        )

