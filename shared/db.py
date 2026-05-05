"""共享：DB 路径与连接获取，所有页面统一从这里拿连接。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

from data_warehouse.db.migrations import init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
OUTPUTS_DIR = DATA_DIR / "outputs"
# v3 决策：所有 module / page 用同一 db 路径
DB_PATH = PROJECT_ROOT / "data_warehouse" / "warehouse.db"


def get_connection() -> sqlite3.Connection:
    """每次新建连接 + check_same_thread=False（Streamlit Cloud 多线程安全）。
    schema 自动 init（幂等），生产首次启动会自动建 16 张表。

    注：不用 @st.cache_resource —— SQLite Connection 不能跨线程共享，
    Streamlit 多 session 渲染时会报 ProgrammingError。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 用 init_db 确保 schema 存在（首次启动）
    init_db(DB_PATH).close()
    # 每次返回新连接（线程安全）+ Row factory（让 row["col"] 索引访问可用）
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
