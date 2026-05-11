# SQLite → Postgres 迁移说明（历史参考）

> 2026-05-08 已落地。本机/开发仍跑 SQLite；生产（Windows 笔记本）跑 Postgres，靠环境变量 `DATABASE_URL` 切换，业务代码零 diff。本文留作改造方案的历史记录。

部署目标已固定：**Windows 笔记本（Inspiron 5405，影刀共存）**，见 [README.md](README.md)。LinkStation LS210DC 只当备份盘（型号太低跑不了 Docker）。

---

## ✅ 已完成（非破坏性，已 commit）

| 文件 | 改造 | 备注 |
|---|---|---|
| [shared/db.py](../../shared/db.py) | 加 `_PostgresAdapter` wrapper + `DATABASE_URL` 检测 | 默认仍 SQLite，零行为变化 |
| [shared/lark_auth.py](../../shared/lark_auth.py) | 新增飞书 OAuth 模块 | 未配 LARK_APP_ID 自动跳过 |
| [shared/auth.py](../../shared/auth.py) | 加 `_try_lark_sso()` 入口 | 飞书未配时 fallback 到现有账密登录 |
| [schema.postgres.sql](schema.postgres.sql) | SQLite schema 自动转 Postgres | INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL，REAL → DOUBLE PRECISION |

---

## ✅ 改造方案（2026-05-08 落地：适配层透明改写）

**最终方案不再要求逐个文件手工改造**。`shared/db.py` 的 `_PostgresAdapter._adapt_sql()`
在执行 SQL 前自动改写：

| SQLite 语法 | Postgres 等价语法 |
|---|---|
| `INSERT OR REPLACE INTO X (cols) VALUES (...)` | `INSERT INTO X (cols) VALUES (...) ON CONFLICT (pk) DO UPDATE SET col=EXCLUDED.col, ...` |
| `INSERT OR IGNORE INTO X (cols) VALUES (...)` | `INSERT INTO X (cols) VALUES (...) ON CONFLICT DO NOTHING` |
| `?` 占位符 | `%s` 占位符 |

每张表的 conflict 列（PK / UNIQUE）登记在 `_PostgresAdapter._UPSERT_CONFLICT` 字典；
新增表如果用 INSERT OR REPLACE 写入，必须在该字典添加映射，否则启动后第一次写入会
立刻抛 `RuntimeError` 提示。

**优点**：业务代码（21 处 INSERT OR REPLACE / IGNORE）一行未动；本地继续跑 SQLite，
部署目标切到 Postgres 仅靠环境变量 `DATABASE_URL`，零代码 diff。

**单元测试**：`tests/unit/test_postgres_adapter.py`（9 个 case 验证改写正确性 +
登记字典完整性），随主测试套件一起跑。

---

## ❌ 历史方案（已废弃，保留供参考）

> 以下是 2026-05-07 设计的"逐个文件手工改"方案，因适配层方案胜出而废弃。

Postgres 没有 SQLite 的 `INSERT OR REPLACE` / `INSERT OR IGNORE` 语法，原本计划必须改成 `ON CONFLICT (...) DO UPDATE SET ...` / `ON CONFLICT DO NOTHING`。

| 文件 | 出现处 | 替换方案 |
|---|---|---|
| `data_warehouse/db/migrations.py` | schema init 兜底 | NAS 启动时 schema 已由 docker-entrypoint 加载，可跳过 |
| `data_warehouse/ingest/base.py` | Ingestor.run() 框架 | `INSERT OR REPLACE INTO {tbl}` → `INSERT INTO {tbl} ... ON CONFLICT (pk) DO UPDATE SET ...` 工厂 |
| `data_warehouse/ingest/excel_orders.py` | shopee_orders / shopee_fees / shopee_adjustments | 改成 ON CONFLICT (order_id, sku) DO UPDATE |
| `data_warehouse/ingest/excel_shopee_income.py` | shopee_payouts | ON CONFLICT (payout_id) DO UPDATE |
| `data_warehouse/ingest/excel_supplier.py` | supplier / supplier_jan_list | ON CONFLICT (supplier_id) DO UPDATE |
| `data_warehouse/ingest/excel_unified.py` | item / item_master / item_master_netsuite | 已经在用 ON CONFLICT，部分 INSERT OR REPLACE 是回退 |
| `data_warehouse/ingest/xls_ingest.py` | sales_line / inventory_snapshot | DELETE + INSERT 模式（已是 Postgres 兼容）|
| `data_warehouse/ingest/xml_netsuite.py` | nst_inventory_snapshot / nst_turnover / nst_store_sales | ON CONFLICT (composite PK) DO UPDATE |
| `modules/inventory_health/metrics.py` | health_grade_monthly 写回 | ON CONFLICT (item_code, month) DO UPDATE |
| `modules/operation_advice/proposal.py` | operation_advice_monthly | 同上 |
| `pages/07_🏷️_商品等级判定.py` | rank_history 回写 | ON CONFLICT 形式 |
| `shared/db.py` | 已修 | — |
| `tests/integration/test_ingest_base.py` | 测试用例 | 跟 base.py 一起改 |

