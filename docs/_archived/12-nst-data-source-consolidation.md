# NST 数据源整合方案 · 9 ingester → 2 NetSuite Saved Search + API

> 状态：v2 · 2026-05-09 (Boss 反馈修正)
> 目标：把现有 NetSuite XLSX 上传 ingester 整合为 **2 个 Saved Search**（A 商品主档 / C 销售），通过 NetSuite REST API 每日自动拉取。
> Report B (库存快照) 移交「自动采购模块」（新项目）。Shopee 2 个 ingester 维持现状（平台数据）。
> 详细字段清单见 [13-nst-saved-search-field-list.md](./13-nst-saved-search-field-list.md)。

---

## 🎯 现状盘点

### 当前 9 个 ingester

| ingester key | 文件名识别 | 落表 | 数据源 |
|---|---|---|---|
| `inventory` | 在庫数残数 / 通常在庫.xlsx | `item_inventory_snapshot_v2` + `item_v2` 库存汇总 | **NST** |
| `turnover` | 在庫回転率.xlsx | `item_inventory_snapshot_v2` (周转维度) | **NST** |
| `asean_monthly` | ASEAN 店舗別.xlsx | `shop_sales` (granularity=monthly) | **NST** |
| `asean_daily` | ASEAN 前日.xlsx | `shop_sales` (granularity=daily) | **NST** |
| `export_item` | 輸出 アイテム別.xlsx | `shop_sales` (item 维度) | **NST** |
| `export_store` | 輸出 店舗別.xlsx | `shop_sales` (store 维度) | **NST** |
| `item_summary` | アイテム概要.xlsx | `item_v2` (基础字段) | **NST** |
| `shopee_orders` | 订单导出.xlsx | `shopee_orders_raw` | Shopee 平台 |
| `shopee_income` | mtkshop.income.已拨款.xlsx | `shopee_income_lines` | Shopee 平台 |

→ **NST 占 7 个** · Shopee 占 2 个

### v2 真表对应字段需求

#### `item_v2` (商品主档 · PK = jan)

| 字段 | 当前来源 | 优先级 |
|---|---|---|
| `jan` (PK) | item_summary / inventory_snapshot | 必需 |
| `item_code` (アイテム) | item_summary / inventory | 必需 |
| `internal_id` (内部ID) | inventory | 必需 |
| `display_name` (表示名) | 全部来源 | 必需 |
| `maker` (メーカー) | item_master_netsuite | 必需 |
| `rank` (ランク) | item_summary 派生 | 必需 |
| `handling_status` (取扱区分) | inventory / item_summary | 必需 |
| `department` (部門) | inventory | 必需 |
| `std_cost` (定義原価) | inventory / item_summary | 必需 |
| `avg_cost` (平均原価) | inventory / item_summary | 必需 |
| `actual_cost` (実績原価) | item_master | 可选 |
| `min_cost` (最安原価) | item_master | 可选 |
| `case_qty` (ケース入数) | item_master / Boss screenshot 「カートン入数」 | 必需 |
| `order_lot` (発注ロット) | item_master | 可选 |
| `weight` (重量) | item_master | 可选 |
| `supplier_default` (仕入先) | item_master / supplier_jan_list | 必需 |
| `supply_cycle_days` (仕入サイクル) | supply_cycle | 必需 |
| `bucket` (仕入バケット) | supply_cycle | 必需 |
| `on_hand_total` (手持) | inventory 聚合 | 必需 |
| `on_order_total` (注文済) | inventory 聚合 | 必需 |
| `qty_committed_total` (確保済) | inventory 聚合 | 必需 |
| `total_amount` (在庫金額) | inventory 聚合 | 必需 |

#### `item_inventory_snapshot_v2` (库存快照 · 仓库 × bin)

| 字段 | 来源 | 备注 |
|---|---|---|
| `jan`, `item_code`, `internal_id`, `display_name` | inventory | 必需 |
| `location` (場所) | inventory | 必需 |
| `bin_number` (保管棚番号) | inventory | 必需 |
| `snapshot_at` (快照时点) | 当前时间戳 | 必需 |
| `qty_on_hand`, `qty_committed`, `qty_backorder` | inventory | 必需 |
| `std_cost`, `avg_cost`, `total_amount` | inventory | 必需 |
| `handling_status`, `status`, `owner`, `department` | inventory | 必需 |

#### `shop_sales` (店铺 × SKU 销售明细)

| 字段 | 来源 | 备注 |
|---|---|---|
| `shop_id` | asean / export 的店铺名 | 必需 |
| `jan` | 全部来源 | 必需 |
| `granularity` | 'monthly' / 'daily' | 必需 |
| `period_start`, `period_end` | 报表 period | 必需 |
| `qty_sold`, `revenue`, `revenue_jpy`, `unit_price` | asean / export | 必需 |
| `cost`, `gross_profit`, `gross_margin` | asean / export | 必需 |
| `rank` | item dimension | 可选 |

