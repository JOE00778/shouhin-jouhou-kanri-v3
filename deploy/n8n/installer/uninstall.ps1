# Smikie N8N Uninstaller
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir

$confirm = [System.Windows.Forms.MessageBox]::Show(
    "确定卸载 Smikie N8N？`n`n会停止并删除容器；本地数据 (./data/n8n) 默认保留，`n选 [Yes] 仅卸载，选 [No] 取消。`n（如要连数据一起删除，卸载后手动删除 deploy/n8n/data/ 目录）",
    "卸载确认",
    [System.Windows.Forms.MessageBoxButtons]::YesNo,
    [System.Windows.Forms.MessageBoxIcon]::Warning
)
if ($confirm -ne "Yes") {
    Write-Host "已取消" -ForegroundColor Yellow
    exit 0
}

Push-Location $RootDir
try {
    Write-Host "==> 停止并删除容器..." -ForegroundColor Cyan
    docker compose down
    Write-Host "[OK] 已删除容器" -ForegroundColor Green
} finally {
    Pop-Location
}

[System.Windows.Forms.MessageBox]::Show(
    "✅ 卸载完成。`n`n本地数据保留在: $RootDir\data\`n如要彻底清理，手动删除该目录。",
    "卸载完成",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
) | Out-Null
