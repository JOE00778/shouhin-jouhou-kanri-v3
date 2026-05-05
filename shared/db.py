"""共享：DB 路径与连接获取，所有页面统一从这里拿连接。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

from data_warehouse.db.migrations import init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "warehouse.db"
INPUTS_DIR = DATA_DIR / "inputs"
OUTPUTS_DIR = DATA_DIR / "outputs"


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    """整个 Streamlit 进程共享一个连接（cache_resource 跨 rerun 持久）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    return init_db(DB_PATH)
