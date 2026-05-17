"""图片文字翻译模块.

把 image-translate skill (`~/.claude/skills/image-translate/`) 的核心
封装成一元管理系统的内部模块：基于 Gemini 2.5 Flash Image，把图中文字
翻译成目标语言，保留人物 / 产品 / 排版 / 配色不变。

入口供 page 26 调用：
    from modules.image_translate import (
        LANG_NAMES, DEFAULT_MODEL,
        get_client, translate_image_bytes, load_image_from_url,
    )
"""
from .translator import (
    DEFAULT_MODEL,
    LANG_NAMES,
    MAX_IMAGE_BYTES,
    build_prompt,
    get_client,
    load_image_from_url,
    strip_metadata,
    translate_image_bytes,
)

__all__ = [
    "DEFAULT_MODEL",
    "LANG_NAMES",
    "MAX_IMAGE_BYTES",
    "build_prompt",
    "get_client",
    "load_image_from_url",
    "strip_metadata",
    "translate_image_bytes",
]
