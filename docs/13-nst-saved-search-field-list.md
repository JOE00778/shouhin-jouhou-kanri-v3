# NST Saved Search · 字段配置清单 (Boss 配置用)

> 状态：v3 · 2026-05-10 (Boss 二次反馈 — 改用现有 6 份 NST 报表组合)
> 目的：Boss 不在 NetSuite 改 Saved Search 字段，而是用 **现有 6 份导出报表** 组合落 v2。

**Boss 2026-05-10 决策（最终）：**

📦 **6 份 NST 现成报表** 已经够覆盖所有数据需求：

| # | 文件 | 用途 | 落点 |
|---|---|---|---|
| 1 | 輸出アイテムCMSData用結果204 | 商品主档（15 字段） | `item_v2` 主体 |
| 2 | 【輸出】在庫のスナップショット-980 | 多仓库 × 7 字段库存（手持/注文済/確保済/注文待ち/輸送中 等） | `item_v2` 库存汇总 + 自动采购模块库存快照 |
| 3 | 【ASEAN】店舗別売上 集計専用-201 | 月度销售（14 字段） | `shop_sales` (monthly) |
| 4 | 【ASEAN】店舗別売上（前日）-354 | 日度销售（7 字段，不扩） | `shop_sales` (daily) |
| 5 | 【ASEAN】在庫回転率-XXX | 库存周转率/平均在庫日数 | `inventory_turnover` (现有) |
| 6 | 【輸出】アイテム月完売率300 | 🆕 月度完売率 (期初/入库/出库/期末完整链路) | 🆕 新表 `item_monthly_turnover` |

**字段决策（针对原 Report A 的 8 个待加字段）：**
- 1, 2, 3 = **仕入先 / 発注ロット / 仕入サイクル日数** → 🔄 移交「自动采购模块」, 不在 結果204 加
- 4, 5, 6, 7 = **手持 / 注文済 / 確保済 / 利用可能** → ✅ 来自 在庫スナップショット-980, 通过 `jan` join 写回 `item_v2`
- 8 = **在庫金額合計** → 🧮 ingest 端公式计算 (见下方 §公式)
- ❌ skuID / 最安原価 / 作成日 / メーカー (已在 結果204) — 不再加

**完売率300 用途明确：**
- 用于 **库存健康度** + **订货依据** 数据 (而非财务/销售)
- 逻辑: 月售罄率 → 判断订货量
  - 売罄率过高 → 断货风险 → 下月加大订货
  - 売罄率过低 → 压库存风险 → 下月减少订货

字段类型说明：
- **Std** = NetSuite 标准字段（直接选）
- **Custom** = 公司自定义字段（需要确认存在 / 不存在则需新建）
- **Calc** = 在 NetSuite formula 内计算
- **Join** = 通过 `jan` / `item_code` 在 ingest 端 join 多份报表

---

## 🧮 §公式：在庫金額合計（ingest 端计算）

数据来源：`【輸出】在庫のスナップショット-980` 含多仓库 × 7 字段
仓库列表（从文件可见）：JD-物流-千葉 / 弁天倉庫 / 合計
每仓库子字段：適正在庫水準 / 手持 / 注文済 / 確保済 / 注文待ち / 輸送中 / 平均原価 / 定義原価

**计算规则（落到 `item_v2` 衍生字段 / `item_inventory_snapshot_v2` 多列）：**

```
弁天在庫金額         = 平均原価 × 弁天.手持
JD在庫金額           = 平均原価 × JD.手持
全手持在庫金額       = 弁天在庫金額 + JD在庫金額

弁天在途金額         = 平均原価 × (弁天.注文待ち + 弁天.輸送中)
JD在途金額           = 平均原価 × (JD.注文待ち + JD.輸送中)
全在途在庫金額       = 弁天在途金額 + JD在途金額

全在庫金額合計       = 全手持在庫金額 + 全在途在庫金額   ← `item_v2.total_amount`
```

> ⚠️ 「在途」定义待 Boss 最终确认（注文待ち+輸送中 vs 仅 輸送中 vs 含 注文済）
> 默认采用 `注文待ち + 輸送中`，与「注文済」（PO 已下未到货）区分

---

