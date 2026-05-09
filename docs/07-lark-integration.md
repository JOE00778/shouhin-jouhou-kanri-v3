# 飞书集成 · 机器人通知（CMS 自建应用 + 机器人能力）

> 状态：v3 · 2026-05-09 · **机器人仅用于通知**
> 涉及代码：`shared/lark_notify.py` / `shared/lark_openapi.py` / `pages/99 Tab 5`

---

## 📌 设计原则

**机器人仅用于通知** — 推卡片消息到群 / 单人。
不做飞书表格 / 文档操作（`stock_monitor` 仍保留 sheets API 单独使用，CMS 端不依赖）。

复用 `stock_monitor` 已有 App，仅启用「机器人」能力 + 加 3 项核心权限。

---

## 🚀 Boss 操作清单（约 8 分钟）

### Step 1 · 找到现有 App（或新建）

打开 https://open.feishu.cn/app

| 情况 | 操作 |
|---|---|
| **stock_monitor 已经在用某个 App** | 进那个 App，复用同一个 ⭐ |
| **从零开始** | 创建企业自建应用，名 `SmikieJapan CMS` |

复制 **App ID** + **App Secret**（左侧「凭证与基础信息」），写到 `.env`：

```bash
LARK_APP_ID=cli_xxxxxxxx
LARK_APP_SECRET=xxxxxxxxxxxxxxxx
```

CMS 容器（`商品信息管理/deploy/windows/.env`）和 N8N 容器（`deploy/n8n/.env`）都要写。

### Step 2 · 启用机器人能力 ⭐

**左侧 → 应用功能 → 机器人 → 启用**

启用后这个 App 才能给群 / 用户发消息。

### Step 3 · 申请权限（仅 3 项）

**左侧 → 权限管理** → 勾以下 3 项 → 顶部「**版本管理与发布**」→ 创建版本 → 提审

| 权限 | 用途 |
|---|---|
| `im:message:send_as_bot` ⭐ | 给群 / 用户发卡片消息（核心）|
| `im:chat` | 列出机器人加入的群（拉 chat_id 给 UI 用） |
| `im:chat.members:read` | 读群成员（可选）|

> 不需要 `sheets:spreadsheet` / `docs:document` / `contact` 等权限。
> stock_monitor 用的写表格权限是它独立申请的，本次不动。

管理员审批 → 通过后生效。

### Step 4 · 把机器人加进群

机器人能发消息的前提：在那个群里。两种加法：

**方法 1**：群设置 → 群机器人 → 添加机器人 → 搜索应用名 → 添加

**方法 2**：群里直接 `@机器人名字` → 系统自动加入

推荐至少加进 2 个群：
- 「Smikie 自动化通知」（默认群，所有 N8N 任务通知）
- 「Smikie 警报」（status='error' 兜底群）

### Step 5 · 拉 chat_id

打开 CMS page 99 → Tab 5「🔔 飞书集成」→ 点「**📋 拉取群列表**」

会列出机器人加入的所有群 + chat_id（形如 `oc_xxxxxxxxxxxxx`）。复制需要的 chat_id 写到 `.env`：

```bash
# 默认目标群（必填）
LARK_DEFAULT_CHAT_ID=oc_xxxxxxxxxxxxx

# 可选 · 按业务模块路由（命中则覆盖默认）
LARK_CHAT_ROUTES={"shopee_mass_upload": "oc_yyy", "discontinue_confirm": "oc_zzz", "_error": "oc_aaa"}
```

### Step 6 · 验证

回 page 99 Tab 5 → 点「🚀 发测试卡片」→ 飞书群里看到卡片就 OK。

---

## 🔧 业务代码调用

### 推消息（唯一用法）

```python
from shared.lark_notify import notify_card

notify_card(
    title="Shopee 自动上架完成",
    rows=[("市场", "TW"), ("成功", "12")],
    status="success",                  # info / success / warning / error
    module="shopee_mass_upload",       # 可选 → 按 module 路由到对应 chat_id
)
```

`shared/lark_notify.py` 自动按以下优先级选目标：

```
chat_id 参数 (手动) > LARK_CHAT_ROUTES[module] > LARK_DEFAULT_CHAT_ID
> webhook fallback (LARK_WEBHOOK_URL_xxx / LARK_WEBHOOK_URL)
```

如果 Bot 凭证 / chat_id 都没配，会自动降级到群机器人 webhook（兼容旧配置）；
两套都没配则静默返回 False（不阻塞业务流）。

### 给单个用户发消息

```python
from shared.lark_openapi import im_send_card

im_send_card(
    receive_id="lixin@mitsukin.info",
    receive_id_type="email",
    card={...},
)
```

---

## 🆘 排查

| 现象 | 原因 / 对策 |
|---|---|
| Tab 5 显示「Token 拿不到」 | LARK_APP_ID / SECRET 写错；容器没重启 |
| 拉群列表 `99991663 access denied` | 缺 `im:chat` 权限 / 版本没发布 |
| 发消息 `99991663 access denied` | 缺 `im:message:send_as_bot` 权限 |
| 发消息 `230001 chat not found` | chat_id 写错 / 机器人不在群里 |
| 发消息 `230002 not in chat` | 机器人没加进群（去群 @机器人 / 群设置加） |

`shared/lark_openapi.health_check()` 自检：

```python
from shared.lark_openapi import health_check
print(health_check())
# {'configured': True, 'app_id': 'cli_a7d2...', 'token_ok': True, 'token_expires_in': 7180}
```

---

## 📁 与 stock_monitor 的关系

| 项 | stock_monitor | CMS（本次） |
|---|---|---|
| App ID/Secret | 同一对（环境变量复用）| 同一对 |
| 机器人能力 | 不需要 | ✅ 启用 |
| 用 OpenAPI 干啥 | 写飞书表格（月度改廃报告）| **仅推消息** |
| 需要的权限 | sheets:spreadsheet | im:message:send_as_bot + im:chat |

两套权限并存于同一个 App，互不干扰。stock_monitor 不需要任何代码改动。

---

## 🧭 后期扩展（暂不做，留位）

| 扩展 | 触发条件 | 需补的权限 |
|---|---|---|
| 写飞书表格 | 业务需要把 CMS 数据写到表格分享 | `sheets:spreadsheet` |
| 写云文档 | 月度自动报告写飞书 doc | `docs:document` |
| @机器人触发 N8N | 群里 @机器人 → 跑指定任务 | `im:message` + 事件订阅 webhook |
| 按 union_id 查用户 | 给指定 Boss 单聊 | `contact:user.id:readonly` |

代码（`shared/lark_openapi.py`）已经实现这些 API，需要时申请权限即可启用。
