# ============================================================
# 自动安装 Docker Desktop
# 优先用 winget（Windows 11 / Win10 22H2+ 自带），失败 fallback 到直链下载
# 退出码:
#   0 = 安装成功且 daemon 就绪
#   3 = 安装成功但需要登出 / 重启
#   1 = 安装失败
# ============================================================
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

Write-Host "============================================================"
Write-Host "  安装 Docker Desktop"
Write-Host "============================================================"

$dockerExe = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerExe) {
    Write-Host "[OK] Docker Desktop 已安装于 $dockerExe" -ForegroundColor Green
} else {
    # ---- 尝试 winget ----
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    $installedByWinget = $false
    if ($wingetCmd) {
        Write-Host "==> 用 winget 安装 Docker Desktop（约 600 MB，需要 5-10 分钟）..." -ForegroundColor Cyan
        try {
            & winget install -e --id Docker.DockerDesktop --silent --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -eq 0) {
                $installedByWinget = $true
                Write-Host "[OK] winget 安装完成" -ForegroundColor Green
            } else {
                Write-Host "[WARN] winget 安装返回非 0（$LASTEXITCODE），尝试直链下载兜底" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "[WARN] winget 异常: $_，尝试直链下载兜底" -ForegroundColor Yellow
        }
    }

    # ---- winget 不可用 / 失败 → 直链下载 ----
    if (-not $installedByWinget) {
        Write-Host "==> 直链下载 Docker Desktop installer..." -ForegroundColor Cyan
        $url = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
        $dest = Join-Path $env:TEMP "DockerDesktopInstaller.exe"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
            Write-Host "[OK] 下载完成: $dest" -ForegroundColor Green
        } catch {
            Write-Host "[ERROR] 下载失败: $_" -ForegroundColor Red
            exit 1
        }

        Write-Host "==> 静默安装 Docker Desktop（约 5 分钟）..." -ForegroundColor Cyan
        $proc = Start-Process -FilePath $dest `
            -ArgumentList "install", "--quiet", "--accept-license", "--backend=wsl-2" `
            -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Host "[ERROR] Docker Desktop 安装失败（exit $($proc.ExitCode)）" -ForegroundColor Red
            exit 1
        }
        Write-Host "[OK] Docker Desktop 安装完成" -ForegroundColor Green
    }
}

# ---- 启动 Docker Desktop（如果还没跑） ----
$dockerProc = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $dockerProc) {
    if (Test-Path $dockerExe) {
        Write-Host "==> 启动 Docker Desktop..." -ForegroundColor Cyan
        Start-Process $dockerExe
    }
}

# ---- 等 daemon 就绪（最长 180 秒） ----
Write-Host "==> 等待 Docker daemon 就绪（首次启动可能需要 1-3 分钟）..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(180)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $null = & docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 5
}

if ($ready) {
    Write-Host "[OK] Docker daemon 就绪" -ForegroundColor Green
    exit 0
}

Write-Host "[WARN] Docker daemon 未在 3 分钟内就绪。" -ForegroundColor Yellow
Write-Host "       这通常意味着首次启动需要您手动:" -ForegroundColor Yellow
Write-Host "         1. 点开 Docker Desktop 窗口接受许可" -ForegroundColor Yellow
Write-Host "         2. 等小图标变绿（系统托盘）" -ForegroundColor Yellow
Write-Host "         3. 重新双击 install.bat" -ForegroundColor Yellow
exit 3
