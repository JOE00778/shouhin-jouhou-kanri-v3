# NST Saved Search · 字段配置清单 (Boss 配置用)

> 状态：v2 · 2026-05-09 (Boss 反馈修正)
> 目的：Boss 在 NetSuite 配置 Saved Search 时，按本清单逐项添加「結果」列，确认字段存在 / 决定是否新增 custom field。

**Boss 2026-05-09 决策：**
- ✅ **先做 Report A** (商品主档)
- 🔄 **Report B (库存快照) 移交「自动采购模块」**（新项目, 不在本次 NST 整合范围）
- 📅 **Report C (销售) 维持** 在本次 NST 整合范围 (A 完成后接续)
- ❌ skuID 不需要（移除）
- 🔄 最安原価 移交「自动采购模块」（移除）
- ✅ 発注ロット 保留在 Report A

字段类型说明：
- **Std** = NetSuite 标准字段（直接选）
- **Custom** = 公司自定义字段（需要确认存在 / 不存在则需新建）
- **Calc** = 在 NetSuite formula 内计算（例: case Quantity)

---

## 📋 Report A · `SS_Item_Master_Daily`（商品主档 + 库存汇总）

> 基于「輸出アイテム(JO)」view 扩展。Boss 截图已有 13 列 (✅), 待加 9 列 (⭐) = 共 22 列
> 触发：每日 04:00 JST · 全量 (~7,400 行) · Filter 条件：全部含失効/停止

### 22 个字段（按推荐添加顺序）

| # | NetSuite 字段名 (JP UI) | 类型 | 当前状态 | DB 落点 (`item_v2.*`) | 备注 |
|---|---|---|---|---|---|
| 1 | 内部ID | Std | ✅ | `internal_id` | NetSuite system ID |
| 2 | 名前 | Std | ✅ | `item_code` | 例 `4582300064280` |
| 3 | UPCコード | Std | ✅ | `jan` (PK) | 8-13 位 JAN |
| 4 | 表示名 | Std | ✅ | `display_name` | 商品中文/日文名 |
| 5 | メーカー | Std (Manufacturer) | ⭐ 新加 | `maker` | Item record 标准字段 |
| 6 | ランク | Custom | ⭐ 新加 | `rank` | 公司 ABC 等级，可能 custom field |
| 7 | 取扱区分 | Custom | ⭐ 新加 | `handling_status` | 取扱中/停止/廃番 等 |
| 8 | 部門 | Std (Department) | ⭐ 新加 | `department` | NetSuite 标准 Department |
| 9 | 商品担当者 | Std/Custom | ✅ | `owner` | 例 「005 川崎里子」 |
| 10 | 仕入先 (デフォルト) | Std (Preferred Vendor) | ⭐ 新加 | `supplier_default` | 标准 Item.preferredVendor |
| 11 | 平均原価 | Std (Average Cost) | ✅ | `avg_cost` | |
| 12 | アイテム定義原価 | Std (Standard Cost) | ✅ | `std_cost` | |
| 13 | 前回購入価格 | Std (Last Purchase Price) | ✅ | `actual_cost` | |
| 14 | カートン入数 | Std (Units per Case) | ✅ | `case_qty` | |
| 15 | 発注ロット | Custom | ⭐ 新加 | `order_lot` | 最小订货量 |
| 16 | 重量 | Std (Weight) | ⭐ 新加 | `weight` | 单 SKU 重量 |
| 17 | 手持 | Std (Quantity On Hand) | ✅ | `on_hand_total` | |
| 18 | 確保済 | Std (Quantity Committed) | ✅ | `qty_committed_total` | |
| 19 | 注文済 | Std (Quantity On Order) | ✅ | `on_order_total` | |
| 20 | 利用可能 | Std (Quantity Available) | ✅ | (DB 不存, 派生) | 用于校验 = 手持-確保済 |
| 21 | 在庫金額合計 | Calc | ⭐ 新加 | `total_amount` | = 平均原価 × 手持 |
| 22 | 仕入サイクル日数 | Custom | ⭐ 新加 | `supply_cycle_days` | 仕入バケット派生用 (page 06 健康度) |

**已移除 (Boss 2026-05-09 决策)：**
- ~~skuID~~ → 不需要
- ~~最安原価~~ → 移交「自动采购模块」
- ~~作成日~~ → 暂不必需 (新品识别可以晚点加)

---

## 📋 ~~Report B · `SS_Inventory_Snapshot_Daily`（多仓库库存快照）~~ → 移交自动采购模块

> 🔄 **Boss 2026-05-09 决策**：库存快照（含多仓库 × bin 维度 + 最安原価）整体移交「自动采购模块」（新项目, 待开 backlog）
> 本次 NST 整合不实现 Report B; 现有 inventory ingester 维持 XLSX fallback 直到自动采购模块上线接管

---

## 📋 Report C · `SS_Sales_Daily`（销售汇总 · 期间 × 店铺 × SKU）