---

## 🚀 整合方案：3 个 NetSuite Saved Search

### Report A · `SS_Item_Master_Daily` （商品主档 + 库存汇总）

**对应当前 ingester：** `item_summary` + 部分 `inventory` (item 级聚合)

**触发：** 每日 1 次 (例: 04:00 JST)
**类型：** Saved Search on `Item` record
**基于 Boss 截图「輸出アイテム(JO)」view 扩展，新增的字段标 ⭐**

| Field (NetSuite) | DB 落点 | 已在视图 |
|---|---|---|
| Internal ID | `item_v2.internal_id` | ✅ 内部ID |
| Name | `item_v2.item_code` | ✅ 名前 |
| UPC | `item_v2.jan` | ✅ UPCコード |
| Display Name | `item_v2.display_name` | ✅ 表示名 |
| Average Cost | `item_v2.avg_cost` | ✅ 平均原価 |
| Standard Cost | `item_v2.std_cost` | ✅ アイテム定義原価 |
| Last Purchase Price | `item_v2.actual_cost` | ✅ 前回購入価格 |
| Carton Qty (ケース入数) | `item_v2.case_qty` | ✅ カートン入数 |
| Quantity On Hand | `item_v2.on_hand_total` | ✅ 手持 |
| Quantity Committed | `item_v2.qty_committed_total` | ✅ 確保済 |
| Quantity On Order | `item_v2.on_order_total` | ✅ 注文済 |
| Item Manager | `item_v2.owner` | ✅ 商品担当者 |
| SKU ID | `item_v2.sku_id` | ✅ skuID |
| Available | (不入库) | ✅ 利用可能 |
| ⭐ Manufacturer | `item_v2.maker` | 需要在视图加 |
| ⭐ Rank (custom) | `item_v2.rank` | 需要在视图加 |
| ⭐ Handling Status (custom) | `item_v2.handling_status` | 需要在视图加 |
| ⭐ Department | `item_v2.department` | 需要在视图加 |
| ⭐ Default Supplier | `item_v2.supplier_default` | 需要在视图加 |
| ⭐ Order Lot (custom) | `item_v2.order_lot` | 需要在视图加 |
| ⭐ Weight | `item_v2.weight` | 需要在视图加 |
| ⭐ Total Inventory Value | `item_v2.total_amount` | 需要在视图加 |
| ⭐ Min Cost (custom) | `item_v2.min_cost` | 需要在视图加 |
| ⭐ Created Date | `item_v2.created_at` | 需要在视图加 |

**Boss 操作：** 在「輸出アイテム(JO)」view 右上「ビューを編集」，追加上面 ⭐ 标的字段。**字段必须直接来自 NetSuite Item record**（含 custom field）。

---

### Report B · `SS_Inventory_Snapshot_Daily` （库存快照 · 多仓库 × bin）

**对应当前 ingester：** `inventory` (location 级别) + `turnover`

**触发：** 每日 1 次 (例: 04:30 JST)
**类型：** Saved Search on `Inventory Detail` 或 `Item` record (Inventory Bin lookup)

| Field (NetSuite) | DB 落点 |
|---|---|
| UPC | `jan` |
| Item Name | `item_code` |
| Internal ID | `internal_id` |
| Display Name | `display_name` |
| Location | `location` |
| Bin Number | `bin_number` |
| On Hand | `qty_on_hand` |
| Committed | `qty_committed` |
| Backorder | `qty_backorder` |
| Standard Cost | `std_cost` |
| Average Cost | `avg_cost` |
| Total Value | `total_amount` |
| Handling Status (custom) | `handling_status` |
| Status | `status` |
| Owner | `owner` |
| Department | `department` |

**Note:** `snapshot_at` 由 ingest 端写当前时间戳。

---

### Report C · `SS_Sales_Daily` （销售汇总 · 期间 × 店铺 × SKU）

**对应当前 ingester：** `asean_monthly` + `asean_daily` + `export_item` + `export_store`（4 合 1）

**触发：** 每日 1 次 (例: 05:00 JST)
**类型：** Saved Search on `Sales Order` 或 `Item Sales` (汇总)

| Field (NetSuite) | DB 落点 | 备注 |
|---|---|---|
| Period Start | `period_start` | 必需 |
| Period End | `period_end` | 必需 |
| Granularity Flag (custom) | `granularity` | 'daily' / 'monthly' |
| Store / Channel | `shop_id` | 'shopee_tw_smikie_main' 等 |
| UPC | `jan` | 必需 |
| Item Code | `item_code` | 必需 |
| Display Name | `display_name` | 必需 |
| Qty Sold | `qty_sold` | 必需 |
| Revenue (Local Currency) | `revenue` | 必需 |
| Revenue (JPY) | `revenue_jpy` | 必需 |
| Unit Price | `unit_price` | 必需 |
| Cost | `cost` | 必需 |
| Gross Profit | `gross_profit` | 必需 |
| Gross Margin | `gross_margin` | 必需 |
| Rank (custom) | `rank` | 可选 |
| Source Tag | `source` | 'asean_monthly' / 'export_item' 等 |

