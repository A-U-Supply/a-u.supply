"""Controlled vocabularies for AI-derived metadata.

These tuples are the source of truth for the values that the vision model
is allowed to return for vibe / color temperature / color character. The
generation function clamps to these — anything else gets dropped (logged
and recorded as NULL).

Adding new values is backwards-compatible: existing rows stay valid, new
generations can pick from the expanded list. Removing values is *not*
backwards-compatible without a backfill, so retire entries cautiously.

See docs/ai-image-descriptions.md for the design.
"""

from __future__ import annotations

VIBE_VOCAB: tuple[str, ...] = (
    "moody",
    "cheerful",
    "melancholic",
    "sterile",
    "chaotic",
    "nostalgic",
    "dystopian",
    "whimsical",
    "aggressive",
    "serene",
    "mundane",
    "surreal",
    "ironic",
    "gritty",
    "clinical",
    "dreamy",
)

COLOR_TEMPERATURE_VOCAB: tuple[str, ...] = (
    "warm",
    "cool",
    "neutral",
)

COLOR_CHARACTER_VOCAB: tuple[str, ...] = (
    "vibrant",
    "muted",
    "pastel",
    "monochrome",
    "high-contrast",
    "earthy",
    "dark",
    "light",
)

BOOL_FLAGS: tuple[str, ...] = (
    "is_screenshot",
    "is_meme",
    "is_photo",
    "is_artwork",
    "is_ai_generated",
    "has_human",
    "has_face",
    "has_text_overlay",
    "is_nsfw",
)

# Bumped whenever the prompt or vocab changes meaningfully. Stored on each
# generated row so we can target re-runs with WHERE ai_description_prompt_v != 'vN'.
AI_DESCRIPTION_PROMPT_VERSION = "v1"

# Default vision model id. Override per-deployment via env var VISION_MODEL.
# Centralised here so swapping is a config change, not a code change.
AI_DESCRIPTION_MODEL = "deepseek-ai/deepseek-vl2"
