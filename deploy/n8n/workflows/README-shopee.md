# Shopee 自动上架 · N8N Workflow 部署 + 测试指南

> 关联任务：**T-309**（Streamlit Page 21 集成 done 2026-05-06）+ **T-313**（端到端验证 2026-05-16）
> 配套代码：`shared/n8n_client.py` · `pages/21_🚀_Shopee上架.py` · `shared/lark_notify.py`
> 当前 workflow 版本：v1.7（含飞书签名校验支持）

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

### 步骤 1：拿飞书群机器人 Webhook + 签名 Secret

1. 飞书群（建一个测试群比如「Smikie 自动化通知」）
2. 群设置 → 群机器人 → 添加 → 自定义机器人
3. 名称：`Smikie N8N`，图标随意
4. **安全设置二选一**（强烈建议至少一个）：
   - **A. 签名校验**（推荐，更安全）：勾选并**复制生成的 Signing Secret**（形如随机字符串）
   - **B. 自定义关键词**：填 `Shopee` 或 `Smikie`（workflow 卡片已含「Shopee」会自动通过）
5. 复制 Webhook URL（Lark Suite 国际版形如 `https://open.larksuite.com/open-apis/bot/v2/hook/...`；国内版 `open.feishu.cn`）

> **如果选 A（签名校验）**：需要把 Signing Secret 填到 `.env` 的 `LARK_BOT_SIGN_SECRET=`，
> N8N workflow 内的「飞书签名计算」Code node 会自动用 HMAC-SHA256 算签名。
> Signing Secret 在飞书机器人**详细设置**页底部的「签名校验」展开后可以看到（启用后才显示）。

### 步骤 2：在 Inspiron 补 LARK_WEBHOOK_URL

在 PowerShell（确保在 `D:\Smikie-N8N\Smikie-N8N-Installer-v1.5\`）：

```powershell
notepad .env
```

找到这两行：

```
LARK_WEBHOOK_URL=
LARK_BOT_SIGN_SECRET=
```

把步骤 1 拿到的两个值分别粘到 `=` 后面（不要加引号、不要带空格）：

```
LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/xxxx-yyyy-zzzz
LARK_BOT_SIGN_SECRET=AbCdEfGhIjKlMnOp...   ← 选签名校验才填，没选留空
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

1. 浏览器开 https://smikie-cms.cc（元川さん · Cloudflare Tunnel）
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

## 🆕 shopee-mass-upload v2.0 完整 B1-B5 编排（2026-05-16）

v1.7 是 MVP scaffold（mock 0/0/0/0）；v2.0 把真业务节点全铺上，18 个节点串成完整 SPU 编排。

### 节点拓扑（v2.0）

```
n01 Webhook · CMS 触发
   ↓
n02 变量提取 (run_id, market_list, sku_master_url, spu_groups_json, ...)
   ↓ ⤳ n07 立即返回 CMS（并行 ACK）
n03 CMS 回调 · processing
   ↓
[B1] n10 查 SKU 主档 (GET CMS_BASE_URL + /api/sku/master?jans=...)
   ↓
[B2] n11 SPU 聚合 + 类目映射 (Code: 按 spu_key 分组 + cat-* → category_id)
   ↓
n12 SPU 循环 · SplitInBatches (size=1)
   ├─[done]→ n17 B5b 导出 XLSX → n18 上传 CMS → n19 B5c Shopee stub
   │                                                  ↓ n20 汇总 → n21 CMS done
   │                                                  → n22 Lark sign → n23 Lark 通知
   └─[loop]→
       [B3] n13 火山方舟 DeepSeek-V3.2 (一次生成 SPU 标题/详情/SEO + 7 国翻译)
           ↓
           n14 解析 LLM JSON 输出
           ↓
       [B4] n15 主图流水线 (Code 内串行调 image-processor):
              foreach SKU → POST /upscale → POST /cutout
              整 SPU → POST /compose-spu
           ↓
       [B5a] n16 拼 XLSX 行 (每 SPU 1 行；7 国 title/desc/category_id 并列)
           ↓ (回 n12 继续下一 SPU)
```

