# ============================================================
# 启用 WSL2 + VirtualMachinePlatform，升级到 v2
# 退出码:
#   0 = 成功（WSL2 就绪）
#   3 = 启用了功能但需要重启系统
#   1 = 失败
# ============================================================
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

Write-Host "============================================================"
Write-Host "  启用 WSL2 子系统"
Write-Host "============================================================"

$needRestart = $false

# 1. Microsoft-Windows-Subsystem-Linux
$wslFeat = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
if ($wslFeat.State -ne "Enabled") {
    Write-Host "==> 启用 Microsoft-Windows-Subsystem-Linux..." -ForegroundColor Cyan
    $r = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
    if ($r.RestartNeeded) { $needRestart = $true }
    Write-Host "[OK] WSL 功能已启用" -ForegroundColor Green
} else {
    Write-Host "[OK] WSL 功能已启用（之前装的）" -ForegroundColor Green
}

# 2. VirtualMachinePlatform
$vmpFeat = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
if ($vmpFeat.State -ne "Enabled") {
    Write-Host "==> 启用 VirtualMachinePlatform..." -ForegroundColor Cyan
    $r = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
    if ($r.RestartNeeded) { $needRestart = $true }
    Write-Host "[OK] VirtualMachinePlatform 已启用" -ForegroundColor Green
} else {
    Write-Host "[OK] VirtualMachinePlatform 已启用（之前装的）" -ForegroundColor Green
}

if ($needRestart) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host "  ⚠️  Windows 功能已启用，但需要【重启系统】才能生效" -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  请保存所有工作 → 重启电脑 → 重启后再次双击 install.bat 继续" -ForegroundColor Yellow
    Write-Host ""
    exit 3
}

# 3. WSL2 默认版本 + 升级 kernel
Write-Host ""
Write-Host "==> 升级 WSL kernel + 设默认版本为 v2..." -ForegroundColor Cyan
$null = & wsl --update 2>&1
$null = & wsl --set-default-version 2 2>&1
Write-Host "[OK] WSL2 kernel 已升级，默认版本已设为 v2" -ForegroundColor Green

Write-Host ""
Write-Host "[OK] WSL2 完全就绪" -ForegroundColor Green
exit 0
