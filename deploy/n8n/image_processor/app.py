"""image_processor — Smikie 商品图处理 sidecar（N8N HTTP 调用）

3 个核心端点：
  POST /upscale       低分原图 → 1500×1500 白底方图（Lanczos / TODO: realesrgan）
  POST /cutout        1500 白底图 → 抠图 + 套 SMIKIE RED 模板
  POST /compose-spu   N 个 SKU 白底图 → SPU 多图（PIL 网格拼接）

文件持久化全部走 /data/whitebg（容器内），由 docker-compose 挂到 Windows D 盘：
  D:\\Smikie-Images\\whitebg → /data/whitebg

目录结构（自动建）：
  /data/whitebg/raw/<JAN>.jpg          上传/下载的原图
  /data/whitebg/upscaled/<JAN>.jpg     1500×1500 白底方图
  /data/whitebg/branded/<JAN>.jpg      套模板成品（Shopee 主图候选）
  /data/whitebg/spu/<SPU_KEY>.jpg      SPU 多图合成（拼接图）

输入策略：所有端点接受 (jan + image_url) 或 (jan + base64_bytes) 二选一；
        优先走 image_url（N8N 抓 CMS 图），避免大字段穿 N8N JSON。
"""
from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from rembg import new_session, remove

# ────────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────────
WHITEBG_ROOT = Path(os.environ.get("WHITEBG_ROOT", "/data/whitebg"))
TEMPLATE_PATH = Path(os.environ.get("TEMPLATE_PATH", "/app/assets/template_red.png"))
CANVAS = 1500
PRODUCT_RATIO = 0.80
MAX_BOX = int(CANVAS * PRODUCT_RATIO)
LOGO_BOTTOM = 290   # 与 compose_with_template.py 一致：产品顶部不能高于此

