"""测试 db 初始化与幂等建表。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data_warehouse.db.migrations import (
    SCHEMA_VERSION,
    get_schema_version,
    init_db,
)

EXPECTED_TABLES = {
    "item",
    "supplier",
    "sales",
    "inventory",
    "purchase",
    "lot",
    "inventory_snapshot",
    "sales_line",
    "inventory_turnover",
    "difficult_items",
    "difficult_items_history",
    "_ingest_runs",
    "_ingest_errors",
    "_export_runs",
    "_schema_version",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _list_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_init_db_creates_all_tables(db_path: Path) -> None:
    conn = init_db(db_path)
    try:
        assert EXPECTED_TABLES.issubset(_list_tables(conn))
    finally:
        conn.close()


def test_init_db_writes_schema_version(db_path: Path) -> None:
    conn = init_db(db_path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_init_db_is_idempotent(db_path: Path) -> None:
    """重复调用不报错且 schema 版本不重复。"""
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    try:
        rows = conn2.execute(
            "SELECT COUNT(*) AS c FROM _schema_version WHERE version = ?",
            (SCHEMA_VERSION,),
        ).fetchone()
        assert rows["c"] == 1
        assert EXPECTED_TABLES.issubset(_list_tables(conn2))
    finally:
        conn2.close()


def test_init_db_creates_parent_dir(tmp_path: Path) -> None:
    """数据目录不存在时应自动创建。"""
    nested = tmp_path / "deep" / "nested" / "warehouse.db"
    conn = init_db(nested)
    try:
        assert nested.exists()
    finally:
        conn.close()


def test_foreign_keys_enabled(db_path: Path) -> None:
    conn = init_db(db_path)
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
    finally:
        conn.close()
