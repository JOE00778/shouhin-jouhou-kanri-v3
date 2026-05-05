-- 商品信息管理平台 · 数据仓库 schema
-- 6 张主表（共享）+ 各模块自有表
-- 全表 UPSERT 兼容；时间字段使用 ISO 8601 字符串
-- 设计依据：/Users/joe/.claude/plans/tidy-yawning-pony.md

-- ============================================================
-- 主表 1：item — 商品主档
-- ============================================================
CREATE TABLE IF NOT EXISTS item (
  internal_id      TEXT PRIMARY KEY,
  item_code        TEXT NOT NULL,
  jan              TEXT,
  display_name     TEXT,
  maker            TEXT,
  rank             TEXT,
  handling_status  TEXT,
  case_qty         INTEGER,
  order_lot        INTEGER,
  weight           REAL,
  avg_cost         REAL,
  std_cost         REAL,
  inactive_flag    INTEGER NOT NULL DEFAULT 0,
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(item_code)
);
CREATE INDEX IF NOT EXISTS idx_item_code           ON item(item_code);
CREATE INDEX IF NOT EXISTS idx_item_maker          ON item(maker);
CREATE INDEX IF NOT EXISTS idx_item_rank           ON item(rank);
CREATE INDEX IF NOT EXISTS idx_item_handling       ON item(handling_status);
CREATE INDEX IF NOT EXISTS idx_item_inactive       ON item(inactive_flag);

