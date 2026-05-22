# AI Image Descriptions — Design Plan

Status: **Designed, in active implementation.** Last updated 2026-05-22.

## Goal

Enrich the media archive's search with vision-model-generated descriptions, tags,
and structured attributes for every image, on top of the existing OCR pipeline.
A user searching "wood chips" should find the Play Ground Chips photo even though
OCR alone only caught the partial banner text. The pipeline must:

- Run on every new ingested image automatically.
- Backfill the existing ~2,300-image archive (one-time).
- Survive deploys and SSH drops (re-uses the OCR backfill's checkpoint pattern).
- Cost ≤ $1 for the full backfill.
- Coexist with the deterministic PIL color palette without UI confusion.
- Allow humans to override AI-derived flags / vibe / color on the detail page.

## Engine

**Provider-agnostic, defaults to SiliconFlow hosting `deepseek-ai/deepseek-vl2`**
(OpenAI-compatible API, free tier, ≈ $0.0002–0.0005 / image). The
`server/ai_description.py` client reads `VISION_API_BASE_URL`,
`VISION_API_KEY`, and `VISION_MODEL` from the environment, so we can swap
to any OpenAI-compatible vision provider (Gemini Flash, Anthropic, Qwen-VL
via DashScope, a local vLLM, etc.) without touching code.

Why not DeepSeek's own API? `api.deepseek.com` only serves `deepseek-v4-flash`
and `deepseek-v4-pro` (text-only) as of May 2026 despite DeepSeek-VL2 existing
as open weights — SiliconFlow is the canonical hosted route for the same model.

Defaults:
- `VISION_API_BASE_URL=https://api.siliconflow.com/v1`
- `VISION_MODEL=deepseek-ai/deepseek-vl2`
- (legacy `DEEPSEEK_API_KEY` is still honoured as a fallback for the key only)

- Total backfill cost ≈ $0.50–1.00.
- One unified prompt returns description + tags + flags + colors + vibe as JSON.

The image is sent as base64 from the `_thumb_lg.webp` (1600 px max dim) that the
extraction pipeline already produces — no public URL exposure, no extra resize.

## Data model

All new fields live on `media_image_meta` (the existing per-image metadata table).

### Generation provenance

| field | type | purpose |
| --- | --- | --- |
| `ai_description` | TEXT | 2-4 sentence detailed scene description |
| `ai_description_model` | TEXT | e.g. `deepseek-vision-preview` |
| `ai_description_prompt_v` | TEXT | e.g. `v1` — bumped when we tune the prompt; allows targeted re-runs |
| `ai_description_generated_at` | DATETIME | UTC timestamp |
| `ai_description_tokens_in` | INTEGER | cost auditing |
| `ai_description_tokens_out` | INTEGER | cost auditing |

### Searchable / semantic fields

| field | type | values |
| --- | --- | --- |
| `ai_tags` | TEXT (JSON list) | 5–10 short search tags, lowercase |
| `ai_color_temperature` | TEXT | `warm` \| `cool` \| `neutral` |
| `ai_color_character` | TEXT | `vibrant` \| `muted` \| `pastel` \| `monochrome` \| `high-contrast` \| `earthy` \| `dark` \| `light` |
| `ai_vibe` | TEXT (JSON list) | 1–3 of: `moody`, `cheerful`, `melancholic`, `sterile`, `chaotic`, `nostalgic`, `dystopian`, `whimsical`, `aggressive`, `serene`, `mundane`, `surreal`, `ironic`, `gritty`, `clinical`, `dreamy` |

Vibe and character/temperature lists are controlled vocabularies enforced in
`server/ai_vocab.py`. They can grow over time without a schema migration.

### Boolean flags

| field | meaning |
| --- | --- |
| `is_screenshot` | clearly a captured UI/app/browser/phone screen |
| `is_meme` | image-with-text overlay used for humor/social context |
| `is_photo` | real-world photograph |
| `is_artwork` | drawing / painting / illustration (non-photographic, non-AI) |
| `is_ai_generated` | visible AI-art signatures (DALL·E, MJ, SD aesthetic) |
| `has_human` | one or more people visible (any size) |
| `has_face` | at least one human face clearly visible |
| `has_text_overlay` | intentional text (signage / captions / watermarks / memes), not incidental text in a scene |
| `is_nsfw` | sexual content, graphic violence, or strong gore |

NULL = not analyzed yet. Distinguishes from explicit `false`.

### Human overrides

`ai_overrides` TEXT (JSON dict, e.g. `{"is_meme": true, "ai_vibe": ["moody"]}`).
When set for a field, that field is **preserved** by future regenerations — the
AI never clobbers a manual override. The detail page provides toggles for the
bool flags and dropdowns for `ai_vibe`, `ai_color_temperature`, `ai_color_character`.

## Pipeline integration

### New uploads

`_run_image_extraction` (`server/extraction.py`) gets a new Step 5: call
`generate_ai_description(file_path, ocr_caption)`. Failures are logged to the
existing `ExtractionFailure` table with `extraction_type="ai_description"` and
can be retried via the existing `/api/extraction-failures/:id/retry` endpoint.

### Retries

`_retry_single_step` learns `extraction_type="ai_description"`.

### Backfill

`manage.py backfill-ai-descriptions [--all] [--restart]`. Same checkpoint
pattern as OCR (`/app/data/.ai-desc-backfill-progress`). By default skips items
that already have `ai_description_generated_at` set; `--all` re-OCRs everything.

## Prompt (v1)

```
You are analyzing an image from a creative archive. The OCR-extracted text
in the image reads:

"<ocr_caption>"

(May be empty or noisy — use only as a hint about textual content.)

Return ONLY a single JSON object with this exact schema. No preamble, no
explanation, no markdown code fences:

{
  "description":        "<2-4 detailed sentences: scene, subjects, style, notable details>",
  "tags":               ["<5-10 short lowercase search tags>"],
  "color_temperature":  "<warm | cool | neutral>",
  "color_character":    "<vibrant | muted | pastel | monochrome | high-contrast | earthy | dark | light>",
  "vibe":               ["<1-3 of the controlled vibe vocab>"],
  "flags": {
    "is_screenshot":    <bool>,
    "is_meme":          <bool>,
    "is_photo":         <bool>,
    "is_artwork":       <bool>,
    "is_ai_generated":  <bool>,
    "has_human":        <bool>,
    "has_face":         <bool>,
    "has_text_overlay": <bool>,
    "is_nsfw":          <bool>
  }
}

Guidelines:
- Be specific and factual in the description; avoid speculation.
- "is_screenshot" = captured UI/app/browser/phone screen.
- "is_meme"       = image-with-text overlay used for humor/social context.
- "is_ai_generated" = visible AI-art signatures (DALL·E, MJ, SD aesthetic).
- "has_text_overlay" = intentional text (signs/memes/captions/watermarks),
                       not incidental text in a real-world scene.
- "is_nsfw" = sexual content, graphic violence, or strong gore.
- Tags help retrieval — subjects, objects, style words, mood. No hashtags.
- Vibe — pick 1 if obvious; up to 3 only if multiple genuinely apply.
```

The prompt version is recorded with each generation so we can target
regenerations cleanly (`WHERE ai_description_prompt_v != 'v2'`).

## Search index

Meilisearch sync forwards all new fields. Filterable: every bool, every
vocab field, `ai_tags` (any-of match). Searchable: `ai_description` and
`ai_tags`. Weights are lower than `caption` (OCR is verbatim and more
authoritative than AI prose).

## UI

### Detail page

A new collapsed `<details>` card at the bottom of `/admin/search/detail`,
labeled clearly as AI-generated. Contains:

- Description prose.
- Tag chips (each with a one-click "promote to manual tag" action).
- Color temperature + character chips.
- Vibe chips.
- Bool flag toggles (click to flip; records the override).
- Small footer: model name + generated_at timestamp + "Regenerate" button.

### Filter sidebar

Three new sections, clearly separated from the existing COLORS section so
the deterministic PIL palette stays unambiguous:

```
COLORS                      [unchanged]
  ⬛ ⬜ 🟥 🟦 …               click-to-filter histogram (PIL color groups)

VIBE                        [new]
  □ moody  □ chaotic  …      controlled vibe vocab, multi-select

COLOR TEMPERATURE           [new]
  ⚪ warm  ⚪ cool  …         single-select chip group
COLOR CHARACTER             [new]
  ⚪ vibrant  ⚪ earthy  …    single-select chip group

CONTENT                     [new]
  ☐ has human   ☐ has face   ☐ has text overlay
  ☐ is photo    ☐ is screenshot
  ☐ is meme     ☐ is artwork
  ☐ is AI-generated
  ☐ exclude NSFW
```

### Nomenclator

The admin nomenclator page lists the new bool / vibe / mood fields alongside
existing controlled vocabularies, with counts per value so the admin can audit
distribution.

## Cost / runtime

- Per image: ~$0.0002–0.0005, ~2–3 s wall.
- Backfill 2,300 images: ~$0.50–1.00, ~1.5–2 hours.
- Same checkpoint resume as OCR so deploys / SSH drops don't lose progress.

## Rollout

Single PR, multiple commits in implementation order so individual steps can
be reverted cleanly:

1. Schema migration (ALTER TABLE entries in `main.py`).
2. Vocab constants (`server/ai_vocab.py`).
3. Generation function (`generate_ai_description` in `server/extraction.py`).
4. `manage.py test-ai-description <id>` for spot-checking.
5. Pipeline wiring into `_run_image_extraction` + `_retry_single_step`.
6. `manage.py backfill-ai-descriptions` with checkpoint.
7. Meilisearch sync of new fields.
8. Detail-page card.
9. Filter-sidebar sections.
10. Nomenclator updates.
11. Doc updates (`docs/api.md`, `docs/architecture.md`, `AGENTS.md` where relevant).

After deploy, run `test-ai-description` on a handful of diverse items to QA
the prompt, then kick off the full backfill (detached run).

## Privacy

Image bytes are sent to DeepSeek's API. The archive is admin-only; per the
project's stance, this is acceptable for AI enrichment. No personal identifiers
are sent — just the image and the OCR text.
