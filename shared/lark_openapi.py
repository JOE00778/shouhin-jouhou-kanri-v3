"""飞书自建应用 OpenAPI 客户端 · 表格 / 文档 / 消息读写。

适用：CMS / N8N 需要往飞书表格写数据、读取 SOP 文档、查群成员等场景。
（仅推消息走 shared/lark_notify.py 的群机器人 webhook 即可，更轻量。）

依赖配置：
    LARK_APP_ID         自建应用 App ID（cli_xxx）
    LARK_APP_SECRET     自建应用 App Secret

权限申请（飞书开发者后台 → 权限管理）：
    - sheets:spreadsheet              电子表格读写
    - docs:document                   云文档读写（如需）
    - im:message                      消息读写（如需双向交互）
    - contact:user.id:readonly        用户信息（按 union_id 查邮箱等）

文档：https://open.feishu.cn/document/server-docs/
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import requests
import streamlit as st


LARK_HOST = "https://open.feishu.cn"
_token_cache: dict[str, Any] = {"token": "", "expire_at": 0}


def _secret(name: str, default: str = "") -> str:
    try:
        v = st.secrets.get(name, None)
        if v:
            return str(v)
    except (FileNotFoundError, KeyError):
        pass
    return os.environ.get(name, "") or default


def is_configured() -> bool:
    return bool(_secret("LARK_APP_ID") and _secret("LARK_APP_SECRET"))


def _tenant_token() -> str:
    """缓存 tenant_access_token（飞书有效期 2h，提前 5 分钟刷新）。"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 300:
        return _token_cache["token"]

    app_id = _secret("LARK_APP_ID")
    app_secret = _secret("LARK_APP_SECRET")
    if not (app_id and app_secret):
        raise RuntimeError(
            "LARK_APP_ID / LARK_APP_SECRET 未配置。先在飞书开发者后台新建自建应用："
            "https://open.feishu.cn/app"
        )

    resp = requests.post(
        f"{LARK_HOST}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expire_at"] = now + data.get("expire", 7200)
    return _token_cache["token"]


def _request(method: str, path: str, **kw) -> dict:
    """通用 OpenAPI 请求，自动注入 tenant_access_token。"""
    token = _tenant_token()
    headers = kw.pop("headers", {})
    headers.setdefault("Authorization", f"Bearer {token}")
    headers.setdefault("Content-Type", "application/json; charset=utf-8")
    url = f"{LARK_HOST}{path}" if path.startswith("/") else path
    resp = requests.request(method, url, headers=headers, timeout=30, **kw)
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        return {"raw": resp.text}
    if data.get("code", 0) != 0:
        raise RuntimeError(f"飞书 API 错误 [{path}]: {data}")
    return data


# ============================================================
# 电子表格（Sheets v2）
# ============================================================
def sheet_append_rows(spreadsheet_token: str, sheet_id: str, rows: list[list[Any]],
                      column_range: str = "A:Z") -> int:
    """往电子表格末尾追加多行。

    Args:
        spreadsheet_token: 电子表格 token（URL 中 /sheets/<token>）
        sheet_id: 子表 ID（在飞书表格 URL 中 ?sheet=xxx）
        rows: 二维数组，每行是 N 列值
        column_range: 列范围如 'A:I'（默认 A:Z 兼容大多数表）

    Returns:
        实际追加的行数
    """
    body = {
        "valueRange": {
            "range": f"{sheet_id}!{column_range}",
            "values": rows,
        }
    }
    data = _request(
        "POST",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_append",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    )
    return data.get("data", {}).get("updates", {}).get("updatedRows", len(rows))


def sheet_read_range(spreadsheet_token: str, sheet_id: str,
                     column_range: str = "A:Z") -> list[list[Any]]:
    """读取电子表格指定范围（返回二维数组）。"""
    data = _request(
        "GET",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}!{column_range}",
    )
    return data.get("data", {}).get("valueRange", {}).get("values", [])


# ============================================================
# 云文档（Docs v2 - 新版云文档）
# ============================================================
def doc_get_content(document_id: str) -> str:
    """读取云文档内容（纯文本格式）。

    document_id 在 URL 中 /docs/<id> 或 /docx/<id>。
    """
    data = _request(
        "GET",
        f"/open-apis/docx/v1/documents/{document_id}/raw_content",
    )
    return data.get("data", {}).get("content", "")