DIRS = {
    "raw": WHITEBG_ROOT / "raw",
    "upscaled": WHITEBG_ROOT / "upscaled",
    "branded": WHITEBG_ROOT / "branded",
    "spu": WHITEBG_ROOT / "spu",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("image_processor")

app = FastAPI(title="Smikie Image Processor", version="0.1.0")

_REMBG_SESSION = None


def _rembg():
    global _REMBG_SESSION
    if _REMBG_SESSION is None:
        log.info("init rembg u2net session")
        _REMBG_SESSION = new_session("u2net")
    return _REMBG_SESSION


def _ensure_dirs():
    for p in DIRS.values():
        p.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
def _startup():
    _ensure_dirs()
    if not TEMPLATE_PATH.exists():
        log.warning(f"template not found: {TEMPLATE_PATH}")
    else:
        log.info(f"template ready: {TEMPLATE_PATH}")
    log.info(f"WHITEBG_ROOT = {WHITEBG_ROOT}")


# ────────────────────────────────────────────────────────────────
# 帮助函数
# ────────────────────────────────────────────────────────────────
async def _fetch_bytes(image_url: str | None, image_b64: str | None) -> bytes:
    if image_url:
        async with httpx.AsyncClient(timeout=30) as cx:
            r = await cx.get(image_url)
            r.raise_for_status()
            return r.content
    if image_b64:
        return base64.b64decode(image_b64)
    raise HTTPException(400, "must provide image_url or image_b64")


def _upscale_lanczos(img: Image.Image) -> Image.Image:
    """最长边 → 1500，白底居中粘贴成 1500×1500 方图。无 AI 超分。"""
    img = img.convert("RGB") if img.mode != "RGB" else img
    img.thumbnail((CANVAS, CANVAS), Image.LANCZOS)
    canvas = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    canvas.paste(img, ((CANVAS - img.width) // 2, (CANVAS - img.height) // 2))
    return canvas


def _cutout_and_compose(white_bg_jpg_bytes: bytes) -> Image.Image:
    """白底图 → rembg 抠图 → 套 RED 模板。逻辑同 shopify/scripts/compose_with_template.py"""
    cut = remove(white_bg_jpg_bytes, session=_rembg())
    prod = Image.open(io.BytesIO(cut)).convert("RGBA")
    bbox = prod.getbbox()
    if not bbox:
        raise HTTPException(422, "rembg produced empty alpha — bad input?")
    prod = prod.crop(bbox)
    prod.thumbnail((MAX_BOX, MAX_BOX), Image.LANCZOS)

    template = Image.open(TEMPLATE_PATH).convert("RGBA")
    if template.size != (CANVAS, CANVAS):
        template = template.resize((CANVAS, CANVAS), Image.LANCZOS)
    out = template.copy()

    x = (CANVAS - prod.width) // 2
    y = (CANVAS - prod.height) // 2
    if x + prod.width > 1050 and y < LOGO_BOTTOM:
        new_top = LOGO_BOTTOM
        if new_top + prod.height > CANVAS - 30:
            max_h = CANVAS - 30 - new_top
            scale = max_h / prod.height
            prod = prod.resize((int(prod.width * scale), max_h), Image.LANCZOS)
            x = (CANVAS - prod.width) // 2
        y = new_top
    out.paste(prod, (x, y), prod)
    return out.convert("RGB")


def _grid_layout(n: int) -> tuple[int, int]:
    """N 张图 → (cols, rows)。1=>1x1, 2=>2x1, 3=>3x1, 4=>2x2, 5-6=>3x2, 7-9=>3x3."""
    if n <= 1: return (1, 1)
    if n == 2: return (2, 1)
    if n == 3: return (3, 1)
    if n == 4: return (2, 2)
    if n <= 6: return (3, 2)
    return (3, 3)


# ────────────────────────────────────────────────────────────────
# 请求/响应模型
# ────────────────────────────────────────────────────────────────
class SingleImageReq(BaseModel):
    jan: str = Field(..., min_length=1, description="JAN/SKU 编号，用于落盘文件名")
    image_url: str | None = Field(None, description="远程图 URL（CMS / NAS）")
    image_b64: str | None = Field(None, description="base64 字节（fallback）")
    method: Literal["lanczos", "realesrgan"] = Field(
        "lanczos",
        description="lanczos=CPU 瞬时；realesrgan=未实装（v1 用 lanczos 起步）",
    )
    overwrite: bool = False


class SingleImageResp(BaseModel):
    jan: str
    saved_path: str
    width: int
    height: int
    skipped: bool = False


class ComposeSpuReq(BaseModel):
    spu_key: str = Field(..., min_length=1)
    sku_jans: list[str] = Field(..., min_length=1, max_length=9)
    source: Literal["branded", "upscaled"] = Field(
        "branded",
        description="拼图素材：branded=套模板成品，upscaled=纯白底（建议 branded）",
    )
    overwrite: bool = False


class ComposeSpuResp(BaseModel):
    spu_key: str
    saved_path: str
    layout: str           # "2x2", "3x3" 等
    skus_used: list[str]
    skus_missing: list[str]


# ────────────────────────────────────────────────────────────────
# 端点
# ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "template_ready": TEMPLATE_PATH.exists(),
        "whitebg_root": str(WHITEBG_ROOT),
        "subdirs": {k: str(v) for k, v in DIRS.items()},
    }


@app.post("/upscale", response_model=SingleImageResp)
async def upscale(req: SingleImageReq):
    """原图 → 1500×1500 白底方图。落 /data/whitebg/upscaled/<JAN>.jpg"""
    _ensure_dirs()
    dst = DIRS["upscaled"] / f"{req.jan}.jpg"
    if dst.exists() and not req.overwrite:
        with Image.open(dst) as im:
            return SingleImageResp(jan=req.jan, saved_path=str(dst), width=im.width, height=im.height, skipped=True)

    raw = await _fetch_bytes(req.image_url, req.image_b64)
    src = Image.open(io.BytesIO(raw))

    if req.method == "realesrgan":
        raise HTTPException(501, "realesrgan backend not wired yet — use method=lanczos for v1")

    out = _upscale_lanczos(src)
    out.save(dst, "JPEG", quality=88, optimize=True)
    return SingleImageResp(jan=req.jan, saved_path=str(dst), width=out.width, height=out.height)


@app.post("/cutout", response_model=SingleImageResp)
async def cutout(req: SingleImageReq):
    """白底图 → 抠图 + 套 SMIKIE RED 模板。落 /data/whitebg/branded/<JAN>.jpg

    若同名 upscaled 已存在，优先用它（避免重复下载）。否则按入参取图。
    """
    _ensure_dirs()
    dst = DIRS["branded"] / f"{req.jan}.jpg"
    if dst.exists() and not req.overwrite:
        with Image.open(dst) as im:
            return SingleImageResp(jan=req.jan, saved_path=str(dst), width=im.width, height=im.height, skipped=True)

    src_pre = DIRS["upscaled"] / f"{req.jan}.jpg"
    if src_pre.exists():
        raw = src_pre.read_bytes()
    else:
        raw = await _fetch_bytes(req.image_url, req.image_b64)

    out = _cutout_and_compose(raw)
    out.save(dst, "JPEG", quality=90, optimize=True)
    return SingleImageResp(jan=req.jan, saved_path=str(dst), width=out.width, height=out.height)


@app.post("/compose-spu", response_model=ComposeSpuResp)
def compose_spu(req: ComposeSpuReq):
    """N 个 SKU 已处理图 → 1 张 SPU 拼接总览图。

    每张子图落到 CANVAS // cols 大小的格子里，居中粘贴，1500×1500 白底。
    """
    _ensure_dirs()
    dst = DIRS["spu"] / f"{req.spu_key}.jpg"
    if dst.exists() and not req.overwrite:
        with Image.open(dst) as im:
            return ComposeSpuResp(
                spu_key=req.spu_key, saved_path=str(dst),
                layout=f"{im.width}x{im.height}", skus_used=req.sku_jans, skus_missing=[],
            )

    src_dir = DIRS[req.source]
    used: list[str] = []
    missing: list[str] = []
    tiles: list[Image.Image] = []
    for jan in req.sku_jans:
        p = src_dir / f"{jan}.jpg"
        if not p.exists():
            missing.append(jan)
            continue
        tiles.append(Image.open(p).convert("RGB"))
        used.append(jan)

    if not tiles:
        raise HTTPException(404, f"no source images found in /data/whitebg/{req.source}/ for any of {req.sku_jans}")

    cols, rows = _grid_layout(len(tiles))
    cell_w = CANVAS // cols
    cell_h = CANVAS // rows
    canvas = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        tile_copy = tile.copy()
        tile_copy.thumbnail((cell_w, cell_h), Image.LANCZOS)
        ox = c * cell_w + (cell_w - tile_copy.width) // 2
        oy = r * cell_h + (cell_h - tile_copy.height) // 2
        canvas.paste(tile_copy, (ox, oy))
    canvas.save(dst, "JPEG", quality=90, optimize=True)

    return ComposeSpuResp(
        spu_key=req.spu_key,
        saved_path=str(dst),
        layout=f"{cols}x{rows}",
        skus_used=used,
        skus_missing=missing,
    )


@app.get("/list/{kind}/{key}")
def list_image(kind: Literal["raw", "upscaled", "branded", "spu"], key: str):
    """查某 JAN/SPU 是否已有图。返回 {exists, path}。"""
    p = DIRS[kind] / f"{key}.jpg"
    return {"exists": p.exists(), "path": str(p) if p.exists() else None}
