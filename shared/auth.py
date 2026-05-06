"""双角色守门（管理员 / SmikieJapan）· 每个 page 顶部调用 require_password()。

密码配置（Streamlit Cloud → Settings → Secrets）：
    ADMIN_USERNAME = "JO043"            # 可选，默认 "JO043"
    ADMIN_PASSWORD = "..."
    GUEST_USERNAME = "smikiejapan"      # 可选，默认 "smikiejapan"
    GUEST_PASSWORD = "..."

向后兼容：仅配置 APP_PASSWORD 时视为管理员单密码（旧行为）。
两者都未配置时开放访问 → 默认管理员角色（避免误锁）。

Page 顶部用法：
    require_password()  # 任一角色登录即过
    require_admin()     # 仅管理员（page 03 / page 99）
    is_admin()          # 局部按钮控制
"""
from __future__ import annotations

import hmac

import streamlit as st


def _secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default) or default)
    except (FileNotFoundError, KeyError):
        return default


def _check(entered: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(entered, expected)


def _login_form() -> None:
    admin_user = _secret("ADMIN_USERNAME", "JO043")
    admin_pwd = _secret("ADMIN_PASSWORD")
    guest_user = _secret("GUEST_USERNAME", "smikiejapan")
    guest_pwd = _secret("GUEST_PASSWORD")
    legacy_pwd = _secret("APP_PASSWORD")
    if not admin_pwd and not guest_pwd and legacy_pwd:
        admin_pwd = legacy_pwd  # 兼容旧 APP_PASSWORD

    if not admin_pwd and not guest_pwd:
        st.session_state["__auth_ok"] = True
        st.session_state["__role"] = "admin"
        return

    st.title("🔒 一元管理系统V2.3")
    st.caption("请选择身份并登录")

    tab_admin, tab_guest = st.tabs(["👑 管理员入口", "👀 SmikieJapan入口"])

    with tab_admin:
        with st.form("admin_login", clear_on_submit=False):
            u = st.text_input("管理员账号", key="__admin_user", placeholder="请输入账号")
            p = st.text_input("管理员密码", type="password", key="__admin_pwd")
            if st.form_submit_button("登录", type="primary", use_container_width=True):
                if u == admin_user and _check(p, admin_pwd):
                    st.session_state["__auth_ok"] = True
                    st.session_state["__role"] = "admin"
                    for k in ("__admin_user", "__admin_pwd", "__guest_user", "__guest_pwd"):
                        st.session_state.pop(k, None)
                    st.rerun()
                else:
                    st.error("管理员账号或密码错误")

    with tab_guest:
        with st.form("guest_login", clear_on_submit=False):
            u = st.text_input("SmikieJapan账号", key="__guest_user", placeholder="请输入账号")
            p = st.text_input("SmikieJapan密码", type="password", key="__guest_pwd")
            if st.form_submit_button("登录", type="primary", use_container_width=True):
                if u == guest_user and _check(p, guest_pwd):
                    st.session_state["__auth_ok"] = True
                    st.session_state["__role"] = "guest"
                    for k in ("__admin_user", "__admin_pwd", "__guest_user", "__guest_pwd"):
                        st.session_state.pop(k, None)
                    st.rerun()
                else:
                    st.error("SmikieJapan账号或密码错误")

    st.stop()


_GUEST_HIDE_CSS = """
<style>
/* 仅 SmikieJapan 角色：隐藏 Streamlit 顶部 toolbar、状态条、部署标记、Manage app 浮动按钮 */
[data-testid="stToolbar"],
[data-testid="stStatusWidget"],
[data-testid="stDecoration"],
[data-testid="stHeader"] button,
.viewerBadge_link__1S137,
.viewerBadge_container__r5tak,
.styles_viewerBadge__1yB5_,
#MainMenu,
header[data-testid="stHeader"] > div:last-child {
    display: none !important;
}
</style>
"""


def _hide_chrome_for_guest() -> None:
    if not is_admin():
        st.markdown(_GUEST_HIDE_CSS, unsafe_allow_html=True)


def require_password() -> None:
    if st.session_state.get("__auth_ok"):
        _hide_chrome_for_guest()
        return
    _login_form()


def is_admin() -> bool:
    return st.session_state.get("__role") == "admin"


def require_admin() -> None:
    """整页禁SmikieJapan。仅 admin 可访问，否则显示提示并 stop。"""
    require_password()
    if not is_admin():
        st.title("⛔ 仅管理员可访问")
        st.warning("此功能涉及数据底盘操作（数据导入 / 定義原価覆盖），仅管理员账号可进入。")
        if st.button("🚪 切换账号"):
            for k in ("__auth_ok", "__role"):
                st.session_state.pop(k, None)
            st.rerun()
        st.stop()


def show_role_badge() -> None:
    """侧边栏切换账号按钮。"""
    role = st.session_state.get("__role")
    if role and st.sidebar.button("🚪 切换账号", key="__auth_logout"):
        for k in ("__auth_ok", "__role"):
            st.session_state.pop(k, None)
        st.rerun()
