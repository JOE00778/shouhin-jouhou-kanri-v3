#!/usr/bin/env python3
"""Shopee Listing Generator (T-303) — title / description / spec JSON via Claude.

Module entry:
    from listing_generator import generate_listing, ListingDraft, ListingGeneratorError
    draft = generate_listing(spu, jan_infos)

CLI:
    python3 scripts/listing_generator.py \\
        --spu-input examples/spu_input_sample.csv \\
        --output drafts/

Design:
- Prompt template lives in prompts/listing_v1.md (versioned, not edited in place).
- Few-shot is dynamic: pull 3 most similar real listings (same brand or
  matching category keyword) from docs/output/existing_titles_clean.csv +
  existing_descriptions.csv.
- LLM call uses the official anthropic SDK; mockable via dependency injection
  (LLMClient protocol) for tests.
- 3 retries on JSON parse error or transient API errors.
- Each SPU → drafts/<spu_key>.json
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Protocol, Sequence

# Make sibling scripts importable when run directly (CLI) or as module.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from parse_spu_input import SPU, Variant, parse_spu_input_csv  # noqa: E402
from jan_collector import JanInfo  # noqa: E402


# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

PROJECT_ROOT = _SCRIPTS_DIR.parent
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "listing_v1.md"
DEFAULT_TITLES_CSV = PROJECT_ROOT / "docs" / "output" / "existing_titles_clean.csv"
DEFAULT_DESCS_CSV = PROJECT_ROOT / "docs" / "output" / "existing_descriptions.csv"
DEFAULT_BRAND_TOP_CSV = PROJECT_ROOT / "docs" / "output" / "brand_top.csv"

DEFAULT_MODEL = "claude-opus-4-7"
ALLOWED_MODELS = {
    # Anthropic（付费）
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    # Google Gemini（免费 tier 充足）
    "gemini-flash-latest",     # 自动指向最新版（当前 = gemini-3-flash-preview）
    "gemini-pro-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
}
# Gemini 默认走 latest 别名，永远拿最新模型版本
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"

DEFAULT_MAX_TOKENS = 16000
DEFAULT_RETRIES = 3
TITLE_MIN, TITLE_MAX = 80, 120
DESC_MIN, DESC_MAX = 1500, 3000

DEFAULT_HOOK = "Direct from Japan"
SIGNATURE_MARKER = "Smikie Japan"
NOTE_MARKER = "[NOTE]"

# HTML / emoji guards (used in validation, not just docs).
_HTML_TAG_RE = re.compile(r"<[a-zA-Z/!][^>]*>")
_EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "]",
    flags=re.UNICODE,
)


# --------------------------------------------------------------------------- #
# Errors / data structures
# --------------------------------------------------------------------------- #


class ListingGeneratorError(Exception):
    """Base error for the listing generator."""


class LLMResponseParseError(ListingGeneratorError):
    """LLM returned non-JSON or JSON missing required fields."""


class LLMCallError(ListingGeneratorError):
    """LLM API call failed (network / quota / refusal)."""


@dataclass
class ListingDraft:
    title: str
    description: str
    key_features: List[str]
    how_to_use: List[str]
    ingredients: Optional[str]
    spec_json: dict
    brand_normalized: str
    hook: str
    # Diagnostic
    model: str = ""
    spu_key: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------- #
# Brand normalization
# --------------------------------------------------------------------------- #


def _load_brand_top(path: Path = DEFAULT_BRAND_TOP_CSV) -> List[str]:
    """Top brands by frequency. Order matters — first match wins."""
    if not path.exists():
        return []
    out: List[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            next(reader)  # header
        except StopIteration:
            return []
        for row in reader:
            if not row:
                continue
            brand = row[0].strip()
            if brand:
                out.append(brand)
    return out


# Manual canonicalization for known capitalization conflicts (KOBAYASHI vs Kobayashi etc.)
_BRAND_CANONICAL_MAP = {
    "kobayashi": "Kobayashi",
    "skater": "Skater",
    "kose": "KOSE",
    "kao": "Kao",
    "biore": "Bioré",
    "bioré": "Bioré",
    "canmake": "CANMAKE",
    "kracie": "KRACIE",
    "mandom": "MANDOM",
    "pigeon": "Pigeon",
    "rohto": "Rohto",
    "lucido": "LUCIDO",
    "thermos": "Thermos",
    "integrate": "INTEGRATE",
    "maquillage": "MAQuilllAGE",
    "aqualabel": "AQUALABEL",
    "kate": "KATE",
    "cezanne": "CEZANNE",
    "meishoku": "MEISHOKU",
    "anessa": "Anessa",
    "elixir": "Elixir",
    "tomica": "TOMICA",
    "sonic": "Sonic",
    "sega": "SEGA",
    "konami": "KONAMI",
    "sanrio": "Sanrio",
    "pelican": "PELICAN",
    "soto": "SOTO",
    "uniliver": "UNILEVER",
    "unilever": "UNILEVER",
    "chifure": "CHIFURE",
    "nivea": "NIVEA",
}


def normalize_brand(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    key = s.lower()
    if key in _BRAND_CANONICAL_MAP:
        return _BRAND_CANONICAL_MAP[key]
    # Capitalize first letter, rest as-is
    return s[0].upper() + s[1:] if s[0].isalpha() else s


# --------------------------------------------------------------------------- #
# Few-shot retrieval
# --------------------------------------------------------------------------- #


@dataclass
class FewShotExample:
    title: str
    description: str


def _load_existing_corpus(
    titles_path: Path = DEFAULT_TITLES_CSV,
    descs_path: Path = DEFAULT_DESCS_CSV,
) -> List[FewShotExample]:
    """Join titles + descriptions on product_id."""
    titles_by_id: dict[str, str] = {}
    if titles_path.exists():
        with titles_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                pid = (row.get("product_id") or "").strip()
                title = (row.get("title") or "").strip()
                if pid and title:
                    titles_by_id[pid] = title

    descs_by_id: dict[str, str] = {}
    if descs_path.exists():
        with descs_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                pid = (row.get("product_id") or "").strip()
                desc = (row.get("description") or "").strip()
                if pid and desc:
                    descs_by_id[pid] = desc

    out: List[FewShotExample] = []
    for pid, title in titles_by_id.items():
        desc = descs_by_id.get(pid)
        if desc:
            out.append(FewShotExample(title=title, description=desc))
    return out


def _category_keywords(category_hint: Optional[str]) -> List[str]:
    """Pull keyword tokens from a slash-separated hint like 'cosmetics/lipstick'."""
    if not category_hint:
        return []
    # Map our internal category hints to title keywords likely to appear in real listings.
    hint = category_hint.lower()
    out: List[str] = []
    if "lipstick" in hint or "lip" in hint:
        out += ["Lip", "Lipstick", "Gloss"]
    if "eye" in hint or "liner" in hint:
        out += ["Eye", "Liner", "Mascara"]
    if "foundation" in hint or "powder" in hint or "base" in hint:
        out += ["Foundation", "Powder", "Base"]
    if "skin" in hint or "lotion" in hint or "cream" in hint:
        out += ["Lotion", "Cream", "Skin"]
    if "shampoo" in hint or "hair" in hint:
        out += ["Shampoo", "Hair"]
    if "food" in hint or "snack" in hint or "candy" in hint:
        out += ["Candy", "Snack", "Chocolate"]
    if "drink" in hint or "tea" in hint:
        out += ["Tea", "Drink"]
    if "kitchen" in hint or "mug" in hint or "cup" in hint or "bottle" in hint:
        out += ["Mug", "Bottle", "Tumbler", "Cup"]
    if "stationery" in hint or "pen" in hint:
        out += ["Pen", "Pencil", "Eraser"]
    if "toy" in hint or "figurine" in hint or "plush" in hint:
        out += ["Figure", "Plush", "Toy"]
    if "bag" in hint or "tote" in hint:
        out += ["Bag", "Tote", "Pouch"]
    return out


def select_few_shot_examples(
    spu: SPU,
    brand_normalized: Optional[str],
    corpus: Sequence[FewShotExample],
    n: int = 3,
) -> List[FewShotExample]:
    """Pick top-N few-shots by:
      1. brand match (case-insensitive substring on first token of title)
      2. category-keyword match
      3. fallback to first N in corpus.
    """
    if not corpus:
        return []

    scored: list[tuple[int, int, FewShotExample]] = []  # (-score, idx, ex)
    keywords = _category_keywords(spu.category_hint)
    brand_lc = (brand_normalized or "").lower()

    for idx, ex in enumerate(corpus):
        title_lc = ex.title.lower()
        # First token (brand position)
        first_token = title_lc.split(" ", 1)[0] if title_lc else ""
        score = 0
        if brand_lc and first_token == brand_lc:
            score += 100
        elif brand_lc and brand_lc in title_lc:
            score += 50
        for kw in keywords:
            if kw.lower() in title_lc:
                score += 5
        # Length sweet-spot bonus (avg 90)
        if 80 <= len(ex.title) <= 120:
            score += 1
        if score > 0:
            scored.append((-score, idx, ex))

    scored.sort()
    picks = [ex for _, _, ex in scored[:n]]

    # Pad with arbitrary corpus entries if short
    if len(picks) < n:
        seen_titles = {ex.title for ex in picks}
        for ex in corpus:
            if ex.title not in seen_titles:
                picks.append(ex)
                seen_titles.add(ex.title)
                if len(picks) >= n:
                    break
    return picks[:n]


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


def _load_prompt_template(path: Path = DEFAULT_PROMPT_PATH) -> str:
    if not path.exists():
        raise ListingGeneratorError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def _build_user_prompt(
    spu: SPU,
    jan_infos: Sequence[JanInfo],
    brand_normalized: Optional[str],
    few_shots: Sequence[FewShotExample],
    hook: str,
) -> str:
    """Build the per-request user message body (system prompt is the template)."""
    spu_payload = {
        "spu_key": spu.spu_key,
        "category_hint": spu.category_hint,
        "variants": [
            {
                "jan": v.jan,
                "variant_attrs": v.variant_attrs,
                "weight_grams": v.weight_grams,
                "notes": v.notes,
            }
            for v in spu.variants
        ],
    }

    jan_payload = []
    for info in jan_infos:
        jan_payload.append(
            {
                "jan": info.jan,
                "found": info.found,
                "brand": info.brand,
                "product_name_jp": info.product_name_jp,
                "description_jp": info.description_jp,
                "price_jpy": info.price_jpy,
                "source_url": info.source_url,
            }
        )

    fs_block = ""
    for i, ex in enumerate(few_shots, 1):
        # Truncate description to keep prompt size bounded
        desc = ex.description if len(ex.description) <= 2400 else ex.description[:2400] + "..."
        fs_block += (
            f"\n--- EXAMPLE {i} ---\n"
            f"TITLE: {ex.title}\n"
            f"DESCRIPTION:\n{desc}\n"
        )

    return (
        "## SPU INPUT\n"
        f"```json\n{json.dumps(spu_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "## JAN LOOKUP RESULTS\n"
        f"```json\n{json.dumps(jan_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "## NORMALIZED BRAND\n"
        f"{brand_normalized or '(unknown — pick from category/notes)'}\n\n"
        "## HOOK\n"
        f"{hook}\n\n"
        "## FEW-SHOT EXAMPLES (3 real Smikie Japan listings — match this style)\n"
        f"{fs_block}\n\n"
        "## TASK\n"
        "Generate the Shopee listing JSON object (8 fields) for the SPU above. "
        "Output ONLY the JSON object — no prose, no markdown fence."
    )


# --------------------------------------------------------------------------- #
# LLM client abstraction (mockable)
# --------------------------------------------------------------------------- #


class LLMClient(Protocol):
    """Minimal interface for an LLM chat completion. Tests inject a fake."""

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        ...


class AnthropicClient:
    """Production LLM client backed by the official Anthropic SDK.

    Lazy-imports `anthropic` so tests / packaging don't require it installed.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise ListingGeneratorError(
                "anthropic SDK not installed. Run: "
                "pip3 install --break-system-packages anthropic"
            ) from e
        self._anthropic = __import__("anthropic")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ListingGeneratorError(
                "ANTHROPIC_API_KEY environment variable is required"
            )
        self._client = self._anthropic.Anthropic(api_key=key)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Concatenate all text blocks
        out = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                out.append(block.text)
        return "".join(out)


