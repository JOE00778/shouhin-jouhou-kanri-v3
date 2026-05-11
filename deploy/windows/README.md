# 一元管理系统 V2.3 · Windows 笔记本部署（影刀友好）

适用：办公室常开的 Windows 笔记本，已经在跑影刀（YingDao）RPA，**绝对不能影响影刀运行**。

> **目标设备**：Dell Inspiron 5405 / AMD Ryzen 5 4500U (6C6T, 2.38 GHz) / 16 GB RAM / 477 GB SSD（剩 226 GB）

## 资源占用承诺（按 16 GB / 6 核）

```
┌─────────────────────────┬───────────┬──────────┐
│ 进程                    │ RAM 上限  │ CPU 上限 │
├─────────────────────────┼───────────┼──────────┤
│ Postgres                │ 1024 MB   │  0.5 核  │
│ Streamlit               │ 2048 MB   │  1.5 核  │
│ Cloudflared             │  256 MB   │ 0.25 核  │
│ pgweb（DB 可视化）      │  256 MB   │ 0.25 核  │
│ Docker Desktop 自身     │  300 MB   │  0.25 核 │
├─────────────────────────┼───────────┼──────────┤
│ CMS 总占用              │ ~3.9 GB   │  ~2.75核 │
├─────────────────────────┼───────────┼──────────┤
│ Windows 11 + 影刀 + 缓冲│ 12 GB+    │  3.25核+ │  ← 16GB / 6核 笔记本剩余
└─────────────────────────┴───────────┴──────────┘
```

通过 `.wslconfig` 全局限制 WSL2 总量 4 GB / 3 核，CMS 永远抢不到比这更多。**影刀进程优先级永远高于 Docker。**

## ⚠️ 前置：BIOS 必须开启 SVM（虚拟化）

Ryzen 5 4500U 出厂 BIOS **可能未开启 AMD-V (SVM)**，Docker WSL2 必需。开启方法：
1. 重启电脑 → 反复按 **F2** 进 BIOS（Dell Inspiron 默认键）
2. 找 **Advanced** → **CPU Configuration** → **SVM Mode** → **Enabled**
3. 保存退出（**F10**）

验证：Windows 任务管理器 → 性能 → CPU → 右下「**虚拟化：已启用**」

---

## 📋 部署清单（80 分钟搞定）

### ① 装 Docker Desktop（10 分钟）

1. 下载：https://www.docker.com/products/docker-desktop/
2. 安装时勾选「**Use WSL 2 instead of Hyper-V**」（必选，否则资源占用大 2-3 倍）
3. 安装完毕重启电脑（影刀重启等会儿没关系）
4. 打开 Docker Desktop → Settings → General：
   - ✅ Start Docker Desktop when you sign in to your computer
   - ✅ Use the WSL 2 based engine
   - ❌ **关掉** "Open Docker Dashboard at startup"（节省资源）

### ② 配 WSL2 资源上限（**关键步骤，影刀友好**）

把 [.wslconfig.example](.wslconfig.example) 复制到 `C:\Users\<你的用户名>\.wslconfig`：

```powershell
# PowerShell 管理员执行
Copy-Item D:\cms-v230\deploy\windows\.wslconfig.example "$env:USERPROFILE\.wslconfig"
notepad "$env:USERPROFILE\.wslconfig"   # 检查内容，按笔记本实际配置调整
wsl --shutdown                          # 让配置生效
```

`.wslconfig` 关键参数（按 8GB / 4 核笔记本默认）：
- `memory=2GB`（WSL2 总内存上限，影刀 + Windows 留 6GB+）
- `processors=2`（WSL2 用 2 核，影刀 + Windows 留 2 核+）
- `autoMemoryReclaim=gradual`（闲置时主动归还内存给影刀）

如果笔记本只有 8GB 还要跑别的，把 `memory=2GB` 改 `1.5GB`，体验会略卡但不影响数据。

### ③ 拉代码 + 配置（10 分钟）

打开 PowerShell（普通用户，不要管理员）：

```powershell
# 创建项目目录（推荐 D 盘，跟 C 盘系统/影刀分开）
New-Item -ItemType Directory -Path D:\cms-v230 -Force
cd D:\cms-v230

# 拉代码
git clone https://github.com/JOE00778/CMS-v230.git .

# 复制环境变量文件
Copy-Item deploy\windows\.env.example .env
notepad .env   # 填入真实密码、Cloudflare token、飞书 credentials
```

### ④ Cloudflare Tunnel 设置（5 分钟，跟 NAS 方案一样）

参见 [../nas/README.md](../nas/README.md) 阶段 2。把拿到的 Tunnel Token 填到 `.env` 的 `CLOUDFLARE_TUNNEL_TOKEN`。

### ⑤ 飞书自建应用配置（10 分钟）

参见 [../nas/LARK_SETUP.md](../nas/LARK_SETUP.md)。

### ⑥ 起容器（30 秒）

```powershell
cd D:\cms-v230\deploy\windows
docker compose up -d

# 看启动日志
docker compose logs -f streamlit
# Ctrl+C 退出日志查看（不会停容器）
```

### ⑦ 验证

```powershell
# 看容器都跑起来了
docker compose ps

# 应该看到 3 个 Up：cms_postgres / cms_streamlit / cms_cloudflared
```

浏览器打开你的 `https://cms.<your-domain>` → 用 JO043 / smikie043 登录 → 进 page 99 上传月度 + 前日 xls。

> 想直接看数据库（所有表、跑 SQL、导出）：在**笔记本本机**浏览器开 `http://localhost:8081`（pgweb，已自动连上 cms 库）。只绑 127.0.0.1，不走公网；远程要看就先远程桌面登进笔记本。

### ⑧ 配置开机自启（5 分钟）

