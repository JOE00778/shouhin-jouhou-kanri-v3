# CMS 自动化架构 · 触发→执行→回报

> 落地日期：2026-05-08
> 涵盖范围：Shopee 自动上架（已实现 MVP）、NST 发注书 → 影刀（设计中）、改廃确认 → N8N（设计中）

---

## 1. 总体闭环

```
┌────────────────┐  trigger_workflow()    ┌─────────────────────────┐
│  CMS Page (UI) ├───────────────────────►│  shared/n8n_client.py  │
└────────────────┘                         └────────────┬────────────┘
       ▲                                                │
       │ poll automation_runs                            │ POST webhook
       │                                                 ▼
┌────────────────┐                         ┌─────────────────────────┐
│ automation_runs│ ◄────UPDATE summary──── │ N8N (n8n.smikie-cms.cc) │
│   (Postgres)   │                         │   或 影刀 RPA            │
└────────────────┘                         └────────────┬────────────┘
                                                        │ POST callback / 直接 UPDATE
                                                        ▼
                                            ┌─────────────────────────┐
                                            │  飞书群机器人通知 Boss  │
                                            └─────────────────────────┘
```

三段：

| 段 | 职责 | 落地物 |
|---|---|---|
| **触发** | CMS 收集业务输入 → 落 `automation_runs.pending` → POST N8N webhook | `shared/n8n_client.trigger_workflow()` + 各 page |
| **执行** | N8N workflow / 影刀 任务跑业务逻辑 | `deploy/n8n/workflows/*.json` + 影刀工程 |
| **回报** | 状态写回 `automation_runs` + 飞书群通知 | `shared/lark_notify.py` + N8N 内 HTTP 节点 |

---

## 2. 关键表 · `automation_runs`

```sql
CREATE TABLE automation_runs (
  run_id        TEXT PRIMARY KEY,    -- uuid4
  module        TEXT NOT NULL,       -- 业务模块标识
  payload       TEXT,                -- 触发参数 JSON
  status        TEXT NOT NULL,       -- pending/processing/completed/failed
  summary       TEXT,                -- 结果汇总 JSON
  triggered_by  TEXT,                -- 触发用户邮箱
  triggered_at  TEXT NOT NULL,
  completed_at  TEXT
);
```

`module` 命名约定（避免冲突）：

| module | 说明 | 执行端 |
|---|---|---|
| `shopee_mass_upload` | Shopee 自动上架 | N8N |
| `nst_order` | NetSuite 发注书生成 + 提交 | 影刀（NetSuite Saved Search 没 API） |
| `discontinue_confirm` | 改廃确认（停售商品最后审核 + 通知） | N8N |
| `dianxiaomi_upload` | 店小秘批量上架（备选）| N8N |

---

## 3. 已实现 · Shopee 自动上架

### 触发路径

[pages/22_🤖_Shopee自动上架.py](../pages/22_🤖_Shopee自动上架.py) → 上传 mass-upload XLSX
（base64 编码进 payload，10 MB 上限）→ `trigger_workflow(module="shopee_mass_upload",
webhook_path="shopee-mass-upload", payload={...})`

### 执行路径

[deploy/n8n/workflows/shopee-mass-upload.json](../deploy/n8n/workflows/shopee-mass-upload.json)
（MVP scaffold，7 节点）：

```
Webhook · CMS 触发
  ↓
环境变量提取 + 立即返回 CMS（accept）
  ↓
CMS 回调 · processing
  ↓
处理 SPU（占位 → 待 Boss 提供 Shopee partner_id/key 后充实）
  ↓
CMS 回调 · completed
  ↓
飞书通知（卡片）
```

**当前状态**：scaffold 可跑通全链路（仅 mock 处理结果）；Shopee API 节点待 Boss 提供
`SHOPEE_PARTNER_ID` / `SHOPEE_PARTNER_KEY` 后充实（在 `.env` 加完即生效）。

### 回报路径

- N8N → CMS：HTTP POST `${CMS_CALLBACK_URL}`（payload `{run_id, status, summary}`）
- N8N → 飞书：HTTP POST `${LARK_WEBHOOK_URL}`（卡片消息）

> ⚠️ **CMS 端接收回调的 endpoint 尚未实现**（Streamlit 没原生 HTTP API）。
> 当前方案：N8N 直接 SQL UPDATE Postgres `automation_runs`（部署在同机时）。
> 长期方案：CMS docker-compose 加 FastAPI sidecar 接收 webhook。

---

## 4. 设计中 · NST 发注书 → 影刀

### 业务背景

NetSuite 发注（Purchase Order 生成 + 提交）没有公开 API，只能通过浏览器操作。
影刀已经有人工录入的脚本，目标是把 CMS 触发链路接进去。

### 集成方案候选

