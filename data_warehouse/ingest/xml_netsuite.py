"""NetSuite XML ingestors: TurnoverIngestor, StoreSalesIngestor, InventoryIngestor."""
from __future__ import annotations
import sqlite3, json, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}

class XMLIngestor:
    """Base XML ingestor."""
    ingestor_name: str = ""
    target_table: str = ""
    def parse_rows(self, path: str) -> list[dict]: raise NotImplementedError
    def parse_row(self, raw: dict[str, str]) -> dict | None: raise NotImplementedError
    def upsert_sql(self) -> str: raise NotImplementedError
    def run(self, path: str, conn: sqlite3.Connection, *, source_name: str) -> dict:
        rows = self.parse_rows(path)
        run_id = self._create_run(conn, source_name)
        # 整表 truncate 再插 (避免累积 — 销售/库存/周转都是覆盖性快照)
        if self.target_table:
            conn.execute(f"DELETE FROM {self.target_table}")
        inserted_or_updated = errors = skipped = 0
        for row_number, row_dict in enumerate(rows, start=1):
            try:
                payload = self.parse_row(row_dict)
            except Exception as e:
                errors += 1
                self._record_error(conn, run_id, row_number, str(e), row_dict)
                continue
            if payload is None: skipped += 1
            else:
                try:
                    conn.execute(self.upsert_sql(), payload)
                    inserted_or_updated += 1
                except sqlite3.Error as e:
                    errors += 1
                    self._record_error(conn, run_id, row_number, f"DB error: {e}", row_dict)
        self._finalize_run(conn, run_id, total=len(rows), inserted=inserted_or_updated, updated=0, errors=errors)
        conn.commit()
        return {"run_id": run_id, "total_rows": len(rows), "inserted": inserted_or_updated, "updated": 0, "skipped": skipped, "errors": errors}
    def _create_run(self, conn: sqlite3.Connection, source_name: str) -> int:
        cur = conn.execute("INSERT INTO _ingest_runs (ingestor, source_file, total_rows, inserted, updated, errors, run_at) VALUES (?, ?, 0, 0, 0, 0, ?)",
            (self.ingestor_name, source_name, datetime.now(timezone.utc).isoformat()))
        return cur.lastrowid
    def _finalize_run(self, conn: sqlite3.Connection, run_id: int, *, total: int, inserted: int, updated: int, errors: int) -> None:
        conn.execute("UPDATE _ingest_runs SET total_rows=?, inserted=?, updated=?, errors=? WHERE run_id=?", (total, inserted, updated, errors, run_id))
    @staticmethod
    def _record_error(conn: sqlite3.Connection, run_id: int, row_number: int, message: str, raw_row: dict) -> None:
        conn.execute("INSERT INTO _ingest_errors (run_id, row_number, error_message, raw_row) VALUES (?, ?, ?, ?)",
            (run_id, row_number, message, json.dumps(raw_row, ensure_ascii=False)))


class TurnoverIngestor(XMLIngestor):
    ingestor_name = "xml_netsuite.TurnoverIngestor"
    target_table = "nst_turnover"
    def parse_rows(self, path: str) -> list[dict]:
        rows = []
        tree = ET.parse(path)
        for ws in tree.getroot().findall("ss:Worksheet", NS):
            table = ws.find("ss:Table", NS)
            if table is None: continue
            all_rows = table.findall("ss:Row", NS)
            if len(all_rows) < 8: continue
            dept = None
            for row in all_rows[7:]:
                cells = self._extract_cells(row)
                if not cells or all(c == "" for c in cells): continue
                if cells[0] and cells[0].startswith("部門: "): dept = cells[0].replace("部門: ", "").strip(); continue
                if cells[0] and not cells[1] and not cells[2]: dept = cells[0]; continue
                if cells[2]:
                    rows.append({"department": dept or "", "item_code": cells[2], "handling_status": cells[3] if len(cells) > 3 else "",
                        "cost": cells[4] if len(cells) > 4 else None, "avg_value": cells[5] if len(cells) > 5 else None,
                        "turnover_rate": cells[6] if len(cells) > 6 else None, "avg_days_on_hand": cells[7] if len(cells) > 7 else None})
        return rows
    def parse_row(self, raw: dict[str, str]) -> dict | None:
        if not raw.get("item_code"): return None
        return {"department": raw.get("department", ""), "item_code": raw.get("item_code", ""), "handling_status": raw.get("handling_status", ""),
            "cost": self._f(raw.get("cost")), "avg_value": self._f(raw.get("avg_value")), "turnover_rate": self._f(raw.get("turnover_rate")),
            "avg_days_on_hand": self._f(raw.get("avg_days_on_hand"))}
    def upsert_sql(self) -> str:
        return "INSERT OR REPLACE INTO nst_turnover (department, item_code, handling_status, cost, avg_value, turnover_rate, avg_days_on_hand) VALUES (:department, :item_code, :handling_status, :cost, :avg_value, :turnover_rate, :avg_days_on_hand)"
    @staticmethod
    def _extract_cells(row) -> list[str]:
        # 处理 ss:Index 稀疏列偏移（避免空 cell 导致字段错位）
        cells = {}; col_index = 0
        for c in row.findall("ss:Cell", NS):
            idx = c.get("{urn:schemas-microsoft-com:office:spreadsheet}Index")
            if idx: col_index = int(idx) - 1
            d = c.find("ss:Data", NS)
            cells[col_index] = d.text if d is not None and d.text else ""
            col_index += 1
        max_idx = max(cells.keys()) if cells else 0
        return [cells.get(i, "") for i in range(max_idx + 1)]
    @staticmethod
    def _f(val: Optional[str]) -> Optional[float]:
        if not val or val.strip() == "": return None
        try: return float(val)
        except ValueError: return None