## 📋 商品主档 · `item_v2` 字段映射（数据源：結果204 + 在庫スナップショット-980）

> 不再要求 Boss 在 NetSuite 改字段。结合 2 份现有 NST 导出 + 公式计算。
> 触发：手动上传 / 后续 NetSuite REST API 拉取

### `item_v2` 主体字段（来源对照）

| # | DB 字段 | 中文名 | 来源文件 | 来源字段 | 备注 |
|---|---|---|---|---|---|
| 1 | `internal_id` | 内部ID | 結果204 | 内部ID | |
| 2 | `item_code` | 名前 | 結果204 | 名前 | |
| 3 | `jan` (PK) | UPCコード | 結果204 | UPCコード | |
| 4 | `display_name` | 表示名 | 結果204 | 表示名 | |
| 5 | `maker` | メーカー | 結果204 | メーカー名 | |
| 6 | `rank` | ランク | 結果204 | 商品ランク | |
| 7 | `handling_status` | 取扱区分 | 結果204 | 取扱区分 | |
| 8 | `department` | 部門 | 結果204 | 部門 | |
| 9 | `owner` | 商品担当者 | 結果204 | 商品担当者 | |
| 10 | `avg_cost` | 平均原価 | 結果204 | 平均原価 | |
| 11 | `std_cost` | アイテム定義原価 | 結果204 | アイテム定義原価 | |
| 12 | `actual_cost` | 前回購入価格 | 結果204 | 前回購入価格 | |
| 13 | `case_qty` | カートン入数 | 結果204 | カートン入数 | |
| 14 | `weight` | 商品重量(g) | 結果204 | 商品重量(g) | |
| 15 | `on_hand_total` | 手持合計 | 在庫スナップショット-980 | 合計.手持 | **Join by jan** |
| 16 | `on_order_total` | 注文済合計 | 在庫スナップショット-980 | 合計.注文済 | **Join by jan** |
| 17 | `qty_committed_total` | 確保済合計 | 在庫スナップショット-980 | 合計.確保済 | **Join by jan** |
| 18 | `total_amount` | 在庫金額合計 | 公式 | 见 §公式 | **ingest 端计算** |

### 移交「自动采购模块」（不在 `item_v2` 主流程）

| 字段 | 说明 |
|---|---|
| `supplier_default` | 仕入先（デフォルト） |
| `order_lot` | 発注ロット |
| `supply_cycle_days` | 仕入サイクル日数 |
| `min_cost` | 最安原価 |

> Boss 决策：这 4 字段不在 結果204 加，也不在 ingest 端处理；等自动采购模块上线后单独走该模块的 ingest pipeline。

### 已舍弃

| 字段 | 理由 |
|---|---|
| ~~skuID~~ | Boss 不需要 |
| ~~作成日~~ | 暂不必需（新品识别可以晚点加） |
| ~~利用可能~~ | 派生字段，不入库（= 手持 - 確保済） |

---

## 📋 在庫スナップショット-980 · 多仓库库存（双用途）

> 用途 1：在 ingest 端按 jan 聚合 → 写回 `item_v2` 的库存汇总字段（手持/注文済/確保済 合計）
> 用途 2：完整结构（多仓库 × bin × 7 字段）→ 「自动采购模块」用于补货决策

### 字段结构（每仓库重复 7 列）

固定列：内部ID / UPCコード / 表示名 / ランク / 取扱区分 / 保管棚番号

每仓库子字段（× JD-物流-千葉 / 弁天倉庫 / 合計）：
1. 適正在庫水準
2. **手持** ← 用于 `item_v2.on_hand_total`
3. **注文済** ← 用于 `item_v2.on_order_total`
4. **確保済** ← 用于 `item_v2.qty_committed_total`
5. 注文待ち ← 在途计算
6. 輸送中 ← 在途计算
7. 平均原価 / 定義原価 ← 公式用

---

## 📋 销售数据 · `shop_sales`

### Monthly · 店舗別売上 集計専用-201（14 字段，已完整）