### Webhook payload schema（Page 21 端要按这个传）

```json
POST https://n8n.smikie-cms.cc/webhook/shopee-mass-upload
{
  "run_id": "uuid-xxx",
  "markets": ["TW","PH","MY","SG","TH","VN","ID"],
  "sku_jans": ["4901872888881","4901872888898", ...],
  "spu_groups": [
    {"spu_key": "smikie-bed-2024-blue", "sku_jans": ["4901872888881","4901872888898"]},
    {"spu_key": "smikie-bath-2024-red", "sku_jans": ["4901872888902"]}
  ]
}
```

不给 `spu_groups` → 自动退化到 1 SKU = 1 SPU。

### v2.0 已知缺口（**部署前必看**） — v2.2 已堵 5 个，只剩 Shopee 凭证

| # | 缺口 | 状态 |
|---|---|---|
| ~~1~~ | ~~CMS 没装 `/api/sku/master` 端点~~ | ✅ **v2.1 已补**：cms-api sidecar |
| ~~2~~ | ~~CMS 没装 `/api/automation/xlsx-upload` 端点~~ | ✅ **v2.1 已补**：cms-api sidecar |
| ~~3~~ | ~~类目映射表手抄~~ | ✅ **v2.2 砍掉**：n14b 节点改调 Shopee `category_recommend` API 自动判定，**Boss 不用抄任何 ID**（凭证齐了 0 工作量） |
| ~~4~~ | ~~category_id 占位数字~~ | ✅ **v2.2 同上**：API 直接返回真实叶子类目 |
| ~~5~~ | ~~SKU 主档字段名假设~~ | ✅ **v2.1 已对齐** |
| 6 | B5c Shopee 上架是 stub | ⏳ 等 Partner Key + shop_id + access_token |

**v2.2 架构升级**：
- n11 节点 (B2 · SPU 聚合) 删除硬编码 CAT_MAP，只做 SPU 分组
- 新增 n14b 节点 (B3.5 · Shopee category_recommend)，每 SPU 对 7 国并行调 API，自动返回该国叶子类目 ID
- HMAC-SHA256 签名内嵌（不依赖外部库）
- Stub fallback：`SHOPEE_PARTNER_KEY` 未设时整节点 skip，workflow 仍跑通（XLSX category_id 列空）
- 飞书卡片字段从「类目命中 N/M」改为「✅ 全市场 OK · ⚠️ 部分 · ❌ 失败」更准确

### Shopee 凭证准备（v2.2 唯一阻塞项）

要让 B3.5 `category_recommend` 节点真调通（不走 stub），需要 **4 件 Shopee 凭证**：

| # | 凭证 | 怎么拿 |
|---|---|---|
| 1 | `SHOPEE_PARTNER_ID` | Shopee Open Platform → 我的应用 → 应用详情 |
| 2 | `SHOPEE_PARTNER_KEY` | 同上（页面有「显示密钥」按钮，64 位 hex 串）|
| 3 | `SHOPEE_SHOP_IDS` (JSON dict) | 7 国分别走 OAuth 授权流程（Test: `openplatform.sandbox.test-stable.shopee.sg` / Live: `openplatform.shopee.sg`）`/api/v2/shop/auth_partner?partner_id=...&redirect=...` 走完拿 `shop_id`，每国一份。**注意 v2 入口已不在老域名 `partner.shopeemobile.com`，那是 v1 域名 v2 partner_id 不识别。** |
| 4 | `SHOPEE_REFRESH_TOKENS` (JSON dict) | OAuth 完成后回调里的 `code` 换 `refresh_token`（30 天有效）|

**v2.3+ 已不需要 ACCESS_TOKEN**：n02b 节点每次 workflow 触发时自动用 `refresh_token` 换新的 `access_token`，无需 cron、无需手动维护。

