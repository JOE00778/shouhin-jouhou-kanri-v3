# Smikie N8N · Windows All-in-One 安装包

> 给 Boss 用的部署包。**全自动**：双击 → 自动启 WSL2 → 自动装 Docker Desktop →
> 自动起 N8N + 改廃监控容器 → 自动配 cron 任务。**不需要命令行**。

包含的服务：

| 服务 | 用途 | 跑法 |
|---|---|---|
| **N8N** | 自动化工作流引擎（全套大脑） | 7×24 后台 |
| **cloudflared** | 公网入口 `https://n8n.smikie-cms.cc` | 7×24 后台 |
| **stock-monitor** | 改廃监控（每月 1 日 02:00 自动扫供应商） | 月度 cron |

预装的 N8N workflow：

| Workflow | 触发 | 状态 |
|---|---|---|
| Shopee JAN 提取（jan-extract-v2） | 手动 | 升级到 claude-opus-4-7 |
| Shopee 自动上架 webhook | CMS 触发 | MVP scaffold（待 Shopee API 凭证）|
| Shopee 自动上架 cron | 每 4 小时 | 7×24 自动扫 CMS 队列 |
| 改廃监控月度 | 每月 1 日 02:00 JST | 生产可用 |

---

## ✅ 安装前

| 项 | 说明 |
|---|---|
| **Windows 版本** | Windows 10 build 19041+ 或 Windows 11（安装包会自动检查） |
| **CPU 虚拟化** | BIOS 中开启 SVM (AMD) / VT-x (Intel) — 装包会检测并提示 |
| **Cloudflare 账号** | 提前拿 Tunnel Token（Zero Trust → Networks → Tunnels → Connectors） |
| **磁盘空间** | C 盘留 4 GB（含 Docker Desktop 600 MB + 镜像 800 MB + 数据） |
| **影刀 / CMS** | 可同机共存；本安装不会动它们 |
| **Docker Desktop** | **不需要预装**，安装包会自动下载并装好 |
| **WSL2** | **不需要预装**，安装包会自动启用 |

---

## 🚀 三步安装

### 1️⃣ 解压

把 `Smikie-N8N-Installer-vX.X.zip` 解压到任意位置，例如：

```
C:\Smikie-N8N\
```

### 2️⃣ 双击运行

进入 `installer\` 文件夹，**双击** `install.bat`：

- Windows 弹出 UAC（用户账户控制）→ 点 **是**
- 黑色命令窗口一闪而过，跟着弹出图形配置窗口
- 填以下 4 个**必填**项：

| 字段 | 填什么 |
|---|---|
| 公网域名 | `n8n.smikie-cms.cc`（已默认，无需改）|
| CF Tunnel Token | Cloudflare → Zero Trust → Networks → Tunnels → 选 Tunnel → Connectors 复制（很长，200+ 字符）|
| N8N 管理员账号 | `admin`（默认）|
| N8N 管理员密码 | 自己设一个（记住！）|

可选 6 项（先空着也能跑）：

- 飞书群机器人 Webhook（部署后想要群通知再填）
- Shopee Partner ID / Partner Key（跑 Shopee 自动上架时填）
- DeepSeek / 火山方舟 API Key（跑 LLM / 图像类 workflow 时填，国产本地化合规）

填好 → 点 **开始安装**。

### 3️⃣ 等 5-15 分钟

安装脚本自动跑以下 4 步（无需手工干预）：

1. **前置依赖检查**：Windows 版本 / CPU 虚拟化 / WSL2 / Docker
2. **WSL2 启用**（如果没启用）：可能要求重启电脑一次（首次安装通常会）
3. **Docker Desktop 自动安装**（如果未装）：约 600 MB 下载 + 5 分钟
4. **N8N + 改廃监控 + cloudflared 三容器启动**：约 1 分钟

如果中间需要重启系统，重启后再次双击 install.bat 即可继续。

完成后弹窗 → 自动开浏览器 → 登录账密就是表单里你设的那个。

---

## 🌐 公网入口配置（一次性，10 分钟）

> **如果你只想本机访问 `http://localhost:5678` 跳过本节。**

