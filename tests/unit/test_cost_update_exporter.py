"""测试 CostUpdateExporter.build_rows + 端到端导出。"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from data_warehouse.db.migrations import init_db
from data_warehouse.exports.cost_update import CostUpdateExporter


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return init_db(tmp_path / "test.db")


def _decision(action="UPDATE", internal_id="1234", std_new=294):
    return {
        "internal_id": internal_id,
        "std_cost_new": std_new,
        "action": action,
    }


class TestBuildRows:
    def test_only_update_rows_included(self):
        decisions = [
            _decision(action="UPDATE", internal_id="1", std_new=100),
            _decision(action="SKIP_AVG_ZERO", internal_id="2", std_new=None),
            _decision(action="SKIP_BELOW_THRESHOLD", internal_id="3", std_new=200),
            _decision(action="UPDATE", internal_id="4", std_new=400),
        ]
        rows = CostUpdateExporter.build_rows(decisions)
        assert len(rows) == 2
        assert rows[0] == {"Internal ID": "1", "Standard Cost": 100}
        assert rows[1] == {"Internal ID": "4", "Standard Cost": 400}

    def test_std_cost_coerced_to_int(self):
        rows = CostUpdateExporter.build_rows([_decision(std_new=294.0)])
        assert rows[0]["Standard Cost"] == 294
        assert isinstance(rows[0]["Standard Cost"], int)

    def test_empty_decisions_returns_empty(self):
        assert CostUpdateExporter.build_rows([]) == []

    def test_no_update_decisions_returns_empty(self):
        decisions = [_decision(action="SKIP_INACTIVE")]
        assert CostUpdateExporter.build_rows(decisions) == []


class TestFullExport:
    def test_writes_correct_csv_to_disk(self, conn, tmp_path):
        decisions = [
            _decision(action="UPDATE", internal_id="1234", std_new=294),
            _decision(action="UPDATE", internal_id="1235", std_new=512),
            _decision(action="SKIP_AVG_ZERO", internal_id="9999", std_new=None),
        ]
        rows = CostUpdateExporter.build_rows(decisions)
        file_path, export_id = CostUpdateExporter().export(
            rows, tmp_path / "outputs", conn, notes="test"
        )

        assert file_path.exists()
        with file_path.open(encoding="utf-8", newline="") as f:
            csv_rows = list(csv.reader(f))
        assert csv_rows[0] == ["Internal ID", "Standard Cost"]
        assert csv_rows[1] == ["1234", "294"]
        assert csv_rows[2] == ["1235", "512"]
        assert len(csv_rows) == 3  # 1 header + 2 data

    def test_audit_records_correct_count(self, conn, tmp_path):
        rows = CostUpdateExporter.build_rows(
            [_decision(internal_id=str(i), std_new=100 + i) for i in range(5)]
        )
        _, export_id = CostUpdateExporter().export(rows, tmp_path / "outputs", conn)
        audit = conn.execute(
            "SELECT row_count, exporter FROM _export_runs WHERE export_id = ?",
            (export_id,),
        ).fetchone()
        assert audit["row_count"] == 5
        assert audit["exporter"] == "cost_update"

    def test_filename_starts_with_prefix(self, conn, tmp_path):
        file_path, _ = CostUpdateExporter().export(
            [{"Internal ID": "1", "Standard Cost": 100}], tmp_path / "outputs", conn
        )
        assert file_path.name.startswith("cost_update_")
        assert file_path.suffix == ".csv"