---

## 🔧 推荐的批量替换策略

写一个 codemod（约 50 行 Python）：

```python
# tools/sqlite_to_postgres.py
import re, pathlib

# 1. INSERT OR REPLACE INTO X (cols) VALUES ... → INSERT INTO X (cols) VALUES ... ON CONFLICT DO UPDATE
# 但需要知道 PK 才能补 ON CONFLICT (pk) → 用 schema.sql 索引找 PK
# 2. INSERT OR IGNORE INTO X ... → INSERT INTO X ... ON CONFLICT DO NOTHING
```

或者最稳：**手工逐个改**，因为每张表的 PK 不同，自动化容易出错。13 个文件预计 2-3 小时。

---

## 🧪 迁移验收脚本

切到 Postgres 后跑一次完整 ingest + 抽样查询验证：

```bash
# 1. 笔记本上启动 Postgres（PowerShell）
#   cd D:\cms-v230\deploy\windows ; docker compose up -d postgres

# 2. 本地 export DATABASE_URL 跑 ingest（先在 docker-compose 给 postgres 临时映射 127.0.0.1:5432:5432）
export DATABASE_URL="postgresql://cms:PASSWORD@<笔记本IP>:5432/cms"
.venv/bin/pytest tests/ -q  # 应全过

# 3. 上传一份月度 + 一份前日 xls
.venv/bin/python -c "
from shared.db import get_connection
from data_warehouse.ingest.xls_ingest import ingest_sales_asean_monthly, ingest_sales_asean_daily
conn = get_connection()
ingest_sales_asean_monthly('【ASEAN】店舗別売上 集計専用-201.xls', conn)
ingest_sales_asean_daily('【ASEAN】店舗別売上（前日）-354.xls', conn)
conn.commit()
"

# 4. 抽样查询
.venv/bin/python -c "
from shared.db import get_connection
conn = get_connection()
for r in conn.execute('SELECT source, COUNT(*) AS n FROM sales_line GROUP BY source').fetchall():
    print(dict(r))
"
```

---

## 📅 迁移时序（明天落地）

```
T+0min     Boss 提供 NAS 5 项 + 域名
T+15min    NAS SSH 上去 git clone + 写 .env
T+25min    Cloudflare Tunnel 创建 + 配 DNS
T+30min    docker compose up -d postgres （单独起 DB 等 schema 初始化）
T+45min    SQL codemod 改 13 个文件 → ON CONFLICT
T+90min    pytest 全过 + 启动 streamlit + cloudflared
T+105min   公网域名打开 → 用 JO043/smikie043 登录验证
T+120min   page 99 重新上传所有月度 + 前日 xls
T+135min   飞书自建应用配置（见 LARK_SETUP.md）
T+165min   飞书工作台测试 SSO 登录
T+180min   关闭 Streamlit Cloud 部署 → DNS 切到 cms.<your-domain>
```

---

## 📌 风险与回退

| 风险 | 概率 | 缓解 |
|---|---|---|
| schema.postgres.sql 自动转换有遗漏 | 中 | 启动时 docker logs 立刻看到 SQL 报错 |
| INSERT OR REPLACE 改造漏改 | 中 | pytest 全跑能覆盖大部分，剩余靠业务测 |
| 飞书 OAuth 配置错 | 低 | 现有账密登录始终保留作 fallback |
| Cloudflare Tunnel 不稳 | 极低 | NAS 主动出站，不依赖入站 |

**回退方案**：把 `.env` 里 `DATABASE_URL` 注释掉，重启 streamlit 容器 → 自动回 SQLite 模式（无数据但不崩）。
