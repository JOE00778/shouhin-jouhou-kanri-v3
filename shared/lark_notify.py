"""飞书消息通知 · Bot-first，webhook fallback。

设计：
    优先走「自建应用 + 机器人能力」（一个 App 推消息 + 写表格 + 文档）
    如果 App 未配齐，自动降级到「群机器人 webhook」（轻量但功能少）

用法（不变）：
    from shared.lark_notify import notify_card
    notify_card(
        title="Shopee 自动上架完成",
        rows=[("市场", "TW"), ("成功", "12")],
        status="success",
        module="shopee_mass_upload",
    )

配置（按优先级，三种任选其一即可）：

    # ────────── 方案 A · CMS 自建应用 + 机器人（推荐）──────────
    LARK_APP_ID=cli_xxx
    LARK_APP_SECRET=xxx
    LARK_DEFAULT_CHAT_ID=oc_xxx                  # 默认目标群（必填，作为兜底）
    LARK_CHAT_ROUTES = {"shopee_mass_upload": "oc_yyy", "_error": "oc_zzz"}  # JSON, 可选

    # ────────── 方案 B · 群机器人 Webhook（fallback）──────────
    LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
    LARK_WEBHOOK_URL_SHOPEE=...
    LARK_WEBHOOK_URL_DISCONTINUE=...
    LARK_WEBHOOK_URL_NST=...
    LARK_WEBHOOK_URL_ERROR=...
    LARK_WEBHOOK_ROUTES = {"...": "..."}    # JSON, 可选

    两套并存时优先走 A（用同一个 App 凭证写表格 + 推消息，配置最干净）
"""
from __future__ import annotations

import json
import os
from typing import Iterable, Optional

import requests
import streamlit as st


_STATUS_COLOR = {
    "info": "blue",
    "success": "green",
    "warning": "orange",
    "error": "red",
}


def _secret(name: str, default: str = "") -> str:
    try:
        v = st.secrets.get(name, None)
        if v:
            return str(v)
    except (FileNotFoundError, KeyError):
        pass
    return os.environ.get(name, "") or default


# ============================================================
# 路由解析（A 方案 chat_id / B 方案 webhook URL）
# ============================================================
def _bot_app_configured() -> bool:
    return bool(_secret("LARK_APP_ID") and _secret("LARK_APP_SECRET"))


def _chat_routes() -> dict[str, str]:
    """方案 A · module → chat_id 字典。"""
    out: dict[str, str] = {}
    routes_json = _secret("LARK_CHAT_ROUTES", "")
    if routes_json:
        try:
            out.update(json.loads(routes_json))
        except (TypeError, ValueError):
            pass
    return out


def _webhook_routes() -> dict[str, str]:
    """方案 B · module → webhook URL 字典。"""
    out: dict[str, str] = {}
    routes_json = _secret("LARK_WEBHOOK_ROUTES", "")
    if routes_json:
        try:
            out.update(json.loads(routes_json))
        except (TypeError, ValueError):
            pass
    convenience = {
        "shopee_mass_upload":  _secret("LARK_WEBHOOK_URL_SHOPEE"),
        "image_gen":           _secret("LARK_WEBHOOK_URL_SHOPEE"),
        "discontinue_confirm": _secret("LARK_WEBHOOK_URL_DISCONTINUE"),
        "nst_order":           _secret("LARK_WEBHOOK_URL_NST"),
        "_error":              _secret("LARK_WEBHOOK_URL_ERROR"),
    }
    for k, v in convenience.items():
        if v and k not in out:
            out[k] = v
    return out


def _resolve_target(
    module: Optional[str], status: str,
) -> tuple[str, str]:
    """返回 (mode, target):
        mode = 'bot' → target 是 chat_id
        mode = 'webhook' → target 是 webhook URL
        mode = '' → 没配置任何路由
    """
    # 优先方案 A · Bot App
    if _bot_app_configured():
        rt = _chat_routes()
        if status == "error" and rt.get("_error"):
            return "bot", rt["_error"]
        if module and rt.get(module):
            return "bot", rt[module]
        default_chat = _secret("LARK_DEFAULT_CHAT_ID", "")
        if default_chat:
            return "bot", default_chat
        # App 配齐但没有 chat_id —— 继续 fallback 到 webhook

    # 方案 B · webhook fallback
    rt = _webhook_routes()
    if status == "error" and rt.get("_error"):
        return "webhook", rt["_error"]
    if module and rt.get(module):
        return "webhook", rt[module]
    default_url = _secret("LARK_WEBHOOK_URL", "")
    if default_url:
        return "webhook", default_url

    return "", ""


