"""DeepSeek vision-model integration for image descriptions + structured
attributes.

Single public function: ``generate_ai_description(file_path, ocr_caption=None)``.
Returns a dict that ``_run_image_extraction`` writes into ``media_image_meta``.
See docs/ai-image-descriptions.md for the full design.

The prompt and the vocab modules are intentionally separate so we can tune
the prompt without touching the network code, and tune the vocab without
touching either.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import httpx

from server.ai_vocab import (
    AI_DESCRIPTION_MODEL,
    AI_DESCRIPTION_PROMPT_VERSION,
    BOOL_FLAGS,
    COLOR_CHARACTER_VOCAB,
    COLOR_TEMPERATURE_VOCAB,
    VIBE_VOCAB,
)

logger = logging.getLogger(__name__)

_DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
_DEEPSEEK_TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "60"))

# Use the large thumbnail (1600px max dim) for vision inference. It's the
# best size/cost trade-off — DeepSeek downsamples larger inputs anyway,
# and the thumbnail is already on disk so we avoid re-resizing on every call.
THUMB_LG_SUFFIX = "_thumb_lg.webp"


# ---------------------------------------------------------------------------
# Prompt — v1
# ---------------------------------------------------------------------------


def _build_prompt(ocr_caption: str | None) -> str:
    """Return the JSON-only prompt sent to the model."""
    vibe_list = " | ".join(VIBE_VOCAB)
    temp_list = " | ".join(COLOR_TEMPERATURE_VOCAB)
    char_list = " | ".join(COLOR_CHARACTER_VOCAB)
    ocr_block = (
        f'"{ocr_caption}"' if ocr_caption else "(no OCR text was extracted)"
    )
    return f"""You are analyzing an image from a creative archive. The OCR-extracted text in the image reads:

{ocr_block}

(May be empty or noisy — use only as a hint about textual content.)

Return ONLY a single JSON object with this exact schema. No preamble, no explanation, no markdown code fences:

{{
  "description":        "<2-4 detailed sentences: scene, subjects, style, notable details>",
  "tags":               ["<5-10 short lowercase search tags>"],
  "color_temperature":  "<{temp_list}>",
  "color_character":    "<{char_list}>",
  "vibe":               ["<1-3 of: {vibe_list}>"],
  "flags": {{
    "is_screenshot":    <bool>,
    "is_meme":          <bool>,
    "is_photo":         <bool>,
    "is_artwork":       <bool>,
    "is_ai_generated":  <bool>,
    "has_human":        <bool>,
    "has_face":         <bool>,
    "has_text_overlay": <bool>,
    "is_nsfw":          <bool>
  }}
}}

Guidelines:
- Be specific and factual in the description; avoid speculation about creator intent.
- "is_screenshot" = captured UI/app/browser/phone screen.
- "is_meme" = image-with-text overlay used for humor or social context.
- "is_ai_generated" = visible AI-art signatures (DALL·E / Midjourney / Stable Diffusion aesthetic, uncanny anatomy, overly polished textures).
- "has_text_overlay" = intentional text added to the image (signs, captions, watermarks, meme text) — NOT incidental text in a photograph.
- "is_nsfw" = sexual content, graphic violence, or strong gore. Casual nudity in an art context is borderline; err toward false unless clearly explicit.
- Tags help retrieval — subjects, objects, style words, mood concepts. No hashtags. Lowercase. Use hyphens for multi-word tags.
- Vibe — pick 1 if the mood is obvious; up to 3 only if multiple genuinely apply.
- Every value must come from the controlled vocabulary listed above; unknown values will be dropped.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thumbnail_lg_path(file_path: str) -> str:
    """Return the conventional ``<stem>_thumb_lg.webp`` sibling path."""
    from pathlib import Path as _Path

    p = _Path(file_path)
    return str(p.with_name(p.stem + THUMB_LG_SUFFIX))


def _image_to_data_url(path: str) -> str:
    """Read a JPEG/PNG/WEBP off disk and return it as a data: URL."""
    from pathlib import Path as _Path

    suffix = _Path(path).suffix.lower().lstrip(".")
    mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
        "heic": "image/heic",
    }.get(suffix, "application/octet-stream")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _clamp_to_vocab(value: Any, vocab: tuple[str, ...]) -> str | None:
    """Return value if it's in vocab (case-insensitive), else None."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    for entry in vocab:
        if entry == v:
            return entry
    return None


def _clamp_list_to_vocab(value: Any, vocab: tuple[str, ...], max_n: int) -> list[str]:
    """Filter a list to entries in vocab, dedupe preserving order, cap length."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        clamped = _clamp_to_vocab(item, vocab)
        if clamped and clamped not in seen:
            seen.add(clamped)
            out.append(clamped)
        if len(out) >= max_n:
            break
    return out


