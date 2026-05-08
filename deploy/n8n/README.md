# Smikie N8N · Windows 一键安装

> 给 Boss 用的部署包。**不需要懂命令行**，只要跟着 1、2、3 走完即可。

---

## ✅ 安装前

| 项 | 说明 |
|---|---|
| **Docker Desktop** | 必须已安装并启动（图标常驻系统托盘且为绿色）。还没装 → https://www.docker.com/products/docker-desktop/ |
| **Cloudflare 账号** | 需要拿 Tunnel Token（在 Zero Trust → Networks → Tunnels）|
| **磁盘空间** | C 盘或 D 盘留 2 GB 足够 |
| **影刀 / CMS** | 可同机共存；本安装不会动它们 |

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
- Anthropic / OpenAI API Key（跑 LLM 类 workflow 时填）

填好 → 点 **开始安装**。

### 3️⃣ 等 1-3 分钟

安装脚本会自动：
- 检查 Docker Desktop 是否在跑（不在 → 自动启动）
- 拉 n8n + cloudflared 镜像（首次约 200 MB）
- 启动容器
- 导入预装 workflow（jan-extract / shopee-mass-upload）
- 弹窗显示安装结果

完成后可选自动打开浏览器到 `http://localhost:5678` 登录 → 输入刚设的账密。

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
│   ├── install.bat       ← 双击安装入口
│   ├── install.ps1       ← 安装主体
│   ├── uninstall.bat     ← 双击卸载入口
│   └── uninstall.ps1     ← 卸载主体
├── workflows\            ← 预装 workflow（首次启动自动导入）
│   ├── jan-extract-v2.json
│   └── shopee-mass-upload.json
├── data\                 ← 容器数据（首次启动后自动生成；卸载默认保留）
│   └── n8n\              ← N8N 配置 + 凭证 + workflow 数据库
├── docker-compose.yml    ← Docker 编排
├── .env.template         ← 配置模板（不要直接改）
├── .env                  ← 真实配置（install.ps1 自动生成；勿提交 git）
└── README.md             ← 本文件
```

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
