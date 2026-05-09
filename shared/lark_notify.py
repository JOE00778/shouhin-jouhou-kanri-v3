"""飞书群机器人通知 · 支持多群路由。

用法（最常见）：
    from shared.lark_notify import notify_card
    notify_card(
        title="Shopee 自动上架完成",
        rows=[("市场", "TW"), ("成功", "12")],
        status="success",
        module="shopee_mass_upload",   # 可选，按 module 路由到不同群
    )

依赖配置（streamlit secrets / env，三层 fallback）：

    # 1) 默认群（兜底，必填）
    LARK_WEBHOOK_URL

    # 2) 按业务模块分群（可选；命中则覆盖默认）
    LARK_WEBHOOK_URL_SHOPEE        → module="shopee_mass_upload" / "image_gen"
    LARK_WEBHOOK_URL_DISCONTINUE   → module="discontinue_confirm"
    LARK_WEBHOOK_URL_NST           → module="nst_order"
    LARK_WEBHOOK_URL_ERROR         → 所有 status="error"（覆盖 module 路由）

    # 3) 完全自定义群路由表（可选；JSON 格式优先级最高）
    LARK_WEBHOOK_ROUTES = {"shopee_mass_upload": "https://...", "image_gen": "..."}
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


def _routes() -> dict[str, str]:
    """合并三层路由源 → 单个 module → URL 字典。"""
    out: dict[str, str] = {}

    # 第 3 层：JSON 路由表（最高优先）
    routes_json = _secret("LARK_WEBHOOK_ROUTES", "")
    if routes_json:
        try:
            out.update(json.loads(routes_json))
        except (TypeError, ValueError):
            pass

    # 第 2 层：按模块的命名约定环境变量
    convenience = {
        "shopee_mass_upload": _secret("LARK_WEBHOOK_URL_SHOPEE"),
        "image_gen":          _secret("LARK_WEBHOOK_URL_SHOPEE"),
        "discontinue_confirm": _secret("LARK_WEBHOOK_URL_DISCONTINUE"),
        "nst_order":          _secret("LARK_WEBHOOK_URL_NST"),
        "_error":             _secret("LARK_WEBHOOK_URL_ERROR"),
    }
    for k, v in convenience.items():
        if v and k not in out:
            out[k] = v

    return out


def _resolve_url(module: Optional[str], status: str, override: Optional[str]) -> str:
    """按优先级解析最终 webhook URL。

    顺序：
        override (调用方传入)
        > LARK_WEBHOOK_URL_ERROR (status=error 时优先)
        > _routes()[module]
        > LARK_WEBHOOK_URL (默认兜底)
        > ""（空字符串 → 调用方静默跳过）
    """
    if override:
        return override
    rt = _routes()
    if status == "error" and rt.get("_error"):
        return rt["_error"]
    if module and rt.get(module):
        return rt[module]
    return _secret("LARK_WEBHOOK_URL", "")


def is_configured() -> bool:
    """至少一个 webhook 配齐就视为开启（默认或 module 专用任一）。"""
    if _secret("LARK_WEBHOOK_URL"):
        return True
    return bool(_routes())


def notify_text(
    content: str,
    *,
    module: Optional[str] = None,
    status: str = "info",
    webhook_url: Optional[str] = None,
) -> bool:
    """简单文本消息。未配置 webhook 时静默返回 False。"""
    url = _resolve_url(module, status, webhook_url)
    if not url:
        return False
    try:
        resp = requests.post(
            url,
            json={"msg_type": "text", "content": {"text": content}},
            timeout=10,
        )
        return resp.ok
    except requests.RequestException:
        return False


def notify_card(
    *,
    title: str,
    rows: Iterable[tuple[str, str]] = (),
    body: str = "",
    status: str = "info",
    module: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """卡片消息（可点击/排版）。

    Args:
        title:  卡片标题
        rows:   (key, value) 对列表，按行排列
        body:   标题下方一段自由文本（支持 lark_md）
        status: info/success/warning/error → 决定卡片头部颜色
        module: 业务模块（用于按模块路由到不同群）
        webhook_url: 临时覆盖默认 webhook（最高优先）
    """
    url = _resolve_url(module, status, webhook_url)
    if not url:
        return False

    elements = []
    if body:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": body},
        })
    rows = list(rows)
    if rows:
        text = "\n".join(f"**{k}**: {v}" for k, v in rows)
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": text},
        })
    if not elements:
        elements.append({
            "tag": "div",
            "text": {"tag": "plain_text", "content": "(无内容)"},
        })

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": _STATUS_COLOR.get(status, "blue"),
            },
            "elements": elements,
        },
    }
    try:
        resp = requests.post(url, json=card, timeout=10)
        return resp.ok
    except requests.RequestException:
        return False


def list_configured_routes() -> dict[str, str]:
    """给 UI 展示当前配置概览（脱敏 URL，仅前 60 字符 + ...）。"""
    out = {"_default": _secret("LARK_WEBHOOK_URL")}
    out.update(_routes())
    return {
        k: (v[:60] + "..." if v and len(v) > 60 else (v or ""))
        for k, v in out.items()
    }
