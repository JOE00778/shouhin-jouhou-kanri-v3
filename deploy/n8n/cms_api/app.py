"""cms_api — CMS 只读/事件 API sidecar（给 N8N workflow 调）

补 README v2.0 缺口 ①② + automation_runs 回调（T-309 设计但未实装的 endpoint）。

挂在 docker-compose 内部网络，N8N 通过 http://cms-api:8789/ 访问。
不暴露公网。warehouse.db 以 ro 方式挂入（cms_api 只读 SKU 主档）。

端点：
  GET  /health
  GET  /api/sku/master?jans=jan1,jan2,...
       返回 [{ jan, sku, display_name, maker, rank, on_hand, tags, cat_l1, cat_l2,
                main_image_url, ...}]
       兼容 N8N shopee v2.0 workflow B1 节点期望字段（jan/sku/tags/main_image_url）
  POST /api/automation/callback
       接 N8N processing/completed 状态回调 → 写 automation_runs.status + summary + completed_at
       Body: { run_id, module, status, summary?, message? }
  POST /api/automation/xlsx-upload
       接 N8N B5b.2 上传的 dry-run XLSX → 落 CMS_OUTPUTS_DIR/<run_id>_<filename>
       multipart/form-data; field=file; header X-Run-Id

数据库字段映射（item_v2 → API 输出）：
  jan              → jan (主键)
  item_code        → sku
  display_name     → display_name
  on_hand_total    → on_hand
  item_shopify_tags.tags_csv 按 ',' split → tags[]
  item_shopify_tags.cat_l1/cat_l2 → cat_l1/cat_l2
  main_image_url 暂取约定路径 /products/<jan>/main.jpg（CMS 没存绝对 URL）
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

# ────────────────────────────────────────────────────────────────
DB_PATH = Path(os.environ.get("CMS_DB_PATH", "/data/warehouse.db"))
OUTPUTS_DIR = Path(os.environ.get("CMS_OUTPUTS_DIR", "/data/outputs"))
CMS_PUBLIC_BASE = os.environ.get("CMS_PUBLIC_BASE", "https://smikie-cms.cc")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cms_api")

app = FastAPI(title="Smikie CMS API sidecar", version="0.1.0")


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, f"warehouse.db not mounted at {DB_PATH}")
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _safe_jan_list(raw: str) -> list[str]:
    """防 SQL injection：白名单 JAN 字符（数字 + 字母 + dash + underscore）"""
    jans = [j.strip() for j in raw.split(",") if j.strip()]
    safe = [j for j in jans if re.fullmatch(r"[\w\-]+", j)]
    if len(safe) != len(jans):
        log.warning("dropped %d malformed JAN values", len(jans) - len(safe))
    return safe


@app.on_event("startup")
def _startup():
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"CMS_DB_PATH = {DB_PATH} (exists={DB_PATH.exists()})")
    log.info(f"CMS_OUTPUTS_DIR = {OUTPUTS_DIR}")


# ────────────────────────────────────────────────────────────────
# GET /health
# ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    db_ok = DB_PATH.exists()
    item_count = None
    if db_ok:
        try:
            with _conn() as c:
                row = c.execute("SELECT COUNT(*) AS n FROM item_v2").fetchone()
                item_count = row["n"] if row else None
        except Exception as e:
            db_ok = False
            log.error("health db probe failed: %s", e)
    return {
        "status": "ok" if db_ok else "degraded",
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "item_v2_count": item_count,
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
    placeholders = ",".join("?" * len(jan_list))
    sql = f"""
        SELECT
            v.jan, v.item_code AS sku, v.display_name, v.maker, v.rank,
            v.handling_status, v.on_hand, v.on_order,
            t.cat_l1, t.cat_l2, t.brand, t.tags_csv
        FROM v_item_master v
        LEFT JOIN item_shopify_tags t ON t.jan = v.jan
        WHERE v.jan IN ({placeholders})
    """
    with _conn() as c:
        rows = c.execute(sql, jan_list).fetchall()
    out: list[SkuRow] = []
    for r in rows:
        tags_raw = r["tags_csv"] or ""
        tags = [x.strip() for x in tags_raw.split(",") if x.strip()]
        # cat tag 合成（cat_l1/l2 不在 tags_csv 时显式补，方便 N8N B2 节点统一查 tags[].startsWith('cat-')）
        if r["cat_l1"] and not any(t == r["cat_l1"] for t in tags):
            tags.insert(0, r["cat_l1"])
        out.append(
            SkuRow(
                jan=r["jan"],
                sku=r["sku"],
                display_name=r["display_name"],
                maker=r["maker"],
                rank=r["rank"],
                handling_status=r["handling_status"],
                on_hand=r["on_hand"],
                on_order=r["on_order"],
                cat_l1=r["cat_l1"],
                cat_l2=r["cat_l2"],
                brand=r["brand"],
                tags=tags,
                # CMS 没存绝对 URL；用 商品登录 APP 约定路径
                # N8N image-processor 拿这个 URL 走 cloudflared 公网回拉
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
    status: str   # processing / completed / failed
    summary: dict[str, Any] | None = None
    message: str | None = None


@app.post("/api/automation/callback")
def automation_callback(req: CallbackReq):
    """N8N → CMS 状态回调；写 automation_runs 表（read-write 模式下打开）"""
    if not DB_PATH.exists():
        raise HTTPException(503, "warehouse.db not mounted")
    now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    summary_json = json.dumps(req.summary, ensure_ascii=False) if req.summary else None
    completed_at = now if req.status in ("completed", "failed") else None

    c = sqlite3.connect(str(DB_PATH))
    try:
        # 现有行就 UPDATE，否则 INSERT（N8N 可能在 CMS Page 21 还没写 row 时先 callback）
        cur = c.execute(
            "UPDATE automation_runs SET status=?, summary=COALESCE(?, summary), completed_at=COALESCE(?, completed_at) "
            "WHERE run_id=?",
            (req.status, summary_json, completed_at, req.run_id),
        )
        if cur.rowcount == 0:
            c.execute(
                "INSERT INTO automation_runs (run_id, module, status, summary, triggered_by, triggered_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (req.run_id, req.module, req.status, summary_json, "n8n-callback", now, completed_at),
            )
        c.commit()
    finally:
        c.close()
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