让 `https://n8n.smikie-cms.cc` 公网可达需要在 Cloudflare 加路由：

1. 浏览器打开 https://one.dash.cloudflare.com
2. **Networks → Tunnels** → 选你的 Tunnel（CMS 在用的那个，复用即可）
3. **Public Hostname** 标签 → **Add a public hostname**
4. 填：

| 字段 | 值 |
|---|---|
| Subdomain | `n8n` |
| Domain | `smikie-cms.cc` |
| Type | `HTTP` |
| URL | `n8n:5678` |

5. **Save** → 几秒后 `https://n8n.smikie-cms.cc` 就能访问

---

## 🔐 加层守门：CF Access 邮箱白名单（可选，强烈推荐）

跟 CMS 一样，把 N8N 也限制为公司邮箱才能访问：

1. Cloudflare Zero Trust → **Access → Applications → Add an application → Self-hosted**
2. Application domain: `n8n.smikie-cms.cc`
3. Policy: **Email Domain** = `mitsukin.info`
4. Save

之后访问 `https://n8n.smikie-cms.cc` 会先弹 CF 邮箱验证码 → 输公司邮箱 → 收码 → 进入 → 再 N8N BasicAuth。**双层守门**。

---

## 📂 文件 / 目录说明

```
Smikie-N8N\
├── installer\
│   ├── install.bat              ← 双击安装入口
│   ├── install.ps1              ← N8N 部署主体
│   ├── check-prerequisites.ps1  ← 前置依赖检查
│   ├── enable-wsl.ps1           ← WSL2 启用
│   ├── install-docker.ps1       ← Docker Desktop 自动安装
│   ├── uninstall.bat            ← 双击卸载入口
│   └── uninstall.ps1            ← 卸载主体
├── workflows\                   ← 预装 workflow（首次启动自动导入）
│   ├── jan-extract-v2.json
│   ├── shopee-mass-upload.json       ← CMS webhook 触发
│   ├── shopee-mass-upload-cron.json  ← 每 4 小时 7×24 cron
│   └── stock-monitor-monthly.json    ← 改廃监控月度 cron
├── stock_monitor\               ← 改廃监控容器源码
│   ├── Dockerfile
│   ├── webhook_server.py
│   ├── scripts\                 ← scraper + check_products + notify_lark
│   ├── cookies\                 ← Boss 放供应商登录 cookie（自填）
│   ├── state\                   ← 上次扫描状态（运行时生成）
│   └── reports\                 ← 月度报告（运行时生成）
├── data\                        ← 容器数据（首次启动后自动生成）
│   ├── n8n\                     ← N8N 配置 + 凭证 + workflow 数据库
│   └── files\                   ← Boss 放 item_master_bilingual.csv 这里
├── docker-compose.yml           ← Docker 编排（n8n + cloudflared + stock-monitor）
├── .env.template                ← 配置模板
├── .env                         ← 真实配置（install.ps1 自动生成；勿提交 git）
└── README.md                    ← 本文件
```

---

## 📊 改廃监控 数据准备（一次性）

stock-monitor 容器需要两份外部数据，安装包不内置（属于 Boss 业务数据）：

### A. JAN 列表 CSV

从 CMS 导出 `item_master_bilingual.csv`（含 JAN + 日中双语商品名）→ 放到：

```
C:\Smikie-N8N\data\files\item_master_bilingual.csv
```

> CMS page 99 数据导入与设置 → 导出工具中提供（如未提供，可从 `item_master` 表 SQL 导出）

### B. 供应商登录 cookies（如果抓取的网站需要登录）

如果 stock_monitor 监控的供应商网站需要登录（如 NEW WIND 经销商专区），把
浏览器导出的 cookies 文件放到：

```
C:\Smikie-N8N\stock_monitor\cookies\
```

