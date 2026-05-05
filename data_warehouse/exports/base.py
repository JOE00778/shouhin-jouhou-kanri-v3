"""通用 Exporter 基类。

负责生成给 NetSuite CSV Import 上传的更新文件：
- 写出 CSV 到 `data/outputs/<exporter>_<timestamp>.csv`
- 在 `_export_runs` 表里留审计记录
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Exporter:
    """所有 exporter 的基类。子类设置类属性，主流程统一。"""

    exporter_name: str = ""           # 用于 _export_runs.exporter
    headers: list[str] = []            # 输出 CSV 的列顺序
    file_prefix: str = "export"        # 输出文件名前缀

    def export(
        self,
        rows: list[dict],
        out_dir: Path,
        conn: sqlite3.Connection,
        *,
        notes: str | None = None,
    ) -> tuple[Path, int]:
        """写出 CSV 并落审计记录。

        Args:
            rows: 待写入的 dict 列表，key 须包含 `headers` 中的所有列
            out_dir: 输出目录（自动创建）
            conn: 已打开的 SQLite 连接

        Returns:
            (file_path, export_id)
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{self.file_prefix}_{ts}.csv"
        file_path = out_dir / filename

        with file_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=self.headers, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)

        cursor = conn.execute(
            """
            INSERT INTO _export_runs
                (exporter, output_file, row_count, run_at, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                self.exporter_name,
                str(file_path),
                len(rows),
                datetime.now(timezone.utc).isoformat(),
                notes,
            ),
        )
        conn.commit()
        export_id = cursor.lastrowid
        if export_id is None:
            raise RuntimeError("无法获取 _export_runs.export_id")
        return file_path, export_id
