# 飞书集成 · 群机器人 + 自建应用 OpenAPI

> 状态：v1 · 2026-05-09
> 涉及代码：`shared/lark_notify.py` / `shared/lark_openapi.py` / `pages/99 Tab 5`

---

## 一、两套机制并行

| 机制 | 适用 | 配置 |
|---|---|---|
| **群机器人 webhook** | 仅推消息（卡片 / 文本）到指定群 | 1 个群 1 个 webhook URL |
| **自建应用 OpenAPI** | 写表格、读写云文档、双向消息、查用户 | App ID + App Secret + 权限申请 |

CMS 默认两套都装，按场景调用：
- N8N workflow 跑完通知 Boss → 走**群机器人**（轻量、零配置）
- stock_monitor 把改廃报告写飞书电子表格 → 走 **OpenAPI**

---

## 二、Boss 操作清单（一次性，约 15 分钟）

### Part A · 群机器人 Webhook

#### 1. 创建群机器人（每个群独立做一次）

1. 飞书 → 进想接收通知的群 → 群设置 → **群机器人** → **添加自定义机器人**
2. 起名（如「Smikie 自动化」）+ 头像
3. **复制 Webhook 地址** → 形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`

#### 2. 推荐至少建 2 个群

| 群 | 用途 |
|---|---|
| **Smikie 自动化通知** | 默认所有 N8N 任务通知（Shopee 上架、出图等）|
| **Smikie 警报** | 仅 status='error' 的兜底群（出问题才响）|

可选拆分（按 module）：
- 改廃监控群 → `LARK_WEBHOOK_URL_DISCONTINUE`
- NST 发注群 → `LARK_WEBHOOK_URL_NST`

#### 3. 配置到 `.env`

CMS 容器（`商品信息管理/deploy/windows/.env`）和 N8N 容器（`deploy/n8n/.env`）都要加：

```bash
# 默认群（必填，作为兜底）
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/<默认群 hook>

# 可选 · 按业务模块分群
LARK_WEBHOOK_URL_SHOPEE=https://...      # shopee_mass_upload + image_gen
LARK_WEBHOOK_URL_DISCONTINUE=https://...
LARK_WEBHOOK_URL_NST=https://...
LARK_WEBHOOK_URL_ERROR=https://...       # 任何 error 都进这个群
```

改完两端都 `docker compose restart`。

---

### Part B · 自建应用 OpenAPI

#### 1. 创建自建应用

1. 浏览器打开 https://open.feishu.cn/app
2. **创建企业自建应用** → 起名（如「SmikieJapan CMS」）
3. 进应用详情页 → 左侧「**凭证与基础信息**」
4. 复制 **App ID**（`cli_xxx`）和 **App Secret**

#### 2. 申请权限

左侧「**权限管理**」→ 申请以下权限 → 顶部「**版本管理与发布**」→ 创建版本 → 提审：

| 权限 ID | 用途 |
|---|---|
| `sheets:spreadsheet` | 电子表格读写（stock_monitor 改廃报告 / 月度数据备份）|
| `docs:document` | 云文档读写（自动报告写飞书 doc）|
| `im:message:send_as_bot` | 给指定用户/群发卡片（双向交互需要）|
| `contact:user.id:readonly` | 用户基础信息（按 union_id 查邮箱）|

公司管理员审批通过即生效。

#### 3. 把要写的表格 / 文档共享给 App

App 默认不能访问任何表格 / 文档，需要逐个共享：

- 飞书表格右上角 **共享** → 添加协作者 → 输入应用名（如「SmikieJapan CMS」）→ 选「可编辑」

#### 4. 配置到 `.env`

CMS + N8N 两端：

```bash
LARK_APP_ID=cli_xxxxxxxx
LARK_APP_SECRET=xxxxxxxxxxxxxxxx
```

---

## 三、CMS 端集成

### 1. 业务代码调用

#### 群机器人推送（最常用）

```python
from shared.lark_notify import notify_card

notify_card(
    title="Shopee 自动上架完成",
    rows=[("市场", "TW"), ("成功", "12"), ("失败", "0")],
    status="success",                 # info / success / warning / error
    module="shopee_mass_upload",      # 可选，按 module 路由
)
```

#### OpenAPI 写表格（stock_monitor 等）

```python
from shared.lark_openapi import sheet_append_rows

sheet_append_rows(
    spreadsheet_token="<URL 中 /sheets/<token>>",
    sheet_id="<URL 中 ?sheet=xxx>",
    rows=[
        ["2026-05-09", "JAN-490..", "停止销售", "已确认"],
    ],
    column_range="A:D",
)
```

#### OpenAPI 读云文档

```python
from shared.lark_openapi import doc_get_content

content = doc_get_content("<URL 中 /docx/<id>>")
```

### 2. UI 自检 + 测试

在 CMS page 99 → Tab 5「🔔 飞书集成」：

- 看当前配置概览（路由表 + token 健康度）
- 点测试按钮发 3 种消息（默认 / success / error）
- 试 OpenAPI 写表格（填 token + sheet_id 测试）

---

## 四、N8N 端集成

N8N 容器里 `LARK_WEBHOOK_URL` 已经在 [docker-compose.yml](../deploy/n8n/docker-compose.yml) 注入。

所有 workflow 用 `{{ $env.LARK_WEBHOOK_URL }}` 引用。  
要按 module 路由，方法 1 是改 workflow JSON 加 if 节点判断 module 选不同 env 变量；
方法 2 是 N8N 容器只用 1 个默认 webhook，让 CMS 端的 `lark_notify.py` 做路由（推荐，避免改 workflow）。

---

## 五、调试 / 排查

| 现象 | 排查 |
|---|---|
| `is_configured()` 返回 False | 检查 .env 是否真的写了 + 容器是否重启 |
| webhook 返回 200 但群里没消息 | 检查 webhook URL 是否被群设置「移除机器人」过 |
| OpenAPI 报 `99991663 access denied` | App 没申请对应权限 / 审核没通过 |
| OpenAPI 报 `1308050 sheet not found` | 表格没共享给 App |
| OpenAPI 报 `tenant_access_token expired` | 不应该出现（自动刷新），除非系统时钟不对 |

`shared/lark_openapi.health_check()` 自检方法：

```python
from shared.lark_openapi import health_check
print(health_check())
# {'configured': True, 'app_id': 'cli_a7d2...', 'token_ok': True, 'token_expires_in': 7180, 'error': ''}
```

---

## 六、与之前 stock_monitor 的关系

stock_monitor（改廃监控容器）已经在用同一对 `LARK_APP_ID/SECRET` 写飞书表格 —
本次 CMS 端集成是**复用同一个 App**，不需要新注册。两端分别从 `.env` 读取，互不干扰。