| 方案 | 影刀触发方式 | 优缺点 |
|---|---|---|
| **A** 影刀 HTTP 触发器 | CMS POST 影刀 webhook | 影刀企业版才有 webhook trigger，确认许可证 |
| **B** 文件触发器 | CMS 把任务文件写到共享目录，影刀目录监听 | 简单稳定，无需 API；延迟 0-30s |
| **C** 影刀计划任务轮询 | CMS 写 `automation_runs.pending`，影刀每分钟查 | 简单但延迟 ≤60s |
| **D** 影刀 RPA SDK | 影刀 Python SDK 触发任务 | 重度集成，开发成本高 |

**推荐 B**：CMS 把发注任务（CSV / JSON）写到 `\\shared\nst_orders\{run_id}.json`，
影刀监听该目录 → 取文件 → 执行 → 把结果写到 `\\shared\nst_results\{run_id}.json`。
CMS 轮询结果目录或读 automation_runs.summary。

### 待办

- 跟影刀工程师确认目录监听触发器的可用性
- 设计 `nst_order_*.json` schema
- CMS page 24（NST 发注） 上传 → 写共享文件 → automation_runs.pending
- 影刀脚本：从共享目录读 → 执行发注 → 写结果文件 + UPDATE automation_runs

---

## 5. 设计中 · 改廃确认 → N8N

### 业务背景

[pages/13_⚠️_改廃確認.py](../pages/13_⚠️_改廃確認.py) 已能列出待改廃的 SKU。
目标：人工确认后自动在 NetSuite / Shopee 多平台同步下架 + 飞书通知。

### 集成方案

```
page 13 改廃确认
  ↓ trigger_workflow(module="discontinue_confirm")
N8N workflow (discontinue.json)
  ├─ 调 Shopee Open API 把对应商品状态改 inactive
  ├─ 写飞书群通知（带商品列表 + 责任人 @）
  └─ 调 NetSuite REST API 把 item handling_status 改"販売終了"
       (NetSuite 部分仍可能要影刀兜底)
  ↓
回写 automation_runs.summary {shopee_off: N, ns_off: M, errors: [...]}
```

### 待办

- 实现 `deploy/n8n/workflows/discontinue.json`
- page 13 加「批量提交改廃」按钮 → trigger_workflow

---

## 6. 通用闭环模式（其他模块复用）

任何"CMS UI 触发 → 第三方系统执行 → 回报 Boss"场景都按这套模式：

```python
# 1. CMS page X 添加触发按钮
from shared.n8n_client import trigger_workflow
run_id = trigger_workflow(
    module="<module_id>",
    webhook_path="<n8n-webhook-path>",
    payload={...},
    conn=conn,
    triggered_by=user,
)

# 2. N8N workflow 写在 deploy/n8n/workflows/<module>.json
#    - Webhook Trigger 节点路径与 CMS 调用 webhook_path 一致
#    - 处理完成调 CMS_CALLBACK_URL POST 回调
#    - 调 LARK_WEBHOOK_URL POST 飞书通知

# 3. CMS page 显示进度（轮询 automation_runs）
from shared.n8n_client import get_run_status
row = get_run_status(conn, run_id)
```

飞书通知统一走 `shared/lark_notify.notify_card()`：

```python
from shared.lark_notify import notify_card
notify_card(
    title="改廃确认完成",
    rows=[("处理 SKU", "23"), ("Shopee 下架", "20"), ("NetSuite 下架", "23")],
    status="success",
)
```

---

## 7. 部署依赖

| 组件 | 部署位置 | 状态 |
|---|---|---|
| CMS（Streamlit + Postgres） | Inspiron 5405 / `C:\Users\smiki\CMS-v230\` | ✅ 已上线 |
| N8N（n8n + cloudflared） | 同机 / `C:\Smikie-N8N\`（独立 docker-compose） | 🟡 安装包就绪，待 Boss 部署 |
| 影刀 RPA | 同机 | ✅ 已稳定运行 |
| 飞书群机器人 | 飞书工作台配置 | 🔴 待 Boss 创建群 + 加机器人 |
| Shopee Open Platform 凭证 | `.env` 中 SHOPEE_PARTNER_ID/KEY | 🔴 待 Boss 申请 |

---

## 8. 风险 + 演进

| 风险 | 缓解 |
|---|---|
| N8N webhook payload 限制 16 MB | 大批量改用共享文件目录传输 |
| Shopee API 限流 | N8N 内 Wait 节点 + 错误队列重试 |
| 影刀脚本不稳定 | 共享文件方案配合超时检测 |
| CMS 接收回调缺失 | 短期 N8N 直连 Postgres；长期加 FastAPI sidecar |
| 飞书群机器人 token 泄露 | webhook URL 仅放 .env，不入 git |