class GeminiClient:
    """Production LLM client backed by Google Gemini (免费 tier).

    Free tier: gemini-2.0-flash 10 RPM / 1500 RPD, gemini-1.5-flash 15 RPM / 1500 RPD.
    Lazy-imports `google.generativeai` so packaging doesn't require it.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        try:
            import google.generativeai as genai  # noqa: F401
        except ImportError as e:
            raise ListingGeneratorError(
                "google-generativeai SDK not installed. Run: "
                "pip3 install --break-system-packages google-generativeai"
            ) from e
        self._genai = __import__("google.generativeai", fromlist=["dummy"])
        key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise ListingGeneratorError(
                "GEMINI_API_KEY environment variable is required"
            )
        self._genai.configure(api_key=key)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        m = self._genai.GenerativeModel(
            model_name=model,
            system_instruction=system,
        )
        resp = m.generate_content(
            user,
            generation_config={"max_output_tokens": max_tokens},
        )
        # Gemini SDK exposes `.text` for the concatenated text of all parts.
        text = getattr(resp, "text", None)
        if not text:
            # Fallback: walk candidates → parts
            out = []
            for cand in getattr(resp, "candidates", []) or []:
                content = getattr(cand, "content", None)
                for part in getattr(content, "parts", []) or []:
                    t = getattr(part, "text", None)
                    if t:
                        out.append(t)
            text = "".join(out)
        return text or ""


def _auto_select_client() -> "LLMClient":
    """Pick a real LLM client based on env keys, preferring free Gemini.

    Order: GEMINI_API_KEY → ANTHROPIC_API_KEY → raise.
    """
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return GeminiClient()
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return AnthropicClient()
    raise ListingGeneratorError(
        "No LLM API key found. Set GEMINI_API_KEY (free) or ANTHROPIC_API_KEY"
    )


def _auto_select_model() -> str:
    """Pick the default model matching the auto-selected client."""
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return DEFAULT_GEMINI_MODEL
    return DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# Response parsing & validation
# --------------------------------------------------------------------------- #


_REQUIRED_FIELDS = {
    "title",
    "description",
    "key_features",
    "how_to_use",
    "ingredients",
    "spec_json",
    "brand_normalized",
    "hook",
}


def _strip_markdown_fence(s: str) -> str:
    """Some models wrap JSON in ```json ... ``` — strip if present."""
    s = s.strip()
    if s.startswith("```"):
        # remove first line and trailing fence
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def parse_llm_response(raw: str) -> dict:
    """Parse the LLM raw text into a validated dict. Raises LLMResponseParseError."""
    if not raw or not raw.strip():
        raise LLMResponseParseError("LLM returned empty response")

    cleaned = _strip_markdown_fence(raw)

    # Extract first {...} block if leading prose slipped in
    if not cleaned.lstrip().startswith("{"):
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not m:
            raise LLMResponseParseError(
                f"No JSON object in response (head: {cleaned[:200]!r})"
            )
        cleaned = m.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(
            f"JSON decode failed: {e}; head: {cleaned[:200]!r}"
        ) from e

    if not isinstance(data, dict):
        raise LLMResponseParseError(f"Expected JSON object, got {type(data).__name__}")

    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise LLMResponseParseError(f"Missing required fields: {sorted(missing)}")

    # Type coercion / soft validation
    if not isinstance(data["title"], str):
        raise LLMResponseParseError("title must be a string")
    if not isinstance(data["description"], str):
        raise LLMResponseParseError("description must be a string")
    if not isinstance(data["key_features"], list):
        raise LLMResponseParseError("key_features must be a list")
    if not isinstance(data["how_to_use"], list):
        raise LLMResponseParseError("how_to_use must be a list")
    if not isinstance(data["spec_json"], dict):
        raise LLMResponseParseError("spec_json must be an object")
    if not isinstance(data["brand_normalized"], str):
        raise LLMResponseParseError("brand_normalized must be a string")
    if not isinstance(data["hook"], str):
        raise LLMResponseParseError("hook must be a string")
    if data["ingredients"] is not None and not isinstance(data["ingredients"], str):
        raise LLMResponseParseError("ingredients must be a string or null")

    return data


def _validate_listing_quality(data: dict) -> None:
    """Soft post-checks. Raises LLMResponseParseError on hard failures
    (bad length, HTML, emoji, missing signature). Used as part of retry loop."""
    title = data["title"]
    desc = data["description"]

    if not (TITLE_MIN <= len(title) <= TITLE_MAX):
        raise LLMResponseParseError(
            f"title length {len(title)} outside [{TITLE_MIN}, {TITLE_MAX}]"
        )
    if _HTML_TAG_RE.search(title) or _HTML_TAG_RE.search(desc):
        raise LLMResponseParseError("HTML tags found in title or description")
    if _EMOJI_RE.search(title) or _EMOJI_RE.search(desc):
        raise LLMResponseParseError("Emoji found in title or description")

    if not (DESC_MIN <= len(desc) <= DESC_MAX):
        raise LLMResponseParseError(
            f"description length {len(desc)} outside [{DESC_MIN}, {DESC_MAX}]"
        )
    if NOTE_MARKER not in desc:
        raise LLMResponseParseError(f"description missing '{NOTE_MARKER}' block")
    if SIGNATURE_MARKER not in desc:
        raise LLMResponseParseError(
            f"description missing '{SIGNATURE_MARKER}' signature"
        )


# --------------------------------------------------------------------------- #
# Main entry: generate_listing
# --------------------------------------------------------------------------- #


def _decide_hook(spu: SPU, jan_infos: Sequence[JanInfo]) -> str:
    """Default hook is 'Direct from Japan'. Use 'Made in Japan' only if SPU/JAN
    notes mention 本土工厂 / Made in Japan."""
    haystack = " ".join(
        filter(
            None,
            [
                spu.category_hint or "",
                *(v.notes or "" for v in spu.variants),
                *(info.description_jp or "" for info in jan_infos),
                *(info.product_name_jp or "" for info in jan_infos),
            ],
        )
    ).lower()
    if "made in japan" in haystack or "本土工厂" in haystack or "本土工場" in haystack:
        return "Made in Japan"
    return DEFAULT_HOOK


def _decide_brand(spu: SPU, jan_infos: Sequence[JanInfo]) -> Optional[str]:
    """Pick a brand from JAN info or notes, normalize."""
    for info in jan_infos:
        if info.brand:
            normalized = normalize_brand(info.brand)
            if normalized:
                return normalized
    # Try notes
    for v in spu.variants:
        if v.notes:
            for token in re.split(r"\s+", v.notes):
                if token and token[0].isalpha() and len(token) >= 3:
                    return normalize_brand(token)
    return None


def generate_listing(
    spu: SPU,
    jan_infos: Sequence[JanInfo],
    *,
    llm_client: Optional[LLMClient] = None,
    model: Optional[str] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    retries: int = DEFAULT_RETRIES,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    corpus: Optional[Sequence[FewShotExample]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ListingDraft:
    """Generate a Shopee listing for one SPU using few-shot Claude prompting.

    Args:
        spu: parsed SPU object (T-301).
        jan_infos: JanInfo per JAN in spu.variants (T-302). May be empty list
            (e.g. all JAN lookups failed).
        llm_client: dependency-injected LLM client. Defaults to AnthropicClient.
        model: claude-opus-4-7 (default) or claude-sonnet-4-6.
        max_tokens: max response tokens.
        retries: total attempts on JSON parse / quality / API failure.
        prompt_path: path to prompts/listing_v1.md (or v2 etc.).
        corpus: pre-loaded few-shot corpus (tests pass an empty/synthetic list).

    Raises:
        ListingGeneratorError: model not allowed / prompt missing.
        LLMResponseParseError: all retries exhausted.
        LLMCallError: all retries exhausted on transport-level errors.
    """
    # Auto-pick model based on which API key is configured (only when caller
    # didn't pin a model and didn't inject a client). Tests inject a client and
    # explicit model.
    if model is None:
        if llm_client is None:
            model = _auto_select_model()
        else:
            model = DEFAULT_MODEL

    if model not in ALLOWED_MODELS:
        raise ListingGeneratorError(
            f"model {model!r} not in {sorted(ALLOWED_MODELS)}"
        )

    system_prompt = _load_prompt_template(prompt_path)
    if corpus is None:
        corpus = _load_existing_corpus()

    brand = _decide_brand(spu, jan_infos)
    hook = _decide_hook(spu, jan_infos)
    few_shots = select_few_shot_examples(spu, brand, corpus, n=3)
    user_prompt = _build_user_prompt(spu, jan_infos, brand, few_shots, hook)

    client = llm_client or _auto_select_client()

    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            raw = client.complete(
                model=model,
                system=system_prompt,
                user=user_prompt,
                max_tokens=max_tokens,
            )
            data = parse_llm_response(raw)
            _validate_listing_quality(data)
            return ListingDraft(
                title=data["title"],
                description=data["description"],
                key_features=list(data["key_features"]),
                how_to_use=list(data["how_to_use"]),
                ingredients=data["ingredients"],
                spec_json=dict(data["spec_json"]),
                brand_normalized=data["brand_normalized"],
                hook=data["hook"],
                model=model,
                spu_key=spu.spu_key,
            )
        except LLMResponseParseError as e:
            last_err = e
            if attempt >= retries:
                break
            sleep(min(2 ** (attempt - 1), 4))
        except Exception as e:  # noqa: BLE001
            # API/network errors — retry with backoff
            last_err = e
            if attempt >= retries:
                # Re-wrap any last-iteration non-parse error as LLMCallError
                raise LLMCallError(f"LLM call failed after {retries} attempts: {e}") from e
            sleep(min(2 ** (attempt - 1), 4))

    # Exhausted retries with parse errors
    assert last_err is not None
    if isinstance(last_err, LLMResponseParseError):
        raise last_err
    raise LLMCallError(f"LLM call failed: {last_err}") from last_err


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="listing_generator",
        description="Generate Shopee listings (title/description/spec) for SPUs via Claude.",
    )
    p.add_argument(
        "--spu-input",
        required=True,
        help="Path to SPU CSV (see examples/spu_input_sample.csv)",
    )
    p.add_argument(
        "--output",
        required=True,
        help="Output directory for per-SPU JSON drafts",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LLM model (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"max_tokens for response (default: {DEFAULT_MAX_TOKENS})",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N SPUs (default: 0 = all). Useful for dry-run.",
    )
    p.add_argument(
        "--no-jan-fetch",
        action="store_true",
        help="Skip JAN collection (use empty JanInfo list). Useful when "
        "RAKUTEN_APP_ID is not set.",
    )
    p.add_argument(
        "--prompt",
        default=str(DEFAULT_PROMPT_PATH),
        help=f"Path to prompt template (default: {DEFAULT_PROMPT_PATH})",
    )
    return p


def _load_jan_infos_for_spu(
    spu: SPU, *, skip: bool
) -> List[JanInfo]:
    if skip:
        return [JanInfo(jan=v.jan, found=False, source="skip") for v in spu.variants]

    # Lazy import to avoid hard dependency on RAKUTEN_APP_ID being set
    try:
        from jan_collector import fetch_batch
    except ImportError:
        return [JanInfo(jan=v.jan, found=False, source="import-error") for v in spu.variants]

    jans = [v.jan for v in spu.variants]
    try:
        return list(fetch_batch(jans))
    except Exception as e:  # noqa: BLE001
        print(
            f"warning: JAN fetch failed for {spu.spu_key}: {e}; using empty JanInfo",
            file=sys.stderr,
        )
        return [JanInfo(jan=v.jan, found=False, source="error") for v in spu.variants]


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    spu_path = Path(args.spu_input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        spus = parse_spu_input_csv(spu_path)
    except Exception as e:  # noqa: BLE001
        print(f"error: failed to parse SPU CSV: {e}", file=sys.stderr)
        return 2

    if not spus:
        print("error: no SPUs in input", file=sys.stderr)
        return 2

    if args.limit and args.limit > 0:
        spus = spus[: args.limit]

    if args.model not in ALLOWED_MODELS:
        print(
            f"error: --model {args.model!r} not in {sorted(ALLOWED_MODELS)}",
            file=sys.stderr,
        )
        return 2

    try:
        client = AnthropicClient()
    except ListingGeneratorError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    prompt_path = Path(args.prompt)
    corpus = _load_existing_corpus()

    ok = 0
    failed = 0
    for spu in spus:
        print(f"==> {spu.spu_key} ({len(spu.variants)} variants)", file=sys.stderr)
        jan_infos = _load_jan_infos_for_spu(spu, skip=args.no_jan_fetch)
        try:
            draft = generate_listing(
                spu,
                jan_infos,
                llm_client=client,
                model=args.model,
                max_tokens=args.max_tokens,
                prompt_path=prompt_path,
                corpus=corpus,
            )
        except ListingGeneratorError as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            failed += 1
            continue

        out_path = out_dir / f"{spu.spu_key}.json"
        out_path.write_text(
            json.dumps(draft.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"  OK: title {len(draft.title)}c / desc {len(draft.description)}c "
            f"-> {out_path}",
            file=sys.stderr,
        )
        ok += 1

    print(f"done: {ok} ok / {failed} failed", file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
