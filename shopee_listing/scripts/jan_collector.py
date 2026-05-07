#!/usr/bin/env python3
"""JAN 商品信息采集器（Rakuten Ichiba API）

CLI 用法:
    RAKUTEN_APP_ID=xxxx python3 scripts/jan_collector.py 4902370548495 4901085196533

模块用法:
    from jan_collector import fetch_by_jan, fetch_batch, JanInfo
    info = fetch_by_jan("4902370548495")
    infos = fetch_batch(["4902370548495", "4901085196533"])

设计要点:
- 单 JAN: fetch_by_jan(jan)
- 批量:   fetch_batch(jans), 并发 <= 3, 全局 rate limit 1 req/s
- 缓存:   SQLite shopee-listing/.cache/jan_info.db, TTL 30 天
- 失败分类:
    1) 网络错误: 重试 3 次（指数退避）
    2) 0 命中:   found=False, 不重试，进缓存
    3) 配额(429): 退避 60s 后重试
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

RAKUTEN_API_URL = (
    "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"
)
DEFAULT_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / ".cache" / "jan_info.db"
)
DEFAULT_CACHE_TTL_DAYS = 30
MAX_CONCURRENCY = 3
RATE_LIMIT_INTERVAL_SEC = 1.0
NETWORK_RETRY_MAX = 3
QUOTA_BACKOFF_SEC = 60
HTTP_TIMEOUT_SEC = 15


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass
class JanInfo:
    jan: str
    found: bool
    brand: str | None = None
    product_name_jp: str | None = None
    description_jp: str | None = None
    price_jpy: int | None = None
    image_urls: list[str] = field(default_factory=list)
    source: str | None = None
    source_url: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "JanInfo":
        return cls(
            jan=d["jan"],
            found=bool(d.get("found", False)),
            brand=d.get("brand"),
            product_name_jp=d.get("product_name_jp"),
            description_jp=d.get("description_jp"),
            price_jpy=d.get("price_jpy"),
            image_urls=list(d.get("image_urls") or []),
            source=d.get("source"),
            source_url=d.get("source_url"),
        )


# --------------------------------------------------------------------------- #
# Errors (for tests / control flow)
# --------------------------------------------------------------------------- #


class JanCollectorError(Exception):
    """Base error for jan_collector."""


class NetworkError(JanCollectorError):
    """Recoverable network/transport error."""


class QuotaError(JanCollectorError):
    """Rakuten API quota / 429 reached."""


# --------------------------------------------------------------------------- #
# HTTP layer (mockable)
# --------------------------------------------------------------------------- #


def _http_get(url: str, params: dict, timeout: int = HTTP_TIMEOUT_SEC) -> dict:
    """GET ``url`` with query ``params`` and decode JSON.

    Raises:
        QuotaError:   on HTTP 429.
        NetworkError: on any other transport / decode error.
    """
    qs = urllib.parse.urlencode(params)
    full = f"{url}?{qs}"
    req = urllib.request.Request(full, headers={"User-Agent": "shopee-listing/jan-collector"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise QuotaError(f"Rakuten quota: HTTP 429 ({e.reason})") from e
        # Treat other 4xx/5xx as network errors so retry logic kicks in;
        # business "0 hits" is a 200 with empty Items.
        raise NetworkError(f"HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise NetworkError(f"URL error: {e.reason}") from e
    except (TimeoutError, OSError) as e:
        raise NetworkError(f"transport error: {e}") from e

    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise NetworkError(f"invalid JSON response: {e}") from e


# --------------------------------------------------------------------------- #
# Rate limiter (token bucket-ish; 1 req/s globally)
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Simple global rate limiter — at most 1 request per ``interval`` seconds."""

    def __init__(self, interval: float = RATE_LIMIT_INTERVAL_SEC) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self.interval


# --------------------------------------------------------------------------- #
# Cache (SQLite)
# --------------------------------------------------------------------------- #


