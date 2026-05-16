# SOP · Cloudflare Tunnel Path Routing（cms-api OAuth callback 公网入口）

> **背景**：v2.11 起 cms-api 提供 Shopee OAuth callback 端点 `/api/automation/shopee/oauth-callback`。Shopee 授权流程要求 redirect URL 是**公网可访问**的。Boss 配的 redirect 是 `https://smikie-cms.cc/api/automation/shopee/oauth-callback`，但当前 Cloudflare Tunnel 把整个 `smikie-cms.cc` 流量路由到 Streamlit 容器（CMS V2.3），cms-api 在 N8N stack 里的 8789 端口没有公网入口，所以浏览器跳转后白屏。
>
> **本 SOP 目标**：配 Cloudflare Tunnel **path-based 路由**，让 `smikie-cms.cc/api/automation/*` 走到 N8N stack 的 `cms-api:8789` 容器，其他流量保留给 Streamlit。

## 工作量 · 5 分钟

## Step 1 · 让 CMS Tunnel 容器能连 cms-api（一次性）

CMS V2.3 的 `cms_cloudflared` 容器当前只在 `cms_net` 网络。cms-api 容器在 N8N stack 的 `smikie_n8n_net` 网络。要让 cloudflared 能反代到 cms-api，必须把 cms_cloudflared 也加入跨 stack 网络 `smikie_shared`。

**修改 `deploy/windows/docker-compose.yml` 里 cloudflared 服务的 networks 段**：

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  container_name: cms_cloudflared
  ...
  networks:
    - cms_net
    - smikie_shared   # ← 加这一行
```

并确保 N8N stack 的 cms-api 服务也加入了 smikie_shared（在 `deploy/n8n/docker-compose.yml`，v2.1 起已经加了，确认即可）：

```yaml
cms-api:
  ...
  networks:
    - smikie_n8n_net
    - smikie_shared   # ← v2.1 已加
```

**应用**：
```powershell
# 在 CMS V2.3 安装目录（D:\Smikie-CMS 或类似）跑
cd D:\Smikie-CMS-Installer-vXX
docker compose up -d --force-recreate cloudflared

# 验证 cms_cloudflared 能 ping 到 cms-api
docker exec cms_cloudflared ping -c 2 cms-api
# 期望: 64 bytes from cms-api.smikie_shared (...)
```

## Step 2 · Cloudflare Dashboard 加 Public Hostname

1. 浏览器打开 https://one.dash.cloudflare.com → 登录公司账号
2. 左侧栏 **Networks → Tunnels**
3. 找当前在用的 Tunnel（应该叫 `smikie-cms` 或 CMS V2.3 部署时建的那个），点进去
4. **Public Hostname** 标签 → **Add a public hostname**
5. **填表（关键）**：

| 字段 | 值 | 说明 |
|---|---|---|
| Subdomain | （空） | 用根域名 `smikie-cms.cc` |
| Domain | `smikie-cms.cc` | 下拉选 |
| **Path** | `api/automation/*` | **关键** · 注意不要以 `/` 开头，Cloudflare 自动加；`*` 是 wildcard |
| Type | `HTTP` | cloudflared 内部通信，不要选 HTTPS |
| URL | `cms-api:8789` | 容器名 + 端口 |

6. **【极其重要】** 点 **Save** 后，回到 Public Hostname 列表 → **把刚加的这条拖到最上面** → 让它的匹配优先级**高于** streamlit 那条通配（`* → streamlit:8501`）。否则通配先匹配，请求被 streamlit 截走还是白屏。

7. 验证（30 秒生效）：

```powershell
# 在 Inspiron 上 / 任何机器
curl -i https://smikie-cms.cc/api/automation/shopee/tokens
# 期望返回 JSON: {"refresh_tokens":{"PH":"..."}, "updated_at":"..."}
# 而不是 HTML（Streamlit 默认页）
```

## Step 3 · 验证 OAuth 回调能通

跑一次完整 OAuth：

```powershell
cd D:\Smikie-N8N-Installer-v2.11
.\installer\oauth-7-markets.ps1 -Market TW
```

浏览器跳到 Shopee 授权页 → Confirm → 应该跳到 **v2.11 的友好 HTML 结果页**（✅ TW 授权成功），**不再白屏**。

如果还白屏：
- 检查 Public Hostname 顺序是不是把 path 路由放到了通配之前
- 检查 cms_cloudflared 能不能 ping 通 cms-api：`docker exec cms_cloudflared ping cms-api`
- 看 cms-api 日志有没有收到请求：`docker compose logs cms-api --tail 30 -f`

## 整体路由表（配完后的状态）

| URL | 路由到 | 用途 |
|---|---|---|
| `smikie-cms.cc/api/automation/*` | `cms-api:8789` | Shopee OAuth callback / token query / automation_runs callback |
| `n8n.smikie-cms.cc/*` | `n8n:5678` | N8N UI + webhook |
| `smikie-cms.cc/*`（其他所有） | `streamlit:8501` | CMS V2.3 主站 |

## 备选方案（不推荐）

如果 Boss 不想动 cloudflared 跨网络，也可以走子域名隔离：

- 加 Public Hostname `cms-api.smikie-cms.cc` → `cms-api:8789`
- cms-api 的 `CMS_PUBLIC_BASE` env 改成 `https://cms-api.smikie-cms.cc`
- Shopee Open Platform Console → SmikieShopeeAutoListing → Test Redirect URL Domain 改 `cms-api.smikie-cms.cc`

但这样要改 Shopee 控制台 + .env + 重新走授权，比 path-based 多走几步。Path-based 是首选。

## 相关

- [[reference_shopee_open_platform_v2]]（OAuth 流程详情）
- v2.11 commit `c2b8790`
- 排查：`商品信息管理/deploy/n8n/installer/oauth-7-markets.ps1 -Fallback` 模式可绕开 Cloudflare 路由问题手动完成授权
