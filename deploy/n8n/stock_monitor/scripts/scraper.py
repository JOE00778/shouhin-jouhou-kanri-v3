"""JAN 状态抓取封装。

实测结论（2026-04-19）：
- NETde卸: /list.php?keyword=<JAN> 支持 JAN 索引；产品若停产，标题带 "【販売終了】" 前缀。
- SUPER DELIVERY: /p/do/psl/?word=<JAN> 不支持按 JAN 搜索（几乎都 no-search-product）。
  故主查 NETde卸，SD 作为弱验证暂只记录 no-search-product 标记。
"""
from __future__ import annotations

import http.cookiejar as cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from config import (
    COOKIE_NETDEOROSHI,
    COOKIE_SUPERDELIVERY,
    REQ_INTERVAL_SEC,
    REQ_TIMEOUT_SEC,
    UA,
)


@dataclass
class LookupResult:
    jan: str
    source: str
    status: str                      # active / discontinued / notfound / error
    hit_count: int = 0
    matched_name: Optional[str] = None
    matched_product_id: Optional[str] = None
    raw_url: Optional[str] = None
    note: Optional[str] = None


def _load_cookies(path) -> cookiejar.CookieJar:
    jar = cookiejar.MozillaCookieJar()
    if path.exists():
        jar.load(str(path), ignore_discard=True, ignore_expires=True)
    return jar


def _build_opener(jar: cookiejar.CookieJar) -> urllib.request.OpenerDirector:
    handler = urllib.request.HTTPCookieProcessor(jar)
    opener = urllib.request.build_opener(handler)
    opener.addheaders = [
        ("User-Agent", UA),
        ("Accept-Language", "ja,en;q=0.8"),
    ]
    return opener


def _fetch(opener, url: str) -> tuple[int, str]:
    try:
        with opener.open(url, timeout=REQ_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return -1, str(e)


# ============================================================
# NETde卸
# ============================================================

NETDEOROSHI_SEARCH_URL = "https://netdeoroshi.com/list.php?keyword={q}"

_RE_NETDE_COUNT = re.compile(
    r'<div class="__item-count">\s*<span>([0-9]+)</span>\s*件中', re.S
)
# 主结果区：以 __block--recommend 为边界，取其之前的 HTML
_RE_NETDE_RECOMMEND = re.compile(r'class="__block __block--recommend"')
# 商品卡：<h2|h3 class="__title">标题</h2|h3>，同时抓取前面的 product.php?id=XXX
_RE_NETDE_ITEM = re.compile(
    r'product\.php\?id=([0-9]+)[^<]*(?:<[^>]+>\s*)*<h[23] class="__title">\s*([^<]+?)\s*</h[23]>',
    re.S,
)


def _parse_netde(body: str) -> tuple[int, list[dict]]:
    """返回 (main_result_count, [ {id, name} ])；解析主结果块，忽略「おすすめ商品」推荐区。"""
    if "見つかりませんでした" in body:
        return 0, []

    # 切分主结果区与推荐区
    rec = _RE_NETDE_RECOMMEND.search(body)
    main_body = body[: rec.start()] if rec else body

    m = _RE_NETDE_COUNT.search(main_body)
    count = int(m.group(1)) if m else 0

    items: list[dict] = []
    for mm in _RE_NETDE_ITEM.finditer(main_body):
        items.append({"id": mm.group(1), "name": mm.group(2).strip()})

    return count, items


def lookup_netdeoroshi(jan: str, opener=None) -> LookupResult:
    """NETde卸 按 JAN 查询；无需登录 Cookie。"""
    if opener is None:
        # Cookie 可选：有就带，没有也能查
        opener = _build_opener(_load_cookies(COOKIE_NETDEOROSHI))
    url = NETDEOROSHI_SEARCH_URL.format(q=urllib.parse.quote(jan))
    status_code, body = _fetch(opener, url)

    if status_code != 200:
        return LookupResult(jan, "netdeoroshi", "error", raw_url=url,
                            note=f"HTTP {status_code}")

    count, items = _parse_netde(body)
    if count == 0 or not items:
        return LookupResult(jan, "netdeoroshi", "notfound", 0, raw_url=url)

    first = items[0]
    name = first.get("name", "")
    # 6 个关键字：販売終了、取扱終了、廃番、製造中止、メーカー欠品、リニューアル
    discontinued_keywords = [
        "【販売終了】", "【取扱終了】", "【廃番】",
        "【製造中止】", "【メーカー欠品】", "【リニューアル】"
    ]
    discontinued = any(kw in name for kw in discontinued_keywords)

    status = "discontinued" if discontinued else "active"
    return LookupResult(
        jan=jan, source="netdeoroshi", status=status, hit_count=count,
        matched_name=name, matched_product_id=first.get("id"), raw_url=url,
    )


# ============================================================
# SUPER DELIVERY（辅助，不按 JAN 索引，留作扩展）
# ============================================================

SUPERDELIVERY_SEARCH_URL = "https://www.superdelivery.com/p/do/psl/?word={q}"


def lookup_superdelivery(jan: str, opener=None) -> LookupResult:
    """SD 不索引 JAN，这里仅返回 notfound（结构保留以便后续扩展为按商品名搜索）"""
    if opener is None:
        opener = _build_opener(_load_cookies(COOKIE_SUPERDELIVERY))
    url = SUPERDELIVERY_SEARCH_URL.format(q=urllib.parse.quote(jan))
    status_code, body = _fetch(opener, url)

    if status_code != 200:
        return LookupResult(jan, "superdelivery", "error", raw_url=url,
                            note=f"HTTP {status_code}")

    if "no-search-product" in body:
        return LookupResult(jan, "superdelivery", "notfound", raw_url=url,
                            note="SD 不索引 JAN，一般均 notfound")
    return LookupResult(jan, "superdelivery", "active", raw_url=url,
                        note="SD 命中 JAN 字段（少见）")


def throttle():
    time.sleep(REQ_INTERVAL_SEC)
