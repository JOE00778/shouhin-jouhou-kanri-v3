# 一元管理系统 V2.3 · NAS Self-hosted 部署

数据 + 应用全本地化，告别 Streamlit Cloud + 任何外部 SaaS。

```
                           ┌─ 公网用户 (国外/国内同事 + 飞书工作台)
                           ↓
                    Cloudflare Edge (300+ POP, 全球)
                           ↓ HTTPS Tunnel
                    cms_cloudflared (Docker)
                           ↓ 内网
                    cms_streamlit (Docker, 8501)
                           ↓
                    cms_postgres (Docker, 5432)
                           ↓
                    NAS 卷 /volume1/docker/cms-v230/postgres
```

---

## 📋 Boss 明天给我的信息（5 + 1 项）

复制下面到飞书 / 邮件回我，一次性能拿全：

```
1. NAS 型号：______________________     (DSM 控制面板 → 信息中心 → 主机名/系统)
2. DSM 版本：______________________     (同上)
3. NAS 内网 IP：__________________      (路由器后台 / DSM 网络)
4. NAS 剩余空间（GB）：____________     (文件总管 / DSM)
5. 套件中心装了哪个：(  ) Docker  (  ) Container Manager  (  ) 都没有
6. 自有域名（Cloudflare 管理的）：______________________   (没有可空，我帮选)
```

---

## 🚀 部署流程（拿到信息后我执行）

### 阶段 1：NAS 上 SSH 准备（10 分钟）

```bash
# 1. SSH 进 NAS
ssh admin@<NAS_IP>

# 2. 创建项目目录
sudo mkdir -p /volume1/docker/cms-v230
sudo chown $(whoami) /volume1/docker/cms-v230
cd /volume1/docker/cms-v230

# 3. 拉代码（跟 GitHub main 分支同步）
git clone https://github.com/JOE00778/CMS-v230.git .
# 或后续更新：git pull origin main

# 4. 复制环境变量文件
cp deploy/nas/.env.example .env
nano .env   # 填入真实密码、Cloudflare token、飞书 credentials
```

### 阶段 2：Cloudflare Tunnel 设置（5 分钟）

1. 浏览器打开 https://one.dash.cloudflare.com → Networks → Tunnels → **Create a tunnel**
2. Tunnel name 填 `cms-nas`
3. **Save tunnel** → 复制 token 字符串（`eyJhxxxxx...`）→ 粘到 `.env` 的 `CLOUDFLARE_TUNNEL_TOKEN`
4. 跳过「install connector」（我们用 Docker 装）
5. **Public Hostname** → Add a public hostname
   - Subdomain：`cms`
   - Domain：选你的域名
   - Type：`HTTP`
   - URL：`streamlit:8501`
6. Save → 域名解析自动配好，1 分钟内生效

### 阶段 3：飞书自建应用配置（见 LARK_SETUP.md）

### 阶段 4：起容器（30 秒）

```bash
cd /volume1/docker/cms-v230/deploy/nas
docker compose up -d

# 看日志确认启动成功
docker compose logs -f streamlit
```

### 阶段 5：导入历史数据（如果之前 Cloud 上有积累）

```bash
# 把 Cloud 上的 SQLite 备份过来（如果 Cloud 容器还在）
# 通过 Streamlit Cloud 的 Manage app → Logs → 用 page 99 重新上传所有 xls

# 或者直接重新上传（推荐，因为 schema 已经 Postgres 化）
# 浏览器打开 https://cms.<your-domain>.com → 用 JO043 / smikie043 登录
# → page 99 → 拖入所有月度 xls
```

---

## 🔄 日常运维

### 更新代码
```bash
cd /volume1/docker/cms-v230
git pull
docker compose up -d --build streamlit
```

### 看日志
```bash
docker compose logs -f streamlit
docker compose logs -f postgres
docker compose logs -f cloudflared
```

### 备份 Postgres（推荐每天 cron）
```bash
docker exec cms_postgres pg_dump -U cms cms | gzip > /volume1/backup/cms-$(date +%Y%m%d).sql.gz
```

### 关闭整个 stack
```bash
docker compose down
```

---

## 🆘 故障排查

| 现象 | 排查 |
|---|---|
| 域名打不开 | `docker compose logs cloudflared` 看 tunnel 是否建立 |
| 登录后白屏 | `docker compose logs streamlit` 看 Python 报错 |
| Postgres 连不上 | `docker exec -it cms_postgres psql -U cms -d cms` 验证 DB 在 |
| 上传 xls 报错 | 看 page 99 的导入汇总，inserted=0 会主动 raise（v2.3.6 起）|

---

## 📐 规格参考

| 资源 | 占用 |
|---|---|
| Postgres 16 镜像 | ~300MB |
| Streamlit 镜像 | ~600MB（含 pandas / openpyxl）|
| Cloudflared | ~50MB |
| **3 容器总 RAM** | ~500-800MB（空载）/ 1-2GB（高峰）|
| 数据库（前 1 年）| ~500MB（按 50w 行 sales_line 估算）|

---

## 🔮 后续

- **阶段 2（NetSuite 自动 cron）**：另写 `deploy/nas/cron-netsuite-fetch.sh`，每天凌晨从 NS 拉数据 → upsert Postgres
- **阶段 3（多副本 / 高可用）**：Postgres → Patroni 集群（暂不必要）
- **监控**：可加 `cms_grafana` + `cms_prometheus` 容器看资源（暂不必要）
