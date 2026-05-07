"""共享：DB 路径与连接获取，所有页面统一从这里拿连接。

后端选择：
- 默认（无 DATABASE_URL）→ SQLite，本地文件 data_warehouse/warehouse.db
- 设 DATABASE_URL=postgresql://... → Postgres（NAS Self-hosted 模式）

兼容性：
- 返回对象始终支持 .execute(sql, params)、.executemany、.commit、.close
- row 始终支持 row["col"] / row[index] 双重访问
- SQL 占位符两边都接受 `?`（Postgres 模式自动转 %s）
- 注：INSERT OR REPLACE / INSERT OR IGNORE 不会自动转，
  迁移到 Postgres 时需手动改成 ON CONFLICT（见 deploy/nas/MIGRATION.md）
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import streamlit as st

from data_warehouse.db.migrations import init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
OUTPUTS_DIR = DATA_DIR / "outputs"
DB_PATH = PROJECT_ROOT / "data_warehouse" / "warehouse.db"


def _is_postgres() -> bool:
    """检测是否走 Postgres 后端。仅当 DATABASE_URL 以 postgres 开头才启用。"""
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith(("postgresql://", "postgres://"))


def _get_sqlite_connection() -> sqlite3.Connection:
    """SQLite 连接（默认 / 现有 Cloud 部署用）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH).close()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get_postgres_connection():
    """Postgres 连接（NAS 部署用）。延迟 import psycopg2，未装时优雅报错。"""
    try:
        import psycopg2
        from psycopg2.extras import DictCursor
    except ImportError as e:
        raise RuntimeError(
            "psycopg2 未安装但设置了 DATABASE_URL=postgresql://。"
            "请装 psycopg2-binary>=2.9 或取消 DATABASE_URL 走 SQLite。"
        ) from e
    raw = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=DictCursor)
    raw.set_session(autocommit=False)
    return _PostgresAdapter(raw)


class _PostgresAdapter:
    """psycopg2 连接的 SQLite-like wrapper.

    让现有代码（用 conn.execute(sql, params)、row["col"]、? 占位符）
    在 Postgres 后端无缝工作，不改任何 ingester / page。
    """

    def __init__(self, raw):
        self._raw = raw

    @staticmethod
    def _adapt_sql(sql: str) -> str:
        """`?` → `%s`。简单替换，假定字符串字面量里不含 `?`（CMS 代码确实如此）。"""
        return sql.replace("?", "%s")

    def execute(self, sql, params=None):
        cur = self._raw.cursor()
        cur.execute(self._adapt_sql(sql), params or ())
        return cur

    def executemany(self, sql, params_seq):
        cur = self._raw.cursor()
        cur.executemany(self._adapt_sql(sql), list(params_seq))
        return cur

    def executescript(self, script: str):
        cur = self._raw.cursor()
        cur.execute(script)  # Postgres 接受多语句
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def get_connection():
    """统一入口。根据 DATABASE_URL 自动选 SQLite or Postgres。

    返回对象兼容 SQLite Connection 接口：
        conn.execute(sql, params).fetchall()
        conn.executemany(sql, params_seq)
        conn.commit() / conn.close()
        row["col"] 索引访问

    注：不用 @st.cache_resource —— 连接不能跨线程共享。
    """
    if _is_postgres():
        return _get_postgres_connection()
    return _get_sqlite_connection()