**未配齐前**：n02b + B3.5 节点自动 stub，飞书卡片显示「**类目自动判定：⏸ STUB（等 Shopee 凭证）**」，其他节点照常跑。

凭证齐了之后，`.env` 改 4 行即可，**workflow JSON 不用再动**：

```
SHOPEE_PARTNER_ID=1234567
SHOPEE_PARTNER_KEY=abcdef0123456789...
SHOPEE_SHOP_IDS={"TW":"100001","PH":"200001","MY":"300001","SG":"400001","TH":"500001","VN":"600001","ID":"700001"}
SHOPEE_REFRESH_TOKENS={"TW":"eyJ...","PH":"eyJ...","..."}
```

每 30 天 Boss 手工重走 1 次 OAuth → 拿新 `refresh_token` 更新 `.env` SHOPEE_REFRESH_TOKENS 这一行 → recreate n8n。**每月人工 5 分钟**。

n02b 自动 refresh 拿到的最新 refresh_token 持久化在 `D:\Smikie-Images\automation_outputs\shopee_tokens.json`（每次 refresh 后更新）。`.env` 里的 SHOPEE_REFRESH_TOKENS 只是「首次启动」和「持久化文件丢失」时的兜底初始值。

### OAuth 一键工具（v2.4 新增 · 让首次拿 refresh_token 不再手工构造 URL）

cms-api 提供 2 个 helper endpoint，**Boss 拿到 Partner ID/Key 后这样走**：

```powershell
# 1. 拿台湾的 OAuth 授权 URL（其他国家同理：PH / MY / SG / TH / VN / ID）
Invoke-RestMethod "http://localhost:8789/api/automation/shopee/oauth-url/TW"

# 输出含 authorize_url，复制到浏览器打开
```

```
浏览器流程：
  authorize_url → 登录 Shopee TW 卖家账号 → 同意授权
   ↓ Shopee 自动跳转到 redirect_url（带 code + shop_id 参数）
   ↓ /api/automation/shopee/oauth-callback 自动执行：
       - 用 code 调 Shopee /auth/token/get 换 refresh_token
       - 把 refresh_token 写进 shopee_tokens.json
       - 返回 shop_id 提示 Boss 加进 .env SHOPEE_SHOP_IDS
```

完成后**重复 7 次**（每国一次），所有 7 国 refresh_token 自动持久化。Boss 只需要手动整理一份 `SHOPEE_SHOP_IDS` JSON dict 写进 `.env`（callback 返回的 tip_for_env 字段帮你拼）。

⚠️ 前置条件：`.env` 必须先填好 `SHOPEE_PARTNER_ID` 和 `SHOPEE_PARTNER_KEY`（cms-api 容器要读这两个生成签名）。

### v2.2 部署前 Boss 要补的 env

`.env` 加这 4 行（v1.8 已有 `WHITEBG_HOST_PATH`）：

```
VOLC_ARK_TEXT_MODEL=deepseek-v3-2-251201
CMS_BASE_URL=https://smikie-cms.cc
POSTGRES_PASSWORD=<跟 deploy/windows/.env 里 POSTGRES_PASSWORD 完全一致>
CMS_OUTPUTS_HOST_PATH=D:/Smikie-Images/automation_outputs
```

**关键**：
- `POSTGRES_PASSWORD` 必须跟 CMS V2.3 部署用的密码**完全一致**（在 `C:\Users\smiki\CMS-v230\deploy\windows\.env` 里找）
- 不一致 → cms-api 启动后 `/health` 报 `authentication failed`

### v2.2 部署步骤（架构升级：跨 stack network + cms-api 改 Postgres）

