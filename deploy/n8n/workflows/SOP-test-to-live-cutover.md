# SOP · Shopee Test sandbox → Live 切换

> **触发条件**：Test 环境跑通 7 国 OAuth + N8N B1-B5 端到端 + Boss 抽审 XLSX 输出合格 → 准备真上架 Live 7 国 shop
>
> **风险等级**：🔴 **高**（一次性切完 7 国所有凭证；切错可能往 Live 商家店发错商品；Boss 必须一行一行核对）
>
> **工作量**：2 小时（仔细做） · 提前 1 小时跟 Boss 同步（防中途打断）

---

## 前置 Gate（不满足就停）

- [ ] Test 环境 7 国 refresh_token 全部落库
- [ ] N8N B1-B5 跑通 ≥ 14/18 节点 ✅（T-314 DOD）
- [ ] Boss 抽审 ≥ 3 个 SPU 的 XLSX 内容合格（标题/描述/类目/主图都对）
- [ ] T-315 cat-shopee-map.csv 已补齐（避免每次调 Shopee API 慢且不稳）
- [ ] **Boss 已申请 Live 应用并审核通过**（Shopee Open Platform 审核 3-7 天）
- [ ] **Boss 已收到 Live Partner ID + Live Partner Key**（不同于 Test！）
- [ ] **Live Redirect URL Domain** 已在 Shopee Open Platform → 应用详情 → Authorization Information 配好（`https://smikie-cms.cc`）

---

## Step 1 · 备份 Test 环境（30 秒）

```powershell
cd D:\Smikie-N8N-Installer-v2.11
# 备份当前 .env + tokens.json + workflow（Test 数据，万一 Live 切失败回滚）
Copy-Item .env .env.test-backup-$(Get-Date -Format 'yyyyMMdd').bak
Copy-Item D:\Smikie-Images\automation_outputs\shopee_tokens.json `
          D:\Smikie-Images\automation_outputs\shopee_tokens.test-backup-$(Get-Date -Format 'yyyyMMdd').json
docker compose logs cms-api --tail 200 > logs\cms-api-test-final.log
```

---

## Step 2 · 卸 Test installer，装 Live installer（5 分钟）

```powershell
# 停 N8N stack（不停 CMS V2.3 stack）
cd D:\Smikie-N8N-Installer-v2.11
docker compose down

# 解压 v2.11-live.zip 到新目录
Expand-Archive D:\Downloads\Smikie-N8N-Installer-v2.11-live.zip -DestinationPath D:\
# 现在 D:\Smikie-N8N-Installer-v2.11-live\ 是 Live 版（.env.template 默认 host = openplatform.shopee.sg）

cd D:\Smikie-N8N-Installer-v2.11-live
```

---

## Step 3 · .env 7 项关键改动 ⚠️（最容易出错的环节）

打开 `.env`，**核对 7 项**：

| # | 字段 | Test 值 | **Live 值** | 来源 |
|---|---|---|---|---|
| 1 | `SHOPEE_PARTNER_ID` | 1232606 | **Boss 新拿到的 Live Partner ID（7-8 位数字）** | Shopee Open Platform Console → Live App |
| 2 | `SHOPEE_PARTNER_KEY` | shpk7344... (64 char) | **Boss 新拿到的 Live Partner Key（可能没 shpk 前缀，长度 64）** | 同上 → Live Partner Key 字段 |
| 3 | `SHOPEE_API_BASE` | `openplatform.sandbox.test-stable.shopee.sg` | **`https://openplatform.shopee.sg`** | v2.11-live default 已对 |
| 4 | `CMS_PUBLIC_BASE` | `https://smikie-cms.cc` | 不变 | 复用 |
| 5 | `SHOPEE_SHOP_IDS` | Test 7 国 shop_id（沙箱虚拟） | **Live 7 国 shop_id（Boss 真店铺 ID）** | Boss 在各国 Shopee Seller Center 看 |
| 6 | `SHOPEE_REFRESH_TOKENS` | Test 7 国 token | **空 `{}`**（Live OAuth 重走一遍拿新 token） | OAuth 流程 |
| 7 | `VOLC_ARK_API_KEY` / `LARK_WEBHOOK_URL` / `POSTGRES_PASSWORD` 等 | Test 值 | **不变**（业务凭证跨环境复用） | 复用 |

⚠️ **关键检查**：
- Live `SHOPEE_PARTNER_KEY` 跟 Test 完全不同！别复制 Test 的过来
- `SHOPEE_API_BASE` 改 Live host 后，Test 那些 refresh_token **没用了**（Test 服务器认的 token Live 不认）

---

## Step 4 · Shopee Open Platform → 改 Test Redirect URL（同时）

去 https://open.shopee.com → SmikieShopeeAutoListing(**Live App**) → Authorization Information：

- Test Redirect URL Domain: `https://smikie-cms.cc` (Test 时填的)
- **Live Redirect URL Domain**: `https://smikie-cms.cc` (必须填这个，否则 OAuth 跳转回来会被 Shopee 拒)

