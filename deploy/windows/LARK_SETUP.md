# 飞书 H5 应用配置（Boss 在飞书开放平台跑 1 次，全员永久受益）

目的：在飞书工作台加一个「一元管理系统V2.3」入口，团队成员点开 → 飞书 SSO → 自动登录 CMS（不用记 URL，不用输账号密码）。

前置：CMS 已经在 Windows 笔记本上跑起来，可通过 https://cms.<your-domain>（现用 `smikie-cms.cc`）公网访问（见 [README.md](README.md) 部署清单 ①-⑦）。

---

## 一. 飞书开放平台创建自建应用（10 分钟）

1. 浏览器打开 https://open.feishu.cn/app
2. **创建企业自建应用**
   - 应用名称：`一元管理系统V2.3`
   - 应用描述：`SmikieJapan 综合商品分析与运营工具集`
   - 应用图标：上传 logo（可选，飞书也可后改）
3. 创建后进入应用详情页 → **凭证与基础信息**：
   - 复制 **App ID**（`cli_xxxxxxxxxxxxxxxx`）
   - 复制 **App Secret**（`xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`）

把这两个值填到 NAS 的 `.env`：

```bash
LARK_APP_ID=cli_xxxxxxxxxxxxxxxx
LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 二. 配置「网页应用」入口（5 分钟）

1. 应用详情页左侧 → **添加应用能力** → **网页**
2. 启用网页应用
3. **桌面端主页 / 移动端主页**：填入 CMS 公网地址，例如：
   ```
   https://cms.smikie.xx
   ```
4. **重定向 URL（OAuth 回调）**：跟桌面端主页同一域名 + `/auth/lark/callback` 后缀（实际 streamlit 里我们用 query 参数处理 code，所以**直接填主页 URL 即可**）：
   ```
   https://cms.smikie.xx
   ```
5. 把同样的值填到 NAS 的 `.env`：
   ```bash
   LARK_REDIRECT_URI=https://cms.smikie.xx
   ```

---

## 三. 申请权限范围（必填，3 分钟）

应用详情页 → **权限管理** → 申请下列权限：

| 权限 | 用途 |
|---|---|
| `contact:user.email:readonly` | 读用户邮箱（用于判断管理员）|
| `contact:user.base:readonly` | 读用户基本信息（姓名/工号）|

**保存** → 等待企业管理员审批（如果你自己是管理员，秒过）。

---

## 四. 测试模式 → 发布（5 分钟）

1. 应用详情页右上 → **版本管理与发布** → **创建版本**
2. 版本号填 `1.0.0`，更新说明随便
3. **可见范围**：
   - 测试期：选「指定成员」+ 加你自己（先验证）
   - 验证 OK 后改「所有员工」（团队全员可见）
4. 提交审核（自建应用不需要飞书审核，企业管理员自审 → 通过即上架到工作台）

---

## 五. （可选但强烈推荐）配置管理员名单

`.env` 里加一行 ADMIN_LARK_EMAILS（逗号分隔的飞书邮箱）：

```bash
ADMIN_LARK_EMAILS=lixin@mitsukin.info,boss2@mitsukin.info
```

效果：
- 名单里的邮箱通过飞书 SSO 进 CMS → 自动 admin 角色
- 其他人通过飞书 SSO → SmikieJapan 角色（团队成员）
- 不需要再单独维护 JO043 / smikiejapan 账号密码（保留作 fallback）

---

## 六. 验证

1. **重启 streamlit 容器让 .env 生效**（PowerShell）：
   ```powershell
   cd D:\cms-v230\deploy\windows
   docker compose restart streamlit
   ```
2. 飞书桌面/手机 → 工作台 → 点「一元管理系统V2.3」
3. 第一次会跳「同意授权」页 → 同意 → 跳回 CMS 主页（已登录，看到 KPI 数字）
4. 再次访问无需授权（飞书记住授权状态）

---

## 七. 故障排查

| 现象 | 原因 / 排查 |
|---|---|
| 工作台没看到应用 | 「可见范围」没加你 / 没创建版本 |
| 点开后跳转失败 | 重定向 URL 跟实际域名不一致 |
| 跳回 CMS 后还是密码框 | `docker compose logs streamlit` 看是不是 `LARK_APP_ID` 没读到 / app_access_token 失败 |
| 进了但是 SmikieJapan 角色（应该 admin）| 检查 `ADMIN_LARK_EMAILS` 是否包含你的飞书登录邮箱（精确大小写不敏感）|

---

## 附：飞书 OAuth 流程图

```
飞书工作台「一元管理系统」
    ↓
飞书 → https://open.feishu.cn/open-apis/authen/v1/index
       ?app_id=cli_xxx&redirect_uri=https://cms.smikie.xx
    ↓
用户同意 (首次) / 自动通过 (后续)
    ↓
飞书 302 → https://cms.smikie.xx/?code=xxxxx
    ↓
shared/lark_auth.py.try_handle_oauth_callback()
    1. code → user_access_token  (用 app_access_token 当 Bearer)
    2. user_access_token → user_info (含 email / union_id / name)
    3. 写入 st.session_state[__auth_ok]=True
    4. email ∈ ADMIN_LARK_EMAILS → role=admin, 否则 role=guest
    ↓
st.rerun() → 进 CMS 主页
```