```powershell
# === Step 0: 一次性 — 建 docker external network ===
docker network create smikie_shared

# === Step 1: 改 CMS V2.3 部署（让 cms_postgres 加入 smikie_shared）===
cd C:\Users\smiki\CMS-v230
# 拉新的 deploy/windows/docker-compose.yml（postgres 服务加 smikie_shared network）
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/JOE00778/CMS-v230/main/deploy/windows/docker-compose.yml" -OutFile deploy\windows\docker-compose.yml -UseBasicParsing
cd deploy\windows
docker compose up -d --force-recreate postgres

# === Step 2: 改 N8N stack（cms-api 加入 smikie_shared + 用 Postgres）===
cd D:\Smikie-N8N\Smikie-N8N-Installer-v1.5
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/JOE00778/CMS-v230/main/deploy/n8n/docker-compose.yml" -OutFile docker-compose.yml -UseBasicParsing
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/JOE00778/CMS-v230/main/deploy/n8n/workflows/shopee-mass-upload.json" -OutFile workflows\shopee-mass-upload.json -UseBasicParsing

# 拉 cms_api / image_processor 源码
New-Item -ItemType Directory -Force -Path cms_api, image_processor\assets
foreach ($f in 'Dockerfile','app.py','requirements.txt') {
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/JOE00778/CMS-v230/main/deploy/n8n/cms_api/$f" -OutFile cms_api\$f -UseBasicParsing
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/JOE00778/CMS-v230/main/deploy/n8n/image_processor/$f" -OutFile image_processor\$f -UseBasicParsing
}
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/JOE00778/CMS-v230/main/deploy/n8n/image_processor/assets/template_red.png" -OutFile image_processor\assets\template_red.png -UseBasicParsing

# === Step 3: 改 .env（关键! POSTGRES_PASSWORD 跟 CMS V2.3 同密码）===
# 先在 CMS V2.3 .env 找 POSTGRES_PASSWORD 抄过来
notepad C:\Users\smiki\CMS-v230\deploy\windows\.env
# 把那行 POSTGRES_PASSWORD=xxxx 复制
notepad .env
# 在 N8N .env 加这 4 行：
#   VOLC_ARK_TEXT_MODEL=deepseek-v3-2-251201
#   CMS_BASE_URL=https://smikie-cms.cc
#   POSTGRES_PASSWORD=<刚抄的那串>
#   CMS_OUTPUTS_HOST_PATH=D:/Smikie-Images/automation_outputs
New-Item -ItemType Directory -Force -Path D:\Smikie-Images\automation_outputs

# === Step 4: 起 sidecar + 重启 n8n ===
docker compose up -d --build cms-api image-processor
docker compose up -d --force-recreate n8n

# === Step 5: 健康检查 ===
Invoke-RestMethod "http://localhost:8788/health"   # image-processor: template_ready=true
Invoke-RestMethod "http://localhost:8789/health"   # cms-api: backend=postgres, item_v2_count=数千
# item_v2_count 若是数字 → ✅ cms-api 成功连上 cms_postgres
# item_v2_count 是 null + error 含 authentication failed → POSTGRES_PASSWORD 不一致，回 Step 3

# === Step 6: N8N UI 重导 workflow（覆盖旧 v1.7）===
# 浏览器 https://n8n.smikie-cms.cc → Workflows → 找到旧 Shopee 自动上架 → 删除
# Workflows → Import from File → 选 D:\Smikie-N8N\...\workflows\shopee-mass-upload.json
# 右上角 Active toggle 打开
```

### v2.0 端到端测试

走 Page 21（v1.7 已有的 Tab 1），上传一份测试 CSV（必须含 SPU 分组列），点「全自动管线」：
- 预期飞书绿卡片显示 `SPU 总数 / LLM 成功 / 图像成功 / 类目命中率 / Shopee 上架: STUB`
- N8N UI Executions 应能看到 18 节点全部 ✅ 或个别 ⚠️（已知缺口节点）
- D 盘 `D:\Smikie-Images\branded\` 出现套模板成品图
- D 盘 `D:\Smikie-Images\spu\` 出现 SPU 拼图

---

## 🖼 商品图处理 sidecar (image-processor v0.1)

shopee-mass-upload v1.8+ 把「白底图准备 + 抠图套模板 + SPU 多图合成」抽到独立容器 `smikie_image_processor`，N8N 用 HTTP Request 节点调用。脚本逻辑来自 `shopify/scripts/compose_with_template.py` + `upscale_images_to_1500.py`（Mac Upscayl 改 Pillow Lanczos 做 CPU baseline，以后插 GPU 再换 Real-ESRGAN）。

**为什么不塞进 N8N Code node**：N8N 沙盒不让 require rembg / PIL；模型权重几百 MB 撑爆 1 GB 内存限制；图像处理跟 N8N 抢 CPU 不合适。

### 端点（容器内 8788，N8N 通过 `http://image-processor:8788/` 调）

