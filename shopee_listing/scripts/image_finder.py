#!/usr/bin/env python3
"""Shopee 主图查找器 — 移植 商品登録ツール_0418.html getMainImage 逻辑。

三级 fallback：
  L1  jancode.xyz/images/{jan}.jpg     bytes > 8000
  L2  amazon.co.jp 検索頁                 bytes > 1000  (img.s-image / [data-component-type=s-product-image] img)
  L3  search.rakuten.co.jp                bytes > 1000  (thumbnail.image.rakuten.co.jp)

CLI:
    python3 image_finder.py 4902370548495 4901085196533 --out-dir /tmp/shopee_images/

Module:
    from image_finder import find_main_image, find_images_batch, ImageRef
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

import requests
from bs4 import BeautifulSoup

# ------------------------------ Config ------------------------------------

SCRAPE_DELAY_MS = 1500  # JAN_SEARCH_CONFIG.SCRAPE_DELAY_MS

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "ja,en-US;q=0.9,en;q=0.8,zh-CN;q=0.7,zh;q=0.6"

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": ACCEPT_LANGUAGE,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
}

JANCODE_MIN_BYTES = 8000
SEARCH_MIN_BYTES = 1000

REQUEST_TIMEOUT = 15  # seconds
DOWNLOAD_RETRIES = 3

# Default cache lives next to the project root (parent of scripts/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DB = PROJECT_ROOT / ".cache" / "image_refs.db"

Source = Literal["jancode.xyz", "amazon", "rakuten"]


# ------------------------------ Data --------------------------------------


@dataclass
class ImageRef:
    """A located main-image reference (URL only — bytes are downloaded on demand)."""

    jan: str
    url: str
    source: Source
    bytes_size: int
    fetched_at: str  # ISO-8601 UTC

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------ Cache -------------------------------------


class ImageRefCache:
    """SQLite-backed cache for ImageRef."""

    def __init__(self, db_path: Path = DEFAULT_CACHE_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS image_refs (
                    jan        TEXT PRIMARY KEY,
                    url        TEXT NOT NULL,
                    source     TEXT NOT NULL,
                    bytes_size INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )

    def get(self, jan: str) -> Optional[ImageRef]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT jan, url, source, bytes_size, fetched_at FROM image_refs WHERE jan = ?",
                (jan,),
            ).fetchone()
        if row is None:
            return None
        return ImageRef(
            jan=row["jan"],
            url=row["url"],
            source=row["source"],
            bytes_size=row["bytes_size"],
            fetched_at=row["fetched_at"],
        )

    def put(self, ref: ImageRef) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO image_refs(jan, url, source, bytes_size, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(jan) DO UPDATE SET
                    url=excluded.url,
                    source=excluded.source,
                    bytes_size=excluded.bytes_size,
                    fetched_at=excluded.fetched_at
                """,
                (ref.jan, ref.url, ref.source, ref.bytes_size, ref.fetched_at),
            )


# ------------------------------ HTTP helpers ------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def _fetch_bytes(
    url: str,
    session: requests.Session,
    *,
    referer: Optional[str] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> tuple[int, bytes]:
    """Return (status_code, body_bytes). Does not raise on non-2xx."""
    headers = {}
    if referer:
        headers["Referer"] = referer
    resp = session.get(url, headers=headers or None, timeout=timeout, allow_redirects=True)
    return resp.status_code, resp.content


def _fetch_html(
    url: str,
    session: requests.Session,
    *,
    timeout: int = REQUEST_TIMEOUT,
) -> Optional[str]:
    resp = session.get(url, timeout=timeout, allow_redirects=True)
    if resp.status_code != 200:
        return None
    # requests handles encoding via apparent_encoding when needed
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


# ------------------------------ URL cleaners ------------------------------

_AMAZON_AC_RE = re.compile(r"\._AC_[A-Z0-9_]+_\.(jpg|png|webp)", re.IGNORECASE)
_RAKUTEN_EX_RE = re.compile(r"\?_ex=\d+x\d+$")


