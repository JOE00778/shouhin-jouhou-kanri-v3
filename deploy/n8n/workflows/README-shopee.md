# Shopee 自动上架 · N8N Workflow 部署 + 测试指南

> 关联任务：**T-309**（Streamlit Page 21 集成 done 2026-05-06）+ **T-313**（端到端验证 2026-05-16）
> 配套代码：`shared/n8n_client.py` · `pages/21_🚀_Shopee上架.py` · `shared/lark_notify.py`

---

## 📦 本目录的 4 个 Shopee 相关 workflow

| 文件 | 触发方式 | 当前状态 |
|---|---|---|
| `shopee-mass-upload.json` | Webhook (path: `shopee-mass-upload`) | ✅ MVP scaffold（mock 数据，可测链路）|
| `shopee-mass-upload-cron.json` | Cron 每 4 小时拉 CMS 队列 | ⏸ active: false（手动开启） |
| `image-gen-cron.json` | Cron 商品图自动生图 | ⏸ active: false |
| `jan-extract-v2.json` | 手动触发 | ⏸ 升级到 claude-opus-4-7（之前预留） |

---

## 🚀 第一次端到端测试（Boss 操作）

整套链路：

```
Page 21 (CMS Streamlit, smikie-cms.cc)
    ↓ trigger_workflow() → POST webhook + N8N_BASIC_AUTH
N8N (smikie-n8n stack, n8n.smikie-cms.cc)
    └─ shopee-mass-upload workflow
        ├─ 接收 payload (run_id, market, xlsx_urls...)
        ├─ CMS 回调 processing (httpRequest → cms_callback_url)  ← 当前 404（缺口①）
        ├─ 处理 SPU（占位 mock 数据）                            ← 当前 mock（缺口②）
        ├─ CMS 回调 completed                                    ← 当前 404
        └─ 飞书通知 (httpRequest → LARK_WEBHOOK_URL)             ← ✅ 这条要通
飞书群 ⬅ 收到结构化卡片
```

### 步骤 1：拿飞书群机器人 Webhook

1. 飞书群（建一个测试群比如「Smikie 自动化通知」）
2. 群设置 → 群机器人 → 添加 → 自定义机器人
3. 名称：`Smikie N8N`，图标随意
4. **关键安全设置**：勾选「**签名校验**」或「**自定义关键词**：Shopee, 自动上架」（防止 webhook URL 泄漏被滥发）
5. 复制 Webhook URL（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx-yyyy-zzzz`）

### 步骤 2：在 Inspiron 补 LARK_WEBHOOK_URL

在 PowerShell（确保在 `D:\Smikie-N8N\Smikie-N8N-Installer-v1.5\`）：

```powershell
notepad .env
```

找到这一行：

```
LARK_WEBHOOK_URL=
```

把刚拿到的 URL 粘到 `=` 后面（不要加引号）：

```
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx-yyyy-zzzz
```

保存（Ctrl+S）+ 关闭。

```powershell
docker compose up -d
```

让 N8N 容器读取新 env（必须 `up -d` 不能 `restart`）。

### 步骤 3：导入 workflow 到 N8N

N8N 容器启动时**只自动导入一次**（首次 install）。之后改 workflow JSON 不会自动重载，需要手工导入。

1. 浏览器开 https://n8n.smikie-cms.cc → 登录 owner 账户
2. 左侧栏「**Workflows**」
3. 右上角「**+ Add workflow**」旁的下拉菜单 → 「**Import from File**」
4. 选 Inspiron 上 `D:\Smikie-N8N\Smikie-N8N-Installer-v1.5\workflows\shopee-mass-upload.json`
5. 导入成功后，**右上角 toggle「Active」打开**（变绿）

可以同时导入其他 3 个 workflow（cron 类的暂时不要打开 Active）。

### 步骤 4：从 CMS Page 21 触发

1. 浏览器开 https://smikie-cms.cc（或 Streamlit Cloud 的 CMS 部署）
2. 登录 admin 账户
3. 进 Page 21「🚀 Shopee 上架」
4. Tab 1「🤖 全自动管线」
5. 上传**任意测试 SPU CSV**（一行也行）
6. 选市场（默认 TW 或换 PH）
7. 点「**启动 N8N 全自动**」按钮
8. UI 应该显示 `run_id: xxx-xxx-xxx`

### 步骤 5：验证结果（3 个地方看）

| 验证点 | 期望看到 |
|---|---|
| **飞书群** | 1-3 秒内收到「Shopee 自动上架完成」绿色卡片，含 run_id / market / 0/0/0/0 计数（mock）|
| **N8N UI Executions** | https://n8n.smikie-cms.cc → 左侧 Executions → 1 条成功记录，每个节点都 ✅ |
| **automation_runs 表** | CMS Page 21 Tab 4「📜 历史运行」应该看到这条记录，但 status 永远停在 `processing`（因为 CMS callback 缺口①）|

任意一项不对，按下面【常见出错速查】排查。

---

## ⚠️ 已知架构缺口（当前 MVP 阶段可容忍）

### 缺口 ① CMS callback endpoint 未实装

**症状**：N8N workflow 里的「CMS 回调 processing/completed」节点 POST 到 `https://smikie-cms.cc/api/automation/callback` 会 404。