class StoreSalesIngestor(XMLIngestor):
    ingestor_name = "xml_netsuite.StoreSalesIngestor"
    target_table = "nst_store_sales"
    # 日文表头 → 英文字段名（自适应两种文件格式）
    HEADER_MAP = {
        "FB_店舗": "fb_store", "アイテム": "item_code", "UPCコード": "upc",
        "取扱区分": "handling_status", "取扱区分: 名前": "handling_status",
        "表示名": "display_name", "販売数量": "qty_sold",
        "購入価格": "unit_price", "総収益": "revenue",
        "定義原価": "defined_cost", "粗利": "gross_profit",
        "粗利率": "gross_margin", "商品ランク": "rank",
    }
    def parse_rows(self, path: str) -> list[dict]:
        rows = []
        tree = ET.parse(path)
        for ws in tree.getroot().findall("ss:Worksheet", NS):
            table = ws.find("ss:Table", NS)
            if table is None: continue
            all_rows = table.findall("ss:Row", NS)
            if len(all_rows) < 8: continue
            # 第 7 行（index 6）是 header
            header_cells = self._extract_cells(all_rows[6])
            col_map = {i: self.HEADER_MAP[h.strip()] for i, h in enumerate(header_cells) if h and h.strip() in self.HEADER_MAP}
            if not col_map: continue
            for row in all_rows[7:]:
                cells = self._extract_cells(row)
                if not cells or all(c == "" for c in cells): continue
                if len(cells) < 2 or not cells[0] or not cells[1]: continue
                row_data = {field: cells[i] if i < len(cells) else "" for i, field in col_map.items()}
                if row_data.get("fb_store") and row_data.get("item_code"):
                    rows.append(row_data)
        return rows
    def parse_row(self, raw: dict[str, str]) -> dict | None:
        if not raw.get("fb_store") or not raw.get("item_code"): return None
        return {"fb_store": raw.get("fb_store", ""), "item_code": raw.get("item_code", ""), "upc": raw.get("upc", ""),
            "handling_status": raw.get("handling_status", ""), "display_name": raw.get("display_name", ""),
            "qty_sold": self._f(raw.get("qty_sold")), "unit_price": self._f(raw.get("unit_price")), "revenue": self._f(raw.get("revenue")),
            "defined_cost": self._f(raw.get("defined_cost")), "gross_profit": self._f(raw.get("gross_profit")),
            "gross_margin": self._f(raw.get("gross_margin")), "rank": raw.get("rank", "")}
    def upsert_sql(self) -> str:
        return "INSERT OR REPLACE INTO nst_store_sales (fb_store, item_code, upc, handling_status, display_name, qty_sold, unit_price, revenue, defined_cost, gross_profit, gross_margin, rank) VALUES (:fb_store, :item_code, :upc, :handling_status, :display_name, :qty_sold, :unit_price, :revenue, :defined_cost, :gross_profit, :gross_margin, :rank)"
    @staticmethod
    def _extract_cells(row) -> list[str]:
        # 处理 ss:Index 稀疏列偏移（避免空 cell 导致字段错位）
        cells = {}; col_index = 0
        for c in row.findall("ss:Cell", NS):
            idx = c.get("{urn:schemas-microsoft-com:office:spreadsheet}Index")
            if idx: col_index = int(idx) - 1
            d = c.find("ss:Data", NS)
            cells[col_index] = d.text if d is not None and d.text else ""
            col_index += 1
        max_idx = max(cells.keys()) if cells else 0
        return [cells.get(i, "") for i in range(max_idx + 1)]
    @staticmethod
    def _f(val: Optional[str]) -> Optional[float]:
        if not val or val.strip() == "": return None
        try: return float(val)
        except ValueError: return None


