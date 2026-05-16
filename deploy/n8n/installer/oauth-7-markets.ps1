#requires -Version 5.0
<#
  Shopee 7 国 OAuth 授权助手（v2.11）

  适用前提：
    - cms-api 已部署（容器 smikie_cms_api）
    - .env 已填 SHOPEE_PARTNER_ID / SHOPEE_PARTNER_KEY / SHOPEE_API_BASE
    - 已在 Shopee Open Platform 控制台 Test Account-Sandbox v2 里
      Add Shop 加好对应国家的测试店铺（每国一个）

  用法：
    .\oauth-7-markets.ps1 -Market PH
    .\oauth-7-markets.ps1 -Market TW

  流程：
    1. 拿 oauth-url + 浏览器弹出 Shopee 授权页
    2. Boss 在 Shopee 页登录 + Confirm Authorization
    3. 浏览器跳转到 callback URL（v2.11 后会显示 ✅ 授权成功 HTML 页）
    4. cms-api 自动写 refresh_token 进 shopee_tokens.json
    5. （Cloudflare 路由若有问题导致白屏）脚本提供 fallback：手动粘贴 callback URL
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('PH','TW','MY','SG','TH','VN','ID')]
    [string]$Market,

    [string]$CmsApiBase = "http://localhost:8789",

    [switch]$Fallback   # 浏览器白屏时手动模式
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Web

Write-Host ""
Write-Host "===== Shopee OAuth: $Market =====" -ForegroundColor Cyan

# Step 1: 拿 oauth-url
Write-Host "Step 1/3: 拿 $Market 授权链接..."
$resp = Invoke-RestMethod "$CmsApiBase/api/automation/shopee/oauth-url/$Market"
Write-Host "  authorize_url: $($resp.authorize_url)" -ForegroundColor DarkGray

# Step 2: 浏览器跳转
Write-Host "Step 2/3: 浏览器跳出授权页面 → Boss 登录测试账号 → Confirm Authorization"
Start-Process $resp.authorize_url

if ($Fallback) {
    Write-Host ""
    Write-Host "Fallback 模式：浏览器跳完后白屏 → 从地址栏复制完整 callback URL 粘贴到下面 ↓"
    $cb = Read-Host "callback URL"
    if (-not $cb) { throw "未输入 callback URL" }

    $query = [System.Web.HttpUtility]::ParseQueryString(([Uri]$cb).Query)
    $code = $query['code']
    $mainAccountId = $query['main_account_id']
    $shopId = $query['shop_id']

    if (-not $code) { throw "callback URL 缺 code 参数" }

    Write-Host "  code: $code"
    Write-Host "  main_account_id: $mainAccountId"
    Write-Host "  shop_id: $shopId"

    # 调本地 cms-api callback（v2.11 已支持 main_account_id）
    $cbUrl = "$CmsApiBase/api/automation/shopee/oauth-callback?market=$Market&code=$code"
    if ($shopId) { $cbUrl += "&shop_id=$shopId" }
    if ($mainAccountId) { $cbUrl += "&main_account_id=$mainAccountId" }

    Write-Host "Step 3/3: 调本地 cms-api callback 换 refresh_token..."
    try {
        $callbackResp = Invoke-RestMethod $cbUrl
        Write-Host "  ✅ $Market 授权成功" -ForegroundColor Green
    } catch {
        Write-Host "  ❌ 失败: $($_.Exception.Message)" -ForegroundColor Red
        throw
    }
} else {
    Write-Host ""
    Write-Host "Step 3/3: Shopee 授权后会跳转到 callback URL（v2.11 显示 ✅ HTML 页）"
    Write-Host "  - 如果浏览器显示 ✅ HTML 页 → 完成，refresh_token 已自动落库"
    Write-Host "  - 如果浏览器白屏（Cloudflare Tunnel 路由问题）→ 重跑加 -Fallback 开关"
    Write-Host ""
    Read-Host "按 Enter 确认已完成授权（或浏览器白屏后重跑 -Fallback）"
}

# 验证 refresh_token 已落库
Write-Host ""
Write-Host "验证: 当前已授权市场列表"
$tokens = Invoke-RestMethod "$CmsApiBase/api/automation/shopee/tokens"
$tokens | ConvertTo-Json -Depth 5

if ($tokens.refresh_tokens.$Market) {
    Write-Host "✅ $Market refresh_token 已在库" -ForegroundColor Green
} else {
    Write-Host "⚠️  $Market 不在库里，可能未授权成功 — 检查 cms-api 日志: docker compose logs cms-api --tail 30" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "下一国：.\oauth-7-markets.ps1 -Market <下一个>" -ForegroundColor DarkGray
