# ============================================================
# 检查 N8N 部署所需的 Windows 前置依赖
# 退出码:
#   0 = 全部就绪，可直接调 install.ps1
#   1 = 致命缺失（Windows 版本 / 硬件虚拟化），需要用户人工干预
#   2 = 可自动修复（缺 WSL / Docker），由 install.bat 路由到下一步
# ============================================================
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

function Write-Pass($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "  [MISSING] $msg" -ForegroundColor Yellow }
function Write-Block($msg) { Write-Host "  [BLOCKER] $msg" -ForegroundColor Red }

Write-Host "============================================================"
Write-Host "  Smikie N8N · 前置依赖检查"
Write-Host "============================================================"

$missing = @()
$blockers = @()

# ------------------------------------------------------------
# 1. Windows 版本：要求 Windows 10 build 19041+ 或 Windows 11
# ------------------------------------------------------------
Write-Host ""
Write-Host "[1/4] Windows 版本"
$os = Get-CimInstance Win32_OperatingSystem
$ver = [Version]$os.Version
$build = [int]$os.BuildNumber

if ($ver.Major -lt 10) {
    Write-Block "Windows 版本过低: $($os.Caption) ($($os.Version))"
    Write-Host "       需要 Windows 10 (build 19041+) 或 Windows 11" -ForegroundColor Red
    $blockers += "Windows 版本不达标"
} elseif ($ver.Major -eq 10 -and $build -lt 19041) {
    Write-Block "Windows 10 build $build 过低，需要 19041+"
    Write-Host "       请运行 Windows Update 升级到最新版" -ForegroundColor Red
    $blockers += "Windows 10 build 过低"
} else {
    Write-Pass "$($os.Caption) build $build"
}

# ------------------------------------------------------------
# 2. CPU 硬件虚拟化（Docker 必需）
# ------------------------------------------------------------
Write-Host ""
Write-Host "[2/4] CPU 硬件虚拟化"
$cpu = Get-CimInstance Win32_Processor
$vmEnabled = $cpu.VirtualizationFirmwareEnabled
if ($vmEnabled -eq $true) {
    Write-Pass "硬件虚拟化已在 BIOS 启用"
} elseif ($vmEnabled -eq $false) {
    Write-Block "BIOS 中硬件虚拟化未启用（VT-x / AMD-V / SVM）"
    Write-Host "       开机按 F2 进 BIOS → Advanced → CPU Config → SVM/VT-x → Enabled" -ForegroundColor Red
    $blockers += "BIOS 虚拟化未开"
} else {
    Write-Host "  [WARN] 无法检测虚拟化状态（部分 AMD 平台 WMI 不报告），继续往下走" -ForegroundColor Yellow
}

# ------------------------------------------------------------
# 3. WSL2（Docker Desktop backend）
# ------------------------------------------------------------
Write-Host ""
Write-Host "[3/4] WSL2 子系统"

# 检查 Windows 功能：Microsoft-Windows-Subsystem-Linux + VirtualMachinePlatform
$wslFeat = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
$vmpFeat = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue

$wslOn = $wslFeat -and $wslFeat.State -eq "Enabled"
$vmpOn = $vmpFeat -and $vmpFeat.State -eq "Enabled"

if ($wslOn -and $vmpOn) {
    # 进一步检查 WSL2 是否能跑
    $wslVer = & wsl --status 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "WSL2 已启用并就绪"
    } else {
        Write-Fail "WSL 功能已启用但 wsl --status 报错（可能未升级到 v2）"
        $missing += "wsl-update"
    }
} else {
    Write-Fail "WSL 功能未启用（WSL=$wslOn, VirtualMachinePlatform=$vmpOn）"
    $missing += "wsl-feature"
}

# ------------------------------------------------------------
# 4. Docker Desktop
# ------------------------------------------------------------
Write-Host ""
Write-Host "[4/4] Docker Desktop"

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCmd) {
    # 装了 → 检查 daemon 是否能连上
    $null = & docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "Docker Desktop 已装且 daemon 就绪"
    } else {
        Write-Fail "Docker 已装但 daemon 未启动 / WSL2 backend 未就绪"
        $missing += "docker-start"
    }
} else {
    Write-Fail "Docker Desktop 未安装"
    $missing += "docker-install"
}

# ------------------------------------------------------------
# 汇总
# ------------------------------------------------------------
Write-Host ""
Write-Host "============================================================"
if ($blockers.Count -gt 0) {
    Write-Host "  ❌ 致命问题（需人工干预）：" -ForegroundColor Red
    foreach ($b in $blockers) { Write-Host "     - $b" -ForegroundColor Red }
    Write-Host "  请按上面提示修复后重新双击 install.bat"
    Write-Host "============================================================"
    exit 1
}
if ($missing.Count -gt 0) {
    Write-Host "  ⚠️  以下项可自动修复（共 $($missing.Count) 项）：" -ForegroundColor Yellow
    foreach ($m in $missing) { Write-Host "     - $m" -ForegroundColor Yellow }
    Write-Host "  下一步：自动启用 WSL + 安装 Docker Desktop"
    Write-Host "============================================================"
    # 把缺失项写到环境变量给上层 .bat 用
    $missingStr = $missing -join ","
    [Environment]::SetEnvironmentVariable("SMIKIE_N8N_MISSING", $missingStr, "Process")
    # 写到一个文件让 .bat 能读（环境变量 process scope 不能传出）
    $stateFile = Join-Path $PSScriptRoot ".prereq-state"
    $missingStr | Out-File -FilePath $stateFile -Encoding ASCII -Force
    exit 2
}

Write-Host "  ✅ 全部就绪 — 可直接进入 N8N 配置阶段" -ForegroundColor Green
Write-Host "============================================================"
exit 0