-- ============================================================
-- 主表 2：supplier — 供应商主档
-- ============================================================
CREATE TABLE IF NOT EXISTS supplier (
  supplier_id      TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  lead_time_days   INTEGER,
  moq              INTEGER,
  payment_terms    TEXT,
  source_file      TEXT,
  imported_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supplier_name       ON supplier(name);

-- ============================================================
-- 主表 3：sales — 销售明细（按订单行）
-- ============================================================
CREATE TABLE IF NOT EXISTS sales (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id         TEXT,
  internal_id      TEXT NOT NULL REFERENCES item(internal_id),
  store            TEXT,
  sold_at          TEXT NOT NULL,
  qty              INTEGER NOT NULL,
  unit_price       REAL,
  unit_cost        REAL,
  currency         TEXT,
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(order_id, internal_id, sold_at)  -- 防重复导入
);
CREATE INDEX IF NOT EXISTS idx_sales_internal      ON sales(internal_id);
CREATE INDEX IF NOT EXISTS idx_sales_sold_at       ON sales(sold_at);
CREATE INDEX IF NOT EXISTS idx_sales_store         ON sales(store);

-- ============================================================
-- 主表 4：inventory — 库存快照（按时点）
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  internal_id      TEXT NOT NULL REFERENCES item(internal_id),
  snapshot_at      TEXT NOT NULL,
  qty_on_hand      INTEGER NOT NULL,
  qty_committed    INTEGER,
  qty_on_order     INTEGER,
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(internal_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_inventory_internal  ON inventory(internal_id);
CREATE INDEX IF NOT EXISTS idx_inventory_snapshot  ON inventory(snapshot_at);

-- ============================================================
-- 主表 5：purchase — 采购明细（PO + Receipt）
-- ============================================================
CREATE TABLE IF NOT EXISTS purchase (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  po_number        TEXT,
  internal_id      TEXT NOT NULL REFERENCES item(internal_id),
  supplier_id      TEXT REFERENCES supplier(supplier_id),
  ordered_at       TEXT,
  received_at      TEXT,
  qty              INTEGER NOT NULL,
  unit_price       REAL NOT NULL,
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(po_number, internal_id, ordered_at)
);
CREATE INDEX IF NOT EXISTS idx_purchase_internal   ON purchase(internal_id);
CREATE INDEX IF NOT EXISTS idx_purchase_supplier   ON purchase(supplier_id);
CREATE INDEX IF NOT EXISTS idx_purchase_ordered    ON purchase(ordered_at);

-- ============================================================
-- 主表 6：lot — 批次 / 赏味期限
-- ============================================================
CREATE TABLE IF NOT EXISTS lot (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  internal_id      TEXT NOT NULL REFERENCES item(internal_id),
  lot_number       TEXT NOT NULL,
  expiry_date      TEXT,
  qty_remaining    INTEGER,
  received_at      TEXT,
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(internal_id, lot_number)
);
CREATE INDEX IF NOT EXISTS idx_lot_internal        ON lot(internal_id);
CREATE INDEX IF NOT EXISTS idx_lot_expiry          ON lot(expiry_date);

-- ============================================================
-- 共享审计表：_ingest_runs — 每次导入操作记录
-- ============================================================
CREATE TABLE IF NOT EXISTS _ingest_runs (
  run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ingestor         TEXT NOT NULL,        -- ingest/items.py 等模块名
  source_file      TEXT NOT NULL,
  total_rows       INTEGER NOT NULL,
  inserted         INTEGER NOT NULL,
  updated          INTEGER NOT NULL,
  errors           INTEGER NOT NULL,
  run_at           TEXT NOT NULL,
  notes            TEXT
);

-- ============================================================
-- 共享审计表：_ingest_errors — 失败行明细
-- ============================================================
CREATE TABLE IF NOT EXISTS _ingest_errors (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES _ingest_runs(run_id),
  row_number       INTEGER,
  error_message    TEXT NOT NULL,
  raw_row          TEXT                  -- JSON 序列化的原始行
);
CREATE INDEX IF NOT EXISTS idx_ingest_errors_run   ON _ingest_errors(run_id);

-- ============================================================
-- 共享审计表：_export_runs — 每次回写 NetSuite CSV 生成记录
-- ============================================================
CREATE TABLE IF NOT EXISTS _export_runs (
  export_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  exporter         TEXT NOT NULL,        -- exports/cost_update.py 等模块名
  output_file      TEXT NOT NULL,
  row_count        INTEGER NOT NULL,
  run_at           TEXT NOT NULL,
  notes            TEXT
);

-- ============================================================
-- 主表 7：inventory_snapshot — 多仓库库存快照（NetSuite 在库数据导出）
-- 同 SKU × 同 location × 同时间快照唯一
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_snapshot (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  internal_id     TEXT NOT NULL,
  item_code       TEXT NOT NULL,        -- アイテム
  upc             TEXT,                  -- UPCコード = JAN
  display_name    TEXT,
  status          TEXT,                  -- ステータス（通常在庫 等）
  bin_number      TEXT,                  -- 保管棚番号
  location        TEXT,                  -- 場所（仓库）
  handling_status TEXT,                  -- 取扱区分
  qty_on_hand     REAL,                  -- 手持合計
  qty_committed   REAL,                  -- 確保済合計
  qty_backorder   REAL,                  -- バック・オーダー合計
  std_cost        REAL,                  -- アイテム定義原価
  total_amount    REAL,                  -- 合計金額
  avg_cost        REAL,                  -- 平均原価合計
  owner           TEXT,                  -- 商品担当者
  department      TEXT,                  -- 部門
  snapshot_at     TEXT NOT NULL,         -- 快照时点（导入时刻或 NetSuite 报告时间）
  source_file     TEXT,
  imported_at     TEXT NOT NULL,
  UNIQUE(internal_id, location, bin_number, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_inv_snap_internal  ON inventory_snapshot(internal_id);
CREATE INDEX IF NOT EXISTS idx_inv_snap_item      ON inventory_snapshot(item_code);
CREATE INDEX IF NOT EXISTS idx_inv_snap_loc       ON inventory_snapshot(location);
CREATE INDEX IF NOT EXISTS idx_inv_snap_at        ON inventory_snapshot(snapshot_at);
CREATE INDEX IF NOT EXISTS idx_inv_snap_handling  ON inventory_snapshot(handling_status);

-- ============================================================
-- 主表 8：sales_line — 销售明细（统一事实表）
-- 4 类销售导出（ASEAN 月度/日度/輸出アイテム別/輸出店舗別）共用
-- 通过 source 字段区分来源
-- ============================================================
CREATE TABLE IF NOT EXISTS sales_line (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  store               TEXT,              -- nullable（SKU-only 报表无）
  item_code           TEXT NOT NULL,     -- アイテム
  upc                 TEXT,              -- UPCコード（如有）
  display_name        TEXT,
  handling_status     TEXT,              -- 取扱区分
  rank                TEXT,              -- 商品ランク（仅 輸出 系列带）
  qty_sold            REAL,              -- 販売数量
  unit_purchase_price REAL,              -- 購入価格（単価）（仅 輸出アイテム別 带）
  revenue             REAL,              -- 総収益
  defined_cost        REAL,              -- 定義原価
  gross_profit        REAL,              -- 粗利
  gross_margin        REAL,              -- 粗利率
  period_start        TEXT NOT NULL,
  period_end          TEXT NOT NULL,
  source              TEXT NOT NULL,     -- 'asean_monthly' / 'asean_daily' / 'export_item' / 'export_store'
  source_file         TEXT,
  imported_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_line_item    ON sales_line(item_code);
CREATE INDEX IF NOT EXISTS idx_sales_line_store   ON sales_line(store);
CREATE INDEX IF NOT EXISTS idx_sales_line_period  ON sales_line(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_sales_line_source  ON sales_line(source);

-- ============================================================
-- 主表 9：inventory_turnover — 库存周转率（在庫回転率 导出）
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_turnover (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  item_code         TEXT NOT NULL,        -- アイテム
  description       TEXT,                  -- 説明
  cost              REAL,                  -- 原価
  avg_value         REAL,                  -- 平均値
  turnover_rate     REAL,                  -- 回転率
  avg_days_on_hand  REAL,                  -- 平均手持日数
  period_start      TEXT NOT NULL,
  period_end        TEXT NOT NULL,
  source_file       TEXT,
  imported_at       TEXT NOT NULL,
  UNIQUE(item_code, period_start, period_end)
);
CREATE INDEX IF NOT EXISTS idx_turnover_item  ON inventory_turnover(item_code);
CREATE INDEX IF NOT EXISTS idx_turnover_rate  ON inventory_turnover(turnover_rate);

-- ============================================================
-- 模块 #11：difficult_items — 入荷困難商品（人工录入）
-- ============================================================
CREATE TABLE IF NOT EXISTS difficult_items (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key     TEXT NOT NULL,           -- ブランド / 商品名 / JAN 自由文本
  reason       TEXT NOT NULL,
  note         TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diff_items_key  ON difficult_items(item_key);

CREATE TABLE IF NOT EXISTS difficult_items_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id     INTEGER NOT NULL,
  item_key    TEXT NOT NULL,
  reason      TEXT,
  note        TEXT,
  action      TEXT NOT NULL,            -- insert / update / delete
  action_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diff_hist_item  ON difficult_items_history(item_id);
CREATE INDEX IF NOT EXISTS idx_diff_hist_at    ON difficult_items_history(action_at);

-- ============================================================
-- ============================================================
-- 模块 #5：item_master — 一元くん sheet 数据（主档扩展）
-- ============================================================
CREATE TABLE IF NOT EXISTS item_master (
  jan              TEXT PRIMARY KEY,       -- JAN コード
  item_code        TEXT,                   -- 商品コード
  rank             TEXT,                   -- ランク
  maker            TEXT,                   -- メーカー名
  display_name     TEXT,                   -- 商品名
  handling_status  TEXT,                   -- 取扱区分
  on_hand          INTEGER,                -- 在庫
  on_order         INTEGER,                -- 発注済
  actual_cost      REAL,                   -- 実績原価
  min_cost         REAL,                   -- 最安原価
  case_qty         INTEGER,                -- ケース入数
  order_lot        INTEGER,                -- 発注ロット
  weight           REAL,                   -- 重量
  source_file      TEXT,
  imported_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_master_code     ON item_master(item_code);
CREATE INDEX IF NOT EXISTS idx_item_master_maker    ON item_master(maker);
CREATE INDEX IF NOT EXISTS idx_item_master_rank     ON item_master(rank);

-- ============================================================
-- 模块 #5：item_master_netsuite — All Item 0405 sheet 数据
-- ============================================================
CREATE TABLE IF NOT EXISTS item_master_netsuite (
  internal_id      TEXT PRIMARY KEY,       -- 内部ID
  upc              TEXT,                   -- UPC
  display_name     TEXT,                   -- 表示名
  avg_cost         REAL,                   -- 平均原価
  std_cost         REAL,                   -- アイテム定義原価
  last_purchase    REAL,                   -- 前回購入価格
  on_hand          REAL,                   -- 手持
  available        REAL,                   -- 利用可能
  on_order         REAL,                   -- 注文済
  department       TEXT,                   -- 部門
  rank             TEXT,                   -- 商品ランク
  sku_id           TEXT,                   -- skuID
  created_at       TEXT,                   -- 作成日
  maker            TEXT,                   -- メーカー名
  source_file      TEXT,
  imported_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_ns_upc       ON item_master_netsuite(upc);
CREATE INDEX IF NOT EXISTS idx_item_ns_name      ON item_master_netsuite(display_name);
CREATE INDEX IF NOT EXISTS idx_item_ns_dept      ON item_master_netsuite(department);

-- ============================================================
-- 模块 #5：store_monthly — 店舗別 sheet 数据（月度 + 店铺维度）
-- ============================================================
CREATE TABLE IF NOT EXISTS store_monthly (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  year_month       TEXT NOT NULL,          -- 年月（YYYYMM）
  market           TEXT,                   -- 市場（US/PH 等）
  store_id         TEXT,                   -- 店铺ID
  online_products  INTEGER,                -- 在线产品数
  revenue          REAL,                   -- 营业額
  profit           REAL,                   -- 利潤
  margin_rate      REAL,                   -- 毛利率
  profit_contrib   REAL,                   -- 利潤貢献率
  store_rating     REAL,                   -- 店舗評価
  deduction_total  REAL,                   -- 扣减合計
  order_count      INTEGER,                -- 訂單數
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(year_month, market, store_id)
);
CREATE INDEX IF NOT EXISTS idx_store_monthly_ym    ON store_monthly(year_month);
CREATE INDEX IF NOT EXISTS idx_store_monthly_mkt   ON store_monthly(market);
CREATE INDEX IF NOT EXISTS idx_store_monthly_store ON store_monthly(store_id);

-- ============================================================
-- 模块 #5：dead_inventory_monthly — 不动库存分析 sheet 数据（月度滚动）
-- ============================================================
CREATE TABLE IF NOT EXISTS dead_inventory_monthly (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  jan              TEXT NOT NULL,          -- JAN
  display_name     TEXT,                   -- 表示名
  year_month       TEXT NOT NULL,          -- 年月（YYYYMM）
  status           TEXT,                   -- 状態（3ヶ月滞留 等）
  inventory_amount REAL,                   -- 庫存金額
  source_file      TEXT,
  imported_at      TEXT NOT NULL,
  UNIQUE(jan, year_month, status)
);
CREATE INDEX IF NOT EXISTS idx_dead_jan     ON dead_inventory_monthly(jan);
CREATE INDEX IF NOT EXISTS idx_dead_ym      ON dead_inventory_monthly(year_month);
CREATE INDEX IF NOT EXISTS idx_dead_status  ON dead_inventory_monthly(status);

-- ============================================================
-- 模块 #5：nst_turnover — 库存周转率（在庫回転率 导出）
-- ============================================================
CREATE TABLE IF NOT EXISTS nst_turnover (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  department        TEXT,                   -- 部門
  item_code         TEXT NOT NULL,          -- アイテム
  handling_status   TEXT,                   -- 取扱区分
  cost              REAL,                   -- 原価
  avg_value         REAL,                   -- 平均値
  turnover_rate     REAL,                   -- 回転率
  avg_days_on_hand  REAL,                   -- 平均在庫日数
  ingested_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(item_code, department)
);
CREATE INDEX IF NOT EXISTS idx_nst_turnover_item  ON nst_turnover(item_code);
CREATE INDEX IF NOT EXISTS idx_nst_turnover_dept  ON nst_turnover(department);

-- ============================================================
-- 模块 #5：nst_store_sales — 店舗別売上（店舗別売上 导出）
-- ============================================================
CREATE TABLE IF NOT EXISTS nst_store_sales (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fb_store          TEXT,                   -- FB_店舗
  item_code         TEXT NOT NULL,          -- アイテム
  upc               TEXT,                   -- UPCコード
  handling_status   TEXT,                   -- 取扱区分
  display_name      TEXT,                   -- 表示名
  qty_sold          REAL,                   -- 販売数量
  unit_price        REAL,                   -- 購入価格（単価）
  revenue           REAL,                   -- 総収益
  defined_cost      REAL,                   -- 定義原価
  gross_profit      REAL,                   -- 粗利
  gross_margin      REAL,                   -- 粗利率
  rank              TEXT,                   -- 商品ランク
  ingested_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(fb_store, item_code)
);
CREATE INDEX IF NOT EXISTS idx_nst_sales_item   ON nst_store_sales(item_code);
CREATE INDEX IF NOT EXISTS idx_nst_sales_store  ON nst_store_sales(fb_store);

-- ============================================================
-- 模块 #5：nst_inventory_snapshot — 多仓库库存快照（输出在庫数据）
-- 含部门硬过滤（仅 輸出事業*）
-- ============================================================
CREATE TABLE IF NOT EXISTS nst_inventory_snapshot (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  internal_id       TEXT NOT NULL,          -- 内部ID
  item_code         TEXT NOT NULL,          -- アイテム
  upc               TEXT,                   -- UPCコード
  display_name      TEXT,                   -- 表示名
  status            TEXT,                   -- ステータス
  bin_number        TEXT,                   -- 保管棚番号
  location          TEXT,                   -- 場所
  handling_status   TEXT,                   -- 取扱区分
  qty_on_hand       REAL,                   -- 手持合計
  qty_committed     REAL,                   -- 確保済合計
  qty_backorder     REAL,                   -- バック・オーダー合計
  std_cost          REAL,                   -- アイテム定義原価
  total_amount      REAL,                   -- 合計金額
  avg_cost          REAL,                   -- 平均原価合計
  owner             TEXT,                   -- 商品担当者
  department        TEXT,                   -- 部門
  ingested_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(internal_id, location, bin_number)
);
CREATE INDEX IF NOT EXISTS idx_nst_inv_internal  ON nst_inventory_snapshot(internal_id);
CREATE INDEX IF NOT EXISTS idx_nst_inv_item      ON nst_inventory_snapshot(item_code);
CREATE INDEX IF NOT EXISTS idx_nst_inv_loc       ON nst_inventory_snapshot(location);
CREATE INDEX IF NOT EXISTS idx_nst_inv_dept      ON nst_inventory_snapshot(department);

-- ============================================================
-- 模块 #2/#5：supplier_cost — 供应商成本管理（仕入先管理リスト）
-- ============================================================
CREATE TABLE IF NOT EXISTS supplier_cost (
  jan TEXT NOT NULL,
  supplier_name TEXT NOT NULL,
  cost_class TEXT,                    -- 'AB' or 'C'
  unit_cost REAL,
  currency TEXT DEFAULT 'PHP',
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (jan, supplier_name)
);
CREATE INDEX IF NOT EXISTS idx_supplier_cost_jan       ON supplier_cost(jan);
CREATE INDEX IF NOT EXISTS idx_supplier_cost_supplier  ON supplier_cost(supplier_name);
CREATE INDEX IF NOT EXISTS idx_supplier_cost_class     ON supplier_cost(cost_class);

-- ============================================================
-- 模块 #2：supply_cycle — 商品进货周期（AB 商品进货周期 sheet）
-- 关键字段用于 模块 #2 健康度计算（3 桶分类）
-- ============================================================
CREATE TABLE IF NOT EXISTS supply_cycle (
  jan TEXT PRIMARY KEY,
  lead_time_days INTEGER,
  bucket TEXT,                        -- 'short' / 'normal' / 'long'
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_supply_cycle_bucket     ON supply_cycle(bucket);

-- ============================================================
-- 模块 #2/#5：supplier_jan_list — 供应商商品清单（各供应商 sheet）
-- 从 NEW WIND, 中央物産, 菅野, Maple 等 sheet 提取
-- ============================================================
CREATE TABLE IF NOT EXISTS supplier_jan_list (
  jan TEXT NOT NULL,
  supplier_name TEXT NOT NULL,
  status TEXT,                        -- 供应商内商品状态（可选）
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (jan, supplier_name)
);
CREATE INDEX IF NOT EXISTS idx_supplier_jan_list_jan        ON supplier_jan_list(jan);
CREATE INDEX IF NOT EXISTS idx_supplier_jan_list_supplier   ON supplier_jan_list(supplier_name);

-- ============================================================
-- 模块 #2：inventory_health — 库存健康度指标
-- ============================================================
CREATE TABLE IF NOT EXISTS stock_sales_ratio_monthly (
  sku               TEXT NOT NULL,
  year_month        TEXT NOT NULL,
  end_inventory     REAL,                   -- 月末在库（手持合計 聚合）
  monthly_sales     REAL,                   -- 月销量（販売数量 聚合）
  ratio_months      REAL,                   -- end_inventory / monthly_sales
  calculated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku, year_month)
);
CREATE INDEX IF NOT EXISTS idx_stock_sales_ratio_sku     ON stock_sales_ratio_monthly(sku);
CREATE INDEX IF NOT EXISTS idx_stock_sales_ratio_month   ON stock_sales_ratio_monthly(year_month);

CREATE TABLE IF NOT EXISTS cross_ratio_monthly (
  sku               TEXT NOT NULL,
  year_month        TEXT NOT NULL,
  gross_margin      REAL,                   -- 粗利率
  turnover          REAL,                   -- 回転率
  cross_ratio       REAL,                   -- gross_margin * turnover
  calculated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku, year_month)
);
CREATE INDEX IF NOT EXISTS idx_cross_ratio_sku          ON cross_ratio_monthly(sku);
CREATE INDEX IF NOT EXISTS idx_cross_ratio_month        ON cross_ratio_monthly(year_month);

CREATE TABLE IF NOT EXISTS health_grade_monthly (
  sku               TEXT NOT NULL,
  year_month        TEXT NOT NULL,
  bucket            TEXT,                   -- 'short' / 'normal' / 'long'
  threshold         REAL,                   -- THRESHOLD[bucket]
  cross_ratio       REAL,
  grade             TEXT,                   -- '🟢 优秀' / '🟡 健康' / '🟠 注意' / '🔴 死钱'
  dead_money_jpy    REAL,                   -- 仅 '🔴 死钱' 时填（库存数量 × 定义原价）
  calculated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku, year_month)
);
CREATE INDEX IF NOT EXISTS idx_health_grade_sku         ON health_grade_monthly(sku);
CREATE INDEX IF NOT EXISTS idx_health_grade_month       ON health_grade_monthly(year_month);
CREATE INDEX IF NOT EXISTS idx_health_grade_grade       ON health_grade_monthly(grade);

-- ============================================================
-- 模块 #1：rank_history — 等级变化历史（T-016）
-- ============================================================
CREATE TABLE IF NOT EXISTS rank_history (
  sku               TEXT NOT NULL,
  quarter           TEXT NOT NULL,          -- e.g. '2026-Q1'
  old_rank          TEXT,
  new_rank          TEXT,
  changed_by        TEXT,                   -- e.g. 'system' / 'admin'
  changed_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku, quarter)
);
CREATE INDEX IF NOT EXISTS idx_rank_history_sku      ON rank_history(sku);
CREATE INDEX IF NOT EXISTS idx_rank_history_quarter  ON rank_history(quarter);

-- ============================================================
-- 模块 #3：discontinue_alerts — 停产监控警报
-- ============================================================
CREATE TABLE IF NOT EXISTS discontinue_alerts (
  jan              TEXT NOT NULL,
  sku              TEXT,
  source           TEXT NOT NULL,          -- 'netdeoroshi' / 'supplier_list'
  signal_type      TEXT NOT NULL,          -- '販売終了' / '削除' / 'NEW' 等
  detected_at      TEXT NOT NULL,          -- ISO 8601
  acknowledged_by  TEXT,
  acknowledged_at  TEXT,
  action           TEXT,                   -- 确认后的处置措施
  PRIMARY KEY (jan, source, signal_type, detected_at)
);
CREATE INDEX IF NOT EXISTS idx_discontinue_jan        ON discontinue_alerts(jan);
CREATE INDEX IF NOT EXISTS idx_discontinue_source     ON discontinue_alerts(source);
CREATE INDEX IF NOT EXISTS idx_discontinue_detected   ON discontinue_alerts(detected_at);
CREATE INDEX IF NOT EXISTS idx_discontinue_acked      ON discontinue_alerts(acknowledged_at);

-- Schema 版本表（用于将来迁移）
-- ============================================================
CREATE TABLE IF NOT EXISTS _schema_version (
  version          INTEGER PRIMARY KEY,
  applied_at       TEXT NOT NULL
);

-- ============================================================
-- 模块 #4：shopee_payouts — Shopee 拨款主档
-- ============================================================
CREATE TABLE IF NOT EXISTS shopee_payouts (
  payout_id        TEXT PRIMARY KEY,
  seller_account   TEXT,
  channel          TEXT,
  payout_date      DATE,
  total_payout     REAL,
  currency         TEXT,
  ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shopee_payouts_seller ON shopee_payouts(seller_account);
CREATE INDEX IF NOT EXISTS idx_shopee_payouts_date   ON shopee_payouts(payout_date);

-- ============================================================
-- 模块 #4：shopee_fees — Shopee 服务费明细
-- ============================================================
CREATE TABLE IF NOT EXISTS shopee_fees (
  fee_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no         TEXT,
  fee_type         TEXT,
  amount           REAL,
  ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shopee_fees_order     ON shopee_fees(order_no);
CREATE INDEX IF NOT EXISTS idx_shopee_fees_type      ON shopee_fees(fee_type);

-- ============================================================
-- 模块 #4：shopee_adjustments — Shopee 调整明细
-- ============================================================
CREATE TABLE IF NOT EXISTS shopee_adjustments (
  adjustment_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  seller_account   TEXT,
  payout_id        TEXT,
  payout_date      DATE,
  ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shopee_adj_seller     ON shopee_adjustments(seller_account);
CREATE INDEX IF NOT EXISTS idx_shopee_adj_payout     ON shopee_adjustments(payout_id);

-- ============================================================
-- 模块 #4：shopee_orders — Shopee 订单明细
-- ============================================================
CREATE TABLE IF NOT EXISTS shopee_orders (
  order_no         TEXT,
  sku_or_jan       TEXT,
  qty              REAL,
  unit_price       REAL,
  payment_amount   REAL,
  currency         TEXT,
  platform         TEXT,
  shop_name        TEXT,
  ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (order_no, sku_or_jan)
);
CREATE INDEX IF NOT EXISTS idx_shopee_orders_sku     ON shopee_orders(sku_or_jan);
CREATE INDEX IF NOT EXISTS idx_shopee_orders_order   ON shopee_orders(order_no);
CREATE INDEX IF NOT EXISTS idx_shopee_orders_date    ON shopee_orders(ingested_at);

-- ============================================================
-- 模块 #2 v3：operation_advice_monthly — 运营调整建议（毛利×周转双轴）
-- ============================================================
CREATE TABLE IF NOT EXISTS operation_advice_monthly (
  sku              TEXT,
  year_month       TEXT,
  rank             TEXT,
  margin_pct       REAL,
  monthly_turnover REAL,
  cross_ratio      REAL,
  margin_lv        TEXT,         -- 低/中/高
  turnover_lv      TEXT,         -- 低/中/高
  advice           TEXT,         -- 🔥重点提价 / 🔥重点降价 / ⬆️/⚠️/⚫/✅
  reason           TEXT,
  inventory_value  REAL,
  calculated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku, year_month)
);
CREATE INDEX IF NOT EXISTS idx_op_advice_advice   ON operation_advice_monthly(advice);
CREATE INDEX IF NOT EXISTS idx_op_advice_rank     ON operation_advice_monthly(rank);
CREATE INDEX IF NOT EXISTS idx_op_advice_value    ON operation_advice_monthly(inventory_value DESC);

-- ============================================================
-- 订货模块 6 张新表（v3.1 from order-management-app）
-- ============================================================

-- 进货价目（jan × supplier × order_lot → price）
CREATE TABLE IF NOT EXISTS purchase_data (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  jan           TEXT NOT NULL,
  supplier      TEXT,
  order_lot     INTEGER,
  price         REAL,
  source_file   TEXT,
  imported_at   TEXT NOT NULL,
  UNIQUE(jan, supplier, order_lot)
);
CREATE INDEX IF NOT EXISTS idx_purchase_data_jan      ON purchase_data(jan);
CREATE INDEX IF NOT EXISTS idx_purchase_data_supplier ON purchase_data(supplier);

-- 订货历史（已下达过的订货单）
CREATE TABLE IF NOT EXISTS purchase_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  jan           TEXT NOT NULL,
  quantity      INTEGER NOT NULL,
  memo          TEXT,
  order_date    TEXT,
  order_id      TEXT,
  source_file   TEXT,
  imported_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purchase_history_jan       ON purchase_history(jan);
CREATE INDEX IF NOT EXISTS idx_purchase_history_orderdate ON purchase_history(order_date);
CREATE INDEX IF NOT EXISTS idx_purchase_history_orderid   ON purchase_history(order_id);

-- JD 仓库库存（product_code = jan）
CREATE TABLE IF NOT EXISTS warehouse_stock (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  product_code    TEXT NOT NULL,
  jan             TEXT,
  stock_available INTEGER NOT NULL DEFAULT 0,
  snapshot_at     TEXT,
  source_file     TEXT,
  imported_at     TEXT NOT NULL,
  UNIQUE(product_code, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_warehouse_stock_jan ON warehouse_stock(jan);

-- 弁天仓库库存
CREATE TABLE IF NOT EXISTS benten_stock (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  jan           TEXT NOT NULL,
  stock         INTEGER NOT NULL DEFAULT 0,
  snapshot_at   TEXT,
  source_file   TEXT,
  imported_at   TEXT NOT NULL,
  UNIQUE(jan, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_benten_stock_jan ON benten_stock(jan);

-- 保质期管理（来自 Lark 同步）
CREATE TABLE IF NOT EXISTS item_expiry (
  jan           TEXT PRIMARY KEY,
  name          TEXT,
  expiry_1      TEXT,
  expiry_2      TEXT,
  expiry_3      TEXT,
  expiry_4      TEXT,
  expiry_5      TEXT,
  expiry_min    TEXT,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_expiry_min ON item_expiry(expiry_min);

-- 每日店铺销售明细
CREATE TABLE IF NOT EXISTS store_profit_daily_lines (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  report_date   TEXT NOT NULL,
  line_type     TEXT NOT NULL,    -- detail / 合計 / 総計 等
  store         TEXT NOT NULL,
  item          TEXT,
  item_name     TEXT,
  qty           INTEGER DEFAULT 0,
  revenue       INTEGER DEFAULT 0,
  defined_cost  INTEGER DEFAULT 0,
  gross_profit  INTEGER DEFAULT 0,
  source_file   TEXT,
  imported_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spdl_date  ON store_profit_daily_lines(report_date);
CREATE INDEX IF NOT EXISTS idx_spdl_store ON store_profit_daily_lines(store);
CREATE INDEX IF NOT EXISTS idx_spdl_type  ON store_profit_daily_lines(line_type);
