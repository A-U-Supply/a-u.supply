"""AI description + tagging for audio samples via text-only LLM.

Generates descriptions and tags from filename + directory context using
DeepSeek text API. Batches files per directory for efficiency.

Hooked into the extraction pipeline via ``_run_audio_extraction`` so every
newly ingested audio file gets tagged automatically.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("VISION_API_KEY")
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_TIMEOUT = 90

BATCH_SIZE = 40


def generate_audio_ai_description(
    filename: str,
    dir_name: str | None = None,
    *,
    api_key: str | None = None,
) -> dict:
    """Generate description + tags for a single audio file via DeepSeek.

    Uses a shared batch when possible (if other files in the same directory
    are being tagged). Returns the same schema as the batch function.

    Returns:
        {"description": str, "tags": [str, ...]}
    """
    result = generate_audio_ai_descriptions(
        [filename], dir_name=dir_name, api_key=api_key,
    )
    return result.get(filename, {"description": "", "tags": []})


def generate_audio_ai_descriptions(
    filenames: list[str],
    dir_name: str | None = None,
    *,
    api_key: str | None = None,
    max_retries: int = 1,
) -> dict[str, dict]:
    """Generate description + tags for audio files in a directory via DeepSeek.

    Batches filenames together in one API call (up to BATCH_SIZE at a time)
    to minimize latency and cost.

    Args:
        filenames: List of filenames (just the basename, no path).
        dir_name: Directory name for context (e.g. ``"percussion_snare"``).
        api_key: Override API key; defaults to DEEPSEEK_API_KEY env var.

    Returns:
        Dict mapping filename -> {"description": str, "tags": [str, ...]}
    """
    key = api_key or DEEPSEEK_API_KEY
    if not key:
        logger.warning("DEEPSEEK_API_KEY not set, skipping AI audio tagging")
        return {}

    all_results: dict[str, dict] = {}
    for i in range(0, len(filenames), BATCH_SIZE):
        batch = filenames[i:i + BATCH_SIZE]
        try:
            results = _call_deepseek(batch, dir_name, key)
            all_results.update(results)
        except Exception as exc:
            logger.error("AI tagging batch failed for dir=%s offset=%d: %s", dir_name, i, exc)
            # Return partial results on failure
            for fname in batch:
                if fname not in all_results:
                    all_results[fname] = {"description": "", "tags": []}

    return all_results


def _call_deepseek(
    filenames: list[str],
    dir_name: str | None,
    api_key: str,
) -> dict[str, dict]:
    """Make a single DeepSeek API call for a batch of filenames."""
    dir_context = f' Directory: "{dir_name}".' if dir_name else ""
    list_str = "\n".join(f"  {f}" for f in filenames)
    prompt = f"""You are tagging one-shot audio samples for a music production search engine. These are royalty-free samples used in DAWs, drum machines, and samplers.{dir_context}

Filenames:
{list_str}

For each file, give a short description (5-15 words) describing what the sample sounds like, and 3-6 relevant tags (instrument, technique, genre, mood, use).
Return ONLY a JSON array, no preamble:
[
  {{"filename": "example.wav", "description": "...", "tags": ["tag1", "tag2", "tag3"]}},
  ...
]"""

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 3000,
    }

    try:
        with httpx.Client(timeout=DEEPSEEK_TIMEOUT) as client:
            resp = client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"DeepSeek transport error: {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:200]}")

    text = resp.json()["choices"][0]["message"]["content"].strip()
    text = _strip_fences(text)

    try:
        results = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek returned non-JSON: {text[:300]}") from exc

    if not isinstance(results, list):
        raise RuntimeError(f"DeepSeek returned non-array: {type(results).__name__}")

    out: dict[str, dict] = {}
    for entry in results:
        fname = entry.get("filename", "")
        if fname:
            out[fname] = {
                "description": (entry.get("description") or "").strip(),
                "tags": [t.strip().lower() for t in (entry.get("tags") or []) if t.strip()],
            }
    return out


def _strip_fences(text: str) -> str:
    """Remove ```json … ``` fences if present."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl != -1 else t[3:]
    if t.endswith("```"):
        t = t[:-3].rstrip()
    return t.strip()
