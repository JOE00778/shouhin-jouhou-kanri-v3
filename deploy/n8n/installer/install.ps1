# ============================================================
# Smikie N8N Installer · 主体（GUI + Docker 启动 + workflow 导入）
# 由 install.bat 以管理员权限调起；不要直接运行本脚本（不会提权）
# ============================================================
#Requires -Version 5.1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# 安装根目录（脚本所在的上一级 = deploy/n8n/）
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$ComposeFile = Join-Path $RootDir "docker-compose.yml"
$EnvTemplate = Join-Path $RootDir ".env.template"
$EnvFile = Join-Path $RootDir ".env"
$WorkflowsDir = Join-Path $RootDir "workflows"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

function Show-Error($msg) {
    [System.Windows.Forms.MessageBox]::Show(
        $msg, "Smikie N8N Installer",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

function Test-DockerReady {
    try {
        $null = docker info 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function New-RandomKey([int]$len = 32) {
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count $len | ForEach-Object { [char]$_ })
}

# ------------------------------------------------------------
# 0. 前置检查：Docker Desktop 必须装且在跑
# ------------------------------------------------------------
Write-Step "检查 Docker Desktop 状态..."

$dockerExe = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerExe) {
    Show-Error "未检测到 Docker。请先安装 Docker Desktop（https://www.docker.com/products/docker-desktop/），启动后重试。"
    exit 1
}

if (-not (Test-DockerReady)) {
    Write-Warn "Docker 命令存在但 daemon 未启动。尝试启动 Docker Desktop..."
    $dd = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) {
        Start-Process $dd
        Write-Host "等待 Docker daemon 就绪（最长 90 秒）..." -ForegroundColor Yellow
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline) {
            if (Test-DockerReady) { break }
            Start-Sleep -Seconds 3
        }
    }
    if (-not (Test-DockerReady)) {
        Show-Error "Docker daemon 未就绪。请手动启动 Docker Desktop，等图标变绿后重新运行 install.bat。"
        exit 1
    }
}
Write-OK "Docker Desktop 就绪"

# ------------------------------------------------------------
# 1. GUI 表单：收集配置
# ------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "Smikie N8N Installer · 配置"
$form.Size = New-Object System.Drawing.Size(640, 700)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$titleLabel = New-Object System.Windows.Forms.Label
$titleLabel.Text = "Smikie N8N · Windows 部署配置"
$titleLabel.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$titleLabel.Location = New-Object System.Drawing.Point(20, 15)
$titleLabel.Size = New-Object System.Drawing.Size(600, 30)
$form.Controls.Add($titleLabel)

$descLabel = New-Object System.Windows.Forms.Label
$descLabel.Text = "请填写以下配置项。带 * 的为必填，留空使用默认值。"
$descLabel.Location = New-Object System.Drawing.Point(20, 50)
$descLabel.Size = New-Object System.Drawing.Size(600, 20)
$form.Controls.Add($descLabel)

# 配置项布局：label 左，input 右
$y = 90
$inputs = @{}

function Add-Field($label, $key, $default = "", $isPassword = $false) {
    $script:y = $script:y
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $label
    $lbl.Location = New-Object System.Drawing.Point(20, $script:y)
    $lbl.Size = New-Object System.Drawing.Size(220, 20)
    $form.Controls.Add($lbl)

    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Location = New-Object System.Drawing.Point(245, ($script:y - 3))
    $tb.Size = New-Object System.Drawing.Size(370, 22)
    $tb.Text = $default
    if ($isPassword) { $tb.UseSystemPasswordChar = $true }
    $form.Controls.Add($tb)
    $script:inputs[$key] = $tb

    $script:y += 32
}

Add-Field "* 公网域名 (N8N_HOST):"           "N8N_HOST"             "n8n.smikie-cms.cc"
Add-Field "* CF Tunnel Token:"                "CLOUDFLARE_TUNNEL_TOKEN" ""           $true
Add-Field "* N8N 管理员账号:"                  "N8N_BASIC_AUTH_USER"  "admin"
Add-Field "* N8N 管理员密码:"                  "N8N_BASIC_AUTH_PASSWORD" ""          $true
Add-Field "  CMS 回调地址:"                    "CMS_CALLBACK_URL"     "https://smikie-cms.cc/api/automation/callback"
Add-Field "  飞书群机器人 Webhook (可空):"     "LARK_WEBHOOK_URL"     ""
Add-Field "  飞书 App ID (改廃监控用):"        "LARK_APP_ID"          ""
Add-Field "  飞书 App Secret (改廃监控用):"    "LARK_APP_SECRET"      ""             $true
Add-Field "  Shopee Partner ID (可空):"        "SHOPEE_PARTNER_ID"    ""
Add-Field "  Shopee Partner Key (可空):"       "SHOPEE_PARTNER_KEY"   ""             $true
Add-Field "  DeepSeek API Key (文本/翻译):"    "DEEPSEEK_API_KEY"     ""             $true
Add-Field "  火山方舟 API Key (图像/生图):"     "VOLC_ARK_API_KEY"     ""             $true
Add-Field "  通义千问 API Key (备用,可空):"     "QWEN_API_KEY"         ""             $true
Add-Field "  智谱 GLM API Key (备用,可空):"     "ZHIPU_API_KEY"        ""             $true

# 加密 keychain（自动生成，不让用户改）
$keyLabel = New-Object System.Windows.Forms.Label
$keyLabel.Text = "  加密密钥已自动生成（不要修改）"
$keyLabel.Location = New-Object System.Drawing.Point(20, $y)
$keyLabel.Size = New-Object System.Drawing.Size(600, 20)
$keyLabel.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($keyLabel)
$y += 30

