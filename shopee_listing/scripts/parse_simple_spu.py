#!/usr/bin/env python3
"""极简 SPU 输入解析器（T-310）：A=SPU, B=SKU 两列 CSV → SPU 列表。

为店小秘批量上架方案设计的简化解析器。Boss 拍板：上架渠道改店小秘后，
原 T-301 的 8 列输入太重，改成两列：
  | A 列    | B 列         |
  |---------|--------------|
  | SPU     | SKU          |
  | MIYO-1  | 4902806441000 |
  | MIYO-1  | 4902806441017 |

合并规则：
  - A 列相同的多行 = 同一 SPU 的不同 variant
  - B 列既是 JAN 也是最终 SKU
  - 其它列（如有）忽略
  - JAN 在整个文件内必须全局唯一
  - 同一 SPU 内 variants 按 JAN 字典序排序
  - 输出 SPU 顺序按 spu_key 字典序

输出：T-301 同样的 `SPU` dataclass（`spu_key, category_hint=None,
variants=[Variant(jan, variant_attrs={}, weight_grams=None, notes=None)]`）

CLI 用法：
  python3 shopee-listing/scripts/parse_simple_spu.py <input.csv>

Module 用法：
  from parse_simple_spu import parse_simple_spu_csv
  spus = parse_simple_spu_csv(path)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Make sibling modules importable
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from parse_spu_input import SPU, SPUInputError, Variant, spus_to_jsonable  # noqa: E402


def _strip(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_simple_spu_csv(path: str | Path) -> List[SPU]:
    """读极简 CSV（A=SPU, B=SKU）并返回 SPU 列表。

    Raises:
        SPUInputError: 文件不存在 / 缺列 / 重复 JAN / SPU 或 SKU 为空。
    """
    p = Path(path)
    if not p.exists():
        raise SPUInputError(f"文件不存在: {p}")

    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration as e:
            raise SPUInputError("CSV 为空 / 缺表头") from e

        if len(header) < 2:
            raise SPUInputError(
                f"CSV 至少要有 2 列（A=SPU, B=SKU），实际表头: {header}"
            )

        spus_by_key: Dict[str, SPU] = {}
        seen_jans: Dict[str, int] = {}

        # 第 1 行是表头，数据从第 2 行开始
        for idx, row in enumerate(reader, start=2):
            if not row:
                continue
            # 跳过完全空行
            if not any(_strip(v) for v in row):
                continue

            spu_key = _strip(row[0]) if len(row) >= 1 else ""
            jan = _strip(row[1]) if len(row) >= 2 else ""

            if not spu_key:
                raise SPUInputError(f"line {idx}: A 列 (SPU) 为空（必填）")
            if not jan:
                raise SPUInputError(f"line {idx}: B 列 (SKU) 为空（必填）")

            if jan in seen_jans:
                raise SPUInputError(
                    f"line {idx}: B 列 (SKU) 出现重复值 {jan!r}（首次出现于 line {seen_jans[jan]}）"
                )
            seen_jans[jan] = idx

            variant = Variant(
                jan=jan,
                variant_attrs={},
                weight_grams=None,
                notes=None,
            )

            spu = spus_by_key.get(spu_key)
            if spu is None:
                spus_by_key[spu_key] = SPU(
                    spu_key=spu_key,
                    category_hint=None,
                    variants=[variant],
                )
            else:
                spu.variants.append(variant)

        # 排序
        for spu in spus_by_key.values():
            spu.variants.sort(key=lambda v: v.jan)

        return [spus_by_key[k] for k in sorted(spus_by_key.keys())]


def _main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python3 parse_simple_spu.py <input.csv>",
            file=sys.stderr,
        )
        return 2

    path = argv[1]
    try:
        spus = parse_simple_spu_csv(path)
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