| Endpoint | 用途 | 输入 | 落盘 |
|---|---|---|---|
| `GET /health` | 健康检查 | — | — |
| `POST /upscale` | 原图 → 1500×1500 白底方图 | `{jan, image_url\|image_b64, method=lanczos}` | `/data/whitebg/upscaled/<JAN>.jpg` |
| `POST /cutout` | 白底图 → 抠图 + 套 RED 模板 | `{jan, image_url\|image_b64}`（优先用 upscaled/<JAN>.jpg 已有） | `/data/whitebg/branded/<JAN>.jpg` |
| `POST /compose-spu` | N 个 SKU 已处理图 → SPU 拼图 | `{spu_key, sku_jans[], source=branded}` | `/data/whitebg/spu/<SPU_KEY>.jpg` |
| `GET /list/{kind}/{key}` | 查 kind∈{raw,upscaled,branded,spu} 下某 key 是否已生成 | — | — |

### Windows D 盘文件夹设置（**一次性 · 必做**）

在 Inspiron PowerShell：

```powershell
New-Item -ItemType Directory -Force -Path D:\Smikie-Images\raw, D:\Smikie-Images\upscaled, D:\Smikie-Images\branded, D:\Smikie-Images\spu
```

`.env` 内已默认 `WHITEBG_HOST_PATH=D:/Smikie-Images`；要换位置改 `.env` 这行即可（用正斜杠，不要反斜杠）。

### 手动测试（不依赖 N8N，直接 curl）

进 Inspiron PowerShell：

```powershell
docker compose up -d --build image-processor
```

```powershell
docker exec smikie_image_processor curl -s http://localhost:8788/health
```

期望返回 `{"status":"ok","template_ready":true,...}`。

从主机测：

```powershell
Invoke-RestMethod -Uri "http://localhost:8788/upscale" -Method Post -ContentType "application/json" -Body (@{jan="4901872888881";image_url="https://example.com/sample.jpg";method="lanczos"} | ConvertTo-Json)
```

成功后 `D:\Smikie-Images\upscaled\4901872888881.jpg` 会出现 1500×1500 白底图。

### 已知限制（v0.1）

| # | 限制 | 后续 |
|---|---|---|
| 1 | `/upscale` 只实装 Lanczos（无 AI 超分），效果不如 Mac Upscayl | 等接 GPU 后加 Real-ESRGAN ncnn-vulkan backend |
| 2 | `/cutout` 用 rembg u2net（首次启动会预热下载到镜像内 ~170MB） | OK |
| 3 | `/compose-spu` 用纯 PIL 网格拼，不调 AI | OK，Boss 已确认此方案 |
| 4 | 图片上传走 URL 拉取，CMS 必须公网可达或共享卷 | shopee-mass-upload v1.8 用 CMS API URL |

### 当 shopee-mass-upload v1.8 出来后的调用顺序

```
B1 拉 SKU 主档 ─→ B2 翻译 ─→ B3 类目映射 ─→
   ↓
B4 主图：
   ├ b4a foreach SKU: POST /upscale (image_url=CMS 商品登录 APP 主图)
   ├ b4b foreach SKU: POST /cutout
   └ b4c POST /compose-spu (sku_jans=本 SPU 的 SKU 列表)
   ↓
B5a XLSX 输出 → 主图列填 /data/whitebg/spu/<spu>.jpg
```

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
