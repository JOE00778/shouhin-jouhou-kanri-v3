# 明早 5 步 · Shopee N8N v2.11 完工清单

> 2026-05-17 凌晨 Claudex 准备的 Boss 早起一开 Inspiron 就能直接跑的索引页。
> 今晚完成 OAuth 链路打通（PH 已通），剩下 6 国 + workflow E2E。

---

## 当前状态 (2026-05-17 01:45)

✅ PH refresh_token 落库 (`shopee_tokens.json`)
✅ v2.10 + v2.11 代码 commit `c2b8790` 推送
✅ v2.11 installer 包打好（两个版本）
   - `dist/Smikie-N8N-Installer-v2.11.zip`（**Test default** — Boss 立刻用这个）
   - `dist/Smikie-N8N-Installer-v2.11-live.zip`（Live default — 7 国 token 拿完后切这个）
✅ 4 份 SOP 文档已落盘
✅ memory 已更新（2 个新文件 + index）

---

## Boss 明早 5 步

| # | 步骤 | 工作量 | SOP |
|---|---|---|---|
| **A** | Cloudflare Tunnel 加 path 路由 `smikie-cms.cc/api/automation/*` → `cms-api:8789` | 5 min | [SOP-cloudflare-path-routing.md](workflows/SOP-cloudflare-path-routing.md) |
| **B** | Shopee Open Platform Test Account-Sandbox v2 → Add Shop 加 6 国（TW MY SG TH VN ID） | 3 min | [SOP-shopee-test-accounts.md](workflows/SOP-shopee-test-accounts.md) |
| **C** | 卸 v1.5 → 装 v2.11 → `docker compose up -d --force-recreate cms-api` | 5 min | `dist/Smikie-N8N-Installer-v2.11.zip` 解压 → `installer/install.bat` |
| **D** | 7 国 OAuth 跑 `oauth-7-markets.ps1` 一国一个（PH 已通 跳过） | 6 min | `installer/oauth-7-markets.ps1 -Market TW` 循环 |
| **E** | N8N B1-B5 workflow 端到端验证 + 看飞书绿卡片 | 10 min | [SOP-n8n-b1-b5-e2e-test.md](workflows/SOP-n8n-b1-b5-e2e-test.md) |

**总时间预估 30 分钟**。每步都有独立 SOP，跑卡了点对应 SOP 链接看。

---

## 5 个根因避坑（今晚刚踩过）

| # | 坑 | 怎么避免 |
|---|---|---|
| 1 | partner_key 'shpk' 是 key 本体不是 UI prefix | v2.11 已修，不要再 strip |
| 2 | SHOPEE_API_BASE 必须是 `openplatform.*` 不是 `partner.*` | v2.11 .env.template 默认正确，**Test 用 sandbox.test-stable / Live 用 openplatform.shopee.sg** |
| 3 | `docker compose restart` 不重读 .env | **改完 .env 用 `docker compose up -d --force-recreate <service>` 不用 restart** |
| 4 | CB Merchant 授权传 main_account_id 不是 shop_id | v2.11 callback 已支持二选一 |
| 5 | Cloudflare Tunnel 默认把 smikie-cms.cc 全部路由到 Streamlit | 明早 Step A 必做 |

---

## 失败兜底

如果 Step E 跑完 B3.5 类目命中 ❌：
- 这是 P0 凭证「cat-* → Shopee 7 国 category_id 表」还没补的预期失败（T-314 任务文件里 Boss 待回填的 3 件凭证 P0）
- workflow 仍能跑通走 fallback，XLSX category_id 列空，飞书卡片显示「类目自动判定：⏸ STUB」
- 这种情况 T-314 仍可标 `done`，cat-* 表是 follow-up task

如果浏览器跳转 OAuth 还白屏：
- Step A 路由没配好，跑 `oauth-7-markets.ps1 -Market TW -Fallback` 手动粘贴 callback URL 也能完成

如果 cms-api 容器起不来：
- `docker compose logs cms-api --tail 30`
- 9 成是 `POSTGRES_PASSWORD` 跟 CMS V2.3 stack 不一致 → 看 `D:\Smikie-CMS-Installer\.env` 的 POSTGRES_PASSWORD 抄过来

---

## T-314 验收 DOD

跑完 Step E 后，T-314 任务文件 `.tasks/doing/T-314-shopee-n8n-v2-pilot.md` 改 `mode: reviewer-needed → done`，填：
- 实际耗时（5 月 16 + 17 两段拼起来）
- 关键 commit：`c2b8790`（v2.10+v2.11 5 根因修复）
- 测试 evidence：飞书绿卡片截图 + N8N Executions 18 节点 ≥ 14 ✅ 截图
- 复盘：本文件 5 个根因清单可以直接抄

---

## 索引

- 任务文件：[.tasks/doing/T-314-shopee-n8n-v2-pilot.md](../../.tasks/doing/T-314-shopee-n8n-v2-pilot.md)
- 主 README：[README.md](README.md) · [README-shopee.md](workflows/README-shopee.md)
- 设计文档：5 月 16 日的 `docker-compose.yml` + `cms_api/app.py` 改动全在 commit `c2b8790`
- Memory 速查：`~/.claude/projects/-Users-joe-CC/memory/reference_shopee_open_platform_v2.md`