def doc_append_block(document_id: str, text: str) -> bool:
    """往云文档末尾追加一段纯文本（作为新 block）。"""
    body = {
        "children": [
            {
                "block_type": 2,  # 2 = text
                "text": {
                    "elements": [{"text_run": {"content": text}}],
                    "style": {},
                },
            }
        ],
        "index": -1,  # -1 表示末尾
    }
    _request(
        "POST",
        f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    )
    return True


# ============================================================
# 消息（IM v1）· 给单个用户 / 群 发卡片 / 文本
# ============================================================
def im_send(receive_id: str, receive_id_type: str, msg_type: str, content: dict) -> str:
    """通用 IM 发送。返回 message_id。

    Args:
        receive_id: 用户 union_id / open_id / email 或群 chat_id
        receive_id_type: "union_id" | "open_id" | "chat_id" | "user_id" | "email"
        msg_type: "text" | "interactive" | "post" | "image" 等
        content: 对应 msg_type 的 content dict
                 text → {"text": "..."}
                 interactive → 卡片 dict（外层 msg_type/card 由本函数包好）
    """
    body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }
    data = _request(
        "POST",
        f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    )
    return data.get("data", {}).get("message_id", "")


def im_send_card(receive_id: str, receive_id_type: str, card: dict) -> str:
    """给指定用户 / 群发交互式卡片，返回 message_id。

    要求权限：im:message:send_as_bot
    """
    return im_send(receive_id, receive_id_type, "interactive", card)


def im_send_text(receive_id: str, receive_id_type: str, text: str) -> str:
    """给指定用户 / 群发纯文本。"""
    return im_send(receive_id, receive_id_type, "text", {"text": text})


# ============================================================
# 群（chat） · 列出 App 加入的群、按名称搜索群、查群成员
# ============================================================
def list_chats(page_size: int = 100) -> list[dict]:
    """列出机器人加入的所有群。

    返回每个 group 含: chat_id / name / description / avatar 等
    要求权限：im:chat (or im:chat:readonly)
    """
    out: list[dict] = []
    page_token = ""
    while True:
        params = f"page_size={page_size}"
        if page_token:
            params += f"&page_token={page_token}"
        data = _request("GET", f"/open-apis/im/v1/chats?{params}")
        items = data.get("data", {}).get("items", [])
        out.extend(items)
        page_token = data.get("data", {}).get("page_token", "")
        if not data.get("data", {}).get("has_more"):
            break
    return out


def search_chat_by_name(name_query: str) -> list[dict]:
    """按名称模糊找群（机器人不在的群也能找到，但发不了消息）。

    要求权限：im:chat (or im:chat:readonly)
    """
    data = _request("GET", f"/open-apis/im/v1/chats/search?query={name_query}")
    return data.get("data", {}).get("items", [])


# ============================================================
# 联系人（contacts v3）· 按 union_id 查用户
# ============================================================
def contact_get_user(union_id: str) -> dict:
    """根据 union_id 拉用户基础信息（name / email / 头像）。"""
    data = _request(
        "GET",
        f"/open-apis/contact/v3/users/{union_id}?user_id_type=union_id",
    )
    return data.get("data", {}).get("user", {})


# ============================================================
# 自检 / 配置概览（用于 UI）
# ============================================================
def health_check() -> dict:
    """跑一次 tenant_token 拿取，返回结果给 UI 显示。"""
    out = {
        "configured": is_configured(),
        "app_id": (_secret("LARK_APP_ID") or "")[:10] + "..." if _secret("LARK_APP_ID") else "",
        "token_ok": False,
        "token_expires_in": 0,
        "error": "",
    }
    if not out["configured"]:
        out["error"] = "LARK_APP_ID / LARK_APP_SECRET 未配置"
        return out
    try:
        _tenant_token()
        out["token_ok"] = True
        out["token_expires_in"] = max(0, int(_token_cache["expire_at"] - time.time()))
    except Exception as e:
        out["error"] = str(e)
    return out
