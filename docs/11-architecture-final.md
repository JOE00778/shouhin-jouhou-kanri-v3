# 数据库最终架构 · Phase 4 全部落地

> 状态：v1 · 2026-05-09 完成
> commits: `7b6bf61` (ingester 直写 v2) + `2dbcf67` (VIEW 桥接 + 删 ETL)

---

## 🎯 一图看懂

```
┌─────────────────────────────────────────────────────────────────┐
│  Boss 上传 .xls / .xlsx                                          │
│  9 类标准文件（在庫数残数 / ASEAN 销售 4 份 / アイテム概要 / 等） │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  9 个 ingester (xls_ingest.py)                                  │
│  - 文件名识别 ingester key                                        │
│  - 解析 + JAN 校验 (8-13 位数字)                                 │
│  - 直写 v2 真表                                                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  v2 真表（4 张主体）                                              │
│   ⭐ item_v2                  (PK = jan)                          │
│      shop_sales              (granularity / period)              │
│      item_inventory_snapshot_v2                                  │
│      shopee_orders_raw / shopee_income_lines (raw 保留)          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         │ 7 个桥接 VIEW（v_*）
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  旧表名 = VIEW                                                    │
│   inventory_snapshot      → v_inventory_snapshot                 │
│   nst_inventory_snapshot  → v_nst_inventory_snapshot             │
│   sales_line              → v_sales_line                         │
│   nst_store_sales         → v_nst_store_sales                    │
│   nst_item_summary        → v_nst_item_summary                   │
│   item_master_netsuite    → v_item_master_netsuite               │
│   item_master             → v_item_master                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  page / module SELECT 旧表名 → 自动读到 v2 数据                    │
│  零代码改动                                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📋 v2 真表清单

| 表 | PK / UNIQUE | 用途 |
|---|---|---|
| **`item_v2`** ⭐ | `jan` | 商品主表（8000+ 行）|
| `item_inventory_snapshot_v2` | `(jan, location, bin, snapshot_at)` | 库存快照 |
| `item_purchase_history` | `(po_number, jan, source)` | 进货明细（PO/预测/历史合并）|
| `item_sales_history` | `(jan, period, channel, source)` | 商品 × 期间 × 渠道聚合 |
| `item_cost_history` | `id` | 原価变更史 |
| `item_supplier_link` | `(jan, supplier_name)` | 商品 × 供应商多对多 |
| `market_segment` | `market_id` | 市场字典（TW/SG/MY/...）|
| `shop` | `shop_id` | 店铺主档 |
| `shop_sales` | `(shop_id, jan, granularity, period_start, period_end, source)` | 店铺销售（**含时间粒度**）|
| `shop_monthly` | `(shop_id, year_month)` | 店铺月度 KPI |
| `shopee_orders_raw` | `order_no` | Shopee 订单 raw（保留）|
| `shopee_income_lines` | `(order_no, refund_id)` | Shopee 拨款 raw |
| `shopee_payouts` / `fees` / `adjustments` | 各自 PK | Shopee 财务 raw |

---

## 🪞 VIEW 字段映射

### `inventory_snapshot` / `nst_inventory_snapshot` → `item_inventory_snapshot_v2`

| VIEW 字段 | 来源 |
|---|---|
| `internal_id / item_code / display_name` | 直读 |
| `upc` | ← `jan`（同义）|
| `location / bin_number / snapshot_at` | 直读 |
| `qty_on_hand / qty_committed / qty_backorder` | 直读 |
| `std_cost / avg_cost / total_amount` | 直读 |
| `handling_status / status / owner / department` | 直读 |
| `source_file` | 固定空（v2 不存源文件名）|

### `sales_line` → `shop_sales`

| VIEW 字段 | 来源 |
|---|---|
| `store` | ← `shop_id` |
| `item_code / upc` | ← `jan`（两个都是 jan，业务无区分）|
| `display_name / handling_status / maker` | 固定空（v2 不存这些字段，需要从 item_v2 LEFT JOIN）|
| `rank / qty_sold / revenue / gross_profit / gross_margin` | 直读 |
| `unit_purchase_price` | ← `unit_price` |
| `defined_cost` | ← `cost` |
| `period_start / period_end / source` | 直读 |

### `nst_item_summary` / `item_master_netsuite` / `item_master` → `item_v2`

8 列 / 16 列 / 15 列 视图，全部从 item_v2 衍生。详见 [schema.sql](../data_warehouse/db/schema.sql) 末尾。

---

## 🔧 启动初始化流程

`init_db()` (SQLite) / `_get_postgres_connection()` (Postgres) 启动时按顺序执行：

```
1. PHASE4_REBUILD_TABLES DROP（shop_sales 等需 UNIQUE 重建）
2. 跑 schema.sql / schema.postgres.sql
   - CREATE TABLE IF NOT EXISTS（旧表如 inventory_snapshot 也会被创建为真表）
   - CREATE VIEW v_*（v2 → 旧字段映射）
3. ALTERS 加列（旧库自动补字段，幂等）
4. DEPRECATED_TABLES DROP（store_profit_*, sales 等）
5. PHASE4_LEGACY_VIEWS 处理（关键！）
   for legacy, target_view in 7 项:
     a. DROP VIEW IF EXISTS legacy CASCADE
     b. DROP TABLE IF EXISTS legacy CASCADE  ← 把刚创建的真表替换掉
     c. CREATE VIEW legacy AS SELECT * FROM v_xxx
6. _schema_version 写入
```

---

## 🚫 已废除

- `tools/migrate_to_v2.py` ETL 工具 → 删除
- `pages/99 Tab 6 v2 数据迁移` → 删除
- `pages/99 Tab 1 自动跑 ETL` → 删除（ingester 直写无需 ETL）
- `_v2_migration_runs` 表 → 保留 schema 但不再写

---

## ✅ Boss 重新部署步骤

```powershell
cd C:\Users\smiki\CMS-v230
git pull origin main
docker compose -f deploy\windows\docker-compose.yml up -d --build streamlit
```

启动后：
- streamlit 启动时自动 DROP 旧表 + CREATE VIEW 桥接
- page / module 的 SELECT 自动透传到 v2
- ingester 上传新 .xls 时直接进 v2 真表

无需手工 ETL。

---

## 🧪 验证

1. **page 03 定義原価編集** → 选条件 → 计算 → 应该看到 SKU 列表（maker 列仍空，C 路线决策）
2. **page 04 销售数据查询** → 应该看到 shop_sales 数据（透过 sales_line VIEW）
3. **page 06 库存健康监控** → 应该看到 v2 库存数据（透过 nst_inventory_snapshot VIEW）
4. **page 07 等级判定** → 月度选 2026-04 应该出建议
5. **任一 page 顶部 v2 数据快查** → 按品牌 / JAN / 供应商查应该正常

任何 SQL 错误把 traceback 发我即可。

---

## 🎉 Phase 4 收尾

数据库整理彻底完成：
- ✅ ingester 直写 v2（删中间层）
- ✅ VIEW 桥接旧表名（page 不动）
- ✅ 删 ETL 工具
- ✅ 删 page 99 Tab 6
- ✅ Postgres 兼容（事务 / 命名占位符 / GROUP BY / 字面量 % / cursor / datetime）

下一阶段（Boss 决定时启动）：
- Shopee 上架流程（N8N 安装包部署 + Shopee API + page 21 全自动）
- Nano Banana 2 详情图
