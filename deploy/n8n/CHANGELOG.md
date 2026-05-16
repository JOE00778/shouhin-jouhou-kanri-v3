# Smikie N8N Installer · CHANGELOG

> 一键 Docker installer 的演进史。每版改了啥 + 关键 commit。
> 给未来 Claudex / Boss 复盘用，也帮 Live 切换时知道每个 v 引入的 breaking change。

## v2.11 · 2026-05-17 · OAuth 链路全通 + CB merchant 支持

**核心**：Shopee Test sandbox OAuth 链路 PH 已通 (refresh_token 落库)，cms-api callback 加 main_account_id 二选一支持。

- ✅ cms-api callback 加 `main_account_id` 参数（CB Merchant 授权 = 跨境主体下挂多 shop）
- ✅ callback 返回 HTMLResponse 友好结果页，不再裸 JSON / 白屏
- ✅ workflow JSON `n02b` / `B3.5` 两节点 `apiBase` fallback 改 `openplatform.shopee.sg`
- ✅ 新增 `installer/oauth-7-markets.ps1` — 7 国 OAuth 单条命令（含 -Fallback 手动模式）
- ✅ 新增 `installer/healthcheck.ps1` — 一键检 9 容器 + 3 网络 + 6 端点 + Shopee 7 国 token + Cloudflare 公网入口
- ✅ 新增 `installer/trigger-shopee-workflow.ps1` — 绕开 Streamlit Page 21 直接打 N8N webhook
- ✅ 新增 4 份 SOP：cloudflare-path-routing / shopee-test-accounts / n8n-b1-b5-e2e-test / MORNING-NEXT 索引
- ✅ 新增 `cms_api/tests/test_shopee_signing.py` — 10 unittest 拦截 stale ref / v1 老域名 / XSS 回归
- ✅ `build-installer.sh` 加 pre-build 检查（语法 + pyflakes + 单元测试 + JSON 合法性，任一失败拒绝打包）
- 🔴 Bugfix: `shopee_oauth_url()` 残留 `_PK_MODE` NameError（v2.10 漏删 → v2.11 装包前修，由 dry-run 测试发现）
- 🔴 Bugfix: `_oauth_result_html` 加 `html.escape()` 防 XSS（partner_key 错误信息含 `< > &` 不破 HTML）

**Boss 看到的变化**：
- OAuth 跳转后不再白屏，看到友好 HTML 结果页（前提是 [[SOP-cloudflare-path-routing]] 已配）
- 一键命令换 token：`.\oauth-7-markets.ps1 -Market TW`
- 一键检健康：`.\healthcheck.ps1`

key commits: `c2b8790`, `6002929`

---

## v2.10 · 2026-05-17 凌晨 · 5 根因修复

**核心**：今晚 Boss 在 Inspiron 跑 OAuth 连续踩 5 个坑，全定位 + 修：

1. partner_key `shpk` 是 key 本体的一部分（不是 UI prefix）→ 去掉 v2.7 的 `removeprefix('shpk')`
2. `SHOPEE_API_BASE` 必须用 `openplatform.shopee.sg` (v2 入口)，**不是** `partner.shopeemobile.com` (v1 老域名)
3. `docker compose restart` 不重读 .env → 必须 `up -d --force-recreate <service>`
4. CB merchant 授权 Shopee 回调传 `main_account_id` 不是 `shop_id`（v2.11 加支持）
5. Cloudflare Tunnel 默认把 `smikie-cms.cc/api/automation/*` 路由到 Streamlit 容器 → 必须 path-based routing

代码改动：
- `cms_api/app.py` 简化 `_shopee_sign`（去 `_PK_MODE` 分支）；`debug-sign` 端点改配置查询型
- `docker-compose.yml` 两处 `SHOPEE_API_BASE` 默认值改 `openplatform.shopee.sg`；删 `SHOPEE_PARTNER_KEY_MODE`
- `.env.template` 加 Test/Live 3 host 列表 + ⚠️ 老域名警告

key commit: `c2b8790`

---

## v2.9 · 2026-05-16 · partner_key 签名调试 ❌（错误方向）

- 加 `SHOPEE_PARTNER_KEY_MODE` env：`utf8` (Live) vs `hex` (Test 假设 partner_key 是 hex string)
- docker-compose.yml 透传 `SHOPEE_PARTNER_KEY_MODE`

⚠️ 这版假设 Test partner_key 需要 hex decode 是**错误方向**，v2.10 起删掉。Test partner_key 跟 Live 一样用 utf-8 encode 当 HMAC key（pyshopee2 标准）。

key commits: `b08119e`, `f3ebef6`

---

## v2.8 · 2026-05-16 · debug-sign 端点 + SHOPEE_PARTNER_KEY_MODE 切换 ❌

- 新增 `/api/automation/shopee/debug-sign` 4 种签名方案对比（raw_utf8 / no_prefix_utf8 / no_prefix_hex / raw_hex）
- 帮今晚定位 partner_key 处理方式（最终结论：`raw_utf8` = 完整 64 char utf-8）

⚠️ v2.11 起 debug-sign 简化为 host/partner_id/key 配置查询型（不再列 4 种 sign，因为 stale ref 风险）

key commit: `b08119e`

---

## v2.7 · 2026-05-16 · partner_key strip 'shpk' ❌（错误假设）

- 启动时自动 `_RAW_PK.removeprefix("shpk")` 假设 'shpk' 是 UI prefix

⚠️ **错误假设**。`shpk` 是 partner_key 本体的一部分（Shopee 控制台显示什么就是什么）。v2.10 起取消 strip。

key commit: `97e3682`

---

## v2.6 · 2026-05-16 · timestamp tz-naive bug fix

