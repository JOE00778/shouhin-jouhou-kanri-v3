# SOP · N8N B1-B5 端到端 Workflow 验证

> **背景**：v2.11 OAuth 链路通了 + 7 国 refresh_token 落库后，需要触发 1 个真实 JAN 跑一遍完整 N8N `shopee-mass-upload` workflow（20 节点），看哪些 ✅ 哪些 ❌。
>
> **Workflow 文件**：`workflows/shopee-mass-upload.json`（名称：Shopee 自动上架 · v2.0 完整编排）

## 工作量 · 10 分钟（首跑 + 排查）

## 20 节点速查表

| 阶段 | 节点 | 类型 | 作用 |
|---|---|---|---|
| 入口 | Webhook · CMS 触发 | webhook | CMS Page 21 POST 触发 |
| 入口 | 立即返回 CMS | respondToWebhook | 同步回 CMS 一个 OK |
| 入口 | 变量提取 | set | 把 webhook payload 拆成变量 |
| **B0** | n02b · Shopee token 自动 refresh | code | 7 国 access_token 实时换 |
| 循环 | SPU 循环 · 每次 1 个 | splitInBatches | foreach SPU |
| **B1** | B1 · 查 SKU 主档 | httpRequest | 从 cms-api 拿 SKU master fields |
| **B2** | B2 · SPU 聚合 | code | 把多 SKU 合成 1 SPU |
| **B3** | B3 · 火山方舟 DeepSeek-V3.2 | httpRequest | 调火山 LLM 生成 7 国标题/描述 |
| **B3.2** | B3.2 · 解析 LLM 输出 | code | 解析 JSON → translations 字典 |
| **B3.5** | B3.5 · Shopee category_recommend | code | 7 国分别调 Shopee 类目推荐 API |
| **B4** | B4 · 主图流水线 | code | 调 image-processor 容器抠图+合成 |
| **B5a** | B5a · 拼 XLSX 行 | code | 拼 5 类目 XLSX 行 |
| **B5b** | B5b · 导出 XLSX | spreadsheetFile | 生成 XLSX 文件 |
| **B5b.2** | B5b.2 · 上传 XLSX 给 CMS | httpRequest | cms-api PUT outputs/ |
| **B5c** | B5c · Shopee 上架（stub） | code | v2.x 仍是 stub 不真上架 |
| 收尾 | 汇总统计 | code | 汇总 7 国 success/fail |
| 收尾 | CMS 回调 · processing | httpRequest | cms-api PATCH automation_runs status=processing |
| 收尾 | CMS 回调 · completed | httpRequest | cms-api PATCH automation_runs status=completed |
| 通知 | 飞书签名计算 | code | HMAC for 飞书 webhook 签名 |
| 通知 | 飞书通知 | httpRequest | POST 飞书绿卡片 |

## Step 1 · 准备 1 个真实 JAN（5 分钟）

去商品信息管理 Streamlit Page 21（🚀 Shopee上架）：

1. 浏览器打开 https://smikie-cms.cc → Page 21
2. 输入 1 个真实 JAN（任意已在售 SKU 即可，比如最近 PH 上架过的）
3. **方案选 A**（Shopee 直传）
4. **不要勾 Mock 模式**（要真跑）
5. 点 **触发 N8N workflow**

或者用 PowerShell 直接 POST webhook：

```powershell
$jan = "4901234567890"  # 替换为真实 JAN
$payload = @{
    spu_input_csv_url = "..."  # CMS 返回的 csv URL
    market_list = "TW,PH,MY,SG,TH,VN,ID"
    run_id = "test-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
} | ConvertTo-Json
Invoke-RestMethod -Method POST `
    -Uri "https://n8n.smikie-cms.cc/webhook/shopee-mass-upload" `
    -Body $payload `
    -ContentType "application/json"