具体文件名格式见 [scripts/scraper.py](stock_monitor/scripts/scraper.py) 顶部说明。

放好后跑：`docker compose restart stock-monitor`

### C. 飞书 OpenAPI（写飞书表格）

stock-monitor 把月度扫描结果写到飞书表格，需要 LARK_APP_ID + LARK_APP_SECRET（在
飞书开发者后台 → 自建应用 → 凭证与基础信息）。已经在 install.ps1 表单里收集，
也可以装完后编辑 `.env` 补上后 `docker compose restart stock-monitor`。

---

## ⏰ 7×24 cron 任务清单

安装包预装两个长期跑的定时任务：

| Workflow | Cron 表达式 | 频率 | 用途 |
|---|---|---|---|
| Shopee 自动上架 cron | `0 */4 * * *` | 每 4 小时 | 拉 CMS 队列里 pending 的上架任务 → Shopee API → 飞书 |
| 改廃监控月度 | `0 2 1 * *` | 每月 1 日 02:00 JST | 扫供应商 → 找停产信号 → 飞书 |

> 默认 `active: false`（防止首次启动就乱跑）。Boss 在 N8N UI 里点开 workflow → 右上角 toggle "Active" 才生效。改 cron 表达式直接在 UI 里编辑 Schedule Trigger 节点。

---

## 🛠️ 常用操作

### 看日志（出问题排查用）

```powershell
cd C:\Smikie-N8N
docker compose logs -f n8n
# Ctrl+C 退出
```

### 改配置后重启

```powershell
cd C:\Smikie-N8N
notepad .env       # 改完保存
docker compose restart
```

### 升级 n8n 到最新版

```powershell
cd C:\Smikie-N8N
docker compose pull
docker compose up -d
```

### 卸载

双击 `installer\uninstall.bat` → 弹窗确认 → 完成。
（默认保留 `data\` 目录里的 workflow 数据；要彻底清理，卸载后手动删除该目录。）

---

## ❓ 常见问题

| 现象 | 解决 |
|---|---|
| 双击 install.bat 没反应 | 鼠标右键 → "以管理员身份运行" |
| 安装到 50% 卡住 | Docker Desktop 没在跑。任务栏看图标颜色，不是绿色就先启动 |
| `https://n8n.smikie-cms.cc` 502 | Cloudflare Public Hostname 没配 / 配错。回到上面【公网入口配置】 |
| 浏览器打开后空白 | 等 30 秒（n8n 首次启动需要初始化）。还不行 → `docker compose logs n8n` 看错误 |
| 忘了 N8N 密码 | 编辑 `.env` 改 `N8N_BASIC_AUTH_PASSWORD` 后 `docker compose restart` |
| 想换域名 | 编辑 `.env` 改 `N8N_HOST` 后 `docker compose restart`（Cloudflare 也要同步改）|
| Docker 资源不够 | Docker Desktop → Settings → Resources → 调高 Memory；本 stack 上限 1.25GB |

---

## 🤝 与 CMS 的协同

CMS（一元管理系统）会主动调本 N8N 来跑 Shopee 自动上架等任务：

```
CMS page 30 上传 XLSX
    ↓ (POST https://n8n.smikie-cms.cc/webhook/shopee-mass-upload)
N8N workflow 跑
    ↓ (POST CMS_CALLBACK_URL 报告状态)
CMS 显示进度 + 结果
    ↓
飞书群机器人通知 Boss
```

完整集成文档：[../../docs/automation-architecture.md](../../docs/automation-architecture.md)

---

## 💰 资源占用 + 月度成本

| 项 | 占用 |
|---|---|
| n8n 容器 | 1 GB RAM / 0.75 CPU 上限 |
| cloudflared 容器 | 256 MB RAM / 0.25 CPU 上限 |
| 磁盘 | 镜像 ~700 MB + 数据增长缓慢 |
| Cloudflare Tunnel | 免费 |
| **月费** | **¥0**（域名 smikie-cms.cc 已在 CMS 算过）|
