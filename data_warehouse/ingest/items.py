"""商品主档导入器。

支持两种数据源（不同 ingestor 子类）：

- `LocalItemMasterIngestor`：导入工作区里 `item_master_cleaned.csv` 形态（Boss 现有的本地表，13 列）
  - 没有 NetSuite Internal ID → 用 `item_code` (= JAN) 兜底填 internal_id
  - 没有 avg_cost / std_cost → 留空（NULL），等 NetSuite ingestor 补
- `NetSuiteItemIngestor`：导入 NetSuite Item Saved Search 导出（Phase 1 接入成本同步时实现）
  - 有真正的 Internal ID
  - 有 Average Cost / Standard Cost
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Ingestor


def _to_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LocalItemMasterIngestor(Ingestor):
    """导入 `item_master_cleaned.csv`（13 列日文表头）。

    用 item_code（商品コード = JAN）作为 internal_id 兜底，因为本地表没有 NetSuite Internal ID。
    后续 NetSuiteItemIngestor 会用真 Internal ID 通过 ON CONFLICT(item_code) 覆盖。
    """

    ingestor_name = "items.local"
    target_table = "item"
    required_columns = ["商品コード", "メーカー名", "商品名", "取扱区分"]
    column_aliases = {
        "商品コード": ["商品コード", "Item Code"],
        "メーカー名": ["メーカー名", "Maker"],
        "商品名": ["商品名", "Display Name", "Name"],
        "取扱区分": ["取扱区分", "Handling Status"],
    }

    def parse_row(self, raw: dict[str, str]) -> dict | None:
        item_code = (raw.get("商品コード") or "").strip()
        if not item_code:
            raise ValueError("商品コード 不能为空")

        handling = (raw.get("取扱区分") or "").strip()
        return {
            "internal_id": item_code,  # 本地无 NS ID，用 JAN 兜底
            "item_code": item_code,
            "jan": (raw.get("jan") or item_code).strip() or None,
            "display_name": (raw.get("商品名") or "").strip() or None,
            "maker": (raw.get("メーカー名") or "").strip() or None,
            "rank": (raw.get("ランク") or "").strip() or None,
            "handling_status": handling or None,
            "case_qty": _to_int(raw.get("ケース入数")),
            "order_lot": _to_int(raw.get("発注ロット")),
            "weight": _to_float(raw.get("重量")),
            "avg_cost": _to_float(raw.get("実績原価")),
            "std_cost": _to_float(raw.get("最安原価")),
            "inactive_flag": 1 if handling == "廃番" else 0,
            "source_file": self._current_source,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }

    # 用 ON CONFLICT(item_code) 覆盖 —— 这样以后 NS ingestor 用真 internal_id 进来时
    # 同 item_code 的记录被更新而不是插入新行（避免 UNIQUE 冲突）
    def upsert_sql(self) -> str:
        return """
        INSERT INTO item (
            internal_id, item_code, jan, display_name, maker, rank, handling_status,
            case_qty, order_lot, weight, avg_cost, std_cost, inactive_flag,
            source_file, imported_at
        ) VALUES (
            :internal_id, :item_code, :jan, :display_name, :maker, :rank, :handling_status,
            :case_qty, :order_lot, :weight, :avg_cost, :std_cost, :inactive_flag,
            :source_file, :imported_at
        )
        ON CONFLICT(item_code) DO UPDATE SET
            jan             = excluded.jan,
            display_name    = excluded.display_name,
            maker           = excluded.maker,
            rank            = excluded.rank,
            handling_status = excluded.handling_status,
            case_qty        = excluded.case_qty,
            order_lot       = excluded.order_lot,
            weight          = excluded.weight,
            avg_cost        = COALESCE(excluded.avg_cost, item.avg_cost),
            std_cost        = COALESCE(excluded.std_cost, item.std_cost),
            inactive_flag   = excluded.inactive_flag,
            source_file     = excluded.source_file,
            imported_at     = excluded.imported_at
        """

    # ------------------------------------------------------------
    # Tracker for source file (parse_row needs it)
    # ------------------------------------------------------------
    _current_source: str = ""

    def run(self, source, conn, *, source_name: str):
        self._current_source = source_name
        try:
            return super().run(source, conn, source_name=source_name)
        finally:
            self._current_source = ""
