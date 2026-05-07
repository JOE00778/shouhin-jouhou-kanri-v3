#!/usr/bin/env python3
"""Shopee 类目映射 + 必填属性填充（T-304）

Module entry:
    from category_mapper import pick_category, fill_attributes
    cat_id = pick_category(spu, listing)
    attrs = fill_attributes(cat_id, spu, listing)

CLI:
    python3 scripts/category_mapper.py <draft.json>
        # draft.json 是 listing_generator 输出的 ListingDraft.to_dict()
        # 需要再带 --spu-input <csv> 才能拿到 SPU
    python3 scripts/category_mapper.py --spu-input examples/spu_input_sample.csv \
        --draft-dir drafts/

Design:
- 类目映射规则进 config/category_rules.yaml（关键词、品牌覆盖、hint 兜底）
- 共性属性默认值进 config/attr_defaults.yaml
- 5 类目 ID:
    100629 食品/饮料
    100630 化妆/个护（默认 fallback）
    100632 母婴/营养/药品
    100638 文具/办公
    100640 综合/电器/收纳
- 不调 Shopee API（无线上类目验证）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import yaml

# 让兄弟模块可导入
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from parse_spu_input import SPU, Variant, parse_spu_input_csv  # noqa: E402
from listing_generator import ListingDraft  # noqa: E402


PROJECT_ROOT = _SCRIPTS_DIR.parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "category_rules.yaml"
DEFAULT_DEFAULTS_PATH = PROJECT_ROOT / "config" / "attr_defaults.yaml"

VALID_CATEGORIES = {"100629", "100630", "100632", "100638", "100640"}
DEFAULT_FALLBACK = "100630"
NOT_SPECIFIED = "Not Specified"


class CategoryMapperError(Exception):
    """配置加载 / 验证失败时抛出。"""


# --------------------------------------------------------------------------- #
# 配置加载（缓存以减少 IO）
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=8)
def _load_yaml(path_str: str) -> Dict[str, Any]:
    p = Path(path_str)
    if not p.exists():
        raise CategoryMapperError(f"配置文件不存在: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise CategoryMapperError(f"配置文件格式错误（顶层非 mapping）: {p}")
    return data


def load_category_rules(path: Path = DEFAULT_RULES_PATH) -> Dict[str, Any]:
    """加载类目规则 yaml。"""
    rules = _load_yaml(str(path))
    fallback = rules.get("fallback", DEFAULT_FALLBACK)
    if fallback not in VALID_CATEGORIES:
        raise CategoryMapperError(
            f"category_rules.fallback {fallback!r} 不在 5 类目 {sorted(VALID_CATEGORIES)} 内"
        )
    return rules


def load_attr_defaults(path: Path = DEFAULT_DEFAULTS_PATH) -> Dict[str, Any]:
    """加载属性默认值 yaml。"""
    return _load_yaml(str(path))


# --------------------------------------------------------------------------- #
# pick_category
# --------------------------------------------------------------------------- #


def _build_haystack(spu: SPU, listing: Optional[ListingDraft]) -> str:
    """把所有可能含品类信息的字段拼成一个小写字符串。"""
    parts: List[str] = []
    if spu.spu_key:
        parts.append(spu.spu_key)
    if spu.category_hint:
        parts.append(spu.category_hint)
    for v in spu.variants:
        if v.notes:
            parts.append(v.notes)
        for k, val in v.variant_attrs.items():
            parts.append(f"{k} {val}")
    if listing is not None:
        if listing.title:
            parts.append(listing.title)
        if listing.description:
            parts.append(listing.description)
        if listing.brand_normalized:
            parts.append(listing.brand_normalized)
        # spec_json 也扫一下（可能含 "Type: Lipstick" 等）
        if listing.spec_json:
            try:
                parts.append(json.dumps(listing.spec_json, ensure_ascii=False))
            except (TypeError, ValueError):
                pass
    return " \n ".join(parts).lower()


def _detect_brand(spu: SPU, listing: Optional[ListingDraft]) -> Optional[str]:
    if listing is not None and listing.brand_normalized:
        return listing.brand_normalized.strip() or None
    # 退而求其次：从 notes 找首个像品牌的 token
    for v in spu.variants:
        if v.notes:
            tokens = v.notes.split()
            for tok in tokens:
                if tok and tok[0].isalpha() and len(tok) >= 3:
                    return tok
    return None


def _category_hint_lookup(
    category_hint: Optional[str], hint_map: Mapping[str, str]
) -> Optional[str]:
    if not category_hint:
        return None
    # 取 slash 前的首段，全小写
    head = category_hint.split("/", 1)[0].strip().lower()
    cat = hint_map.get(head)
    if cat in VALID_CATEGORIES:
        return cat
    return None


def pick_category(
    spu: SPU,
    listing: Optional[ListingDraft] = None,
    *,
    rules: Optional[Mapping[str, Any]] = None,
) -> str:
    """SPU + ListingDraft → 类目 ID（5 选 1）。

    Args:
        spu: 已解析的 SPU
        listing: 可选 ListingDraft（提供 title/description/brand 用于关键词匹配）
        rules: 测试可注入的 rules dict；缺省读 config/category_rules.yaml

    Returns:
        100629 / 100630 / 100632 / 100638 / 100640 之一
    """
    if rules is None:
        rules = load_category_rules()

    fallback = rules.get("fallback", DEFAULT_FALLBACK)
    priority = rules.get("priority_order") or [
        "100632",
        "100629",
        "100638",
        "100640",
        "100630",
    ]
    brand_overrides: Mapping[str, str] = rules.get("brand_overrides", {}) or {}
    keywords: Mapping[str, Sequence[str]] = rules.get("keywords", {}) or {}
    hint_map: Mapping[str, str] = rules.get("category_hint_map", {}) or {}

    # 1. brand override
    brand = _detect_brand(spu, listing)
    if brand:
        # 大小写不敏感匹配
        brand_lower = brand.lower()
        for b, cat in brand_overrides.items():
            if b.lower() == brand_lower and cat in VALID_CATEGORIES:
                return cat

    haystack = _build_haystack(spu, listing)

    # 2. 关键词（按 priority_order）
    for cat in priority:
        if cat not in VALID_CATEGORIES:
            continue
        kws = keywords.get(cat, []) or []
        for kw in kws:
            if not kw:
                continue
            if kw.lower() in haystack:
                return cat

    # 3. category_hint slash-prefix
    cat = _category_hint_lookup(spu.category_hint, hint_map)
    if cat:
        return cat

    # 4. fallback
    if fallback not in VALID_CATEGORIES:
        return DEFAULT_FALLBACK
    return fallback


# --------------------------------------------------------------------------- #
# fill_attributes
# --------------------------------------------------------------------------- #


def _infer_texture(
    haystack: str,
    category_id: str,
    common: Mapping[str, Any],
) -> str:
    """100036 质地：先扫 texture_keywords，命中则用对应 texture；否则按 category_default。"""
    t_block = common.get("100036", {}) or {}
    keyword_map: Mapping[str, Sequence[str]] = t_block.get("texture_keywords", {}) or {}
    for texture, kws in keyword_map.items():
        for kw in kws or []:
            if kw and kw.lower() in haystack:
                return texture
    cat_default = (t_block.get("category_default") or {}).get(category_id)
    if cat_default:
        return cat_default
    return NOT_SPECIFIED


def _extract_volume(spec_json: Optional[Mapping[str, Any]]) -> Optional[str]:
    """从 spec_json 抽 'volume' / '容量' / 'size' 等键的字符串值。"""
    if not spec_json:
        return None
    for key in ("volume", "Volume", "容量", "size", "Size", "net_weight", "Net Weight"):
        v = spec_json.get(key)
        if v not in (None, ""):
            return str(v)
    return None


def _extract_packaging_dims(spec_json: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not spec_json:
        return None
    # 期望键：dimensions / 长宽高 / package_size
    for key in ("dimensions", "Dimensions", "package_size", "Package Size", "包装尺寸"):
        v = spec_json.get(key)
        if v not in (None, ""):
            return str(v)
    # 长×宽×高 三件套
    L = spec_json.get("length") or spec_json.get("L")
    W = spec_json.get("width") or spec_json.get("W")
    H = spec_json.get("height") or spec_json.get("H")
    if L and W and H:
        return f"{L}x{W}x{H}"
    return None


def _extract_weight(
    spu: SPU, listing: Optional[ListingDraft]
) -> Optional[float]:
    """从 SPU 第一个 variant 拿 weight_grams；失败再从 spec_json 找。"""
    for v in spu.variants:
        if v.weight_grams:
            return float(v.weight_grams)
    if listing is not None and listing.spec_json:
        for key in ("weight", "Weight", "weight_grams", "重量"):
            val = listing.spec_json.get(key)
            if val not in (None, ""):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None
    return None


def _extract_material(
    spu: SPU, listing: Optional[ListingDraft]
) -> Optional[str]:
    if listing is not None and listing.spec_json:
        for key in ("material", "Material", "材质"):
            v = listing.spec_json.get(key)
            if v not in (None, ""):
                return str(v)
    return None


def fill_attributes(
    category_id: str,
    spu: SPU,
    listing: Optional[ListingDraft] = None,
    *,
    defaults: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """按类目填 11 个共性属性。返回 {attr_id: value} dict。

    Args:
        category_id: pick_category 返回的 5 选 1
        spu: SPU 对象
        listing: 可选 ListingDraft
        defaults: 测试可注入；缺省读 config/attr_defaults.yaml

    Returns:
        {
          "100036": <质地>,
          "100037": "Japan",
          "100095": <重量>|"Not Specified",
          "100134": <材质>|"Not Specified",
          "100162": "Plain",
          "100248": <体积>|"Not Specified",
          "100999": 1,
          "101029": <包装尺寸>|"Not Specified",
          "101081": <产品类型>,    # 仅 4/5 类目
          "101219": "No",
          "102412": "No",
        }

    缺失值统一退到 "Not Specified"。
    """
    if category_id not in VALID_CATEGORIES:
        raise CategoryMapperError(
            f"category_id {category_id!r} 不在 5 类目内 {sorted(VALID_CATEGORIES)}"
        )

    if defaults is None:
        defaults = load_attr_defaults()

    common: Mapping[str, Any] = defaults.get("common", {}) or {}
    not_specified = defaults.get("NOT_SPECIFIED", NOT_SPECIFIED)
    haystack = _build_haystack(spu, listing)

    out: Dict[str, Any] = {}

    # 100037 原产地
    out["100037"] = (common.get("100037", {}) or {}).get("default", "Japan")

    # 100036 质地（先关键词后 category_default）
    out["100036"] = _infer_texture(haystack, category_id, common)

    # 100095 重量
    weight = _extract_weight(spu, listing)
    out["100095"] = weight if weight is not None else not_specified

    # 100134 材质
    material = _extract_material(spu, listing)
    if material:
        out["100134"] = material
    else:
        out["100134"] = (common.get("100134", {}) or {}).get(
            "default", not_specified
        )

    # 100162 图案
    out["100162"] = (common.get("100162", {}) or {}).get("default", "Plain")

    # 100248 体积
    volume = _extract_volume(listing.spec_json if listing else None)
    out["100248"] = volume if volume else not_specified

    # 100999 数量
    out["100999"] = (common.get("100999", {}) or {}).get("default", 1)

    # 101029 包装尺寸
    pkg = _extract_packaging_dims(listing.spec_json if listing else None)
    out["101029"] = pkg if pkg else not_specified

    # 101081 产品类型（按类目）
    pt_block = common.get("101081", {}) or {}
    pt_default = (pt_block.get("category_default") or {}).get(category_id)
    out["101081"] = pt_default if pt_default else not_specified

    # 101219 Custom Product
    out["101219"] = (common.get("101219", {}) or {}).get("default", "No")

    # 102412 Bundle
    out["102412"] = (common.get("102412", {}) or {}).get("default", "No")

    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="category_mapper",
        description="Shopee 类目映射 + 必填属性填充（T-304）",
    )
    p.add_argument(
        "--spu-input",
        required=True,
        help="SPU CSV（同 T-301 格式）",
    )
    p.add_argument(
        "--draft-dir",
        default=None,
        help="可选：listing_generator 输出 JSON 所在目录。"
        "若提供，则用 ListingDraft 增强匹配；否则只用 SPU。",
    )
    p.add_argument(
        "--output",
        default="-",
        help="输出 JSON 路径（- 表示 stdout）",
    )
    p.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help=f"category_rules.yaml 路径（默认 {DEFAULT_RULES_PATH}）",
    )
    p.add_argument(
        "--defaults",
        default=str(DEFAULT_DEFAULTS_PATH),
        help=f"attr_defaults.yaml 路径（默认 {DEFAULT_DEFAULTS_PATH}）",
    )
    return p


def _maybe_load_draft(draft_dir: Optional[Path], spu_key: str) -> Optional[ListingDraft]:
    if draft_dir is None:
        return None
    p = draft_dir / f"{spu_key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"warning: 解析 draft {p} 失败：{e}; 跳过 listing 增强",
            file=sys.stderr,
        )
        return None
    return ListingDraft(
        title=data.get("title", ""),
        description=data.get("description", ""),
        key_features=list(data.get("key_features", []) or []),
        how_to_use=list(data.get("how_to_use", []) or []),
        ingredients=data.get("ingredients"),
        spec_json=dict(data.get("spec_json", {}) or {}),
        brand_normalized=data.get("brand_normalized", ""),
        hook=data.get("hook", ""),
        model=data.get("model", ""),
        spu_key=data.get("spu_key", spu_key),
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    rules = _load_yaml(args.rules)
    defaults = _load_yaml(args.defaults)

    try:
        spus = parse_spu_input_csv(args.spu_input)
    except Exception as e:  # noqa: BLE001
        print(f"error: 解析 SPU CSV 失败: {e}", file=sys.stderr)
        return 2

    draft_dir = Path(args.draft_dir) if args.draft_dir else None

    out_records: List[Dict[str, Any]] = []
    for spu in spus:
        listing = _maybe_load_draft(draft_dir, spu.spu_key)
        cat_id = pick_category(spu, listing, rules=rules)
        attrs = fill_attributes(cat_id, spu, listing, defaults=defaults)
        out_records.append(
            {
                "spu_key": spu.spu_key,
                "category_id": cat_id,
                "attributes": attrs,
            }
        )

    payload = json.dumps(out_records, ensure_ascii=False, indent=2)
    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