**原因**：Streamlit 是单页 app，没法挂自定义 HTTP API。当时设计这个 URL 是预留位，实装要起一个 FastAPI sidecar。

**当前影响**：
- automation_runs 表里 status 永远是 `processing`（trigger 时写的状态），不会被更新成 `completed`
- Page 21 Tab 4「历史运行」看到的全是 `processing`
- 但 workflow 因为 `neverError: true`，回调失败不影响其他节点继续

**后续补法**（独立 task）：
- A. 起 FastAPI sidecar 容器，挂 `/api/automation/callback`，与 CMS Streamlit 共享 warehouse.db
- B. 让 N8N 通过 Postgres node 直接写 cms_postgres 的 automation_runs 表（需 cross-stack network，见缺口③）

### 缺口 ② shopee-mass-upload.json 是 MVP scaffold

**症状**：n04「处理 SPU（占位）」是 mock JavaScript 代码，返回 `{ total_spu: 0, created: 0, failed: 0, skipped: 0 }`。

**原因**：当时 T-309 完成时 Shopee Open API Partner ID/Key 还没拿，先做了 scaffold 等凭证齐了再补。

**后续补法**（独立 task，等 Boss 拿到凭证 + 决定 dry-run 策略）：
1. 在 .env 填 `SHOPEE_PARTNER_ID` 和 `SHOPEE_PARTNER_KEY`
2. 改 n04 代码节点：从 `$json.body.xlsx_urls` 拉 XLSX → 解析 → 按 SPU 分组
3. 加 HTTP Request 节点调 Shopee Item API 创建商品
4. 加 HTTP Request 节点调 Shopee Image API 上传商品图
5. 汇总成功/失败/跳过计数返回给 n05

### 缺口 ③ N8N stack 与 CMS stack 不共享 docker network

**症状**：N8N 容器无法通过 `http://cms_streamlit:8501` 或 `cms_postgres:5432` 直接访问 CMS 内部服务。

**原因**：两个 stack 的 docker-compose 各自独立 network（cms_net / smikie_n8n_net）。

**当前影响**：所有 N8N → CMS 的通讯都得走公网域名（`smikie-cms.cc`）+ Cloudflare Tunnel。延迟+流量浪费，但功能不阻塞。

**后续补法**（独立 task）：
- 改两边 docker-compose 加 external network `smikie_shared`
- 必要的服务（n8n, cms_streamlit, cms_postgres）加入该 network
- 跨 stack 调用用容器名，CF Tunnel 只服务外部访问

---

## 🆘 常见出错速查

| 现象 | 原因 | 解决 |
|---|---|---|
| Page 21 触发显示 `N8N webhook 调用失败: 404` | workflow 没 Active 或没导入 | 步骤 3 检查 N8N UI workflow 是绿的 |
| Page 21 触发显示 `401 Unauthorized` | N8N_BASIC_AUTH_PASSWORD 在 Streamlit secrets 里没设 | 在 CMS 部署的 secrets / 环境变量加 `N8N_BASIC_AUTH_USER=admin` 和 `N8N_BASIC_AUTH_PASSWORD=<密码>` |
| N8N UI executions 显示飞书节点失败 | LARK_WEBHOOK_URL 没设或填错 | 步骤 2 重新填 + `docker compose up -d` |
| 飞书群没收到通知但 N8N execution 显示绿 | 飞书签名校验/关键词配置阻拦 | 群机器人设置里临时关签名校验测试，或确保卡片内容包含「Shopee」「自动上架」之一 |
| Page 21 Tab 4 看不到历史运行 | automation_runs 表未建 | 跑 `data_warehouse/db/migrations.py` 重新建表 |
| `https://n8n.smikie-cms.cc` 502 | cloudflared 容器挂了 | `docker compose logs cloudflared --tail 30` |

---

## 🎯 下一步路线图（按优先级）

| 优先级 | 任务 | 谁做 |
|---|---|---|
| **P1** | Boss 实测本指南端到端（步骤 1-5） | Boss |
| P2 | 补 CMS callback FastAPI sidecar（缺口①） | Claude，独立 task |
| P2 | 配 cross-stack docker network（缺口③） | Claude，独立 task |
| P3 | 等 Shopee Partner Key 后补 workflow 实际逻辑（缺口②） | Claude + Boss |
| P3 | 文案生成升级：加 DeepSeek API 节点做 7 国翻译 | Claude，独立 task |
| P3 | 图像生成：把 `smikie-batch-product-images.json` 串进上架流程 | Claude，独立 task |
