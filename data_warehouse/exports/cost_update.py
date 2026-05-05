"""模块 #1 成本同步 · 输出 CSV 生成器。

输出格式（NetSuite CSV Import · Inventory Item Update）：
    Internal ID,Standard Cost
    1234,294
    1235,512
    ...

只输出 action=UPDATE 的行；行数 = 触发更新的 SKU 数。
"""
from __future__ import annotations

from .base import Exporter


class CostUpdateExporter(Exporter):
    exporter_name = "cost_update"
    headers = ["Internal ID", "Standard Cost"]
    file_prefix = "cost_update"

    @staticmethod
    def build_rows(decisions: list[dict]) -> list[dict]:
        """从 decide_action 的输出列表中筛 UPDATE 并转成 NetSuite 列名格式。"""
        return [
            {
                "Internal ID": d["internal_id"],
                "Standard Cost": int(d["std_cost_new"]),
            }
            for d in decisions
            if d.get("action") == "UPDATE"
        ]
