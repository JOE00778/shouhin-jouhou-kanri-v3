# 数据模型 v2 · 以 JAN 为核心，item 主表

> 状态：Phase 3.1 实施 · 2026-05-09
> 涉及：[schema.sql](../data_warehouse/db/schema.sql) / [schema.postgres.sql](../deploy/nas/schema.postgres.sql) / [tools/migrate_to_v2.py](../tools/migrate_to_v2.py) / [pages/99 Tab 6](../pages/99_⚙️_数据导入与设置.py)

---

## 1. 设计原则（Boss 决策 2026-05-09）

| Q | 决策 | 落地 |
|---|---|---|
| Q1 · JAN 不规范怎么办 | **A** 强制必填，无 JAN 不入 v2 | `_is_valid_jan()` 校验 8-13 位数字 |
| Q2 · 店铺粒度 | **C** 两层都建 | `market_segment` (粗 TW/SG/MY...) + `shop` (细，账号级) |
| Q3 · purchase 三表 | **A** 全合并按 source 区分 | `item_purchase_history.source ∈ {netsuite_po, predict, history}` |
| Q4 · 边缘表 | **C** 我去查代码确认 | `benten_stock`/`warehouse_stock` 保留（page 08/19 在用）；`store_profit_*` 废弃（无 SELECT 引用）|
| 额外 | 去掉 `category` 字段 | item_v2 不含 category（Shopee 类目自动填暂不需要） |

---

## 2. 核心结构

```
                       ┌──────────────────────┐
                       │  item_v2 (PK = JAN)   │ ⭐ 主表
                       └──────────┬───────────┘
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                   ▼
    ┌──────────────────────┐           ┌──────────────────────┐
    │ 维度 A · item × 时间  │           │ 维度 B · shop × item  │
    ├──────────────────────┤           ├──────────────────────┤
    │ item_purchase_history │           │ market_segment (粗)   │
    │ item_sales_history    │           │ shop (细)             │
    │ item_inventory_*_v2   │           │ shop_sales            │
    │ item_cost_history     │           │ shop_monthly          │
    └──────────────────────┘           └──────────────────────┘
```

---

## 3. v2 表清单（10 张新表）

### `item_v2` ⭐ 主表 · PK = JAN
合并 4 张旧商品主表（`item_master_netsuite` / `nst_item_summary` / `item_master` / `supply_cycle`）。
含基础信息（display_name / maker / rank / handling_status）+ 进货核心（5 种 cost / case_qty / order_lot / weight / supplier_default / supply_cycle_days / bucket）+ 库存汇总（on_hand_total / on_order_total）+ 来源（item_code / internal_id / source_priority）。

### 维度 A · item × 时间（4 张）
- `item_purchase_history` — 进货明细（PO + 预测 + 历史 三 source 合并）
- `item_sales_history` — 销售明细（jan × period × channel 聚合）
- `item_inventory_snapshot_v2` — 库存快照（jan × location × bin × snapshot_at）
- `item_cost_history` — 原価历史（替代 std_cost_history）

### 维度 B · shop × item（4 张）
- `market_segment` — 市场字典（TW/SG/MY/PH/TH/VN/ID/JP + US/CN/KR/BR）
- `shop` — 店铺主档（含 platform / market_id / display_name / currency / owner）
- `shop_sales` — 店铺 × SKU 销售明细（含 revenue / revenue_jpy / 利润）
- `shop_monthly` — 店铺月度 KPI（替代 store_monthly）

### 元数据
- `_v2_migration_runs` — ETL 历史（每步 read / written / errors / notes）

---

## 4. 旧 → 新 字段映射

| 旧表（48 中的） | 新表 | 关键字段映射 |
|---|---|---|
| `item_master_netsuite` | `item_v2` | upc → jan, internal_id, std_cost, avg_cost, maker, rank |
| `nst_item_summary` | `item_v2` | 补 std_cost / avg_cost / handling_status |
| `item_master` | `item_v2` | jan, maker, actual_cost, min_cost, case_qty, order_lot, weight |
| `supply_cycle` | `item_v2` | lead_time_days → supply_cycle_days, bucket |
| `nst_inventory_snapshot` | `item_inventory_snapshot_v2` + `item_v2.on_hand_total` | upc → jan |
| `nst_store_sales` | `shop_sales` (fb_store → shop_id) + `item_sales_history` | item_code (实际是 13 位 JAN) → jan |
| `sales_line` | `shop_sales` (store → shop_id) + `item_sales_history` | upc / item_code → jan |
| `store_monthly` | `shop_monthly` | store_id → shop_id |
| `purchase` | `item_purchase_history` (source=netsuite_po) | upc/jan → jan |
| `purchase_data` | `item_purchase_history` (source=predict) | jan |
| `purchase_history` | `item_purchase_history` (source=history) | jan |
| `std_cost_history` | `item_cost_history` | jan / upc → jan |

