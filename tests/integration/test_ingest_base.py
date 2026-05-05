"""测试 Ingestor 基类的通用导入流水线。"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from data_warehouse.db.migrations import init_db
from data_warehouse.ingest.base import Ingestor, MissingColumnsError


# ============================================================
# 测试用 Ingestor 实现：往一个简单的 demo 表里写
# ============================================================
class DemoIngestor(Ingestor):
    """测试用：向 _demo_target 表写 (key, value)。"""

    ingestor_name = "demo"
    target_table = "_demo_target"
    required_columns = ["KeyCol", "ValueCol"]
    # 框架接受任一别名，并在交给 parse_row 前归一化到规范名（KeyCol/ValueCol）
    column_aliases = {"KeyCol": ["K"], "ValueCol": ["V"]}

    def parse_row(self, raw: dict[str, str]) -> dict | None:
        key = (raw.get("KeyCol") or "").strip()
        value = (raw.get("ValueCol") or "").strip()
        if not key:
            raise ValueError("key 不能为空")
        return {"key": key, "value": value}

    def upsert_sql(self) -> str:
        return "INSERT OR REPLACE INTO _demo_target (key, value) VALUES (:key, :value)"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    c = init_db(db)
    c.execute("CREATE TABLE _demo_target (key TEXT PRIMARY KEY, value TEXT)")
    c.commit()
    return c


# ============================================================
# Happy path
# ============================================================
def test_happy_path_inserts_rows(conn):
    csv = io.StringIO("KeyCol,ValueCol\nA,1\nB,2\nC,3\n")
    summary = DemoIngestor().run(csv, conn, source_name="demo.csv")
    assert summary["inserted"] + summary["updated"] == 3
    assert summary["errors"] == 0
    rows = conn.execute("SELECT key, value FROM _demo_target ORDER BY key").fetchall()
    assert [(r["key"], r["value"]) for r in rows] == [("A", "1"), ("B", "2"), ("C", "3")]


def test_records_ingest_run(conn):
    csv = io.StringIO("KeyCol,ValueCol\nA,1\n")
    DemoIngestor().run(csv, conn, source_name="demo.csv")
    runs = conn.execute("SELECT * FROM _ingest_runs WHERE ingestor='demo'").fetchall()
    assert len(runs) == 1
    assert runs[0]["source_file"] == "demo.csv"
    assert runs[0]["total_rows"] == 1


# ============================================================
# Idempotency
# ============================================================
def test_running_twice_is_idempotent(conn):
    csv1 = io.StringIO("KeyCol,ValueCol\nA,1\nB,2\n")
    DemoIngestor().run(csv1, conn, source_name="demo.csv")
    csv2 = io.StringIO("KeyCol,ValueCol\nA,1\nB,2\n")
    DemoIngestor().run(csv2, conn, source_name="demo.csv")
    count = conn.execute("SELECT COUNT(*) AS c FROM _demo_target").fetchone()["c"]
    assert count == 2  # 仍然只有两条


def test_second_run_updates_changed_value(conn):
    csv1 = io.StringIO("KeyCol,ValueCol\nA,1\n")
    DemoIngestor().run(csv1, conn, source_name="demo.csv")
    csv2 = io.StringIO("KeyCol,ValueCol\nA,99\n")
    DemoIngestor().run(csv2, conn, source_name="demo.csv")
    row = conn.execute("SELECT value FROM _demo_target WHERE key='A'").fetchone()
    assert row["value"] == "99"


# ============================================================
# 列缺失
# ============================================================
def test_missing_required_column_raises(conn):
    csv = io.StringIO("KeyCol\nA\n")  # 缺 ValueCol
    with pytest.raises(MissingColumnsError) as exc_info:
        DemoIngestor().run(csv, conn, source_name="bad.csv")
    assert "ValueCol" in str(exc_info.value)


def test_missing_required_column_does_not_create_run(conn):
    csv = io.StringIO("KeyCol\nA\n")
    with pytest.raises(MissingColumnsError):
        DemoIngestor().run(csv, conn, source_name="bad.csv")
    runs = conn.execute("SELECT COUNT(*) AS c FROM _ingest_runs").fetchone()["c"]
    assert runs == 0  # 没建 run 记录


# ============================================================
# 列别名（NetSuite Saved Search 列名漂移）
# ============================================================
def test_column_aliases_accepted(conn):
    csv = io.StringIO("K,V\nX,42\n")  # 用别名 K/V 而不是 KeyCol/ValueCol
    summary = DemoIngestor().run(csv, conn, source_name="alias.csv")
    assert summary["errors"] == 0
    row = conn.execute("SELECT * FROM _demo_target WHERE key='X'").fetchone()
    assert row["value"] == "42"


# ============================================================
# 错误行处理
# ============================================================
def test_bad_row_recorded_to_errors_not_target(conn):
    # 第二行 key 为空 → parse_row 会抛 ValueError
    csv = io.StringIO("KeyCol,ValueCol\nA,1\n,bad\nB,2\n")
    summary = DemoIngestor().run(csv, conn, source_name="mixed.csv")
    assert summary["inserted"] + summary["updated"] == 2
    assert summary["errors"] == 1

    # 错误表里有那条
    err_rows = conn.execute(
        "SELECT * FROM _ingest_errors ORDER BY id"
    ).fetchall()
    assert len(err_rows) == 1
    assert "key 不能为空" in err_rows[0]["error_message"]
    assert err_rows[0]["row_number"] == 2  # 数据第 2 行
    raw = json.loads(err_rows[0]["raw_row"])
    assert raw["ValueCol"] == "bad"


# ============================================================
# 空 CSV
# ============================================================
def test_empty_csv_ok(conn):
    csv = io.StringIO("KeyCol,ValueCol\n")
    summary = DemoIngestor().run(csv, conn, source_name="empty.csv")
    assert summary["total_rows"] == 0
    assert summary["inserted"] + summary["updated"] == 0


# ============================================================
# UTF-8 BOM 自动剥离
# ============================================================
def test_bom_stripped(conn):
    # BOM + header + row
    raw_bytes = b"\xef\xbb\xbfKeyCol,ValueCol\nA,1\n"
    csv = io.BytesIO(raw_bytes)
    summary = DemoIngestor().run(csv, conn, source_name="bom.csv")
    assert summary["errors"] == 0
    row = conn.execute("SELECT * FROM _demo_target").fetchone()
    assert row["key"] == "A"


# ============================================================
# parse_row 返回 None 表示主动跳过（不算错误）
# ============================================================
class SkipNullValueIngestor(DemoIngestor):
    def parse_row(self, raw):
        if (raw.get("ValueCol") or "").strip() == "":
            return None  # 主动跳过
        return super().parse_row(raw)


def test_parse_row_returning_none_skips_silently(conn):
    csv = io.StringIO("KeyCol,ValueCol\nA,1\nB,\nC,3\n")
    summary = SkipNullValueIngestor().run(csv, conn, source_name="skip.csv")
    assert summary["inserted"] + summary["updated"] == 2
    assert summary["errors"] == 0
    assert summary["skipped"] == 1