def clean_amazon_url(url: str) -> str:
    """Strip Amazon's `._AC_xxxx_.` thumb suffix to get the original-size image."""
    return _AMAZON_AC_RE.sub(r".\1", url)


def clean_rakuten_url(url: str) -> str:
    """Strip Rakuten's `?_ex=NxN` thumb suffix."""
    return _RAKUTEN_EX_RE.sub("", url)


# ------------------------------ Resolvers ---------------------------------


def _try_jancode(jan: str, session: requests.Session) -> Optional[ImageRef]:
    url = f"https://www.jancode.xyz/images/{jan}.jpg"
    try:
        status, body = _fetch_bytes(url, session)
    except requests.RequestException:
        return None
    if status != 200:
        return None
    if len(body) <= JANCODE_MIN_BYTES:
        return None
    return ImageRef(
        jan=jan,
        url=url,
        source="jancode.xyz",
        bytes_size=len(body),
        fetched_at=_now_iso(),
    )


def _extract_amazon_img(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    img = soup.select_one(
        'img.s-image, [data-component-type="s-product-image"] img'
    )
    if img is None:
        return None
    src = img.get("src") or img.get("data-src") or ""
    src = src.strip()
    if not src:
        return None
    return clean_amazon_url(src)


def _try_amazon(jan: str, session: requests.Session) -> Optional[ImageRef]:
    search_url = f"https://www.amazon.co.jp/s?k={jan}"
    try:
        html = _fetch_html(search_url, session)
    except requests.RequestException:
        return None
    if not html:
        return None
    img_url = _extract_amazon_img(html)
    if not img_url:
        return None
    try:
        status, body = _fetch_bytes(img_url, session, referer=search_url)
    except requests.RequestException:
        return None
    if status != 200 or len(body) <= SEARCH_MIN_BYTES:
        return None
    return ImageRef(
        jan=jan,
        url=img_url,
        source="amazon",
        bytes_size=len(body),
        fetched_at=_now_iso(),
    )


def _extract_rakuten_img(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    img = soup.select_one(
        'img[src*="thumbnail.image.rakuten.co.jp"], '
        'img[data-src*="thumbnail.image.rakuten.co.jp"]'
    )
    if img is None:
        return None
    src = img.get("src") or img.get("data-src") or ""
    src = src.strip()
    if not src:
        return None
    return clean_rakuten_url(src)


def _try_rakuten(jan: str, session: requests.Session) -> Optional[ImageRef]:
    search_url = f"https://search.rakuten.co.jp/search/mall/{jan}/"
    try:
        html = _fetch_html(search_url, session)
    except requests.RequestException:
        return None
    if not html:
        return None
    img_url = _extract_rakuten_img(html)
    if not img_url:
        return None
    try:
        status, body = _fetch_bytes(img_url, session, referer=search_url)
    except requests.RequestException:
        return None
    if status != 200 or len(body) <= SEARCH_MIN_BYTES:
        return None
    return ImageRef(
        jan=jan,
        url=img_url,
        source="rakuten",
        bytes_size=len(body),
        fetched_at=_now_iso(),
    )


# ------------------------------ Public API --------------------------------


def find_main_image(
    jan: str,
    *,
    cache: Optional[ImageRefCache] = None,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
) -> Optional[ImageRef]:
    """Return an ImageRef for `jan` or None if all 3 fallbacks miss.

    Cache is consulted first; on miss, jancode.xyz → Amazon → Rakuten in order.
    """
    jan = str(jan).strip()
    if not jan:
        return None

    if cache is None and use_cache:
        cache = ImageRefCache()

    if cache and use_cache:
        cached = cache.get(jan)
        if cached is not None:
            return cached

    own_session = False
    if session is None:
        session = _new_session()
        own_session = True

    try:
        for resolver in (_try_jancode, _try_amazon, _try_rakuten):
            ref = resolver(jan, session)
            if ref is not None:
                if cache and use_cache:
                    cache.put(ref)
                return ref
        return None
    finally:
        if own_session:
            session.close()


def find_images_batch(
    jans: Iterable[str],
    *,
    delay_ms: int = SCRAPE_DELAY_MS,
    cache: Optional[ImageRefCache] = None,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
    sleeper=time.sleep,
) -> list[Optional[ImageRef]]:
    """Serial lookup for a list of JANs with `delay_ms` between **uncached** hits.

    Cached lookups do not consume the delay budget.
    """
    if cache is None and use_cache:
        cache = ImageRefCache()

    own_session = False
    if session is None:
        session = _new_session()
        own_session = True

    results: list[Optional[ImageRef]] = []
    last_was_network = False
    delay_s = max(0.0, delay_ms / 1000.0)

    try:
        jans_list = list(jans)
        for idx, jan in enumerate(jans_list):
            cached = None
            if cache and use_cache:
                cached = cache.get(jan)
            if cached is not None:
                results.append(cached)
                last_was_network = False
                continue

            # Throttle only between consecutive network hits.
            if last_was_network and delay_s > 0:
                sleeper(delay_s)

            ref = find_main_image(
                jan,
                cache=cache,
                session=session,
                use_cache=use_cache,
            )
            results.append(ref)
            last_was_network = True
        return results
    finally:
        if own_session:
            session.close()


# ------------------------------ Download / save ---------------------------


def download_image(
    url: str,
    *,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = DOWNLOAD_RETRIES,
    session: Optional[requests.Session] = None,
    sleeper=time.sleep,
) -> bytes:
    """Download bytes for `url` with up to `retries` attempts (15s each)."""
    own_session = False
    if session is None:
        session = _new_session()
        own_session = True

    last_exc: Optional[Exception] = None
    try:
        for attempt in range(1, retries + 1):
            try:
                resp = session.get(url, timeout=timeout, allow_redirects=True)
                resp.raise_for_status()
                return resp.content
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retries:
                    sleeper(min(2 ** (attempt - 1), 4))
                    continue
                raise
        # Defensive — loop always returns or raises above.
        if last_exc:
            raise last_exc
        raise RuntimeError("download_image: unreachable")
    finally:
        if own_session:
            session.close()


def save_image(jan: str, ref: ImageRef, out_dir: Path) -> Path:
    """Download `ref.url` and write to `{out_dir}/{jan}.jpg`. Returns the path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = download_image(ref.url)
    out_path = out_dir / f"{jan}.jpg"
    out_path.write_bytes(data)
    return out_path


# ------------------------------ CLI ---------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="image_finder",
        description="Resolve Shopee main images by JAN (jancode.xyz → Amazon → Rakuten).",
    )
    p.add_argument("jans", nargs="+", help="One or more JAN codes")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="If set, download each found image and save as {out-dir}/{jan}.jpg",
    )
    p.add_argument(
        "--delay-ms",
        type=int,
        default=SCRAPE_DELAY_MS,
        help=f"Delay between network lookups (default: {SCRAPE_DELAY_MS})",
    )
    p.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_CACHE_DB,
        help=f"SQLite cache path (default: {DEFAULT_CACHE_DB})",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip the SQLite cache for this run",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    use_cache = not args.no_cache
    cache = ImageRefCache(args.cache_db) if use_cache else None

    refs = find_images_batch(
        args.jans,
        delay_ms=args.delay_ms,
        cache=cache,
        use_cache=use_cache,
    )

    hits = 0
    for jan, ref in zip(args.jans, refs):
        if ref is None:
            print(f"[MISS] {jan}", file=sys.stderr)
            continue
        hits += 1
        print(f"[{ref.source:>12}] {jan}  bytes={ref.bytes_size}  url={ref.url}")
        if args.out_dir:
            try:
                path = save_image(jan, ref, args.out_dir)
                print(f"             -> saved {path}")
            except Exception as exc:  # noqa: BLE001
                print(f"             ! save failed: {exc}", file=sys.stderr)

    total = len(args.jans)
    print(f"\n{hits}/{total} resolved.")
    return 0 if hits > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
