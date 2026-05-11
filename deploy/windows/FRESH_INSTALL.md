# 一元管理系统 V2.3 · Windows 重装 + CMS 一并部署 全流程

适用：Dell Inspiron 5405 准备重装系统时，**一次到位**装好「Windows + 影刀 + Docker + CMS」。

预计总耗时：**3-4 小时**（包含 Windows 安装 + 影刀重装 + CMS 上线）。

---

## 📋 重装前必备：备份 4 项数据

⚠️ **重装会清空 C 盘**。下面 4 项**必须先备份**：

| # | 项目 | 在哪 | 备份去哪 |
|---|---|---|---|
| 1 | **影刀工程文件** | `C:\Users\<用户>\YingDao\` 或影刀客户端 → 工程目录 | LS210DC `\\LS210DC\backup\yingdao\` |
| 2 | **影刀账号 + 自动化脚本** | 影刀官网账号关联，工程文件本地 | 影刀云端通常已同步，本地再备一份 |
| 3 | **Cloudflare / 域名信息** | 浏览器收藏夹 | 截图发自己邮箱 |
| 4 | **重要文档 / 桌面文件** | `C:\Users\<用户>\Desktop`、`Documents` | 外置硬盘 / LS210DC SMB |

`D 盘`如果是单独分区**不会被格式化**（看你重装时分区设置），但**最稳还是备份**。

---

## 🛠️ 阶段 1：装 Windows 11（30-60 分钟）

### 1.1 准备安装介质

- 用另一台电脑下载 [Windows 11 Media Creation Tool](https://www.microsoft.com/zh-cn/software-download/windows11)
- 准备 8 GB+ U 盘 → 制作启动盘

### 1.2 进 BIOS 调几项设置（**很重要**）

开机反复按 **F2** 进 BIOS：

| 设置 | 值 | 原因 |
|---|---|---|
| **Advanced** → CPU Config → **SVM Mode** | **Enabled** | Docker WSL2 必需的 AMD 虚拟化 |
| **Boot** → **Boot Mode** | **UEFI** | Windows 11 必需 |
| **Boot** → **Secure Boot** | Enabled | Windows 11 推荐 |
| **Boot Order** → 第 1 个 | USB U 盘 | 从 U 盘启动安装 |

保存退出（F10）。

### 1.3 装 Windows 11

- 插 U 盘启动 → 选「**自定义安装**」（不是「升级」）
- 分区方案推荐：
  - `C:` 系统盘 **256 GB**（Windows + 应用）
  - `D:` 数据盘 **221 GB**（CMS 数据 + 影刀工程 + 备份）
- 选 D 盘是因为 CMS 全套（容器镜像 + Postgres + xls 输入输出）放 D 盘更安全 + 重装系统不丢

### 1.4 装完后基础配置

- 跳过 Microsoft 账号 → 用本地账号
- 用户名建议**纯英文**（不要中文，避免后续路径问题）
- 装完先跑 **Windows Update**（让系统打全补丁）
- 装日文输入法（如果需要）

---

## 🛠️ 阶段 2：装影刀（30 分钟）

1. 影刀官网下载安装包 → 装到 `C:\Program Files\YingDao\`
2. 登录账号 → 同步云端工程
3. 把备份的本地工程文件复制回 `C:\Users\<你>\YingDao\`
4. 跑一次影刀验证一切正常
5. **影刀 → 设置 → 开机自启** 勾上

---

## 🛠️ 阶段 3：装 Docker Desktop + CMS（60-90 分钟）

### 3.1 启用 Windows 必需功能（5 分钟）

PowerShell **管理员**运行：

```powershell
# 启用 WSL + 虚拟机平台
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

# 重启
Restart-Computer
```

重启后再 PowerShell 管理员：

```powershell
# WSL2 升级 + 装 Ubuntu (Docker Desktop backend)
wsl --update
wsl --set-default-version 2
```

### 3.2 装 Docker Desktop（10 分钟）

- 下载：https://www.docker.com/products/docker-desktop/
- 安装时**勾选**「Use WSL 2 instead of Hyper-V」
- 安装完毕需要登出 / 重启
- 第一次启动 Docker Desktop → Sign in 跳过（不需要账号）

### 3.3 配置 Docker Desktop（5 分钟）

Docker Desktop → ⚙️ Settings：

- **General**：✅ Start Docker Desktop when you sign in
- **General**：❌ Open Docker Dashboard at startup
- **Resources** → **WSL Integration**：勾上 default distro
- **Apply & Restart**

### 3.4 配 WSL2 全局资源限制（重要 - 影刀友好）

```powershell
# 把 .wslconfig 复制到用户目录
mkdir D:\cms-v230 -ea 0
cd D:\cms-v230
git clone https://github.com/JOE00778/CMS-v230.git .
Copy-Item deploy\windows\.wslconfig.example "$env:USERPROFILE\.wslconfig"
notepad "$env:USERPROFILE\.wslconfig"   # 不需要改，按 16GB / 6 核默认即可
wsl --shutdown
# 重新打开 Docker Desktop
```

### 3.5 配 .env（5 分钟）

```powershell
cd D:\cms-v230
Copy-Item deploy\windows\.env.example .env
notepad .env
```

填入以下值：
- `POSTGRES_PASSWORD` → 32 位随机字符串（建议用 1Password / Bitwarden 生成）
- `ADMIN_PASSWORD` / `GUEST_PASSWORD` → 你设的密码
- `CLOUDFLARE_TUNNEL_TOKEN` → 见阶段 4
- `LARK_APP_ID` / `LARK_APP_SECRET` / `LARK_REDIRECT_URI` → 见阶段 5
- `LS210DC_*` → LS210DC 备份配置（可空，明天再配）

---

## 🛠️ 阶段 4：Cloudflare Tunnel（10 分钟）

参见 [README.md](README.md) 阶段 ④（流程一样）。

简化版：
1. 打开 https://one.dash.cloudflare.com → Networks → Tunnels → **Create a tunnel**
2. 名字 `cms-inspiron` → 复制 Tunnel Token → 填到 `.env` 的 `CLOUDFLARE_TUNNEL_TOKEN`
3. **Public Hostname** → Subdomain `cms` + 你的域名 → Type `HTTP` + URL `streamlit:8501`

---

## 🛠️ 阶段 5：起 CMS 容器（30 秒）

```powershell
cd D:\cms-v230\deploy\windows
docker compose up -d

