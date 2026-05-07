#!/usr/bin/env python3
"""
分析 Shopee 已在售产品的标题/描述/规格格式，输出统计报告。

输入：shopee上架模板/mass_update_basic_info_*.xlsx
输出：
  - 控制台统计报告
  - shopee-listing/docs/output/existing_titles_clean.csv     · 干净的标题清单
  - shopee-listing/docs/output/existing_descriptions.csv     · 干净的描述清单
  - shopee-listing/docs/output/brand_top.csv                 · 品牌频次
  - shopee-listing/docs/output/spec_units.csv                · 规格单位频次

用法：
  cd ~/CC && python3 shopee-listing/scripts/analyze_existing.py
"""
from __future__ import annotations
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from python_calamine import CalamineWorkbook
except ImportError:
    sys.exit("缺 python-calamine。安装：pip3 install --break-system-packages python-calamine")

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "shopee上架模板"
OUTPUT_DIR = ROOT / "docs" / "output"

HOOK_PATTERNS = [
    "Direct from Japan", "Direct From Japan",
    "Made in Japan", "Authentic Japanese",
    "Japanese", "Imported from Japan",
]

DESC_SECTIONS = [
    "Key Features", "Features", "How to Use", "How to use",
    "Country of origin", "Ingredients", "Notes", "[NOTE]",
    "Specifications", "Smikie Japan", "Shipping & Processing",
]

SPEC_UNIT_RE = re.compile(
    r"\d+\.?\d*\s?(g|ml|kg|mg|L|oz|cm|mm|pcs|pack|sheet|sheets|tablet|tablets|capsule|capsules|piece|pieces)\b",
    re.IGNORECASE,
)


def find_mass_update_file() -> Path:
    candidates = sorted(TEMPLATE_DIR.glob("mass_update_basic_info_*.xlsx"))
    if not candidates:
        sys.exit(f"找不到 mass_update_basic_info_*.xlsx 在 {TEMPLATE_DIR}")
    return candidates[-1]


def load_products(xlsx: Path) -> list[dict]:
    wb = CalamineWorkbook.from_path(str(xlsx))
    rows = wb.get_sheet_by_name("Sheet1").to_python()
    out = []
    for r in rows[3:]:
        if not r[2]:
            continue
        if len(str(r[2])) <= 5:
            continue
        out.append({
            "product_id": str(r[0]) if r[0] else "",
            "parent_sku": str(r[1]) if r[1] else "",
            "title": str(r[2]),
            "description": str(r[3]) if r[3] else "",
        })
    return out


def analyze(prods: list[dict]) -> dict:
    title_lens = [len(p["title"]) for p in prods]
    desc_lens = [len(p["description"]) for p in prods if p["description"]]

    brands = Counter()
    for p in prods:
        first = p["title"].strip().split()[0] if p["title"] else ""
        if first:
            brands[first] += 1

    hooks = Counter()
    for p in prods:
        tail = p["title"][-50:].lower()
        for kw in HOOK_PATTERNS:
            if kw.lower() in tail:
                hooks[kw] += 1
                break

    spec_units = Counter()
    for p in prods:
        for m in SPEC_UNIT_RE.findall(p["title"]):
            spec_units[m.lower()] += 1

    sections = Counter()
    for p in prods:
        d = p["description"]
        if not d:
            continue
        for sec in DESC_SECTIONS:
            if sec in d:
                sections[sec] += 1

    return {
        "n": len(prods),
        "title": {
            "min": min(title_lens) if title_lens else 0,
            "max": max(title_lens) if title_lens else 0,
            "avg": sum(title_lens) // len(title_lens) if title_lens else 0,
        },
        "desc": {
            "min": min(desc_lens) if desc_lens else 0,
            "max": max(desc_lens) if desc_lens else 0,
            "avg": sum(desc_lens) // len(desc_lens) if desc_lens else 0,
            "count": len(desc_lens),
        },
        "brands": brands,
        "hooks": hooks,
        "spec_units": spec_units,
        "sections": sections,
    }


def write_outputs(prods: list[dict], stats: dict):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with (OUTPUT_DIR / "existing_titles_clean.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product_id", "parent_sku", "title_len", "title"])
        for p in prods:
            w.writerow([p["product_id"], p["parent_sku"], len(p["title"]), p["title"]])

    with (OUTPUT_DIR / "existing_descriptions.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product_id", "parent_sku", "desc_len", "description"])
        for p in prods:
            w.writerow([p["product_id"], p["parent_sku"], len(p["description"]), p["description"]])

    with (OUTPUT_DIR / "brand_top.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "count"])
        for b, c in stats["brands"].most_common():
            w.writerow([b, c])

    with (OUTPUT_DIR / "spec_units.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unit", "count"])
        for u, c in stats["spec_units"].most_common():
            w.writerow([u, c])


def print_report(stats: dict):
    print(f"\n=== 分析报告 ===")
    print(f"有效产品数: {stats['n']}")
    print(f"\n标题长度: min={stats['title']['min']}  max={stats['title']['max']}  avg={stats['title']['avg']}")
    print(f"描述长度: min={stats['desc']['min']}  max={stats['desc']['max']}  avg={stats['desc']['avg']}  ({stats['desc']['count']} 条有描述)")

    print(f"\n--- Top 10 品牌 ---")
    for b, c in stats["brands"].most_common(10):
        print(f"  {c:5} × {b}")

    print(f"\n--- Hook 短语 ---")
    for k, c in stats["hooks"].most_common():
        print(f"  {c:5} × {k}")

    print(f"\n--- Top 10 规格单位 ---")
    for u, c in stats["spec_units"].most_common(10):
        print(f"  {c:5} × {u}")

    print(f"\n--- 描述 section 出现率 ---")
    for s, c in stats["sections"].most_common():
        pct = c * 100 // stats["n"] if stats["n"] else 0
        print(f"  {c:5} × {s}  ({pct}%)")


def main():
    xlsx = find_mass_update_file()
    print(f"📄 数据源: {xlsx.name}")
    prods = load_products(xlsx)
    stats = analyze(prods)
    print_report(stats)
    write_outputs(prods, stats)
    print(f"\n✅ 详细数据写入 {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
