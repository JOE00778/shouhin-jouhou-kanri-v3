"""cms_api — CMS 只读/事件 API sidecar（给 N8N workflow 调）

补 README v2.0 缺口 ①② + automation_runs 回调（T-309 设计但未实装的 endpoint）。

挂在 docker external network smikie_shared，N8N 通过 http://cms-api:8789/ 访问。
不暴露公网。

后端选择（按 shared/db.py 同款逻辑）：
  - 有 DATABASE_URL=postgresql://... → Postgres（Inspiron 部署用，主路径）
  - 否则 → SQLite 文件 CMS_DB_PATH（Mac 本地开发兜底）

端点：
  GET  /health
  GET  /api/sku/master?jans=jan1,jan2,...
  POST /api/automation/callback
  POST /api/automation/xlsx-upload
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

# psycopg2 在 SQLite 模式下不会被使用，但导入失败应是软错误（Mac 开发不强求装）
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PG = True
except ImportError:
    _HAS_PG = False

# ────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH = Path(os.environ.get("CMS_DB_PATH", "/data/warehouse.db"))
OUTPUTS_DIR = Path(os.environ.get("CMS_OUTPUTS_DIR", "/data/outputs"))
CMS_PUBLIC_BASE = os.environ.get("CMS_PUBLIC_BASE", "https://smikie-cms.cc")

IS_PG = DATABASE_URL.startswith(("postgresql://", "postgres://"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cms_api")

app = FastAPI(title="Smikie CMS API sidecar", version="0.2.0")


@contextmanager
def _conn():
    """统一连接 context manager；返回带 row_factory（key 访问）的连接。"""
    if IS_PG:
        if not _HAS_PG:
            raise HTTPException(500, "DATABASE_URL=postgres 但 psycopg2 未安装")
        c = psycopg2.connect(DATABASE_URL)
        c.cursor_factory = psycopg2.extras.RealDictCursor
        try:
            yield c
        finally:
            c.close()
    else:
        if not DB_PATH.exists():
            raise HTTPException(503, f"SQLite warehouse.db 不存在: {DB_PATH}")
        c = sqlite3.connect(str(DB_PATH))
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()


def _qmark(sql: str) -> str:
    """SQLite 用 `?`，Postgres 用 `%s`。SQL 写 `?`，按后端自动替换。"""
    return sql.replace("?", "%s") if IS_PG else sql


def _placeholders(n: int) -> str:
    return ",".join("%s" * n) if IS_PG else ",".join("?" * n)


def _safe_jan_list(raw: str) -> list[str]:
    """防 SQL injection：白名单 JAN 字符（数字 + 字母 + dash + underscore）"""
    jans = [j.strip() for j in raw.split(",") if j.strip()]
    safe = [j for j in jans if re.fullmatch(r"[\w\-]+", j)]
    if len(safe) != len(jans):
        log.warning("dropped %d malformed JAN values", len(jans) - len(safe))
    return safe


def _row_get(row, key: str, default=None):
    """SQLite Row 和 psycopg2 RealDictRow 都支持 row[key]，但访问语义有差。"""
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError):
        return default


@app.on_event("startup")
def _startup():
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"backend = {'postgres' if IS_PG else 'sqlite'}")
    if IS_PG:
        safe_url = re.sub(r":[^:@]*@", ":***@", DATABASE_URL)
        log.info(f"DATABASE_URL = {safe_url}")
    else:
        log.info(f"CMS_DB_PATH = {DB_PATH} (exists={DB_PATH.exists()})")
    log.info(f"CMS_OUTPUTS_DIR = {OUTPUTS_DIR}")


# ────────────────────────────────────────────────────────────────
# GET /health
# ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    backend = "postgres" if IS_PG else "sqlite"
    ok = True
    item_count = None
    err = None
    try:
        with _conn() as c:
            cur = c.cursor() if IS_PG else c
            cur.execute("SELECT COUNT(*) AS n FROM item_v2")
            row = cur.fetchone()
            item_count = row["n"] if row else None
    except Exception as e:
        ok = False
        err = str(e)[:200]
        log.error("health db probe failed: %s", e)
    return {
        "status": "ok" if ok else "degraded",
        "backend": backend,
        "item_v2_count": item_count,
        "error": err,
        "outputs_dir": str(OUTPUTS_DIR),
    }


# ────────────────────────────────────────────────────────────────
# GET /api/sku/master?jans=jan1,jan2,...
# ────────────────────────────────────────────────────────────────
class SkuRow(BaseModel):
    jan: str
    sku: str | None = None
    display_name: str | None = None
    maker: str | None = None
    rank: str | None = None
    handling_status: str | None = None
    on_hand: int | None = None
    on_order: int | None = None
    cat_l1: str | None = None
    cat_l2: str | None = None
    brand: str | None = None
    tags: list[str] = Field(default_factory=list)
    main_image_url: str | None = None


@app.get("/api/sku/master", response_model=list[SkuRow])
def sku_master(jans: str = Query(..., description="逗号分隔 JAN 列表")):
    jan_list = _safe_jan_list(jans)
    if not jan_list:
        return []
    ph = _placeholders(len(jan_list))
    sql = f"""
        SELECT
            v.jan, v.item_code AS sku, v.display_name, v.maker, v.rank,
            v.handling_status, v.on_hand, v.on_order,
            t.cat_l1, t.cat_l2, t.brand, t.tags_csv
        FROM v_item_master v
        LEFT JOIN item_shopify_tags t ON t.jan = v.jan
        WHERE v.jan IN ({ph})
    """
    with _conn() as c:
        cur = c.cursor() if IS_PG else c
        cur.execute(sql, jan_list)
        rows = cur.fetchall()

    out: list[SkuRow] = []
    for r in rows:
        tags_raw = _row_get(r, "tags_csv", "") or ""
        tags = [x.strip() for x in tags_raw.split(",") if x.strip()]
        cat_l1 = _row_get(r, "cat_l1")
        if cat_l1 and not any(t == cat_l1 for t in tags):
            tags.insert(0, cat_l1)
        out.append(
            SkuRow(
                jan=r["jan"],
                sku=_row_get(r, "sku"),
                display_name=_row_get(r, "display_name"),
                maker=_row_get(r, "maker"),
                rank=_row_get(r, "rank"),
                handling_status=_row_get(r, "handling_status"),
                on_hand=_row_get(r, "on_hand"),
                on_order=_row_get(r, "on_order"),
                cat_l1=cat_l1,
                cat_l2=_row_get(r, "cat_l2"),
                brand=_row_get(r, "brand"),
                tags=tags,
                main_image_url=f"{CMS_PUBLIC_BASE}/products/{r['jan']}/main.jpg",
            )
        )
    return out


# ────────────────────────────────────────────────────────────────
# POST /api/automation/callback
# ────────────────────────────────────────────────────────────────
class CallbackReq(BaseModel):
    run_id: str
    module: str
    status: str
    summary: dict[str, Any] | None = None
    message: str | None = None


@app.post("/api/automation/callback")
def automation_callback(req: CallbackReq):
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    summary_json = json.dumps(req.summary, ensure_ascii=False) if req.summary else None
    completed_at = now if req.status in ("completed", "failed") else None

    with _conn() as c:
        cur = c.cursor() if IS_PG else c
        update_sql = _qmark(
            "UPDATE automation_runs SET status=?, summary=COALESCE(?, summary), "
            "completed_at=COALESCE(?, completed_at) WHERE run_id=?"
        )
        cur.execute(update_sql, (req.status, summary_json, completed_at, req.run_id))
        # SQLite cursor.rowcount 与 psycopg2 cursor.rowcount 行为一致
        if cur.rowcount == 0:
            insert_sql = _qmark(
                "INSERT INTO automation_runs "
                "(run_id, module, status, summary, triggered_by, triggered_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
            cur.execute(
                insert_sql,
                (req.run_id, req.module, req.status, summary_json, "n8n-callback", now, completed_at),
            )
        c.commit()
    return {"run_id": req.run_id, "status": req.status, "updated_at": now}


# ────────────────────────────────────────────────────────────────
# POST /api/automation/xlsx-upload
# ────────────────────────────────────────────────────────────────
@app.post("/api/automation/xlsx-upload")
async def xlsx_upload(
    file: UploadFile = File(...),
    x_run_id: str = Header(..., alias="X-Run-Id"),
):
    if not re.fullmatch(r"[\w\-]+", x_run_id):
        raise HTTPException(400, "invalid X-Run-Id")
    safe_name = re.sub(r"[^\w\.\-]", "_", file.filename or "upload.xlsx")
    dst = OUTPUTS_DIR / f"{x_run_id}_{safe_name}"
    body = await file.read()
    dst.write_bytes(body)
    log.info("xlsx saved: %s (%d bytes)", dst, len(body))
    return {
        "run_id": x_run_id,
        "saved_path": str(dst),
        "bytes": len(body),
        "filename": dst.name,
    }


# ────────────────────────────────────────────────────────────────
# Shopee tokens persistence （v2.3 给 shopee-mass-upload n02b 节点用）
# 文件存储，0 DB 改动；每次 N8N workflow 触发时 refresh 一次 access_token
# 并把新的 refresh_token 持久化（refresh_token 每次也会变）。
# ────────────────────────────────────────────────────────────────
SHOPEE_TOKENS_FILE = OUTPUTS_DIR / "shopee_tokens.json"
SHOPEE_API_BASE = os.environ.get("SHOPEE_API_BASE", "https://partner.shopeemobile.com")
SHOPEE_PARTNER_ID = os.environ.get("SHOPEE_PARTNER_ID", "")
_RAW_PK = os.environ.get("SHOPEE_PARTNER_KEY", "")
# Shopee Test 环境 partner_key 带 'shpk' UI 前缀，签名时必须去掉
SHOPEE_PARTNER_KEY = _RAW_PK.removeprefix("shpk") if _RAW_PK.startswith("shpk") else _RAW_PK


@app.get("/api/automation/shopee/tokens")
def get_shopee_tokens():
    if SHOPEE_TOKENS_FILE.exists():
        try:
            return json.loads(SHOPEE_TOKENS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("read shopee_tokens.json failed: %s", e)
            return {"refresh_tokens": {}, "updated_at": None, "error": str(e)}
    return {"refresh_tokens": {}, "updated_at": None}


class ShopeeTokensReq(BaseModel):
    refresh_tokens: dict[str, str]
    updated_at: str | None = None


@app.put("/api/automation/shopee/tokens")
def put_shopee_tokens(req: ShopeeTokensReq):
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    body = {
        "refresh_tokens": req.refresh_tokens,
        "updated_at": req.updated_at or dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    SHOPEE_TOKENS_FILE.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved": True, "path": str(SHOPEE_TOKENS_FILE), "markets": list(req.refresh_tokens.keys())}


# ────────────────────────────────────────────────────────────────
# Shopee OAuth helper（v2.4 新增）
# Boss 拿到 Partner ID/Key 后，从 cms-api 拿 7 国授权链接 → 浏览器登录 → 自动回写 refresh_token
# 替代手工构造 OAuth URL 和手工换 token 的繁琐流程
# ────────────────────────────────────────────────────────────────
@app.get("/api/automation/shopee/oauth-url/{market}")
def shopee_oauth_url(market: str, redirect: str | None = None):
    """生成某国的 Shopee OAuth 授权链接。

    Shopee 公式: HMAC-SHA256(partner_id + path + ts, partner_key)
    path = /api/v2/shop/auth_partner
    """
    if not SHOPEE_PARTNER_ID or not SHOPEE_PARTNER_KEY:
        raise HTTPException(503, "SHOPEE_PARTNER_ID/KEY 未在 cms-api 容器 env 设置")
    import hashlib
    import hmac

    path = "/api/v2/shop/auth_partner"
    ts = int(time.time())   # 永远 UTC epoch，不受容器 TZ 影响
    base = f"{SHOPEE_PARTNER_ID}{path}{ts}"
    sign = hmac.new(SHOPEE_PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()
    cb = redirect or f"{CMS_PUBLIC_BASE}/api/automation/shopee/oauth-callback?market={market}"
    url = (
        f"{SHOPEE_API_BASE}{path}"
        f"?partner_id={SHOPEE_PARTNER_ID}&timestamp={ts}&sign={sign}&redirect={cb}"
    )
    return {
        "market": market,
        "authorize_url": url,
        "redirect_url": cb,
        "instruction": (
            f"1) 浏览器打开 authorize_url 登录 Shopee {market} 卖家账号；"
            f"2) 授权后 Shopee 会跳转 redirect_url，带上 code 和 shop_id；"
            f"3) 浏览器会自动调 callback 端点完成 refresh_token 持久化。"
        ),
    }


@app.get("/api/automation/shopee/oauth-callback")
def shopee_oauth_callback(market: str, code: str, shop_id: int):
    """Shopee OAuth 回调；用 code 换 refresh_token，自动写入 shopee_tokens.json + shop_ids 提示"""
    if not SHOPEE_PARTNER_ID or not SHOPEE_PARTNER_KEY:
        raise HTTPException(503, "SHOPEE_PARTNER_ID/KEY 未设置")
    import hashlib
    import hmac
    import urllib.request

    path = "/api/v2/auth/token/get"
    ts = int(time.time())   # 永远 UTC epoch，不受容器 TZ 影响
    base = f"{SHOPEE_PARTNER_ID}{path}{ts}"
    sign = hmac.new(SHOPEE_PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()
    url = f"{SHOPEE_API_BASE}{path}?partner_id={SHOPEE_PARTNER_ID}&timestamp={ts}&sign={sign}"
    body = json.dumps({"code": code, "shop_id": shop_id, "partner_id": int(SHOPEE_PARTNER_ID)}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        raise HTTPException(502, f"Shopee /token/get failed: {e}")
    if data.get("error"):
        raise HTTPException(502, f"Shopee error: {data.get('error')} {data.get('message','')}")
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise HTTPException(502, f"no refresh_token in response: {data}")

    # 持久化：合并进 shopee_tokens.json
    existing = {}
    if SHOPEE_TOKENS_FILE.exists():
        try:
            existing = json.loads(SHOPEE_TOKENS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    refresh_tokens = existing.get("refresh_tokens", {})
    refresh_tokens[market] = refresh_token
    body_out = {
        "refresh_tokens": refresh_tokens,
        "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    SHOPEE_TOKENS_FILE.write_text(json.dumps(body_out, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "market": market,
        "shop_id": shop_id,
        "refresh_token_stored": True,
        "expire_in_seconds": data.get("expire_in"),
        "tip_for_env": (
            f'记得把 shop_id 加进 .env 的 SHOPEE_SHOP_IDS dict: '
            f'"{market}": "{shop_id}"'
        ),
    }