| # | DB 字段 | 来源字段 | 备注 |
|---|---|---|---|
| 期间 | `period_start` / `period_end` | 文件 row 3 「2026年04月01日 - 2026年04月30日」 | header 解析 |
| 1 | `shop_id` | FB_店舗 | 'Shopee BR' 等 |
| 2 | `item_code` | アイテム | |
| 3 | `jan` | UPCコード | |
| 4 | `handling_status` | 取扱区分 | 也写回 `item_v2` |
| 5 | `display_name` | 表示名 | |
| 6 | `qty_sold` | 販売数量 | |
| 7 | `revenue` | 総収益 | |
| 8 | `cost` | 定義原価 | |
| 9 | `gross_profit` | 粗利 | |
| 10 | `gross_margin` | 粗利率 | |
| 11 | `maker` | メーカー名 | 也写回 `item_v2` |
| 12 | `rank` | 商品ランク | 也写回 `item_v2` |
| 13 | `avg_cost` | 平均原価 | 也写回 `item_v2` |
| 14 | (—) | 発注ロット | 自动采购模块用 |

`granularity = 'monthly'` (ingest 端固定写)

### Daily · 店舗別売上（前日）-354（7 字段，**Boss 决定不扩**）

| # | DB 字段 | 来源字段 |
|---|---|---|
| 期间 | `period_start` = `period_end` | 文件 row 3 单日日期 |
| 1 | `shop_id` | （文件 row 7 第 1 列出现 'Shopee Mall PH' 形式 — 是 group header） |
| 2 | `item_code` | アイテム |
| 3 | `display_name` | 表示名 |
| 4 | `qty_sold` | 販売数量 |
| 5 | `revenue` | 総収益 |
| 6 | `cost` | 定義原価 |
| 7 | `gross_profit` | 粗利 |
| 8 | `gross_margin` | 粗利率 |

`granularity = 'daily'`
缺失字段（jan / 取扱区分 / メーカー / ランク 等）通过 `item_code` 在 ingest 端 join `item_v2` 补齐

---

## 📋 月度完売率 · `アイテム月完売率300` → 🆕 新表 `item_monthly_turnover`

> Boss 用途明确：**库存健康度** + **订货依据**（用于判断次月订货量）
> 售罄率高 → 断货风险 / 售罄率低 → 压库存风险

### 字段映射 (19 列)

```sql
CREATE TABLE IF NOT EXISTS item_monthly_turnover (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  jan               TEXT NOT NULL,           -- アイテム (= jan)
  location          TEXT,                    -- 場所
  department        TEXT,                    -- 部門
  year_month        TEXT NOT NULL,           -- YYYYMM (从文件 row 3 提取)
  -- 期初
  open_qty          REAL,                    -- 開始時の手持在庫数量
  open_avg_cost     REAL,                    -- 開始平均原価
  open_amount       REAL,                    -- 開始時の手持在庫額
  -- 入库
  qty_received      REAL,                    -- 受領
  qty_other_in      REAL,                    -- その他の在庫入庫
  qty_total_in      REAL,                    -- 合計入庫数量
  manual_input      REAL,                    -- 入力値
  last_received_at  TEXT,                    -- 前回の受領日
  -- 出库
  qty_sold          REAL,                    -- 売上
  qty_other_out     REAL,                    -- その他の在庫出庫
  qty_total_out     REAL,                    -- 合計出庫数量
  out_amount        REAL,                    -- 出庫価額
  last_sold_at      TEXT,                    -- 前回の売上日
  -- 期末
  close_qty         REAL,                    -- 終了時の手持在庫数量
  close_avg_cost    REAL,                    -- 期末平均原価
  close_amount      REAL,                    -- 終了時の手持在庫額
  -- 派生指标 (ingest 端计算)
  sell_through_rate REAL,                    -- 完売率 = qty_sold / (open_qty + qty_total_in)
  imported_at       TEXT NOT NULL,
  UNIQUE(jan, location, year_month)
);
CREATE INDEX idx_imt_jan ON item_monthly_turnover(jan);
CREATE INDEX idx_imt_ym  ON item_monthly_turnover(year_month);
CREATE INDEX idx_imt_rate ON item_monthly_turnover(sell_through_rate);
```

### 派生指标 (page 06 健康度 / 订货依据)

