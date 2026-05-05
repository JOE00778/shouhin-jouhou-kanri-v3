# Legacy Streamlit App · 迁入说明

## 现状（更新于 2026-05-02）

- Boss 已提供干净版源码（日语正确）
- ⚠️ 由于单消息 50,000 字符限制，**只接收到约 1/2 内容**（到 `monthly_sales` mode 中段截断）
- 待 Boss 通过 **GitHub URL / 本地文件路径 / 分段重发** 提供完整版后补齐

## 已确认的关键事实（基于已收到的清洁部分）

### 真源 = Supabase

`main.py` 全程通过 `requests.get/post` 直连 Supabase REST API：
- `SUPABASE_URL` / `SUPABASE_KEY` 来自 `st.secrets`
- 上传密码：`UPLOAD_PASSWORD`（默认 `pass1234`）

### 涉及的 Supabase 表（已确认）

| 表 | 用途 | 关键列 |
|---|---|---|
| `item_master` | 商品主档 | jan, 商品コード, 商品名, メーカー名, ランク, 取扱区分, ケース入数, 発注ロット, 重量, 在庫, 利用可能, 発注済, 仕入価格, average_cost, purchase_cost, updated_at |
| `sales` | 销售（最近 30 天聚合） | jan, quantity_sold, stock_total, stock_available, stock_ordered |
| `purchase_data` | 候选采购单价 + lot | jan, supplier, order_lot, price |
| `purchase_history` | 实际下单履历 | jan, quantity, memo, order_date, order_id |
| `warehouse_stock` | JD 仓库库存（接 Excel） | product_code, jan, stock_available |
| `benten_stock` | 弁天仓库库存（接 BENTEN CSV） | jan, stock, updated_at |

后续 mode 补齐后还会有：
- `difficult_items` + `_history`
- `store_profit_lines`
- `store_profit_daily_lines`
- `item_expiry`（含 Lark Sheets 同步源）

### CSV 上传时的列名映射（NetSuite Excel → Supabase）

`csv_upload` mode 里的关键映射（item_master）：
```python
"表示名"           → "商品名"
"アイテム定義原価" → "仕入価格"
"カートン入数"     → "ケース入数"
"発注ロット"       → "発注ロット"
"パッケージ重量(g)" → "重量"
"手持"             → "在庫"
"利用可能"         → "利用可能"
"注文済"           → "発注済"
"名前"             → "商品コード"
"商品ランク"       → "ランク"
UPC 含有列         → "jan"
```

这是 **NetSuite → Supabase** 接入的字段约定，**模块 #1 成本同步**也要参考这个约定。

### 业务规则关键发现

`order_ai` mode 的发注算法（已读完）：
- **A/B ランク（含 ★ 后缀）**：
  - 发注点：`在庫 + 発注済 < ceil(实绩 × 1.2)` 才发注
  - 发注数：`ceil(实绩 × 1.7)`
- **C/TEST ランク**：
  - 发注点：`在庫 + 発注済 > floor(实绩 × 0.7)` 跳过
  - 发注数：`ceil(实绩 × 倍率) - 在庫 - 発注済`，其中 TEST 倍率=1.5、C=1.0
- **上海发注**：从 `purchase_history` 里 memo 含「上海」的发注量，从 `item_master.発注済` 中扣减
- **直近 2 天发注的 SKU**：跳过避免重复
- **最低 1 个特例**：在庫≤1 且实绩≥1 时强制至少 1
- **ロット最优匹配**：A/B 选 ≥需求的最小 lot；C/TEST 优先 ≤需求的最近 lot，否则在 (需求, 1.5×需求] 范围找，再不行用 lot=1，最后兜底取最小

### 13 个 mode 与新平台 10 模块的对应

| mode | 业务 | 跟新平台 10 模块对应 | 完成度 |
|---|---|---|---|
| home | 首页 | 0 平台首页 | - |
| order_ai | 发注 AI 判定（按 rank A/B/C/TEST 不同算法） | **#5 自动订货** | ~70% |
| search_item | 商品情报检索 | **#7 商品情报检索** | ~80% |
| purchase_history | 发注履历查询 | （新增维度，进采购/销售分析） | - |
| price_improve | 仕入价格改善列表 | **#3 进货价格波动** 子集 | ~40% |
| csv_upload | CSV 上传到 Supabase | 平台数据导入页 | ~80% |
| monthly_sales | 销售业绩（最近 1ヶ月） | **#8 销售数据查询** | ~60% |
| rank_check | rank 别发注确认 | **#2 商品等级判定** 查询面 | ~50% |
| difficult_items | 入荷困难商品 | （新增维度） | - |
| order | 发注书作成（粘贴/CSV → NetSuite 风格 CSV） | **#10 商品登录** 相邻能力 | ~50% |
| store_profit | 店铺别毛利一览 | **#6 店铺别毛利** | ~80% |
| daily_sales | 店铺别日次売上 | **#6 日维度** | ~80% |
| expiry_manage | 赏味期限（含 Lark Sheets 同步） | **#9 赏味期限** | ~80% |

**全新要做的只有 #1 成本同步 和 #4 库存健康监控。**

## 现有架构特点

- 单文件 `main.py`，按 `mode` 分支（巨型 if/elif）
- 直接用 `requests` 打 Supabase REST，无 ORM、无连接池、无迁移
- 多语言（日 / 中）通过两层 dict 字符串映射
- URL 路由：`?mode=xxx` 是关键，所有侧边栏导航通过 `<a href>` 实现
- 自定义 CSS（multiselect tag 样式覆盖）
- 无单元测试
- 配置通过 `st.secrets`

## 待完成

- [ ] 接收完整源码（GitHub URL / 多段消息）
- [ ] secrets.toml.example 提供 SUPABASE_URL / SUPABASE_KEY / LARK_APP_ID 等占位符
- [ ] 增加 `streamlit_javascript` 依赖到 pyproject.toml
- [ ] 在新平台主入口侧边栏增加「📦 经典模式」入口，链接到 legacy