def _normalize_payload(raw: dict) -> dict:
    """Validate + clamp the model's JSON to our schema. Drops bad values."""
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        description = None
    else:
        description = description.strip()

    # Tags: list of lowercase strings, dedupe, up to 12.
    tags_raw = raw.get("tags") or []
    tags: list[str] = []
    seen_tags: set[str] = set()
    if isinstance(tags_raw, list):
        for t in tags_raw:
            if isinstance(t, str):
                t_clean = t.strip().lower()
                if t_clean and t_clean not in seen_tags:
                    seen_tags.add(t_clean)
                    tags.append(t_clean)
                if len(tags) >= 12:
                    break

    temperature = _clamp_to_vocab(raw.get("color_temperature"), COLOR_TEMPERATURE_VOCAB)
    character = _clamp_to_vocab(raw.get("color_character"), COLOR_CHARACTER_VOCAB)
    vibe = _clamp_list_to_vocab(raw.get("vibe"), VIBE_VOCAB, max_n=3)

    flags_raw = raw.get("flags") or {}
    flags: dict[str, bool | None] = {name: None for name in BOOL_FLAGS}
    if isinstance(flags_raw, dict):
        for name in BOOL_FLAGS:
            val = flags_raw.get(name)
            if isinstance(val, bool):
                flags[name] = val

    return {
        "description": description,
        "tags": tags,
        "color_temperature": temperature,
        "color_character": character,
        "vibe": vibe,
        "flags": flags,
    }


def _strip_json_fence(text: str) -> str:
    """Some models wrap JSON in ```json … ``` despite being told not to."""
    t = text.strip()
    if t.startswith("```"):
        # drop the opening fence (with optional language tag)
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: -3].rstrip()
    return t


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class DeepSeekError(RuntimeError):
    """Raised when DeepSeek returns an error or unparseable response."""


def generate_ai_description(
    file_path: str,
    ocr_caption: str | None = None,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    """Call DeepSeek vision and return a normalized, vocab-clamped dict.

    Raises DeepSeekError on transport / parsing / vocab failure. Callers
    (the extraction pipeline) wrap this in their own try/except and log
    to ExtractionFailure.

    Returned dict shape::

        {
          "description":         str | None,
          "tags":                list[str],          # 0-12, lowercase
          "color_temperature":   str | None,         # from controlled vocab
          "color_character":     str | None,         # from controlled vocab
          "vibe":                list[str],          # 0-3 from vocab
          "flags":               dict[str, bool | None],  # 9 named booleans
          "model":               str,                # echoed for provenance
          "prompt_version":      str,                # echoed for provenance
          "tokens_in":           int,
          "tokens_out":          int,
        }
    """
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY not set")

    # Prefer the lg thumbnail; fall back to the original file.
    thumb = _thumbnail_lg_path(file_path)
    image_path = thumb if os.path.exists(thumb) else file_path
    if not os.path.exists(image_path):
        raise DeepSeekError(f"image file not found: {image_path}")

    data_url = _image_to_data_url(image_path)
    prompt = _build_prompt(ocr_caption)
    model = model or AI_DESCRIPTION_MODEL

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        # response_format=json_object isn't universally supported, so we
        # rely on the prompt and strip code fences defensively.
        "temperature": 0.2,
        "max_tokens": 800,
    }

    try:
        with httpx.Client(timeout=_DEEPSEEK_TIMEOUT) as client:
            resp = client.post(
                f"{_DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.RequestError as exc:
        raise DeepSeekError(f"DeepSeek transport error: {exc}") from exc

    if resp.status_code >= 400:
        raise DeepSeekError(
            f"DeepSeek HTTP {resp.status_code}: {resp.text[:400]}"
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise DeepSeekError(f"DeepSeek returned non-JSON body: {resp.text[:200]}") from exc

    try:
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {}) or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError(f"DeepSeek response shape unexpected: {body!r}") from exc

    text = _strip_json_fence(text)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(
            f"DeepSeek output was not valid JSON: {text[:300]}"
        ) from exc

    normalized = _normalize_payload(raw)
    normalized["model"] = model
    normalized["prompt_version"] = AI_DESCRIPTION_PROMPT_VERSION
    normalized["tokens_in"] = tokens_in
    normalized["tokens_out"] = tokens_out
    return normalized
