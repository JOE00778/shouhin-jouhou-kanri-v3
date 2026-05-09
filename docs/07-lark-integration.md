# 飞书集成 · CMS 自建应用 + 机器人能力（一应用全包）

> 状态：v2 · 2026-05-09 · 复用 stock_monitor 已有 App
> 涉及代码：`shared/lark_notify.py` / `shared/lark_openapi.py` / `pages/99 Tab 5`

---

## 📌 设计原则

**一个自建应用 + 启用机器人能力 = 全部搞定**：
- 主动推消息（卡片 / 文本，到群 / 单人）
- 写飞书表格 / 云文档（OpenAPI）
- 接收用户消息（双向交互，留作扩展）

**不再用群机器人 webhook**（除非作为 fallback）。原因：
- 群机器人 webhook 只能推消息，功能单一
- 自建应用 + 机器人能力 = 上面所有 + webhook 能做的一切
- 一份凭证（LARK_APP_ID/SECRET）管所有事

> 与 `stock_monitor` 复用同一个 App。stock_monitor 已经在用 App 写飞书表格，
> CMS 这次只是给同一个 App 启用机器人能力。

---

## 🚀 Boss 操作清单（约 10 分钟）

### Step 1 · 找到现有 App（或新建）

打开 https://open.feishu.cn/app

| 情况 | 操作 |
|---|---|
| **stock_monitor 已经在用某个 App** | 进那个 App，复用同一个（推荐）|
| **从零开始** | 创建企业自建应用，名 `SmikieJapan CMS` |

复制 **App ID** + **App Secret**（左侧「凭证与基础信息」），写到 `.env`：

```bash
LARK_APP_ID=cli_xxxxxxxx
LARK_APP_SECRET=xxxxxxxxxxxxxxxx
```

CMS 容器（`商品信息管理/deploy/windows/.env`）和 N8N 容器（`deploy/n8n/.env`）都要写。

### Step 2 · 启用机器人能力（这次的关键）

**左侧 → 应用功能 → 机器人 → 启用**

启用后这个 App 就能：
- 主动给群 / 用户发消息（需配合 `im:message:send_as_bot` 权限）
- 接收用户 @机器人 的消息（事件订阅，留扩展）

### Step 3 · 申请权限

**左侧 → 权限管理** → 勾以下 7 项 → 顶部「**版本管理与发布**」→ 创建版本 → 提审

| 权限 | 用途 |
|---|---|
| `im:message:send_as_bot` ⭐ | 主动给群 / 用户发消息（核心）|
| `im:message` | 接收消息（双向交互，可后期）|
| `im:chat` | 列出 / 搜索机器人加入的群 |
| `im:chat.members:read` | 读群成员 |
| `sheets:spreadsheet` | 写飞书表格（stock_monitor / 月度报告）|
| `docs:document` | 写云文档（自动报告）|
| `contact:user.id:readonly` | 按 union_id 查用户 |

管理员审批 → 通过后生效。

### Step 4 · 把机器人加进群

机器人能发消息的前提：在那个群里。两种加法：

**方法 1**：群设置 → 群机器人 → 添加机器人 → 搜索应用名 → 添加

**方法 2**：群里直接 `@机器人名字` → 系统自动加入

推荐至少加进 2 个群：
- 「Smikie 自动化通知」（默认群）
- 「Smikie 警报」（status='error' 兜底群）

### Step 5 · 拉 chat_id 写到 .env

打开 CMS page 99 → Tab 5「🔔 飞书集成」→ 点「**📋 拉取群列表**」

会列出机器人加入的所有群 + chat_id（形如 `oc_xxxxxxxxxxxxx`）。复制需要的 chat_id 写到 `.env`：

```bash
# 默认目标群（必填）
LARK_DEFAULT_CHAT_ID=oc_xxxxxxxxxxxxx

# 可选 · 按业务模块路由（命中则覆盖默认）
LARK_CHAT_ROUTES={"shopee_mass_upload": "oc_yyy", "discontinue_confirm": "oc_zzz", "_error": "oc_aaa"}
```

### Step 6 · 把要写的飞书表格 / 文档共享给 App

OpenAPI 操作表格 / 文档的前提：表格 / 文档**已共享给这个 App**。

- 飞书表格右上角 → 共享 → 添加协作者 → 输入应用名（如 SmikieJapan CMS）→ 选「可编辑」
- 同上对每个要操作的飞书云文档执行一次

完成后回 page 99 Tab 5 点测试按钮验证全套。

---

## 🔧 业务代码调用

### 推消息（最常用）

```python
from shared.lark_notify import notify_card

notify_card(
    title="Shopee 自动上架完成",
    rows=[("市场", "TW"), ("成功", "12")],
    status="success",                  # info / success / warning / error
    module="shopee_mass_upload",       # 可选 → 路由到对应 chat_id
)
```

`shared/lark_notify.py` 自动按以下优先级选目标：

```
chat_id 参数 (手动)
   > webhook_url 参数 (手动)
   > LARK_CHAT_ROUTES[module] (Bot 模式)
   > LARK_DEFAULT_CHAT_ID (Bot 模式)
   > LARK_WEBHOOK_ROUTES[module] (webhook fallback)
   > LARK_WEBHOOK_URL (webhook fallback)
```

### 写飞书表格

```python
from shared.lark_openapi import sheet_append_rows

sheet_append_rows(
    spreadsheet_token="<URL /sheets/<token>>",
    sheet_id="<URL ?sheet=xxx>",
    rows=[["2026-05-09", "JAN-490..", "停止销售"]],
    column_range="A:C",
)
```

### 拉机器人加入的群

```python
from shared.lark_openapi import list_chats
chats = list_chats()
for c in chats:
    print(c["chat_id"], c["name"])
```

### 给单个用户发消息

```python
from shared.lark_openapi import im_send_card

im_send_card(
    receive_id="lixin@mitsukin.info",
    receive_id_type="email",         # union_id / open_id / chat_id / user_id / email
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
| 写表格 `1308050 sheet not found` | 表格没共享给 App |
| 写表格 `1308000 forbidden` | 共享时只给了「可阅读」，要给「可编辑」 |

`shared/lark_openapi.health_check()`：

```python
from shared.lark_openapi import health_check
print(health_check())
# {'configured': True, 'app_id': 'cli_a7d2...', 'token_ok': True, 'token_expires_in': 7180}
```

---

## 📁 与 stock_monitor 的关系

| 项 | stock_monitor | CMS |
|---|---|---|
| App ID/Secret | 同一对（环境变量复用）| 同一对 |
| 启用「机器人」 | （之前没用） | ✅ 这次启用 |
| 用 OpenAPI 干啥 | 写飞书表格（月度报告） | 写表格 + 推消息 + 写文档 |

stock_monitor 不需要任何代码改动，启用机器人能力 + 加权限对它无影响。

---

## 🧭 双向交互（@机器人触发任务）· 后期扩展

启用机器人能力后，将来可以做：

- 群里 `@机器人 出图 SKU=4901085196533` → 触发 image_gen workflow
- 群里 `@机器人 改廃确认 第 3 行` → 触发 discontinue_confirm

实现要点（暂不做，仅留位）：
1. 飞书后台 → 事件订阅 → 添加 `im.message.receive_v1` 事件
2. 配 Webhook 接收地址（指向 N8N webhook 节点 / CMS FastAPI sidecar）
3. N8N workflow 解析 @机器人 消息 → dispatch 到对应业务

需要时再开。
