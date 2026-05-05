# 商品信息管理 v3 · SmikieJapan 综合商品分析平台

> Streamlit 多页 App · JD-千叶仓库 SKU 三维体检系统
> 4 月份真实数据落地：3,594 SKU · 137,010 行业务数据 · 等级 + 健康度 + 运营建议三维标签

## 功能页（12 page）

| Page | 模块 | 用途 |
|---|---|---|
| 02_🔍_商品情报检索 | #7 | 多维筛选 + 全文搜索 + CSV 导出 |
| 03_💰_定義原価編集 | #1 | NetSuite Standard Cost 统一管理（自动判定 + Boss 手动覆盖）|
| 04_📊_销售数据查询 | #8 | 时间序列 + 维度交叉 |
| 05_🏪_店铺别毛利 | #6 | 整月 / 单月切换 + Plotly |
| **06_📦_库存健康监控** | **②** | **库存月数 4 档健康度（🟢🟡🟠🔴）** |
| **07_🏷️_商品等级判定** | **①** | **A/B/C/停售 4 档（季度·Boss-only） + ⬆️⬇️ 趋势 + 运营建议**|
| **11_💡_运营调整建议** | **②** | **毛利 × 周转 5 档矩阵**|
| 12_🚫_入荷困難商品 | - | 难进货管理 |
| **13_⚠️_改廃確認** | **③** | **Boss 三按钮（取扱中止 / 継続 / 代替品調査） · 联动停售**|
| **14_💱_Shopee財務** | **④** | **拨款 + 订单级对账 + 站点对比**|
| **15_📝_商品登录** | **⑦** | **iframe 嵌入现有 HTML 工具 + Supabase 同步**|
| **16_📈_等级历史趋势** | **②** | **Sankey 图 + 跨季度等级流向**|
| 99_⚙️_数据导入与设置 | - | 数据上传 + 系统设置 |

## 三维标签体系

每个 JD-千叶仓库 SKU 拿到三个独立维度：

```
等级（订货依据 + 死钱信号）
├── A（销售前 80% × 利润率 ≥ 59%）
├── B（销售前 80% × 利润率 < 59%）
├── C（销售后 20%）
└── 停售（取扱中止）

健康度（库存视角 · 库存月数 ratio_months）
├── 🟢 优秀（≤ 0.7 月卖完）
├── 🟡 健康（0.7-2 月 · 黄金区）
├── 🟠 注意（2-6 月 · 偏滞）
└── 🔴 死钱（> 6 月 · 严重滞销）

运营建议（毛利 × 周转矩阵）
├── 🔥 重点提价 / 🔥 重点降价
├── ⬆️ 提价候选 / ⚠️ 降价候选
├── ⬇️ 降级候选（B→C / C→停售）
└── ✅ 维持
```

## 技术栈

- Streamlit 多页 App（>=1.32）
- Pandas / Plotly / openpyxl
- 后端兼容：本地 SQLite（dev）/ Supabase（prod）
- 5 个 ingestor：excel_unified / xml_netsuite / excel_supplier / excel_shopee_income / excel_orders

## 启动（本地 dev）

```bash
cd ~/CC/商品信息管理

# 装依赖（首次）
uv venv && uv pip install -e ".[dev]"
# 或纯 pip：
pip install -r requirements.txt

# 跑测试
uv run pytest

# 启动 Web App
uv run streamlit run 商品信息管理.py
```

## 部署到 Streamlit Cloud

详见 [deploy/README-DEPLOY.md](deploy/README-DEPLOY.md)。一键脚本：

```bash
bash deploy/push.sh https://github.com/<your-username>/<repo>.git
```

部署完成后在 Streamlit Cloud → app → Settings → Secrets 粘贴：

```toml
BACKEND = "supabase"
SUPABASE_URL = "https://xxx.supabase.co"
SUPABASE_KEY = "eyJ..."
UPLOAD_PASSWORD = "pass1234"
LARK_APP_ID = "..."
LARK_APP_SECRET = "..."
```

## 设计文档

- `docs/01-增强方案-2026-05-05.md` v1 设计草案
- `docs/02-增强方案-v2-2026-05-05.md` v2 战略修订
- **`docs/03-metrics-v3-2026-05-05.md` v3 公式 + 阈值 + 业务规则（最新）**

## 重要术语区分

| 词 | 含义 | 处理 page |
|---|---|---|
| **改廃** | 品牌方/メーカー产品迭代（外部信号）| page 13 改廃確認 |
| **降级** | 内部数据驱动的渐变下调（A→B→C→停售）| page 11 运营建议 |

两者**完全不同**，处理流程互相独立。

## License

Private · SmikieJapan internal use only.