> 触发：每日 05:00 JST · 含 yesterday + MTD 累计 · 涵盖 4 个旧 ingester 的合并视图
> **建议在 NetSuite 端拆 2 个 saved search**（`_monthly` 和 `_daily`），共享下面的字段表

### 16 个字段（按推荐添加顺序）

| # | NetSuite 字段名 (JP UI) | 类型 | DB 落点 (`shop_sales.*`) | 备注 |
|---|---|---|---|---|
| 1 | 期間開始日 | Calc | `period_start` | YYYY-MM-DD |
| 2 | 期間終了日 | Calc | `period_end` | YYYY-MM-DD |
| 3 | 粒度 | Calc | `granularity` | 'monthly' / 'daily' (saved search 内固定) |
| 4 | 店舗 / 販売チャネル | Std/Custom | `shop_id` | 例 `shopee_tw_smikie_main` |
| 5 | 内部ID (商品) | Std | (映射 `item_v2.internal_id`) | item record ID |
| 6 | 名前 (商品) | Std | (映射 `item_v2.item_code`) | |
| 7 | UPCコード | Std | `jan` | |
| 8 | 表示名 | Std | (映射 `item_v2.display_name`) | |
| 9 | ランク | Custom | `rank` | 与 Report A 一致 |
| 10 | 数量 (販売) | Std (Quantity) | `qty_sold` | |
| 11 | 売上 (現地通貨) | Std (Amount) | `revenue` | |
| 12 | 売上 (JPY) | Calc | `revenue_jpy` | NetSuite 内换汇或 ingest 端换 |
| 13 | 単価 | Calc | `unit_price` | = 売上 / 数量 |
| 14 | 原価 | Std (Cost) | `cost` | |
| 15 | 粗利 | Calc | `gross_profit` | = 売上 - 原価 |
| 16 | 粗利率 | Calc | `gross_margin` | = 粗利 / 売上 |

---

## 🚦 Custom Field 确认清单（Report A 必需）

下面 ⭐ 标的字段如果在 NetSuite Item record 上不存在 standard 字段，需要 Boss 先在 NetSuite 后台新建 custom field（Customization → Lists, Records, & Fields → Item Fields）：

| 字段名 (JP) | 用途 | 推荐 type | 推荐 ID | 优先级 |
|---|---|---|---|---|
| 取扱区分 | 取扱中/停止/廃番/輸出専用 等 | List/Record | `custitem_handling_status` | 🔴 先决 |
| ランク | ABC 等级 / 商品 rank | List/Record (含值 A/B/C/...) | `custitem_rank` | 🔴 先决 |
| 仕入サイクル日数 | 仕入周期 (用于 bucket 分类) | Integer | `custitem_supply_cycle_days` | 🟡 次决 |
| 発注ロット | 最小订货量 | Integer | `custitem_order_lot` | 🟢 可选 |

**移除项**：
- ~~skuID~~ → Boss 决定不需要
- ~~最安原価~~ → 移交「自动采购模块」

---

## 📐 Saved Search 配置要点（NetSuite 端）

### 通用设置

| 设置项 | 推荐值 |
|---|---|
| **Search Type** | Item / Inventory Detail / Item Sales |
| **Available As** | Saved Search → mark as "Public" + "Available External" (供 RESTlet 调用) |
| **Run Unrestricted** | ☑️ 勾选（避免 role 限制) |
| **Filter** (公共) | `Inactive = no` (Report A/B), `Date = yesterday/MTD` (Report C) |
| **Result columns** | 按本清单顺序加，Boss 截图视图基础 + ⭐ |
| **Sort** | 内部ID asc (A/B), 期間開始日 desc (C) |

### Report-specific Filter

- **Report A**：无额外 filter，全 item 拉
- **Report B**：`Quantity On Hand > 0` OR `Quantity Committed > 0`（去掉空 bin）
- **Report C**：
  - `_daily` saved search → `Date = yesterday`
  - `_monthly` saved search → `Date = current month`

---

## ⏭️ Boss 操作步骤建议（按 v2 决策 · A 先做）

### 🥇 第一步：Report A · SS_Item_Master_Daily

1. 打开 **NetSuite Setup → Customization → Lists, Records, & Fields → Item Fields**，确认 4 个 custom field 是否存在；不存在则按上表新建
   - 必加：取扱区分 / ランク
   - 推荐：仕入サイクル日数 / 発注ロット
2. **复制「輸出アイテム(JO)」view → 改名为 `SS_Item_Master_Daily`**，按 Report A 22 个字段排好顺序
3. 存档 saved search ID + 截图发我 → CC 这边写 ingester 直写 `item_v2`

### 🥈 第二步：Report C · SS_Sales_Daily / SS_Sales_Monthly （A 完成后接续）

4. 新建 2 个 Item Sales saved search → `SS_Sales_Monthly` / `SS_Sales_Daily`，按 Report C 16 个字段排好（共享字段 schema）

### 🚧 移交自动采购模块（不在本次范围）

- Report B (库存快照, 多仓库 × bin)
- 最安原価 字段
- 仕入先报价 / 供应商管理 / 自动询价等
