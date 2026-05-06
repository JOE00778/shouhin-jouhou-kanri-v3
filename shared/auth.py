"""双角色守门（管理员 / 访客）· 每个 page 顶部调用 require_password()。

密码配置（Streamlit Cloud → Settings → Secrets）：
    ADMIN_PASSWORD = "..."
    GUEST_PASSWORD = "..."

向后兼容：仅配置 APP_PASSWORD 时视为管理员单密码（旧行为）。
两者都未配置时开放访问 → 默认管理员角色（避免误锁）。

角色：
- admin：完全权限
- guest：只读 + 日常写入（等级判定 / 改廃確認 / 商品登录），但禁数据导入 + 定義原価编辑

Page 顶部用法：
    require_password()          # 所有 page，登录任一密码即过
    require_admin()             # 仅 admin（用于 page 03 / page 99）
    if is_admin(): ...          # 局部按钮控制
"""
from __future__ import annotations

import hmac

import streamlit as st


def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "")
    except (FileNotFoundError, KeyError):
        return ""


def require_password() -> None:
    if st.session_state.get("__auth_ok"):
        return

    admin_pwd = _secret("ADMIN_PASSWORD")
    guest_pwd = _secret("GUEST_PASSWORD")
    legacy_pwd = _secret("APP_PASSWORD")
    if not admin_pwd and not guest_pwd and legacy_pwd:
        admin_pwd = legacy_pwd  # 兼容旧 APP_PASSWORD

    if not admin_pwd and not guest_pwd:
        st.session_state["__auth_ok"] = True
        st.session_state["__role"] = "admin"
        return

    def _on_submit() -> None:
        entered = st.session_state.get("__auth_input", "")
        if admin_pwd and hmac.compare_digest(entered, admin_pwd):
            st.session_state["__role"] = "admin"
            st.session_state["__auth_ok"] = True
            st.session_state["__auth_failed"] = False
        elif guest_pwd and hmac.compare_digest(entered, guest_pwd):
            st.session_state["__role"] = "guest"
            st.session_state["__auth_ok"] = True
            st.session_state["__auth_failed"] = False
        else:
            st.session_state["__auth_failed"] = True
        st.session_state.pop("__auth_input", None)

    st.title("🔒 商品信息管理平台")
    st.caption("请输入访问密码（管理员 / 访客）")
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


def is_admin() -> bool:
    return st.session_state.get("__role") == "admin"


def require_admin() -> None:
    """整页禁访客。仅 admin 可访问，否则显示提示并 stop。"""
    require_password()
    if not is_admin():
        st.title("⛔ 仅管理员可访问")
        st.warning("此功能涉及数据底盘操作（数据导入 / 定義原価覆盖），仅管理员密码可进入。")
        st.caption("请退出后用管理员密码重新登录")
        if st.button("🚪 切换账号"):
            for k in ("__auth_ok", "__role", "__auth_failed"):
                st.session_state.pop(k, None)
            st.rerun()
        st.stop()


def show_role_badge() -> None:
    """侧边栏显示当前身份 + 切换账号按钮。"""
    role = st.session_state.get("__role")
    if role == "admin":
        st.sidebar.success("👑 管理员")
    elif role == "guest":
        st.sidebar.info("👀 访客")
    if role and st.sidebar.button("🚪 切换账号", key="__auth_logout"):
        for k in ("__auth_ok", "__role", "__auth_failed"):
            st.session_state.pop(k, None)
        st.rerun()
