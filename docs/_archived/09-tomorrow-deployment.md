# 明天部署清单 · Boss 在 Windows 一步一步跑

> 创建：2026-05-09 · 跨多个 commit 的累计变更
> 目标：在 Windows Inspiron 5405 上把 Phase 3 的 v2 数据模型实际跑起来

---

## 🎯 整体目标

把 GitHub 上的最新代码（含 v2 数据模型 11 张新表 + ETL + page 修复）拉到 Windows，
让 Postgres 自动建好新表，跑 ETL 把现有数据填进去，验证关键 page 显示正常。

预计总耗时：**15-25 分钟**。

---

## ✅ Step 1 · 拉新代码 + 重启 streamlit（2 分钟）

```powershell
cd C:\Users\smiki\CMS-v230
git pull origin main
docker compose -f deploy\windows\docker-compose.yml restart streamlit
```

**自动发生的事**：
- streamlit 容器启动时 init_db 会跑 schema.sql → 在 SQLite 上 IF NOT EXISTS 建 11 张 v2 新表
- _PostgresAdapter 第一次连库时跑 schema.postgres.sql → 在 Postgres 上同步建 11 张新表
- migrations.py 的 `DEPRECATED_TABLES` 会 DROP 掉 `store_profit_lines / store_profit_daily_lines / sales`（幂等）

**验证 streamlit 起来了**：
```powershell
docker compose -f deploy\windows\docker-compose.yml ps
# cms_streamlit 应是 (healthy)
```

如果起不来，看日志：
```powershell
docker compose -f deploy\windows\docker-compose.yml logs --tail 60 streamlit
```

---

## ✅ Step 2 · 浏览器开 page 99 验证表已建（30 秒）

打开 https://smikie-cms.cc → 输一级密码 `smikie043` → page 99 → 输二级密码 `0043` → **Tab 6 「🧬 v2 数据迁移」**

应该看到 row count 对照表，11 张 v2 新表全部显示（new_count 都是 0，等下一步填）。

如果报错「`_v2_migration_runs` 表还没建」 → 说明 streamlit 没正常重启。回 Step 1 看日志。

---

## ✅ Step 3 · 跑全套 ETL（30-60 秒）

Tab 6 → 点 **「🚀 开始全套 ETL」** → 等几秒 → 看每个 step 的 read / written / errors。

**预期数据**（基于本地 SQLite warehouse.db 实测；Windows Postgres 数据量可能更大）：

| Step | 预期 written |
|---|---|
| `market_segment` | 12（市场字典种子）|
| **`item_v2`** ⭐ | **8000+**（解决 item 表为空的问题）|
| `shop` | 20+ |
| `shop_monthly` | 0 或 30+（看 store_monthly 数据）|
| **`shop_sales`** | **6000-12000+** |
| `item_sales_history` | 4000+ |
| `item_inventory_snapshot_v2` | 5000-17000+ |
| `item_inventory_extra` | 看 benten_stock / warehouse_stock |
| `item_purchase_history` | 看 purchase 表 |
| `item_cost_history` | 看 std_cost_history |
| **`item_supplier_link`** | **4000+**（合并 supplier_cost + supplier_jan_list）|

**绿色全过 = ETL 成功**。任何 errors > 0 把 notes 列发给我。

---

## ✅ Step 4 · 验证 page 03 maker 列（1 分钟）

打开 page 03「💰 定義原価編集」→ 选条件 → 点「🚀 计算并预览」

**应该看到**：maker 列填了品牌名（之前可能为空）。

之前 nst_item_summary 经常空 → maker 通过 v2 fallback 从 item_v2 拿。

---

## ✅ Step 5 · 验证 admin v2 快查（2 分钟）

进 page 04 / 06 / 07 任一 → 顶部展开「🧬 v2 数据快查（admin）」

测试 4 个 Tab：

| Tab | 操作 | 预期 |
|---|---|---|
| 按品牌查 | 选 `スケーター` | 列出 Skater 品牌的 SKU + cost / rank |
| 按 JAN 查 | 输任一 13 位 JAN（如 `4901085196533`）| 显示 item_v2 详情 + 销售 + 库存 + 进货 |
| 按供应商查 | 选任一供应商 | 列出该供应商关联的所有 SKU + cost_class + 报价 |
| 整体概览 | — | 5 列指标 + 店铺分布 + 最近 ETL 历史 |

任何 Tab 显示空白 / 报错把截图发我。

---

## ✅ Step 6 · 测试导入新 xls 自动同步 v2（可选 · 5 分钟）

如果 Boss 手头有新 NetSuite 导出文件，可以测一下自动同步：

page 99 → Tab 1「📤 一键导入」→ 拖一个新 xls → 点「开始导入」

**预期**：导入完成后自动看到 「🧬 v2 模型自动同步完成：写入 X 行」 提示。

这是 Phase 3.2 ingest 双写功能，让 v2 数据持续保鲜，无需手工去 Tab 6 触发。

---

## 📊 验证完成后给我反馈

复制 page 99 Tab 6 的 row count 对照表数字给我，特别关注：

1. **item_v2 实际写入多少 JAN**（应该 8000+）
2. **shop_sales 多少行**
3. **item_supplier_link 多少配对**
4. **是否有 step errors > 0**

数据齐了我就接着做：
- 后天：N8N 安装包部署 + Shopee 上架流程走通
- 之后：Nano Banana 2 详情图

---

## 🆘 排查清单

| 现象 | 排查 |
|---|---|
| streamlit 起不来 | `docker compose logs streamlit` 看错误，常见 schema 冲突 |
| Postgres 没建 v2 表 | 看日志有没有 `[postgres init warn]`，可能 schema.postgres.sql 路径问题 |
| Tab 6 看不到 | 二级密码 `0043` 输了吗？require_extra_password 才显示 |
| ETL 报错 errors=N | 看 _v2_migration_runs 表的 notes 列具体原因 |
| page 03 maker 仍空 | item_v2 的 maker 字段是否填了？v2 browser → 按 JAN 查看 |
| v2 browser 看不到 | 你登录的是 admin 角色吗？require_admin 才显示 |

---

## 📁 这次推送的内容（commit 链）

```
5838809  Phase 3.2/3.3/3.5  ingest 双写 + page v2 fallback + 删废弃表
37b8959  Phase 3.1          v2 数据模型落地（10 表 + ETL + UI）
（本次再加） Phase 3.6        item_supplier_link + benten/warehouse 整合 + v2_browser 4 Tab
```

涉及文件：
- `data_warehouse/db/schema.sql` + `deploy/windows/schema.postgres.sql` — 11 张新表
- `data_warehouse/db/migrations.py` — DEPRECATED_TABLES + SCHEMA_VERSION 14
- `tools/migrate_to_v2.py` — 11 步骤 ETL（最新加 item_supplier_link / benten/warehouse）
- `shared/db.py` — Postgres 自动 init schema
- `shared/v2_browser.py` — admin 4 Tab 快查（按品牌 / JAN / 供应商 / 概览）
- `pages/03/04/06/07/99` — v2 fallback + Tab 6 + 自动 ETL
- `docs/08-data-model-v2.md` — 设计文档
- `docs/09-tomorrow-deployment.md` — 本文件
