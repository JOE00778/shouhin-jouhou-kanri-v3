"""数据库初始化与迁移。

调用 `init_db(db_path)` 即可幂等建表。schema 写在同目录的 `schema.sql` 中。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 10
SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# 增量 ALTER（旧 db 已建过表,补充缺失列）
# 格式: (table, column_def_in_ALTER) — 如果列已存在会被 try/except 吞掉
ALTERS: list[tuple[str, str]] = [
    ("sales_line", "maker TEXT"),
]


def init_db(db_path: Path) -> sqlite3.Connection:
    """初始化数据库（幂等）。

    - 自动创建父目录
    - 执行 schema.sql 中所有 CREATE TABLE/INDEX IF NOT EXISTS
    - 应用 ALTERS 增量补列（已存在则忽略）
    - 写入 schema 版本号
    - 返回打开的 connection（调用方负责关闭）
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False：Streamlit 跨 rerun 复用 cache_resource 连接，
    # 不同 session/rerun 可能在不同 thread。SQLite 的串行写入特性 + 我们的
    # commit-per-write 模式保证安全。
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    conn.executescript(sql)

    # 增量列补丁（旧 db 已建过 sales_line 等表,通过 ALTER 补列）
    for table, col_def in ALTERS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # 列已存在

    # 写入版本号（幂等）
    conn.execute(
        "INSERT OR IGNORE INTO _schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    """返回当前 schema 版本（无记录返回 0）。"""
    row = conn.execute(
        "SELECT MAX(version) AS v FROM _schema_version"
    ).fetchone()
    return row["v"] or 0


def run(db_path: str = "data_warehouse/warehouse.db") -> sqlite3.Connection:
    """幂等初始化数据库，返回连接。用于命令行或测试。"""
    return init_db(Path(db_path))