class InventoryIngestor(XMLIngestor):
    ingestor_name = "xml_netsuite.InventoryIngestor"
    target_table = "nst_inventory_snapshot"
    DEPT_FILTER = "輸出事業"
    def parse_rows(self, path: str) -> list[dict]:
        rows = []
        tree = ET.parse(path)
        for ws in tree.getroot().findall("ss:Worksheet", NS):
            table = ws.find("ss:Table", NS)
            if table is None: continue
            all_rows = table.findall("ss:Row", NS)
            if len(all_rows) < 2: continue
            for row in all_rows[1:]:
                cells = self._extract_cells(row)
                if not cells or all(c == "" for c in cells): continue
                if not cells[0] or not cells[1]: continue
                dept = cells[15] if len(cells) > 15 else ""
                if not dept.startswith(self.DEPT_FILTER): continue
                rows.append({"internal_id": cells[0], "item_code": cells[1], "upc": cells[2] if len(cells) > 2 else "",
                    "display_name": cells[3] if len(cells) > 3 else "", "status": cells[4] if len(cells) > 4 else "",
                    "bin_number": cells[5] if len(cells) > 5 else "", "location": cells[6] if len(cells) > 6 else "",
                    "handling_status": cells[7] if len(cells) > 7 else "", "qty_on_hand": cells[8] if len(cells) > 8 else None,
                    "qty_committed": cells[9] if len(cells) > 9 else None, "qty_backorder": cells[10] if len(cells) > 10 else None,
                    "std_cost": cells[11] if len(cells) > 11 else None, "total_amount": cells[12] if len(cells) > 12 else None,
                    "avg_cost": cells[13] if len(cells) > 13 else None, "owner": cells[14] if len(cells) > 14 else "", "department": dept})
        return rows
    def parse_row(self, raw: dict[str, str]) -> dict | None:
        if not raw.get("internal_id") or not raw.get("item_code"): return None
        return {"internal_id": raw.get("internal_id", ""), "item_code": raw.get("item_code", ""), "upc": raw.get("upc", ""),
            "display_name": raw.get("display_name", ""), "status": raw.get("status", ""), "bin_number": raw.get("bin_number", ""),
            "location": raw.get("location", ""), "handling_status": raw.get("handling_status", ""),
            "qty_on_hand": self._f(raw.get("qty_on_hand")), "qty_committed": self._f(raw.get("qty_committed")),
            "qty_backorder": self._f(raw.get("qty_backorder")), "std_cost": self._f(raw.get("std_cost")),
            "total_amount": self._f(raw.get("total_amount")), "avg_cost": self._f(raw.get("avg_cost")),
            "owner": raw.get("owner", ""), "department": raw.get("department", "")}
    def upsert_sql(self) -> str:
        return "INSERT OR REPLACE INTO nst_inventory_snapshot (internal_id, item_code, upc, display_name, status, bin_number, location, handling_status, qty_on_hand, qty_committed, qty_backorder, std_cost, total_amount, avg_cost, owner, department) VALUES (:internal_id, :item_code, :upc, :display_name, :status, :bin_number, :location, :handling_status, :qty_on_hand, :qty_committed, :qty_backorder, :std_cost, :total_amount, :avg_cost, :owner, :department)"
    @staticmethod
    def _extract_cells(row) -> list[str]:
        cells = {}; col_index = 0
        for c in row.findall("ss:Cell", NS):
            idx = c.get("{urn:schemas-microsoft-com:office:spreadsheet}Index")
            if idx: col_index = int(idx) - 1
            d = c.find("ss:Data", NS)
            cells[col_index] = d.text if d is not None and d.text else ""
            col_index += 1
        max_idx = max(cells.keys()) if cells else 0
        return [cells.get(i, "") for i in range(max_idx + 1)]
    @staticmethod
    def _f(val: Optional[str]) -> Optional[float]:
        if not val or val.strip() == "": return None
        try: return float(val)
        except ValueError: return None


def main():
    from data_warehouse.db.migrations import run as init_db
    conn = init_db(str(Path("data_warehouse/warehouse.db")))
    files = [("【ASEAN】在庫回転率699.xls", TurnoverIngestor()),
        ("【ASEAN】店舗別売上　集計専用-694.xls", StoreSalesIngestor()),
        ("【輸出】店舗別売上_JO-366.xls", StoreSalesIngestor()),
        ("輸出通常在庫数残数検索結果480.xls", InventoryIngestor())]
    print("=" * 60 + "\nNetSuite XML Ingestor Start\n" + "=" * 60)
    for path, ingestor in files:
        if not Path(path).exists(): print(f"\n✗ {path} - NOT FOUND"); continue
        print(f"\n{ingestor.ingestor_name}\n  File: {path}")
        try:
            r = ingestor.run(path, conn, source_name=path)
            print(f"  Total: {r['total_rows']} | Inserted: {r['inserted']} | Errors: {r['errors']} | Skipped: {r['skipped']}")
        except Exception as e: print(f"  ✗ Error: {e}")
    conn.close()
    print("\n" + "=" * 60 + "\nNetSuite XML Ingestor Complete\n" + "=" * 60)
if __name__ == "__main__": main()
