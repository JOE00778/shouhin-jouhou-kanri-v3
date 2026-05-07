# SQLite → Postgres 迁移清单

明天 NAS 部署时需要逐个改造的文件。当前 Cloud 仍跑 SQLite，本清单是切到 Postgres 时的 "to-do"。

---

## ✅ 已完成（非破坏性，已 commit）

| 文件 | 改造 | 备注 |
|---|---|---|
| [shared/db.py](../../shared/db.py) | 加 `_PostgresAdapter` wrapper + `DATABASE_URL` 检测 | 默认仍 SQLite，零行为变化 |
| [shared/lark_auth.py](../../shared/lark_auth.py) | 新增飞书 OAuth 模块 | 未配 LARK_APP_ID 自动跳过 |
| [shared/auth.py](../../shared/auth.py) | 加 `_try_lark_sso()` 入口 | 飞书未配时 fallback 到现有账密登录 |
| [deploy/nas/schema.postgres.sql](schema.postgres.sql) | SQLite schema 自动转 Postgres | INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL，REAL → DOUBLE PRECISION |

---

## ❌ 还需要改的 13 个文件（INSERT OR REPLACE / INSERT OR IGNORE）

Postgres 没有 SQLite 的 `INSERT OR REPLACE` / `INSERT OR IGNORE` 语法，必须改成 `ON CONFLICT (...) DO UPDATE SET ...` / `ON CONFLICT DO NOTHING`。

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
# 1. 启动 NAS Postgres
cd /volume1/docker/cms-v230/deploy/nas
docker compose up -d postgres

# 2. 本地 export DATABASE_URL 跑 ingest
export DATABASE_URL="postgresql://cms:PASSWORD@<NAS_IP>:5432/cms"
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