不需要改 cms-api 代码（CMS_PUBLIC_BASE 不变）。

---

## Step 5 · 启动 Live stack + 验证 env 透传

```powershell
docker network create smikie_shared 2>$null
docker compose up -d --force-recreate
Start-Sleep 10

# 验证 env 是 Live
docker exec smikie_cms_api python -c "import os; print('PARTNER_ID:', os.environ['SHOPEE_PARTNER_ID']); print('API_BASE:', os.environ['SHOPEE_API_BASE'])"
# 期望:
#   PARTNER_ID: <Live 7-8 位数字>
#   API_BASE: https://openplatform.shopee.sg

# 跑 healthcheck
.\installer\healthcheck.ps1
# 应该:
#   ✅ 所有容器 healthy
#   ✅ partner_key_len = 64
#   ✅ api_base = openplatform.shopee.sg (Live)
#   ⚠️ 7 国 refresh_token 全部 ⏳ 未授权（Step 6 会逐国授权）
```

---

## Step 6 · 7 国 Live OAuth（10 分钟）

⚠️ **跟 Test 流程一样但跳转的 Shopee 页面是 Live 真账号**：

```powershell
foreach ($mk in @("TW","PH","MY","SG","TH","VN","ID")) {
    Write-Host ""
    Write-Host "========== Live OAuth: $mk ==========" -ForegroundColor Cyan
    Write-Host "Boss: 登录浏览器要用 $mk Live 真账号（不是 Sandbox 测试账号！）" -ForegroundColor Yellow
    Pause
    .\installer\oauth-7-markets.ps1 -Market $mk
}
```

**每国 Boss 浏览器登录时务必核对**：
- URL 包含 `openplatform.shopee.sg` (Live)，**不是** `openplatform.sandbox.test-stable.shopee.sg`
- 用 Boss SmikieJapan 在该国的真实卖家账号（Live Seller Center 用的那个）登录
- 授权前看下要授权哪个店铺，确认 shop_id 跟 .env 里 SHOPEE_SHOP_IDS 一致

---

## Step 7 · 端到端 Smoke Test（10 分钟）

```powershell
# 用 1 个 SmikieJapan 已在售 SPU 触发 N8N
.\installer\trigger-shopee-workflow.ps1 -Jans 4901234567890   # 替换为真实 JAN
```

⚠️ **关键**：v2.11 N8N workflow B5c 仍是 stub（**不会真上架**）。Boss 可以放心跑，不会污染 Live 店铺。
真上架要等 v2.12+ Boss 拍板。

跑完去 N8N Executions 看 18 节点：
- ✅ B1 cms-api 拉 SKU master
- ✅ n02b refresh Live 7 国 access_token
- ✅ B3.5 调 Live Shopee category_recommend API（注意：调真实 API，不是 sandbox）
- ✅ B5b 导出 XLSX

XLSX 输出 Boss 抽审 → 内容合格才往下 Live 真上架。

---

## Step 8 · 升 Boss Live 真上架（v2.12+，**本 SOP 不涵盖**）

T-315 cat-shopee-map.csv 补齐后，单独立项做 v2.12：
- N8N B5c 节点改为真调 Shopee `product/add_item` API
- 第一波只跑 1-3 个 SPU 试水
- Boss 在 Shopee Seller Center 看上架是否成功
- 失败回滚机制（`product/delete_item`）

---

## 回滚（如果 Step 5-7 任何一步出问题）

```powershell
# 停 Live stack
cd D:\Smikie-N8N-Installer-v2.11-live
docker compose down

# 重启 Test stack
cd D:\Smikie-N8N-Installer-v2.11
docker compose up -d --force-recreate

# 验证 Test 链路通
.\installer\healthcheck.ps1
```

Test 备份的 .env / tokens 没动，回滚成本接近 0。

---

## 跨环境数据隔离 ⚠️

| 类 | Test | Live | 复用？ |
|---|---|---|---|
| partner_id / partner_key | Sandbox 给的 | Live 申请后给的 | ❌ 不同 |
| shop_id_list | Sandbox 虚拟 shop | 真店铺 ID | ❌ 不同 |
| refresh_token | Sandbox 服务器认 | Live 服务器认 | ❌ 不同（且互不认） |
| XLSX 模板（5 类目）| 同 | 同 | ✅ 复用 |
| LLM API key（火山方舟）| 同 | 同 | ✅ 复用 |
| `cat-shopee-map.csv`（T-315）| 同 | 同 | ✅ 复用 |
| Cloudflare Tunnel | 同 | 同 | ✅ 复用 |
| cms_postgres 数据 | 同 | 同 | ✅ 复用（同一 stack） |

**所以**：Test → Live 切换实际只动 `.env` 里 6 个 Shopee 字段 + 浏览器重走 7 国 OAuth，其他不动。

## 相关

- [[SOP-cloudflare-path-routing]] · [[SOP-shopee-test-accounts]] · [[SOP-n8n-b1-b5-e2e-test]]
- [[reference_shopee_open_platform_v2]]
- T-315 cat-* 映射表（Live 真上架前的 P0 凭证）
