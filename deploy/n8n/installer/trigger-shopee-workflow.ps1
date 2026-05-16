#requires -Version 5.0
<#
  Smikie Shopee Mass Upload · N8N workflow 直接触发助手

  绕过 Streamlit Page 21，PowerShell 一行命令打 N8N webhook，方便 E2E 验证。

  用法：
    .\trigger-shopee-workflow.ps1 -Jans 4901234567890
    .\trigger-shopee-workflow.ps1 -Jans 4901234567890,4901234567891 -Markets PH,TW
    .\trigger-shopee-workflow.ps1 -Jans 4901234567890 -Local   # 走 localhost:5678 不绕 cloudflare
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string[]]$Jans,                # 1 个或多个 JAN（用逗号或多次 -Jans）

    [string[]]$Markets = @("TW","PH","MY","SG","TH","VN","ID"),

    [switch]$Local,                 # 走本机 5678（默认走公网 n8n.smikie-cms.cc）

    [string]$N8nBasicAuthUser = "admin",
    [string]$N8nBasicAuthPassword = $env:N8N_BASIC_AUTH_PASSWORD
)

$base = if ($Local) { "http://localhost:5678" } else { "https://n8n.smikie-cms.cc" }
$url = "$base/webhook/shopee-mass-upload"

$runId = "trigger-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([Guid]::NewGuid().ToString().Substring(0,8))"
$payload = @{
    run_id = $runId
    sku_jans = $Jans
    markets = $Markets
    # spu_groups: 默认让 cms-api /sku/master 拉完后由 B2 节点自动聚合（1 JAN = 1 SPU）
    spu_groups = @()
} | ConvertTo-Json -Depth 5

Write-Host ""
Write-Host "===== Trigger Shopee Mass Upload =====" -ForegroundColor Cyan
Write-Host "URL:       $url"
Write-Host "run_id:    $runId"
Write-Host "jans:      $($Jans -join ', ')"
Write-Host "markets:   $($Markets -join ', ')"
Write-Host ""

# N8N webhook 默认开 Basic Auth；如果 .env 没设密码用空（开发模式）
$headers = @{ "Content-Type" = "application/json" }
if ($N8nBasicAuthPassword) {
    $cred = "${N8nBasicAuthUser}:${N8nBasicAuthPassword}"
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($cred))
    $headers["Authorization"] = "Basic $b64"
}

try {
    $resp = Invoke-RestMethod -Method POST -Uri $url -Headers $headers -Body $payload -TimeoutSec 30
    Write-Host "✅ 触发成功" -ForegroundColor Green
    Write-Host "返回:" -ForegroundColor DarkGray
    $resp | ConvertTo-Json -Depth 5
} catch {
    Write-Host "❌ 触发失败: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Response) {
        try {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            Write-Host "Response body:" -ForegroundColor DarkGray
            Write-Host $reader.ReadToEnd()
        } catch {}
    }
    exit 1
}

Write-Host ""
Write-Host "查看执行进度："
Write-Host "  浏览器: $base   →   Executions → 找 run_id=$runId"
Write-Host "  飞书群: 等绿卡片到达（workflow 跑完会触发飞书通知节点）"
Write-Host ""
Write-Host "tail cms-api 日志（如果想看 B1 /sku/master 命中情况）："
Write-Host "  docker compose logs cms-api --tail 50 -f"
