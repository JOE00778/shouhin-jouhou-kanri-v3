"""通用 Ingestor 基类。

负责把 NetSuite Saved Search 导出的 CSV → 本地 SQLite，全过程统一处理：
- 列映射（支持 alias，应对 Saved Search 列名漂移）
- UTF-8 BOM 自动剥离
- UPSERT 语义（子类提供 INSERT OR REPLACE 语句）
- 错误行落 `_ingest_errors` 表
- 整次运行落 `_ingest_runs` 表（审计 + UI 反馈）
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import IO


class MissingColumnsError(ValueError):
    """输入 CSV 缺少必需列时抛出。"""

    def __init__(self, missing: list[str], present: list[str]) -> None:
        super().__init__(
            f"输入 CSV 缺少必需列：{missing}（实际列：{present}）"
        )
        self.missing = missing
        self.present = present


class Ingestor:
    """所有 ingestor 的基类。子类必须设置类属性并实现两个方法。"""

    # 子类必须覆盖
    ingestor_name: str = ""           # 用于 _ingest_runs.ingestor 字段
    target_table: str = ""             # 仅用于自检/日志
    required_columns: list[str] = []  # 必需的列名（支持 alias）
    column_aliases: dict[str, list[str]] = {}  # 内部字段名 → [可接受的 CSV 列名]

    def parse_row(self, raw: dict[str, str]) -> dict | None:
        """子类实现：把 CSV 一行（dict[列名, 值]）转成 UPSERT 参数 dict。

        - 抛异常 → 落 _ingest_errors，继续下一行
        - 返回 None → 主动跳过（不入库也不入错误表）
        - 返回 dict → 走 upsert_sql() 写入 target_table
        """
        raise NotImplementedError

    def upsert_sql(self) -> str:
        """子类实现：返回 `INSERT OR REPLACE INTO ... VALUES (:field, ...)` 语句。"""
        raise NotImplementedError

    # ------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------
    def run(
        self,
        source: IO | str,
        conn: sqlite3.Connection,
        *,
        source_name: str,
    ) -> dict:
        """执行一次完整导入。

        Args:
            source: 文件路径字符串、文本流（StringIO）或字节流（BytesIO/上传文件）
            conn: 已打开的 SQLite 连接（调用方负责关闭）
            source_name: 写入 _ingest_runs.source_file 的可读名称

        Returns:
            汇总 dict：{run_id, total_rows, inserted, updated, skipped, errors}

        Raises:
            MissingColumnsError: 输入 CSV 缺必需列（不会创建 run 记录）
        """
        text = self._read_text(source)
        reader = csv.DictReader(io.StringIO(text))
        present = reader.fieldnames or []
        self._check_required_columns(present)

        # 创建 run 记录（先占位，结束时再 UPDATE）
        run_id = self._create_run(conn, source_name)
        sql = self.upsert_sql()

        inserted_or_updated = 0
        skipped = 0
        errors = 0
        total = 0

        # 构造别名 → 规范名的反查表（运行时一次性）
        alias_to_canonical: dict[str, str] = {}
        for canonical, aliases in self.column_aliases.items():
            for alt in aliases:
                alias_to_canonical[alt] = canonical

        for row_number, raw in enumerate(reader, start=1):
            total += 1
            normalized = self._normalize_row(raw, alias_to_canonical)
            try:
                payload = self.parse_row(normalized)
            except Exception as e:
                errors += 1
                self._record_error(conn, run_id, row_number, str(e), raw)
                continue

            if payload is None:
                skipped += 1
                continue

            try:
                conn.execute(sql, payload)
                inserted_or_updated += 1
            except Exception as e:  # SQLite + Postgres 通用
                # Postgres: 出错后事务进入 aborted 状态，必须 rollback 才能继续
                try:
                    conn.rollback()
                except Exception:
                    pass
                errors += 1
                try:
                    self._record_error(conn, run_id, row_number, f"DB 错误: {e}", raw)
                    conn.commit()  # record_error 落库，下一行能继续
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        # 简化：inserted+updated 不区分（INSERT OR REPLACE 不便区分）
        # 如要区分，未来改用 ON CONFLICT 显式判断
        self._finalize_run(
            conn,
            run_id,
            total=total,
            inserted=inserted_or_updated,
            updated=0,
            errors=errors,
        )
        conn.commit()

        return {
            "run_id": run_id,
            "total_rows": total,
            "inserted": inserted_or_updated,
            "updated": 0,
            "skipped": skipped,
            "errors": errors,
        }

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------
    @staticmethod
    def _normalize_row(
        raw: dict[str, str], alias_to_canonical: dict[str, str]
    ) -> dict[str, str]:
        """把 row 中的别名列改写成规范列名。规范名优先（已有则不覆盖）。"""
        out = dict(raw)
        for alt, canonical in alias_to_canonical.items():
            if alt in out and canonical not in out:
                out[canonical] = out[alt]
        return out

    @staticmethod
    def _read_text(source: IO | str) -> str:
        """把任意来源读成 str 并剥 UTF-8 BOM。"""
        if isinstance(source, str):
            with open(source, "rb") as f:
                raw = f.read()
        else:
            data = source.read()
            raw = data.encode("utf-8") if isinstance(data, str) else data
        # 剥 BOM
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return raw.decode("utf-8")

    def _check_required_columns(self, present: list[str]) -> None:
        """对每个 required_column 检查它本身或任一别名是否在 present 中。"""
        present_set = set(present)
        missing: list[str] = []
        for req in self.required_columns:
            aliases = self.column_aliases.get(req, []) + [req]
            if not any(a in present_set for a in aliases):
                missing.append(req)
        if missing:
            raise MissingColumnsError(missing, present)

    def _create_run(self, conn: sqlite3.Connection, source_name: str) -> int:
        cursor = conn.execute(
            """
            INSERT INTO _ingest_runs
                (ingestor, source_file, total_rows, inserted, updated, errors, run_at)
            VALUES (?, ?, 0, 0, 0, 0, ?)
            """,
            (
                self.ingestor_name,
                source_name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        run_id = cursor.lastrowid
        if run_id is None:
            raise RuntimeError("无法获取 _ingest_runs.run_id")
        return run_id

    def _finalize_run(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        *,
        total: int,
        inserted: int,
        updated: int,
        errors: int,
    ) -> None:
        conn.execute(
            """
            UPDATE _ingest_runs
            SET total_rows=?, inserted=?, updated=?, errors=?
            WHERE run_id=?
            """,
            (total, inserted, updated, errors, run_id),
        )

    @staticmethod
    def _record_error(
        conn: sqlite3.Connection,
        run_id: int,
        row_number: int,
        message: str,
        raw_row: dict,
    ) -> None:
        conn.execute(
            """
            INSERT INTO _ingest_errors (run_id, row_number, error_message, raw_row)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                row_number,
                message,
                json.dumps(raw_row, ensure_ascii=False),
            ),
        )
