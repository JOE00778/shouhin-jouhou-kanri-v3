"""图片文字翻译核心 · 基于 Gemini 2.5 Flash Image.

纯库版本，无 argparse / 文件 I/O；输入输出都是 bytes，
便于 Streamlit 上传文件 / 批处理 / 测试统一调用。

API key 由调用方传入（page 端从 st.secrets / env 读 GEMINI_API_KEY）。

⚠️ 安全：图片字节会被发送到 Google Gemini API。
- AI Studio 免费 key 默认会用于改进模型 → 建议升级到付费层关闭训练用途
- 调用方应先用 `strip_metadata()` 剥离 EXIF（GPS / 设备 / 时间戳）再发送
"""
from __future__ import annotations

import io
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.genai import Client  # noqa: F401

# 单图字节上限（默认 15 MB，超过则 strip_metadata 抛错；可由 page 端在 caps 处拦截）
MAX_IMAGE_BYTES = 15 * 1024 * 1024

LANG_NAMES: dict[str, str] = {
    "en": "English",
    "tl": "Tagalog (Filipino)",
    "ja": "Japanese",
    "zh": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "ko": "Korean",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "ms": "Malay",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
}

DEFAULT_MODEL = "gemini-2.5-flash-image-preview"


def build_prompt(target_lang_code: str, source_lang_code: str, note: str) -> str:
    target = LANG_NAMES.get(target_lang_code, target_lang_code)
    source_clause = (
        f"The source language of the text is "
        f"{LANG_NAMES.get(source_lang_code, source_lang_code)}."
        if source_lang_code != "auto"
        else "Auto-detect the source language of the text."
    )
    extra = f"\n\nAdditional instructions: {note}" if note else ""
    return (
        f"Edit this image: translate ALL visible text into {target}. "
        f"{source_clause}\n\n"
        "Strict requirements:\n"
        "- Keep every non-text element identical: people, faces, products, "
        "packaging shapes, background, lighting, colors, composition, "
        "photography style.\n"
        "- Preserve the typographic style (size relative to image, weight, "
        "alignment, color, decorations like underlines or bullet pills) as "
        "closely as possible.\n"
        "- Do NOT translate brand names, product model codes (e.g. #05), "
        "trademarked logos, or numeric values.\n"
        "- Do NOT add new text, watermarks, or design elements that were not "
        "in the original.\n"
        "- The translated copy should sound natural to a native speaker, not "
        "literal.\n"
        f"- Render the result as a single edited image in {target}."
        f"{extra}"
    )


def get_client(api_key: str):
    """构造 Gemini 客户端（懒加载 google.genai，避免 page 启动开销）。"""
    from google import genai  # type: ignore

    return genai.Client(api_key=api_key)


def strip_metadata(image_bytes: bytes) -> tuple[bytes, str]:
    """剥离 EXIF / GPS / 设备元数据，重新编码成干净 PNG。

    防止 Google 端拿到 GPS 坐标 / 拍摄设备 / 时间戳等附带信息。
    超过 MAX_IMAGE_BYTES 抛 ValueError。
    返回 (cleaned_bytes, "image/png")。
    """
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片过大（{len(image_bytes) / 1024 / 1024:.1f} MB）"
            f"> 上限 {MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB"
        )
    from PIL import Image  # Pillow 已是 streamlit 间接依赖

    with Image.open(io.BytesIO(image_bytes)) as raw:
        target_mode = raw.mode if raw.mode in ("RGB", "RGBA") else "RGB"
        # 新建空白画布 + paste，彻底丢弃 EXIF / XMP / ICC / GPS / 设备指纹
        clean = Image.new(target_mode, raw.size)
        if raw.mode == target_mode:
            clean.paste(raw)
        else:
            clean.paste(raw.convert(target_mode))
    buf = io.BytesIO()
    clean.save(buf, format="PNG", optimize=False)
    return buf.getvalue(), "image/png"


def load_image_from_url(url: str, timeout: int = 60) -> tuple[bytes, str]:
    """下载 URL 图片，返回 (bytes, mime)。"""
    import requests  # type: ignore

    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    mime = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    return r.content, mime


def translate_image_bytes(
    client,
    image_bytes: bytes,
    mime: str,
    target_lang: str,
    source_lang: str = "auto",
    note: str = "",
    model: str = DEFAULT_MODEL,
    retries: int = 2,
) -> bytes:
    """调用 Gemini 翻译图中文字，返回新图片 PNG/JPEG 字节流。

    失败时最多重试 `retries` 次（指数退避 2s/4s/6s...），全部失败抛 RuntimeError。
    """
    from google.genai import types  # type: ignore

    prompt = build_prompt(target_lang, source_lang, note)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime),
                    prompt,
                ],
            )
            for cand in response.candidates or []:
                for part in cand.content.parts or []:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        return inline.data
            raise RuntimeError("Gemini response contained no image part")
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Failed after {retries} attempts: {last_err}")
