"""单密码守门 · 每个 page 顶部调用一次。

密码配置：
- Streamlit Cloud 部署：Manage app → Settings → Secrets 加 APP_PASSWORD = "..."
- 本地：.streamlit/secrets.toml 加 APP_PASSWORD = "..."（已在 .gitignore 内）

未配置 APP_PASSWORD 时视为开放访问，避免误锁。
"""
from __future__ import annotations

import hmac

import streamlit as st


def _expected_password() -> str:
    try:
        return str(st.secrets.get("APP_PASSWORD", "") or "")
    except (FileNotFoundError, KeyError):
        return ""


def require_password() -> None:
    if st.session_state.get("__auth_ok"):
        return

    expected = _expected_password()
    if not expected:
        st.session_state["__auth_ok"] = True
        return

    def _on_submit() -> None:
        entered = st.session_state.get("__auth_input", "")
        if hmac.compare_digest(entered, expected):
            st.session_state["__auth_ok"] = True
            st.session_state["__auth_failed"] = False
            st.session_state.pop("__auth_input", None)
        else:
            st.session_state["__auth_failed"] = True

    st.title("🔒 商品信息管理平台")
    st.caption("请输入访问密码")
    st.text_input(
        "访问密码",
        type="password",
        key="__auth_input",
        on_change=_on_submit,
        label_visibility="collapsed",
    )
    if st.session_state.get("__auth_failed"):
        st.error("密码不对")
    st.stop()
