# =============================================================
# CMS Postgres → LS210DC SMB 备份脚本
# 每天凌晨 3 点自动跑（Windows 任务计划）
# 影刀通常不在凌晨跑，资源完全空闲
#
# 配置 Windows 任务计划：
#   schtasks /Create /TN "CMS Backup to LS210DC" `
#     /TR "powershell.exe -File D:\cms-v230\deploy\windows\backup-to-ls210dc.ps1" `
#     /SC DAILY /ST 03:00 /F
# =============================================================

# 读 .env 配置
$envPath = "$PSScriptRoot\..\..\.env"
if (!(Test-Path $envPath)) {
    Write-Error "未找到 .env 文件: $envPath"
    exit 1
}

$envVars = @{}
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$pgUser = $envVars['POSTGRES_USER'] ?? 'cms'
$pgDb = $envVars['POSTGRES_DB'] ?? 'cms'
$ls210dcPath = $envVars['LS210DC_SMB_PATH']
$ls210dcUser = $envVars['LS210DC_USER']
$ls210dcPwd = $envVars['LS210DC_PASSWORD']

if (!$ls210dcPath) {
    Write-Error "LS210DC_SMB_PATH 未配置"
    exit 1
}

# 时间戳
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpFile = "$env:TEMP\cms_${pgDb}_${timestamp}.sql.gz"

Write-Host "=== CMS Backup to LS210DC ==="
Write-Host "时间: $(Get-Date)"

# 1. Postgres dump（管道直接 gzip）
Write-Host "[1/3] 跑 pg_dump → 压缩到 $dumpFile"
docker exec cms_postgres pg_dump -U $pgUser $pgDb | gzip > $dumpFile

if (!(Test-Path $dumpFile)) {
    Write-Error "pg_dump 失败，文件不存在"
    exit 1
}

$dumpSize = (Get-Item $dumpFile).Length / 1MB
Write-Host "    完成: $([math]::Round($dumpSize, 2)) MB"

# 2. 挂载 LS210DC SMB
Write-Host "[2/3] 挂载 LS210DC: $ls210dcPath"
$secPwd = ConvertTo-SecureString $ls210dcPwd -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($ls210dcUser, $secPwd)
try {
    New-PSDrive -Name "Z" -PSProvider FileSystem -Root $ls210dcPath -Credential $cred -ErrorAction Stop | Out-Null
} catch {
    Write-Error "LS210DC 挂载失败: $_"
    Remove-Item $dumpFile -ErrorAction SilentlyContinue
    exit 1
}

# 3. 复制 + 清理（保留最近 30 天）
Write-Host "[3/3] 上传 + 清理旧备份"
Copy-Item $dumpFile -Destination "Z:\" -Force

# 删除 30 天前的备份
Get-ChildItem "Z:\" -Filter "cms_*.sql.gz" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force

Remove-PSDrive -Name "Z"
Remove-Item $dumpFile -Force

Write-Host "✅ 完成 ($(Get-Date))"