```

## Step 2 · 看 N8N Executions（实时）

1. 浏览器 https://n8n.smikie-cms.cc → 登录 N8N Basic Auth
2. 左侧栏 **Executions**
3. 找最新一条 status=Running（或 Success/Error）→ 点进去
4. **节点视图**：每个节点圆圈
   - 🟢 绿 = ✅ Success
   - 🔴 红 = ❌ Error（点开看 error message）
   - ⚪ 空 = 还没跑到 / stub skip

## Step 3 · 期望状态（首跑通过线 ≥ 14/20）

| 节点 | 期望状态 | 失败时排查 |
|---|---|---|
| Webhook · CMS 触发 | ✅ | n8n 公网入口 / BasicAuth |
| 立即返回 CMS | ✅ | — |
| 变量提取 | ✅ | webhook payload 字段 |
| **n02b · Shopee token 自动 refresh** | ✅ 7 国都 OK 或 partial | 7 国 refresh_token 是否都在；`docker logs smikie_n8n` |
| SPU 循环 | ✅ | — |
| **B1 · 查 SKU 主档** | ✅ | cms-api `/sku/master/<jan>` 通；smikie_shared 网络 |
| **B2 · SPU 聚合** | ✅ | — |
| **B3 · 火山方舟** | ✅ | VOLC_ARK_API_KEY 配；VOLC_ARK_TEXT_MODEL endpoint ID 是 `deepseek-v3-2-251201` 或 Boss 自定义 |
| **B3.2 · 解析 LLM 输出** | ✅ | LLM 是否吐合法 JSON |
| **B3.5 · category_recommend** | ✅ all_ok 或 partial（取决于是否补了 cat-* 映射） | Shopee category_recommend API 通；access_token 有；shop_id 有 |
| **B4 · 主图流水线** | ✅ | image-processor 容器 :8788/health 通；TEMPLATE_PATH 存在 |
| **B5a · 拼 XLSX 行** | ✅ | — |
| **B5b · 导出 XLSX** | ✅ | n8n 文件挂载 /data/files/ |
| **B5b.2 · 上传 XLSX 给 CMS** | ✅ | cms-api `/xlsx-upload` ；D:/Smikie-Images/automation_outputs/ 写权限 |
| **B5c · Shopee 上架（stub）** | ✅（stub mode）| v2.x 仍 stub，不实际打 Shopee /product/add_item |
| 汇总统计 | ✅ | — |
| CMS 回调 · processing | ✅ | cms-api `/automation_runs/<id>` PATCH |
| CMS 回调 · completed | ✅ | 同上 |
| 飞书签名计算 | ✅ | LARK_BOT_SIGN_SECRET 配 |
| **飞书通知** | ✅ | LARK_WEBHOOK_URL 配；飞书群机器人没禁；签名正确 |

**底线 DOD**（T-314 验收）：18 节点 ≥ 14 个 ✅，飞书绿卡片到达。

## Step 4 · 检查飞书绿卡片

去 SmikieJapan 飞书群（或 Boss 的 N8N 通知群）→ 看 N8N 机器人发的卡片：

期望卡片显示：
```
🚀 Shopee 自动上架完成 · JAN 4901234567890
✅ 类目命中: 7/7  ⚠️ 部分 0/7  ❌ 失败 0/7
✅ 主图: 7/7 国
✅ XLSX 已生成: outputs/4901234567890_mass_upload_<ts>.xlsx
（V2.x 仍 stub 模式，不实际 /product/add_item）
```

如果显示「⏸ STUB（等 Shopee 凭证）」说明 SHOPEE_PARTNER_KEY 没读到，回 [[SOP-shopee-test-accounts]] 确认 .env。

## Step 5 · 检查 XLSX 产出

```powershell
ls D:\Smikie-Images\automation_outputs\*.xlsx | Sort-Object LastWriteTime -Descending | Select -First 1
```

期望看到刚才那个 JAN 的 XLSX。打开看 5 个 Sheet（5 类目分别）：
- Sheet 1: 100630 化妆/个护（默认 fallback）
- Sheet 2-5: 100629 / 100632 / 100638 / 100640

每个 Sheet 第 1 行表头跟 [`shopee-listing/docs/02-shopee-template-fields.json`](../../../shopee-listing/docs/02-shopee-template-fields.json) 对齐。

## 常见失败 + 修复

### B3.5 category_recommend stub_no_shopee_credentials

意思是 partner_id/key 没读到。`docker exec smikie_n8n sh -c 'echo $SHOPEE_PARTNER_KEY'` 应该非空。

### B3.5 partial（某几国失败）

每国独立调 Shopee API，某国 `shop_id` 或 `access_token` 缺会跳过。看 B3.5 输出 JSON 里 `category_errors[]`。

### B4 主图流水线超时

image-processor 容器 download + cutout 慢，单 JAN 可能 30-60 秒。如果 timeout 调 n8n 节点 timeout 到 120s。

### 飞书签名错

`LARK_BOT_SIGN_SECRET` 不能含空格 / 换行。`docker exec smikie_n8n sh -c 'echo "[$LARK_BOT_SIGN_SECRET]"'` 看是不是干净。

## 跑通后

T-314 任务可以从 `reviewer-needed` → `done`，关键 commit `c2b8790`（v2.10/v2.11 5 个根因修复）+ 截图（飞书卡片 + N8N Executions 18 节点 ✅）填进任务 `## 完成后填写` 段。

## 相关

- [[SOP-cloudflare-path-routing]] · [[SOP-shopee-test-accounts]]
- [[reference_shopee_open_platform_v2]]
- T-314 任务文件：`.tasks/doing/T-314-shopee-n8n-v2-pilot.md`