class JanCache:
    def __init__(
        self,
        path: Path | str = DEFAULT_CACHE_PATH,
        ttl_days: int = DEFAULT_CACHE_TTL_DAYS,
    ) -> None:
        self.path = Path(path)
        self.ttl_seconds = int(ttl_days * 86400)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS jan_info (
                    jan         TEXT PRIMARY KEY,
                    info        TEXT NOT NULL,
                    fetched_at  INTEGER NOT NULL
                )
                """
            )

    def get(self, jan: str) -> JanInfo | None:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT info, fetched_at FROM jan_info WHERE jan=?", (jan,)
            ).fetchone()
        if not row:
            return None
        info_json, fetched_at = row
        if time.time() - fetched_at > self.ttl_seconds:
            return None
        try:
            return JanInfo.from_dict(json.loads(info_json))
        except (ValueError, KeyError):
            return None

    def put(self, info: JanInfo) -> None:
        payload = json.dumps(info.to_dict(), ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO jan_info (jan, info, fetched_at) VALUES (?, ?, ?)",
                (info.jan, payload, int(time.time())),
            )


# --------------------------------------------------------------------------- #
# Rakuten parsing
# --------------------------------------------------------------------------- #


_THUMB_RE = re.compile(r"\?_ex=\d+x\d+")


def _strip_thumb(url: str) -> str:
    return _THUMB_RE.sub("", url)


def _parse_rakuten_response(jan: str, payload: dict) -> JanInfo:
    """Map Rakuten formatVersion=2 response → JanInfo. Empty Items → found=False."""
    items = payload.get("Items") or []
    if not items:
        return JanInfo(jan=jan, found=False, source="rakuten")

    first = items[0]
    # formatVersion=2 returns flat dicts; older formatVersion wraps in {"Item": {...}}
    if isinstance(first, dict) and "Item" in first and isinstance(first["Item"], dict):
        first = first["Item"]

    raw_images = first.get("mediumImageUrls") or []
    image_urls: list[str] = []
    for img in raw_images:
        if isinstance(img, str):
            url = img
        elif isinstance(img, dict):
            url = img.get("imageUrl", "")
        else:
            url = ""
        if url:
            image_urls.append(_strip_thumb(url))

    price = first.get("itemPrice")
    try:
        price_int = int(price) if price is not None else None
    except (TypeError, ValueError):
        price_int = None

    return JanInfo(
        jan=jan,
        found=True,
        brand=(first.get("shopName") or None),  # Rakuten 没有独立 brand 字段，用店铺名占位
        product_name_jp=first.get("itemName") or None,
        description_jp=first.get("itemCaption") or None,
        price_jpy=price_int,
        image_urls=image_urls,
        source="rakuten",
        source_url=first.get("itemUrl") or None,
    )


# --------------------------------------------------------------------------- #
# Collector
# --------------------------------------------------------------------------- #


HttpGet = Callable[[str, dict], dict]


class JanCollector:
    """Encapsulates cache + rate limiter + retry logic.

    Tests inject a fake ``http_get`` to avoid real Rakuten calls.
    """

    def __init__(
        self,
        app_id: str,
        cache: JanCache | None = None,
        rate_limiter: RateLimiter | None = None,
        http_get: HttpGet | None = None,
        max_workers: int = MAX_CONCURRENCY,
        network_retry_max: int = NETWORK_RETRY_MAX,
        quota_backoff_sec: int = QUOTA_BACKOFF_SEC,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not app_id:
            raise ValueError("RAKUTEN_APP_ID is required")
        self.app_id = app_id
        self.cache = cache if cache is not None else JanCache()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.http_get = http_get or _http_get
        self.max_workers = max(1, min(max_workers, MAX_CONCURRENCY))
        self.network_retry_max = network_retry_max
        self.quota_backoff_sec = quota_backoff_sec
        self._sleep = sleep

    # ---- single ----------------------------------------------------------- #

    def fetch_by_jan(self, jan: str, *, use_cache: bool = True) -> JanInfo:
        jan = str(jan).strip()
        if not jan:
            raise ValueError("jan must be a non-empty string")

        if use_cache:
            cached = self.cache.get(jan)
            if cached is not None:
                return cached

        info = self._fetch_with_retry(jan)
        self.cache.put(info)
        return info

    def _fetch_with_retry(self, jan: str) -> JanInfo:
        params = {
            "applicationId": self.app_id,
            "keyword": jan,
            "hits": 3,
            "format": "json",
            "formatVersion": 2,
        }

        attempt = 0
        last_err: Exception | None = None
        while attempt < self.network_retry_max:
            attempt += 1
            self.rate_limiter.acquire()
            try:
                payload = self.http_get(RAKUTEN_API_URL, params)
                return _parse_rakuten_response(jan, payload)
            except QuotaError as e:
                # Quota: back off 60s then retry without consuming a network attempt.
                last_err = e
                self._sleep(self.quota_backoff_sec)
                # Don't increment attempt for quota; loop again (cap by retry_max anyway).
                attempt -= 1
                # But guard against infinite loop if quota persists:
                if attempt >= self.network_retry_max:
                    break
                continue
            except NetworkError as e:
                last_err = e
                if attempt >= self.network_retry_max:
                    break
                # Exponential-ish backoff: 1s, 2s, 4s
                self._sleep(min(2 ** (attempt - 1), 4))

        # Exhausted retries: return found=False marker (don't crash batch).
        return JanInfo(
            jan=jan,
            found=False,
            source="rakuten",
            description_jp=f"ERROR: {type(last_err).__name__}: {last_err}" if last_err else None,
        )

    # ---- batch ------------------------------------------------------------ #

    def fetch_batch(self, jans: Iterable[str], *, use_cache: bool = True) -> list[JanInfo]:
        jan_list = [str(j).strip() for j in jans if str(j).strip()]
        if not jan_list:
            return []

        results: list[JanInfo | None] = [None] * len(jan_list)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self.fetch_by_jan, jan, use_cache=use_cache): idx
                for idx, jan in enumerate(jan_list)
            }
            for fut in futures:
                idx = futures[fut]
                results[idx] = fut.result()

        return [r for r in results if r is not None]


# --------------------------------------------------------------------------- #
# Module-level convenience wrappers (default singleton)
# --------------------------------------------------------------------------- #


_default_collector: JanCollector | None = None
_default_lock = threading.Lock()


def _get_default_collector() -> JanCollector:
    global _default_collector
    with _default_lock:
        if _default_collector is None:
            app_id = os.environ.get("RAKUTEN_APP_ID", "").strip()
            if not app_id:
                raise RuntimeError(
                    "RAKUTEN_APP_ID environment variable is required"
                )
            _default_collector = JanCollector(app_id=app_id)
        return _default_collector


def fetch_by_jan(jan: str) -> JanInfo:
    return _get_default_collector().fetch_by_jan(jan)


def fetch_batch(jans: list[str]) -> list[JanInfo]:
    return _get_default_collector().fetch_batch(jans)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jan_collector",
        description="Fetch JP product info by JAN code (Rakuten Ichiba).",
    )
    p.add_argument("jans", nargs="+", help="One or more JAN codes")
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip cache read (still writes results back)",
    )
    p.add_argument(
        "--cache-path",
        default=str(DEFAULT_CACHE_PATH),
        help=f"Cache DB path (default: {DEFAULT_CACHE_PATH})",
    )
    p.add_argument(
        "--ttl-days",
        type=int,
        default=DEFAULT_CACHE_TTL_DAYS,
        help=f"Cache TTL in days (default: {DEFAULT_CACHE_TTL_DAYS})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    app_id = os.environ.get("RAKUTEN_APP_ID", "").strip()
    if not app_id:
        print(
            "ERROR: RAKUTEN_APP_ID environment variable is not set",
            file=sys.stderr,
        )
        return 2

    cache = JanCache(path=args.cache_path, ttl_days=args.ttl_days)
    collector = JanCollector(app_id=app_id, cache=cache)

    results = collector.fetch_batch(args.jans, use_cache=not args.no_cache)
    json.dump(
        [r.to_dict() for r in results],
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