# 看日志确认全部 up
docker compose ps
docker compose logs -f streamlit
# Ctrl+C 退出查看
```

浏览器打开 `https://cms.<your-domain>` → 用 JO043 / `<ADMIN_PASSWORD>` 登录 → page 99 上传 xls。

---

## 🛠️ 阶段 6：飞书自建应用（10 分钟）

参见 [LARK_SETUP.md](LARK_SETUP.md)。

---

## 🛠️ 阶段 7：开机自启 + 备份（10 分钟）

### 7.1 开机自启检查

✅ Docker Desktop 已设开机自启（阶段 3.3）  
✅ 容器有 `restart: unless-stopped`，跟着 Docker 自启  
✅ 影刀已设开机自启（阶段 2.5）

### 7.2 LS210DC 自动备份

```powershell
# Windows 任务计划：每天凌晨 3 点跑备份
schtasks /Create /TN "CMS Backup to LS210DC" `
  /TR "powershell.exe -File D:\cms-v230\deploy\windows\backup-to-ls210dc.ps1" `
  /SC DAILY /ST 03:00 /F
```

### 7.3 Watchdog（可选 - 防 Docker 假死）

```powershell
# 每小时检查容器是否还在跑，挂了自动重启
schtasks /Create /TN "CMS Watchdog" `
  /TR "powershell.exe -Command \"if (-not (docker ps --filter name=cms_streamlit -q)) { cd D:\cms-v230\deploy\windows; docker compose up -d }\"" `
  /SC HOURLY /F
```

---

## ✅ 验收清单

```
[ ] BIOS SVM Mode 已开启
[ ] D 盘 221 GB 可用
[ ] Windows 11 已激活
[ ] 影刀已重装并能跑（验证 1-2 个自动化任务）
[ ] Docker Desktop 已装并开机自启
[ ] WSL2 资源限制 4GB/3核 已生效（任务管理器看 vmmemWSL 进程）
[ ] CMS 三容器都 Up：cms_postgres / cms_streamlit / cms_cloudflared
[ ] 浏览器打开 https://cms.<your-domain> 能登录（JO043 / ADMIN_PASSWORD）
[ ] page 99 能上传 xls 并 inserted > 0
[ ] page 05「前日」维度能看到数据
[ ] 飞书工作台「一元管理系统V2.3」可见 + 点击免登录进入
[ ] LS210DC 备份任务计划已配，T+1 天检查 \\LS210DC\backup\cms-v230\ 有 dump 文件
[ ] 影刀连续跑 24 小时无明显变慢（任务平均时长对比上线前）
```

---

## 🆘 常见问题

| 现象 | 处理 |
|---|---|
| `wsl --update` 报 0x80369033 | BIOS SVM 没开 |
| Docker Desktop 启动转圈 | 任务管理器结束 vmmemWSL → 再启动 |
| 容器启动后立刻退出 | `docker compose logs streamlit` 看错误（多半 .env 漏填）|
| 影刀变卡 | `.wslconfig` 把 memory 从 4GB → 3GB，wsl --shutdown |
| LS210DC 备份失败 | 测 `\\LS210DC\backup\` 能不能在文件资源管理器打开 + LS210DC SMB 用户名密码 |

---

## 💰 成本核算

| 项 | 一次性 | 月度 |
|---|---|---|
| Inspiron 5405（已有）| ¥0 | ¥0 |
| LS210DC（已有）| ¥0 | ¥0 |
| Cloudflare Tunnel | ¥0 | ¥0 |
| 自有域名（如 .xyz）| ¥10/年 | ~¥1 |
| 飞书自建应用 | ¥0 | ¥0 |
| **总计** | **¥10** | **¥1** |

复用办公室常开的影刀笔记本 → 不用额外买 NAS / 不用租 VPS，月成本几乎为 0。