$installBtn = New-Object System.Windows.Forms.Button
$installBtn.Text = "开始安装"
$installBtn.Size = New-Object System.Drawing.Size(150, 36)
$installBtn.Location = New-Object System.Drawing.Point(245, ($y + 10))
$installBtn.BackColor = [System.Drawing.Color]::FromArgb(0, 120, 215)
$installBtn.ForeColor = [System.Drawing.Color]::White
$installBtn.FlatStyle = "Flat"
$installBtn.Add_Click({
    if ([string]::IsNullOrWhiteSpace($inputs["CLOUDFLARE_TUNNEL_TOKEN"].Text) -or
        [string]::IsNullOrWhiteSpace($inputs["N8N_BASIC_AUTH_PASSWORD"].Text)) {
        [System.Windows.Forms.MessageBox]::Show(
            "CF Tunnel Token 和 N8N 管理员密码必填。",
            "缺少必填项",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
        return
    }
    $form.DialogResult = "OK"
    $form.Close()
})
$form.Controls.Add($installBtn)

$cancelBtn = New-Object System.Windows.Forms.Button
$cancelBtn.Text = "取消"
$cancelBtn.Size = New-Object System.Drawing.Size(100, 36)
$cancelBtn.Location = New-Object System.Drawing.Point(415, ($y + 10))
$cancelBtn.Add_Click({
    $form.DialogResult = "Cancel"
    $form.Close()
})
$form.Controls.Add($cancelBtn)

$result = $form.ShowDialog()
if ($result -ne "OK") {
    Write-Warn "用户取消安装"
    exit 0
}

# 收集配置
$config = @{}
foreach ($k in $inputs.Keys) {
    $config[$k] = $inputs[$k].Text
}
$config["N8N_ENCRYPTION_KEY"] = New-RandomKey 32

# ------------------------------------------------------------
# 2. 写 .env
# ------------------------------------------------------------
Write-Step "写入配置到 .env..."
$envLines = @(
    "# Auto-generated by install.ps1 at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "# 修改本文件后请重启容器：docker compose restart",
    ""
)
foreach ($k in @(
    "N8N_HOST", "CLOUDFLARE_TUNNEL_TOKEN",
    "N8N_BASIC_AUTH_USER", "N8N_BASIC_AUTH_PASSWORD", "N8N_ENCRYPTION_KEY",
    "CMS_CALLBACK_URL", "LARK_WEBHOOK_URL",
    "LARK_APP_ID", "LARK_APP_SECRET",
    "SHOPEE_PARTNER_ID", "SHOPEE_PARTNER_KEY",
    "DEEPSEEK_API_KEY", "VOLC_ARK_API_KEY",
    "QWEN_API_KEY", "ZHIPU_API_KEY"
)) {
    $envLines += "$k=$($config[$k])"
}
$envLines | Out-File -FilePath $EnvFile -Encoding UTF8 -Force
Write-OK ".env 已生成: $EnvFile"

# ------------------------------------------------------------
# 3. docker compose pull + up
# ------------------------------------------------------------
Push-Location $RootDir
try {
    Write-Step "拉取镜像（首次约 200MB，需要 1-3 分钟）..."
    docker compose pull
    if ($LASTEXITCODE -ne 0) { throw "docker compose pull 失败" }
    Write-OK "镜像就绪"

    Write-Step "启动容器..."
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "docker compose up -d 失败" }
    Write-OK "容器已启动"

    # 等 n8n healthcheck 通过
    Write-Step "等待 N8N 就绪（最长 60 秒）..."
    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest "http://localhost:5678/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 3
    }
    if ($ready) {
        Write-OK "N8N 就绪"
    } else {
        Write-Warn "N8N 健康检查超时；继续往下走，请稍后用 docker compose logs n8n 排查"
    }
}
finally {
    Pop-Location
}

# ------------------------------------------------------------
# 4. 导入预装 workflow
# ------------------------------------------------------------
if (Test-Path $WorkflowsDir) {
    Write-Step "导入预装 workflow..."
    $jsons = Get-ChildItem -Path $WorkflowsDir -Filter *.json -ErrorAction SilentlyContinue
    if ($jsons.Count -eq 0) {
        Write-Warn "workflows/ 目录内无 JSON，跳过"
    } else {
        foreach ($f in $jsons) {
            try {
                docker exec smikie_n8n n8n import:workflow --input="/workflows/$($f.Name)" 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-OK "导入: $($f.Name)"
                } else {
                    Write-Warn "导入失败（可能已存在同名）: $($f.Name)"
                }
            } catch {
                Write-Warn "导入异常: $($f.Name) - $_"
            }
        }
    }
}

# ------------------------------------------------------------
# 5. 完成提示 + 自动开浏览器
# ------------------------------------------------------------
$publicUrl = "https://$($config['N8N_HOST'])"
$localUrl = "http://localhost:5678"

$msg = @"
✅ Smikie N8N 安装完成！

公网入口: $publicUrl
本机入口: $localUrl

登录账号: $($config['N8N_BASIC_AUTH_USER'])
登录密码: <你刚设置的密码>

提示:
  - 公网入口需要先在 Cloudflare Dashboard 配 Public Hostname
    (Tunnels → 选 Tunnel → Public Hostname → Add: n8n / type=HTTP / URL=n8n:5678)
  - 修改配置: 编辑 deploy/n8n/.env 后跑 docker compose restart
  - 卸载: 双击 uninstall.bat

是否立刻打开本机入口？
"@

$openIt = [System.Windows.Forms.MessageBox]::Show(
    $msg, "安装完成",
    [System.Windows.Forms.MessageBoxButtons]::YesNo,
    [System.Windows.Forms.MessageBoxIcon]::Information
)
if ($openIt -eq "Yes") {
    Start-Process $localUrl
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " 安装完成！容器在后台运行，关闭此窗口即可。" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
exit 0