---

## 5. ETL 流程

```
tools/migrate_to_v2.py
  ├─ 9 步骤 (RUN_STEPS)
  │   1. market_segment   静态 12 市场
  │   2. item_v2          合并 4 表 → 8000+ JAN
  │   3. shop             从 sales_line + store_monthly + nst_store_sales distinct
  │   4. shop_monthly     copy from store_monthly
  │   5. shop_sales       nst_store_sales + sales_line → shop × jan
  │   6. item_sales_history  从 shop_sales 聚合（GROUP BY jan + period + channel）
  │   7. item_inventory_snapshot_v2  nst_inventory_snapshot 转换
  │   8. item_purchase_history  purchase + purchase_data + purchase_history 合并
  │   9. item_cost_history  std_cost_history 转换
  │
  ├─ 幂等：可重复跑（INSERT OR REPLACE）
  ├─ JAN 强制：无效 jan 计入 skipped_no_jan，不写入
  └─ 每步落 _v2_migration_runs（read / written / errors / notes）
```

---

## 6. 实测结果（2026-05-09 本地 SQLite warehouse.db）

| Step | 读入 | 写入 | 错误 | 备注 |
|---|---:|---:|---:|---|
| market_segment | 12 | 12 | 0 | 12 市场种子 |
| **item_v2** | **13,842** | **8,056** | **0** | 4 表合并去重 → 8056 商品 |
| shop | 20 | 20 | 0 | 20 个店铺识别 |
| shop_monthly | 42 | 0 | 0 | store_monthly.store_id 全空（旧数据问题） |
| **shop_sales** | **6,099** | **6,075** | **0** | nst_store_sales 直接 item_code 当 jan |
| item_sales_history | 4,004 | 4,004 | 0 | jan × period × channel 聚合 |
| item_inventory_snapshot_v2 | 5,772 | 5,748 | 0 | 24 条 JAN 不规范跳过 |
| item_purchase_history | 0 | 0 | 0 | purchase 表本地空 |
| item_cost_history | 0 | 0 | 0 | std_cost_history 本地空 |

**核心成果**：之前「item 表为空 → 无法按 maker 过滤」的根本问题解决 — `item_v2` 现在有 **8,056 个 JAN 商品** + maker / rank / cost 全字段。

---

## 7. Boss 部署到 Windows 的步骤

```powershell
# 1. 拉新版
cd C:\Users\smiki\CMS-v230
git pull

# 2. 重启 streamlit 容器（init_db 自动跑 schema.sql 加 10 张 v2 表）
docker compose -f deploy\windows\docker-compose.yml restart streamlit

# 3. 浏览器开 https://smikie-cms.cc → page 99 → Tab 6「🧬 v2 数据迁移」
#    点「🚀 开始全套 ETL」→ 等几秒 → 看 row count 对照
```

---

## 8. 当前不动的旧表（保留并行运行）

```
✅ 保留（原计划继续读）：
   - item / item_master / item_master_netsuite / nst_item_summary
   - inventory_snapshot / nst_inventory_snapshot / inventory_turnover / nst_turnover
   - sales_line / nst_store_sales / store_monthly
   - purchase* / std_cost_history / supplier* / supply_cycle
   - shopee_orders* / shopee_payouts / shopee_fees / shopee_income_lines
   - benten_stock / warehouse_stock / item_expiry / lot
   - 月度产物表（health_grade_monthly 等 4 张）
   - 元数据（_ingest_runs / _ingest_errors）

❌ 标记废弃（暂不删，后期手工 DROP）：
   - store_profit_lines / store_profit_daily_lines （无 SELECT 引用）
   - sales（空表）
```

---

## 9. 下一步（Phase 3.2+）

| Phase | 内容 |
|---|---|
| **3.2 双写改造** | 在现有 ingest 路径里同步写 v2 表（保 v2 数据持续新鲜） |
| **3.3 page 切换** | page 04 / 06 / 07 / 03 / 13 改读 item_v2 + 维度表 |
| **3.4 旧表退场** | v2 稳定 1-2 周后，旧表 rename `_legacy` 后缀；1 个月后 DROP |
| **3.5 删 store_profit_*** | 立即可做（无引用） |

每步独立可回滚 — 旧表保留至 v2 全面验证后才动。
