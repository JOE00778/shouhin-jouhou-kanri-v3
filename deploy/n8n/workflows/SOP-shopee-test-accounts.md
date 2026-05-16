# SOP · Shopee Test Account-Sandbox v2 加 6 国测试 Shop

> **背景**：今晚 PH OAuth 已通（refresh_token 落库）。其他 6 国（TW MY SG TH VN ID）需要先在 Shopee Open Platform Test Account-Sandbox v2 里加对应国家的 Test Shop，才能授权拿 token。
>
> Boss 当前 Test Account 状态（2026-05-17 凌晨截图）：
> - Merchant ID `1000009271`（**CN CB 跨境主体**：[CN]Openplatform GP 1777362390）
> - Main Account: `OpenplatformUser90937:main`
> - 当前 Shop 1/10：只有 PH 一个（Shop ID 227466553, Shop Account `SANDBOX.22be01c6aaac3da2cf6e`）

## 工作量 · 每国 30 秒，6 国 3 分钟

## Step 1 · 进 Test Account 控制台

1. 浏览器打开 https://open.shopee.com → 登录 Boss 账号
2. 左侧栏 **Test Account-Sandbox v2** → 点进去
3. **关键**：上方 tab 选 **China Merchant and CB Shops**（不是 Local Shop —— Boss 业务是跨境主体）
4. 找到 `Merchant ID: 1000009271` 那一项

## Step 2 · 每国 Add Shop（6 国循环）

在 Shop (1/10) 区域点 **Add Shop** 按钮 → 弹窗：

| 字段 | 值 |
|---|---|
| Shop Area | 下拉选目标国家（TW / MY / SG / TH / VN / ID） |
| 其他字段 | 默认即可（Shop Account 自动生成 `SANDBOX.xxxxxxxxxxxxxxxxxx`） |

点 **Create** / **Confirm** → 列表会多一行，记下 Shop ID（**后面要填进 .env SHOPEE_SHOP_IDS**）。

**6 次循环后期望状态**：

| Shop Area | Shop ID | 状态 |
|---|---|---|
| PH | 227466553 | ✅ 已有（今晚拿的） |
| TW | 待 Boss 加 | ⏳ |
| MY | 待 Boss 加 | ⏳ |
| SG | 待 Boss 加 | ⏳ |
| TH | 待 Boss 加 | ⏳ |
| VN | 待 Boss 加 | ⏳ |
| ID | 待 Boss 加 | ⏳ |

Shop 列表会变成 `Shop (7/10)`。

## Step 3 · 收集 Shop ID 填进 .env

把 7 个 shop_id 拼成 JSON dict，填进 `D:\Smikie-N8N-Installer-v2.11\.env`：

```env
SHOPEE_SHOP_IDS={"PH":"227466553","TW":"<TW shop_id>","MY":"<MY>","SG":"<SG>","TH":"<TH>","VN":"<VN>","ID":"<ID>"}
```

⚠️ **JSON 字符串里 shop_id 用字符串包**（双引号），不用 int。一行内不要换行。

应用：

```powershell
# 改完 .env 后必须 force-recreate（[[feedback_docker_compose_force_recreate]]）
docker compose up -d --force-recreate n8n cms-api

# 验证 SHOPEE_SHOP_IDS 已透传到 n8n 容器（B3.5 节点会读这个 env）
docker exec smikie_n8n sh -c 'echo $SHOPEE_SHOP_IDS'
# 期望: {"PH":"227466553","TW":"...",...}
```

## Step 4 · 跑 6 国 OAuth 拿 refresh_token

```powershell
cd D:\Smikie-N8N-Installer-v2.11
foreach ($mk in @("TW","MY","SG","TH","VN","ID")) {
    Write-Host ""
    Write-Host "========== $mk ==========" -ForegroundColor Cyan
    .\installer\oauth-7-markets.ps1 -Market $mk
    # 每国走完后会提示 "按 Enter 继续下一国"
}
```

每国授权流程跟今晚 PH 一样：
1. 浏览器跳 Shopee 授权页（Boss 已登录过，应该不用再输用户名密码）
2. **Confirm Authorization**
3. 浏览器跳 v2.11 友好 HTML 结果页 ✅ 授权成功（前提是已配 [[SOP-cloudflare-path-routing]]）
4. 如果还白屏 → 重跑 `-Fallback` 模式手动粘贴 callback URL：
   ```powershell
   .\installer\oauth-7-markets.ps1 -Market $mk -Fallback
   ```

## Step 5 · 验证 7 国 refresh_token 全部落库

```powershell
Invoke-RestMethod "http://localhost:8789/api/automation/shopee/tokens" | ConvertTo-Json -Depth 5
```

期望输出（7 国都有）：

```json
{
  "refresh_tokens": {
    "PH": "6d77...",
    "TW": "xxxx...",
    "MY": "xxxx...",
    "SG": "xxxx...",
    "TH": "xxxx...",
    "VN": "xxxx...",
    "ID": "xxxx..."
  },
  "updated_at": "2026-05-17Txx:xx:xxZ"
}
```

## 故障排查

| 现象 | 路径 |
|---|---|
| Add Shop 按钮置灰 | 检查 Shop (X/10) — 如果已 10 个 shop 满了需要先 Delete 不用的 |
| Add Shop 弹窗某国家选不到 | Shopee Sandbox 偶尔某国测试环境维护中，等几小时再试 |
| 浏览器 OAuth 授权页报 `partner_id not found` | partner_id 错（不是 1232606）或 host 错（不是 `openplatform.sandbox.test-stable.shopee.sg`） |
| 浏览器跳 Shopee 报 Wrong sign | 见 [[reference_shopee_open_platform_v2]] · 大概率是 [[feedback_docker_compose_force_recreate]] 没 force-recreate |
| 跳到白屏 streamlit 不是 v2.11 HTML 页 | Cloudflare 路由未配，先做 [[SOP-cloudflare-path-routing]]；或用 `-Fallback` 模式手动 |
| `error_param: There is no code` | code 过期了（>10 分钟），重跑授权 |
| `error_auth: shop not authorized` | Boss 在 Confirm Authorization 时没勾这个 shop，回授权页重选 |

## 完成后状态

- 7 国 refresh_token 落 `shopee_tokens.json`
- .env 填 `SHOPEE_SHOP_IDS` 7 国完整
- n02b workflow 节点每次触发都会自动 refresh access_token

下一步：[[SOP-n8n-b1-b5-e2e-test]] 跑 N8N workflow 端到端验证