**单一 Saved Search 对应多种粒度的方法：**
- 选项 A：每天跑 2 个 saved search instance（一个 daily 一个 monthly），共享 schema
- 选项 B：在 Saved Search 加自定义筛选参数（period type），调用方根据需要传入

推荐 **选项 A**（更易在 NetSuite 端配置 + 在 ingest 端识别 source）。

---

## 🔌 API 接入方案

### NetSuite REST API (SuiteTalk REST)

```
GET https://{accountId}.suitetalk.api.netsuite.com/services/rest/record/v1/savedSearch/{searchId}/result
Authorization: OAuth 2.0 (Token-Based) 或 OAuth 1.0
```

或更简单：使用 **RESTlet** + Saved Search execute

```python
# data_warehouse/ingest/nst_api.py（新增）
import requests
from requests_oauthlib import OAuth1

NST_ACCOUNT = "6806569"  # 从 Boss 截图 url 提取
NST_REST_BASE = f"https://{NST_ACCOUNT}.suitetalk.api.netsuite.com/services/rest"

SAVED_SEARCHES = {
    "item_master":   {"id": "customsearch_item_master_daily",   "ingester": "item_master_v2"},
    "inventory":     {"id": "customsearch_inventory_snapshot",  "ingester": "inventory_v2"},
    "sales_monthly": {"id": "customsearch_sales_monthly",       "ingester": "sales_v2"},
    "sales_daily":   {"id": "customsearch_sales_daily",         "ingester": "sales_v2"},
}

def fetch_saved_search(key: str) -> list[dict]:
    cfg = SAVED_SEARCHES[key]
    r = requests.get(
        f"{NST_REST_BASE}/record/v1/savedSearch/{cfg['id']}/result",
        auth=oauth_provider,
        params={"limit": 5000},
    )
    return r.json()["items"]


def daily_sync():
    """每日定时调用 (cron/scheduler)."""
    for key in SAVED_SEARCHES:
        rows = fetch_saved_search(key)
        ingester = INGESTOR_REGISTRY[SAVED_SEARCHES[key]["ingester"]]
        ingester(rows)  # 直写 v2 真表
```

**部署位置：**
- `cms-v230` Docker 容器内运行 cron（windows task scheduler 触发 docker exec）
- 或单独跑 `daily_sync.py` 脚本，用 systemd timer

---

## 📊 整合前后对比

| 项 | 前 | 后 |
|---|---|---|
| ingester 数量 | 9 | 5（3 NST Saved Search + 2 Shopee 平台） |
| 数据源类型 | XLSX 手工上传 | NST → API 拉取 / Shopee → 平台导出 |
| 更新频率 | 不定时（Boss 手动上传） | NST 每日自动 / Shopee 周拨款 |
| 失败可见性 | 看 page99 ingest 日志 | 同 + API 调用日志 |
| 可维护性 | 多个文件名 + 多个表头 | 3 个 Saved Search + 2 个 Shopee export |

---

## ⏭️ 落地步骤

1. **Boss：在 NetSuite 配置 3 个 Saved Search**（参考上面字段表）
   - `customsearch_item_master_daily`（基于 Boss 截图「輸出アイテム(JO)」加 ⭐ 字段）
   - `customsearch_inventory_snapshot`
   - `customsearch_sales_monthly` + `customsearch_sales_daily`
2. **生成 Token-Based Authentication 凭据**（NetSuite Setup → Users → TBA Tokens）
3. **CC：实现 `data_warehouse/ingest/nst_api.py`**
   - `fetch_saved_search(key)` 拉数据
   - 复用现有 9 个 ingester 中 `inventory` / `_ingest_sales` / `item_summary` 的核心逻辑（已直写 v2）
4. **CC：调度 + 监控**
   - 每日 04:00 / 04:30 / 05:00 自动跑
   - 失败 Lark 通知
5. **page 99 改造**
   - Tab 1 「文件上传」保留作为 fallback（API 不通时手动上传）
   - 新增 Tab 「API 同步状态」显示最近 7 天每个 Saved Search 的拉取状态

---

## ❓ 待 Boss 确认

1. NetSuite Account ID 是 `6806569` 吗？（从截图 URL 提取）
2. Saved Search 的 customsearch ID 命名规范？
3. API 凭据 (TBA Token) 谁来生成？
4. 现有的 7 个 NST XLSX 上传 fallback 是否保留？
5. **新增字段** ⭐ 是否需要确认在 NetSuite Item record 都存在？（特别是 maker / rank / handling_status / supplier_default 等 custom field）