def is_configured() -> bool:
    """A 或 B 任一就视为开启。"""
    if _bot_app_configured() and (_secret("LARK_DEFAULT_CHAT_ID") or _chat_routes()):
        return True
    if _secret("LARK_WEBHOOK_URL") or _webhook_routes():
        return True
    return False


def get_active_mode() -> str:
    """返回当前主用模式: 'bot' / 'webhook' / 'none'，给 UI 显示。"""
    if _bot_app_configured() and (_secret("LARK_DEFAULT_CHAT_ID") or _chat_routes()):
        return "bot"
    if _secret("LARK_WEBHOOK_URL") or _webhook_routes():
        return "webhook"
    return "none"


# ============================================================
# 发送 · 自动路由
# ============================================================
def notify_text(
    content: str,
    *,
    module: Optional[str] = None,
    status: str = "info",
    chat_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    if chat_id:
        return _send_bot_text(chat_id, content)
    if webhook_url:
        return _send_webhook_text(webhook_url, content)
    mode, target = _resolve_target(module, status)
    if mode == "bot":
        return _send_bot_text(target, content)
    if mode == "webhook":
        return _send_webhook_text(target, content)
    return False


def notify_card(
    *,
    title: str,
    rows: Iterable[tuple[str, str]] = (),
    body: str = "",
    status: str = "info",
    module: Optional[str] = None,
    chat_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """卡片消息。chat_id / webhook_url 优先级最高（手动覆盖）。"""
    card = _build_card(title=title, rows=rows, body=body, status=status)

    if chat_id:
        return _send_bot_card(chat_id, card)
    if webhook_url:
        return _send_webhook_card(webhook_url, card)

    mode, target = _resolve_target(module, status)
    if mode == "bot":
        return _send_bot_card(target, card)
    if mode == "webhook":
        return _send_webhook_card(target, card)
    return False


# ============================================================
# 卡片构造 · 两套机制共用
# ============================================================
def _build_card(*, title: str, rows: Iterable[tuple[str, str]], body: str, status: str) -> dict:
    elements = []
    if body:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body}})
    rows = list(rows)
    if rows:
        text = "\n".join(f"**{k}**: {v}" for k, v in rows)
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": text}})
    if not elements:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "(无内容)"}})

    return {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _STATUS_COLOR.get(status, "blue"),
        },
        "elements": elements,
    }


# ============================================================
# 方案 A · Bot 发送（用 OpenAPI tenant_token + im:message:send_as_bot）
# ============================================================
def _send_bot_card(chat_id: str, card: dict) -> bool:
    try:
        from shared.lark_openapi import im_send_card
        im_send_card(chat_id, "chat_id", card)
        return True
    except Exception:
        return False


def _send_bot_text(chat_id: str, content: str) -> bool:
    try:
        from shared.lark_openapi import im_send_text
        im_send_text(chat_id, "chat_id", content)
        return True
    except Exception:
        return False


# ============================================================
# 方案 B · Webhook 发送（不需要 App，仅 URL）
# ============================================================
def _send_webhook_card(url: str, card: dict) -> bool:
    payload = {"msg_type": "interactive", "card": card}
    try:
        return requests.post(url, json=payload, timeout=10).ok
    except requests.RequestException:
        return False


def _send_webhook_text(url: str, content: str) -> bool:
    try:
        return requests.post(
            url, json={"msg_type": "text", "content": {"text": content}}, timeout=10
        ).ok
    except requests.RequestException:
        return False


# ============================================================
# 配置概览（UI 用）
# ============================================================
def list_configured_routes() -> dict:
    """两套配置一览，给 page 99 Tab 5 展示。"""
    bot_routes = _chat_routes()
    bot_default = _secret("LARK_DEFAULT_CHAT_ID", "")

    web_routes = _webhook_routes()
    web_default = _secret("LARK_WEBHOOK_URL", "")

    def _trunc(s: str, n: int = 60) -> str:
        return (s[:n] + "...") if s and len(s) > n else (s or "")

    return {
        "active_mode": get_active_mode(),
        "bot": {
            "configured": _bot_app_configured(),
            "default_chat_id": bot_default,
            "routes": bot_routes,
        },
        "webhook": {
            "default_url": _trunc(web_default),
            "routes": {k: _trunc(v) for k, v in web_routes.items()},
        },
    }
