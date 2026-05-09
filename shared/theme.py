"""全局 UI 主题 · Phase 1 落地 mockup (docs/14-ui-redesign-mockup.html)

用法：每个 page 顶部调用 inject_theme()，建议放在 require_password() 之后。
- 注入 Inter + Noto Sans JP fonts（CDN）
- 加强 KPI 卡片 / sidebar / dataframe / tab 的视觉
- 提供 .badge-A/.badge-B/.badge-C/.badge-NEW 类（page 内可直接 st.markdown 使用）

Streamlit 1.x 限制：不支持顶部 header / 真 modal / 自定义键盘快捷键。
本文件用 CSS 注入逼近 mockup 70-80% 视觉，剩余靠 Streamlit 替代方案。
"""
from __future__ import annotations

import streamlit as st

_THEME_CSS = """
<style>
/* ===== Fonts ===== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', 'Noto Sans JP', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

/* ===== KPI Card 增强 ===== */
[data-testid="stMetric"] {
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1rem 1.25rem;
    transition: transform 0.15s, box-shadow 0.15s;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
}
[data-testid="stMetricLabel"] {
    color: #64748b !important;
    font-size: 12px !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500 !important;
}
[data-testid="stMetricValue"] {
    color: #0f172a !important;
    font-weight: 700 !important;
}

/* ===== Sidebar 主题 ===== */
[data-testid="stSidebar"] {
    background: white;
    border-right: 1px solid #e2e8f0;
}
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4,
[data-testid="stSidebar"] h5 {
    letter-spacing: 0.08em;
    font-size: 11px;
    text-transform: uppercase;
    color: #64748b !important;
    margin-top: 1.5rem;
    font-weight: 600;
}

/* ===== Dataframe 表格主题 ===== */
[data-testid="stDataFrame"] {
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    overflow: hidden;
}

/* ===== Tabs 主题 ===== */
[data-baseweb="tab-list"] {
    border-bottom: 1px solid #e2e8f0;
    gap: 0.25rem;
}
[data-baseweb="tab"] {
    font-weight: 500;
}

/* ===== 按钮 / page_link 主题 ===== */
[data-testid="stPageLink"] a {
    border-radius: 8px;
    transition: background 0.15s;
}
[data-testid="stPageLink"] a:hover {
    background: #f1f5f9;
}

/* ===== 风险胸章 (page 内可用) ===== */
.badge-A {
    background: #dcfce7; color: #166534;
    padding: 1px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
    display: inline-block;
}
.badge-B {
    background: #dbeafe; color: #1e40af;
    padding: 1px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
    display: inline-block;
}
.badge-C {
    background: #f3f4f6; color: #374151;
    padding: 1px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
    display: inline-block;
}
.badge-NEW {
    background: #fef3c7; color: #92400e;
    padding: 1px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
    display: inline-block;
}
.badge-RED {
    background: #fee2e2; color: #991b1b;
    padding: 1px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
    display: inline-block;
}

/* ===== Density toggle (page 18 用) ===== */
.density-compact [data-testid="stDataFrame"] td,
.density-compact [data-testid="stDataFrame"] th {
    padding: 0.25rem 0.5rem !important;
    font-size: 12px !important;
}
.density-comfy [data-testid="stDataFrame"] td,
.density-comfy [data-testid="stDataFrame"] th {
    padding: 1rem 1rem !important;
    font-size: 14px !important;
}

/* ===== caption 主题 ===== */
[data-testid="stCaptionContainer"] {
    color: #64748b;
    font-size: 13px;
}

/* ===== 圆角输入框 ===== */
[data-baseweb="input"] input,
[data-baseweb="select"] > div {
    border-radius: 8px !important;
}
</style>
"""


def inject_theme() -> None:
    """注入全局主题 CSS（每页 require_password 之后调用一次）。

    Streamlit 在每次 rerun 时都会重新执行 page 文件，
    所以 st.markdown 注入的 CSS 会跟着 rerun，整个 session 都生效。
    """
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


__all__ = ["inject_theme"]