Docker Desktop 已勾「开机自启」（步骤 ① 里设置过）。
docker-compose 的 `restart: unless-stopped` 会让容器随 Docker Desktop 启动。

**额外保险**（防止 Docker Desktop 假死）：在 PowerShell 任务计划新建一个每小时检查的脚本：

```powershell
# C:\cms-v230\watchdog.ps1
$running = docker ps --filter "name=cms_streamlit" --format "{{.Names}}" | Out-String
if (-not $running.Contains("cms_streamlit")) {
    cd D:\cms-v230\deploy\windows
    docker compose up -d
    # 可加飞书机器人通知（用 webhook）
}
```

任务计划 → 创建任务 → 触发器：每小时一次 → 操作：`powershell -File C:\cms-v230\watchdog.ps1`。

---

## 🛡️ 影刀友好配置（重点验证）

每次配完之后，**用 7 天观察影刀有没有变慢**。如果有，按下表调整：

| 影刀症状 | 调小哪个值 | 理由 |
|---|---|---|
| RPA 步骤间延迟变长 | `.wslconfig` `memory` 从 2GB → 1.5GB | 内存抢得太多 |
| 鼠标键盘定位偶尔失灵 | `.wslconfig` `processors` 从 2 → 1 | CPU 抢得太多 |
| 点击元素失败 | docker-compose `cpus` 全部减半 | 重负载干扰 UI 线程 |
| 全无影响 | 保持当前配置 ✅ | 完美 |

观察方式：影刀的执行日志看任务平均时长，对比上线前 vs 上线后 7 天。

---

## 🔄 日常运维

```powershell
# 更新代码
cd D:\cms-v230
git pull
docker compose -f deploy\windows\docker-compose.yml up -d --build streamlit

# 看日志
docker compose -f deploy\windows\docker-compose.yml logs -f --tail 100

# 重启某个服务
docker compose -f deploy\windows\docker-compose.yml restart streamlit

# 完全停掉（影刀不受影响）
docker compose -f deploy\windows\docker-compose.yml down
```

> 首次启用 pgweb（新加的 DB 可视化）：跑一次 `docker compose -f deploy\windows\docker-compose.yml up -d`（把全部服务带起来），或者直接双击 `redeploy.bat`（已含 pgweb 启动步骤）。之后笔记本本机浏览器开 `http://localhost:8081` 就能看库。

---

## 🔎 DB 可视化（pgweb · localhost:8081）

`docker compose up -d` 会顺带起一个 **pgweb** 容器（`sosedoff/pgweb`，限 256 MB / 0.25 核），已用 `DATABASE_URL` 自动连上 `cms` 库。

- 访问：**笔记本本机**浏览器 `http://localhost:8081`（只绑 `127.0.0.1`，**不走公网、不挂 cloudflared**）
- 能干啥：左侧列出所有表 → 点进去看 / 排序 / 筛选行；上方 SQL 编辑器跑任意查询；导出 CSV·JSON；看表结构、索引、约束
- 远程查看：先用 Windows 远程桌面登进笔记本，再开 `localhost:8081`（不要把端口改成 `0.0.0.0`）
- 想再加一层口令：在 `.env` 填 `PGWEB_AUTH_USER` / `PGWEB_AUTH_PASS`，重启 `docker compose up -d pgweb`
- 它和 Streamlit App 是两回事：App 是给业务看的成品页面，pgweb 是给临时 SQL / 看表结构 / 改数据用的

---

## 💾 LS210DC 自动备份（每天凌晨 3 点）

LS210DC 不能跑应用，但是当**Postgres dump 的备份目的地**完美。见 [backup-to-ls210dc.ps1](backup-to-ls210dc.ps1)：

```powershell
# 配置开机自启的 Windows 任务计划
schtasks /Create /TN "CMS Backup to LS210DC" `
  /TR "powershell.exe -File D:\cms-v230\deploy\windows\backup-to-ls210dc.ps1" `
  /SC DAILY /ST 03:00 /F
```

每天凌晨 3 点（影刀通常不在跑）把当天 Postgres dump 上传 LS210DC，gzip 压缩后约几 MB / 天，LS210DC 100GB 装得下 N 年。

---

## 🆘 故障排查

| 现象 | 处理 |
|---|---|
| Docker Desktop 启动失败 | 确认开了 Hyper-V / WSL2（控制面板 → 程序与功能 → Windows 功能） |
| 影刀变卡 | `.wslconfig` 调小 memory/processors，wsl --shutdown |
| 域名打不开 | `docker compose logs cloudflared` 看 tunnel 是否建立 |
| 上传 xls 报 0 行 | 看 page 99 导入汇总，inserted=0 已会主动 raise（v2.3.6 起）|
| 笔记本待机后容器停 | 检查 Docker Desktop Settings → Resources → 「Open Docker Desktop dashboard at startup」勾上 + 别让笔记本休眠 |

---

## 📐 跟 NAS 方案差异

| 项 | NAS 方案 | Windows 笔记本方案 |
|---|---|---|
| 镜像 / 容器 | 一样 | 一样（Linux 容器在 WSL2 跑）|
| docker-compose | [../nas/docker-compose.yml](../nas/docker-compose.yml) | [docker-compose.yml](docker-compose.yml) 加资源限制 |
| schema | [../nas/schema.postgres.sql](../nas/schema.postgres.sql) | 同一份（symlink）|
| Cloudflare Tunnel | 一样 | 一样 |
| 飞书 H5 | 一样 | 一样 |
| 资源管理 | 不限（NAS 专用）| .wslconfig 严控（影刀共存）|
| 备份目的地 | NAS 自己的另一卷 | LS210DC SMB |

代码层 100% 共用，明天配完只是多个落地选项。
