"""飞书群机器人通知工具。

用法：
    from shared.lark_notify import notify_card
    notify_card(
        title="Shopee 自动上架完成",
        rows=[("市场", "TW"), ("成功", "12"), ("失败", "0")],
        status="success",  # 决定卡片头部颜色
    )

依赖配置（streamlit secrets / env）：
    LARK_WEBHOOK_URL  # 飞书群机器人 webhook 地址（必须）
"""
from __future__ import annotations

import os
from typing import Iterable

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


def _webhook_url(override: str | None = None) -> str:
    return override or _secret("LARK_WEBHOOK_URL", "")


def is_configured() -> bool:
    return bool(_webhook_url())


def notify_text(content: str, *, webhook_url: str | None = None) -> bool:
    """简单文本消息。LARK_WEBHOOK_URL 未配置时静默返回 False。"""
    url = _webhook_url(webhook_url)
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
    webhook_url: str | None = None,
) -> bool:
    """卡片消息（可点击/排版）。

    Args:
        title: 卡片标题
        rows: (key, value) 对列表，按行排列
        body: 标题下方一段自由文本（支持 lark_md）
        status: info/success/warning/error → 决定卡片头部颜色
        webhook_url: 临时覆盖默认 webhook

    Returns:
        发送成功 True，未配置或失败 False。
    """
    url = _webhook_url(webhook_url)
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