- `_shopee_sign` timestamp 用 `time.time()` 替代 `datetime.utcnow().timestamp()`
- 后者在容器 TZ=Asia/Tokyo 时换算错误，可能多/少几小时

key commit: `cbce8e7`

---

## v2.5 · 2026-05-16 · ports 127.0.0.1 映射

- cms-api / image-processor 容器 ports 加 `127.0.0.1:` 前缀（仅本机访问，不暴露公网）
- 删 docker-compose `version:` obsolete 字段

key commit: `98e365e`

---

## v2.4 · 2026-05-16 · cms-api OAuth helper

- 新增 `/api/automation/shopee/oauth-url/{market}` 端点
- 新增 `/api/automation/shopee/oauth-callback` 端点（v2.11 加 main_account_id 支持）
- 替代手工构造 Shopee OAuth URL 和手工换 token

key commit: `5d1173c`

---

## v2.3 · 2026-05-16 · n02b refresh-on-trigger

- N8N workflow 加 `n02b` 节点：每次 workflow 触发时自动用 refresh_token 换 access_token
- 持久化新 refresh_token 回 cms-api（refresh_token 每次也轮换）
- 替代 cron + 静态 SHOPEE_ACCESS_TOKENS 设计

key commit: `99a7a93`

---

## v2.2 · 2026-05-16 · n11 砍 CAT_MAP + B3.5 调 Shopee category_recommend

- N8N workflow n11 节点去掉 hardcoded CAT_MAP
- 新增 B3.5 节点：动态调 Shopee `category_recommend` API 7 国分别判类目
- ⏳ T-315 backlog：补 `cat-shopee-map.csv` 静态表，恢复 O(1) 命中（不依赖网络 API）

key commit: `41f6edc`

---

## v2.1 · 2026-05-16 · cms-api 改 Postgres + smikie_shared 跨 stack network

- cms-api 初版 SQLite 是设计错误：N8N stack 和 CMS V2.3 stack 共享同一 Postgres 实例更合理
- 改用 `postgresql://cms:${POSTGRES_PASSWORD}@cms_postgres:5432/cms`
- 新建 docker external network `smikie_shared` 打通两个 stack（CMS V2.3 的 `cms_postgres` + N8N stack 的 `cms-api`）

key commit: `8c62d1a`

---

## v2.0 · 2026-05-16 · 18 节点完整 B1-B5 编排

- N8N workflow `shopee-mass-upload.json` 完整 18 节点（B1 SKU 查询 → B2 SPU 聚合 → B3 LLM 文案 → B3.5 类目 → B4 主图 → B5a-c XLSX）
- cms-api sidecar 容器（initial SQLite, v2.1 改 Postgres）
- 飞书绿卡片通知

key commits: `09bbdfd`, `b5a2bbc`

---

## v1.7-1.9 · 2026-05-16 · 飞书签名 + image-processor sidecar

- v1.7 飞书机器人签名校验（HMAC-SHA256，避免群机器人被滥发）
- v1.8 image-processor sidecar：抠图/超分/SPU 多图合成
- v1.9 18 节点 workflow（v2.0 前身）

key commits: `2ffaa27`, `84e37cb`, `09bbdfd`

---

## v1.6 · 2026-05-16 · N8N_DEFAULT_LOCALE

- docker-compose 加 `N8N_DEFAULT_LOCALE` env 映射，让 N8N UI 用 zh-CN/ja/en

key commit: `3a54b51`

---

## v1.3-v1.5 · 2026-05-16 · 国产化 + 安装脚本细节

- v1.3 国产 LLM (DeepSeek / 火山方舟) 默认替代 Anthropic
- v1.4 .ps1 加 UTF-8 BOM + .bat chcp 65001（Windows 中文乱码修复）
- v1.5 install.ps1 Add-Field 函数清理

key commits: `e44763f`, `8785c04`, `01d3bb8`

---

## v1.1-1.2 · 2026-05-08 · 改廃监控 + Nano Banana 2

- v1.1 Docker 自动装 + 改廃监控 (stock_monitor) + 7×24 cron
- v1.2 Nano Banana 2 商品图自动管线（后被 image-processor 替代）

key commits: `84b24a4`, `b7c1d38`

---

## v1.0 · 2026-05-08 · 首版

- N8N Windows 安装包 + CMS 自动化集成层 + Shopee 自动上架 page
- 5 容器：n8n + cloudflared + stock-monitor + (后续) cms-api + image-processor

key commit: `6488274`

---

## 演进总结

```
v1.0 (2026-05-08) ─── 首版 N8N installer
  │
  ├─ v1.1-1.5 (2026-05-08 → 05-16) 增量改进
  │
  ├─ v1.6-1.9 (2026-05-16 上午) 飞书 + image-processor
  │
  ├─ v2.0 (2026-05-16 下午) 18 节点完整编排
  │
  ├─ v2.1-2.6 (2026-05-16 下午~晚) cms-api Postgres + Shopee 凭证修复
  │
  ├─ v2.7-2.9 (2026-05-16 晚) ❌ partner_key 处理走错方向（strip+hex）
  │
  ├─ v2.10 (2026-05-17 凌晨) 5 个根因修复 → OAuth 链路通
  │
  └─ v2.11 (2026-05-17 凌晨) CB merchant + HTML 结果页 + healthcheck/trigger 脚本
```

## v2.10 → 后续路线（v2.12+）

- T-315: cat-shopee-map.csv 静态表 + n11 节点改查表（去 LLM/API 依赖）
- v2.12 (TBD): N8N B5c stub 改真上架（依赖 T-315 + Boss Live partner credentials）
- v2.13 (TBD): 7 国并行执行（当前是 SPU 循环串行）