```python
# 月售罄率
sell_through_rate = qty_sold / (open_qty + qty_total_in)

# 次月订货建议量（基于过去 N 月 sell-through）
suggested_order_qty = avg_monthly_demand * (1 + safety_buffer)
where avg_monthly_demand = mean(qty_sold over last 3 months)

# 风险标记 (初始阈值, 待业务验证后调整)
if sell_through_rate >= 0.9: risk = "断货风险"  → 加大订货
elif sell_through_rate < 0.5: risk = "压库存"   → 减少订货
else: risk = "正常"  # 0.5 ≤ rate < 0.9
```

---

## 📋 库存周转率 · `【ASEAN】在庫回転率` → 现有表 `inventory_turnover`

> 现有 `turnover` ingester 已支持。Boss 把这份纳入正式来源清单。

| DB 字段 | 来源字段 |
|---|---|
| `department` | 部門: 名前 |
| `item_code` (= jan) | アイテム |
| `handling_status` | 取扱区分 |
| `cost` | 原価 |
| `avg_value` | 平均値 |
| `turnover_rate` | 回転率 |
| `avg_inventory_days` | 平均在庫日数 |

---

## 🚦 Custom Field 确认清单（自动采购模块上线时再处理）

**全部 4 个字段（仕入先 / 発注ロット / 仕入サイクル日数 / 最安原価）→ 移交自动采购模块**

本次 NST 整合不再要求 Boss 在 NetSuite Item record 上新建 custom field。

`商品ランク` / `取扱区分` 已在 結果204 中作为现有字段使用，无需新建。

---

## 📦 6 份 NST 报表 → DB 落点总览

```
┌────────────────────────────────────────────────────────────────────────┐
│  輸出アイテムCMSData用結果204.xls          (商品主档 15 字段)          │
└──────┬─────────────────────────────────────────────────────────────────┘
       │ 直写 item_v2 主体 14 字段
       │
┌──────▼──────────────────────────────────────────────────────────────────┐
│  【輸出】在庫のスナップショット-980.xls    (多仓库库存)                 │
│    ↓ 用途 1: 按 jan 聚合写回 item_v2.{on_hand_total/on_order_total/...} │
│    ↓ 用途 2: 公式计算 item_v2.total_amount = 平均原価 × (手持 + 在途)   │
│    ↓ 用途 3: 完整结构留给「自动采购模块」(多仓库 × bin × 7 字段)        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  【ASEAN】店舗別売上 集計専用-201.xls       (月度销售 14 字段)          │
│    → shop_sales (granularity='monthly')                                 │
│  【ASEAN】店舗別売上（前日）-354.xls         (日度销售 7 字段, 不扩)    │
│    → shop_sales (granularity='daily', 缺字段通过 jan join item_v2 补)  │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  【ASEAN】在庫回転率-XXX.xls                 (库存周转率)               │
│    → inventory_turnover (现有 ingester)                                 │
│  【輸出】アイテム月完売率300.xls 🆕           (月度完売率)              │
│    → 🆕 item_monthly_turnover (新表)                                    │
│    → page 06 健康度 + 订货依据 (新增页面)                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## ⏭️ 落地步骤（按当前 v3 方案）

### 🥇 第一步：扩 ingester 支持 6 份报表

1. **`item_summary` ingester** 改成读 結果204（当前已支持，确认列名映射）
2. **`inventory` ingester** 改成读 在庫スナップショット-980 + 公式计算 `total_amount`
3. **`asean_monthly` ingester** 已支持 集計専用-201
4. **`asean_daily` ingester** 已支持（前日）-354
5. **`turnover` ingester** 已支持 在庫回転率
6. **🆕 `monthly_turnover` ingester** 新增 — 读 月完売率300 → `item_monthly_turnover`

### 🥈 第二步：完売率落到「库存健康度」+「订货依据」

7. 新建 `pages/XX_📦_订货依据.py`：
   - 输入：完売率历史（最近 3-6 个月）
   - 输出：每个 SKU 的「次月推荐订货量 + 风险标记」
8. 改造 `pages/06_库存健康監控.py`：
   - 引入 sell_through_rate 作为新维度（替代/补充 stock_sales_ratio）

### 🚧 移交自动采购模块（不在本次范围）

- 仕入先 / 発注ロット / 仕入サイクル日数 / 最安原価（4 字段）
- 多仓库 × bin × 7 字段完整库存（在庫スナップショット-980 完整保留）
