#!/usr/bin/env python3
"""
Shopee SPU 输入解析器：CSV → 内部 SPU 数据模型。

输入 CSV 列（顺序无所谓，但 spu_key + jan 必填）：
  spu_key, jan, category_hint, variant_attr_color,
  variant_attr_size, variant_attr_other, weight_grams, notes

合并规则：
  - 同 spu_key 的多行合并成一个 SPU 对象
  - variants[] 按 JAN 字典序排序
  - JAN 在整个文件内必须全局唯一
  - category_hint 取该 SPU 第一行非空的值（多行不一致时以首行为准 + warning 到 stderr）

输出（stdout）：JSON 数组，每元素对应一个 SPU。

CLI 用法：
  python3 shopee-listing/scripts/parse_spu_input.py <input.csv>

Module 用法：
  from parse_spu_input import parse_spu_input_csv
  spus = parse_spu_input_csv(path)

退出码：
  0 = 成功
  2 = 输入校验错误（缺列、缺 JAN、JAN 重复等）
  1 = 其他（IO 错误等）
"""
from __future__ import annotations

import csv
import dataclasses
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


REQUIRED_COLUMNS = ["spu_key", "jan"]
KNOWN_COLUMNS = [
    "spu_key",
    "jan",
    "category_hint",
    "variant_attr_color",
    "variant_attr_size",
    "variant_attr_other",
    "weight_grams",
    "notes",
]
VARIANT_ATTR_PREFIX = "variant_attr_"


class SPUInputError(ValueError):
    """输入 CSV 校验失败时抛出，message 已带行号/列名。"""


@dataclass
class Variant:
    jan: str
    variant_attrs: Dict[str, str] = field(default_factory=dict)
    weight_grams: Optional[float] = None
    notes: Optional[str] = None


@dataclass
class SPU:
    spu_key: str
    category_hint: Optional[str] = None
    variants: List[Variant] = field(default_factory=list)


def _strip(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_weight(raw: str, lineno: int) -> Optional[float]:
    s = _strip(raw)
    if not s:
        return None
    try:
        return float(s)
    except ValueError as e:
        raise SPUInputError(
            f"line {lineno}: column 'weight_grams' 不是合法数字（got {raw!r}）"
        ) from e


def _collect_variant_attrs(row: Dict[str, str]) -> Dict[str, str]:
    """把所有 variant_attr_* 列折叠成 {color: 'Red', size: 'M', ...}。空值跳过。"""
    out: Dict[str, str] = {}
    for col, raw in row.items():
        if col is None or not col.startswith(VARIANT_ATTR_PREFIX):
            continue
        key = col[len(VARIANT_ATTR_PREFIX):]
        val = _strip(raw)
        if val:
            out[key] = val
    return out


def parse_spu_input_csv(path: str | Path) -> List[SPU]:
    """读 CSV 并返回 SPU 列表（按 spu_key 字典序排序，每个 SPU 内 variants 按 JAN 排序）。"""
    p = Path(path)
    if not p.exists():
        raise SPUInputError(f"文件不存在: {p}")

    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SPUInputError("CSV 为空 / 缺表头")

        fieldnames = [_strip(c) for c in reader.fieldnames]
        missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
        if missing:
            raise SPUInputError(f"CSV 缺少必填列: {missing}（已有列 {fieldnames}）")

        spus_by_key: Dict[str, SPU] = {}
        seen_jans: Dict[str, int] = {}  # jan -> first lineno (for dup error msg)

        # csv.DictReader 行号：表头是第 1 行，第一条数据从第 2 行开始
        for idx, row in enumerate(reader, start=2):
            # 跳过完全空行
            if not any(_strip(v) for v in row.values()):
                continue

            spu_key = _strip(row.get("spu_key"))
            jan = _strip(row.get("jan"))

            if not spu_key:
                raise SPUInputError(f"line {idx}: column 'spu_key' 为空（必填）")
            if not jan:
                raise SPUInputError(f"line {idx}: column 'jan' 为空（必填）")

            if jan in seen_jans:
                raise SPUInputError(
                    f"line {idx}: column 'jan' 出现重复值 {jan!r}（首次出现于 line {seen_jans[jan]}）"
                )
            seen_jans[jan] = idx

            category_hint = _strip(row.get("category_hint")) or None
            weight_grams = _parse_weight(row.get("weight_grams", ""), idx)
            notes = _strip(row.get("notes")) or None
            variant_attrs = _collect_variant_attrs(row)

            variant = Variant(
                jan=jan,
                variant_attrs=variant_attrs,
                weight_grams=weight_grams,
                notes=notes,
            )

            spu = spus_by_key.get(spu_key)
            if spu is None:
                spu = SPU(spu_key=spu_key, category_hint=category_hint, variants=[variant])
                spus_by_key[spu_key] = spu
            else:
                # category_hint：以首行为准；若后续行给了不一致值 → 仅 warn 到 stderr
                if category_hint and not spu.category_hint:
                    spu.category_hint = category_hint
                elif (
                    category_hint
                    and spu.category_hint
                    and category_hint != spu.category_hint
                ):
                    print(
                        f"warning: line {idx}: spu_key={spu_key!r} 的 category_hint "
                        f"{category_hint!r} 与首行 {spu.category_hint!r} 不一致，已忽略后者",
                        file=sys.stderr,
                    )
                spu.variants.append(variant)

        # 排序：每 SPU 内 variants 按 JAN；输出 SPU 顺序按 spu_key 字典序
        for spu in spus_by_key.values():
            spu.variants.sort(key=lambda v: v.jan)

        return [spus_by_key[k] for k in sorted(spus_by_key.keys())]


def spus_to_jsonable(spus: Iterable[SPU]) -> List[Dict[str, Any]]:
    return [asdict(spu) for spu in spus]


def _main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python3 parse_spu_input.py <input.csv>",
            file=sys.stderr,
        )
        return 2

    path = argv[1]
    try:
        spus = parse_spu_input_csv(path)
    except SPUInputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: IO 错误 {e}", file=sys.stderr)
        return 1

    json.dump(spus_to_jsonable(spus), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
