# Latent hero cards — visual identity for the Latents grid

**Branch:** `latent-hero-cards` (implementation in stacked follow-up branches)
**Status:** plan-only

## Goal

Make each Latent instantly distinguishable on `/admin/latents` — at a glance, without
reading — via a contributor-chosen background image on its card, with the existing
info text kept legible, and a per-latent accent color that follows the Latent into
its detail page.

## Decisions (from brainstorm, fixed)

1. **One image per Latent.** Storage is the existing, never-wired
   `projects.hero_media_item_id` column — PATCH already validates and stores it,
   `_project_summary` already returns it; nothing sets or renders it today.
2. The image is the **card background, cropped by the card border**. Legibility
   treatment defaults to a **scrim** and is per-latent togglable to two alternatives
   (below). Contributors (any admin — flat access, no new ACL) control image and style.
3. **Accent color auto-extracted from the image, manually overridable**, carried into
   the detail-page header.
4. **A Latent with no hero renders exactly today's plain card.** No generative
   fallback — undecorated stays plain.
5. Hero on other surfaces (Slack, OG, header backdrop) is surveyed here but deferred.

Scale check: 6 latents today, 12–40 expected within a year; 4 admins.

## Data model

Three nullable TEXT columns on `projects` (ALTER guards in `main.py`, matching the
existing pattern):

| column | meaning |
|---|---|
| `hero_style` | `scrim \| plate \| treat`; `NULL` reads as `scrim` |
| `hero_accent_auto` | server-extracted `#rrggbb`; recomputed whenever the hero changes |
| `hero_accent_override` | manual `#rrggbb`; wins over auto; survives hero changes |

Two accent columns (not one + flag) so "reset to auto" is instant and re-extraction
never clobbers a manual choice.

## API surface (`server/latents_api.py`)

- `UpdateProjectBody` gains `hero_style` and `hero_accent_override`.
  - `hero_style`: 400 unless in `{"scrim","plate","treat"}`.
  - `hero_accent_override`: `""` clears (reset to auto); otherwise strict
    `^#[0-9a-fA-F]{6}$`, normalized lowercase, else 400. **This regex is the entire
    style-injection defense** — the client drops the value into a
    `style="--latent-accent:…"` attribute, so nothing outside that grammar may be stored.
- Existing `hero_media_item_id` block tightens to **image-only**
  (`media_type == "image"`; today it accepts any media item, which would render
  audio heroes as placeholder-SVG backgrounds). On set-with-change, recompute
  `hero_accent_auto` best-effort (never blocks the PATCH); on clear, null the auto,
  leave the override.
- `_project_summary` adds `hero_style` (defaulted), effective `hero_accent`
  (`override or auto`), and both raw accent fields (detail UI shows auto/manual
  state without extra requests; the index card reads only `hero_accent`).
- No Meilisearch changes — display-only fields, list endpoint reads SQLite directly.

## Accent auto-extraction

Server-side at hero-set time (client canvas rejected: racing second PATCH, skips
non-browser writers, duplicates logic the server owns).

New pure `pick_accent_color(hex_colors) -> str | None` in `server/extraction.py`,
fed by the **already-existing** dominant-color machinery
(`extract_dominant_colors()` → `MediaImageMeta.dominant_colors`, populated by the
extraction worker for every indexed image):

1. Score candidates in HLS: `saturation × (1 − 0.12·rank)` — mild dominance
   weighting so a vivid minority color beats a dominant near-gray background.
2. Monochrome guard: best saturation < 0.12 → keep neutral, don't invent a hue.
3. Legibility clamp: lightness into `[0.35, 0.62]`; saturation raised to ≥ 0.30
   when colored. Stdlib `colorsys`, no new deps.

Source order: stored `dominant_colors` if present, else live
`extract_dominant_colors()` on the sm-thumbnail/original (downsamples internally;
milliseconds; result not written back to `image_meta` — that stays the worker's
job). Any failure → `None`; card renders without accent tint.

## Card rendering (`src/pages/admin/latents/index.astro`)

Invariant: **the hero-less branch emits byte-identical markup to today** — all new
styling lives under `.card--hero` classes.

Hero branch: `.card--hero .card--<style>` + inline `--latent-accent` var,
`<img class="card__bg">` from `GET /api/media/{id}/thumbnail?size=md`
(srcset `sm` 128w / `md` 400w, lazy, `onerror` removes the img), a `.card__veil`
treatment layer, and the existing title/sub/meta wrapped in `.card__content`.
Both interpolations are injection-safe (style whitelisted client-side, accent
server-validated hex); text stays `escapeHtml`'d.

Treatments (all design tokens — passes design lint):

- **scrim** (default): bottom-up gradient veil `--color-overlay → transparent`;
  text `--color-on-overlay` (overlay tokens stay dark in both themes by design).
- **plate**: image fills the card; title/sub/meta sit on a solid `--color-bg` strip
  pinned to the bottom with a `--color-border` top rule — theme-native text,
  legibility guaranteed regardless of image.
- **treat**: image gets `grayscale(1) brightness(0.45)`; veil is the accent with
  `mix-blend-mode: color` at ~0.55 opacity — a per-latent duotone, like a sheet of
  darkroom test prints. Text on-overlay (brightness clamp guarantees a dark field).

Accent placement: hero-card border via `color-mix(accent 55%, --color-border)`;
hover keeps today's translate + hard shadow but resolves to the accent. The
**status pill keeps its semantic color** (forming/developing/fixing must stay
comparable across cards); inside scrim/treat it gains a `--color-overlay-soft`
backing so its 1px border reads over imagery. Hero cards get
`min-height: 148px` (plain cards keep their natural ~92px). Skeletons, empty/error
states, chip filters, and the `astro:page-load`/`after-swap` re-init are untouched.

## Detail-page controls

- New island **`LatentHero.svelte`** (~180 lines) mounted in the header region
  (the page already mounts six islands — established pattern). One compact row:
  64px hero thumb chip (or dashed "no card image") · Set/Replace · Remove ·
  style toggle as three mono chips · accent swatch (`<input type="color">`,
  debounced override PATCH) + `auto` reset chip shown only when an override exists.
  All writes are single-field PATCHes — safe alongside the header's debounced
  name/slug/description autosaves.
- **`PullFromIndex.svelte` gains a `selectMode` prop** (not a fork): image-type
  filter default, radio-style single select, footer primary "Use as card image"
  calling `onSelect(id)`. The three existing attach call sites are untouched.
- **Quick action in v1**: image tiles in `LatentLooseFiles.svelte` get a
  "Set as card image" action (~15 lines) — cover art naturally lands in loose
  files via the existing Uploader, so upload-then-click is the whole journey.
  The same action on slot tiles is deferred (pin-oriented, busier UI).

## Detail-page header carry (v1)

Set `--latent-accent` on the header when present: a 4px accent left-border spine +
accent focus rings on the name/description inputs. Identity learned on the grid
persists inside; zero interference with editability.

**Option for review (phase 3b):** a hero *backdrop* behind the header — absolutely
positioned `md` thumbnail at ~0.15 opacity under a `--color-overlay-soft` wash.
Legibility-safe (header inputs have opaque backgrounds) but visually loud next to
an editable form. Call it on this PR: in or out?

## Future surfaces (deferred, separate plan when picked up)

- **Slack**: `latent.created` / `latent.status_changed` can append an image block
  via the existing `_text_and_image_blocks` helper, exactly as release events do,
  using the unauthenticated `GET /api/media/{id}/og-thumb`. Privacy nod required:
  private pre-release art becomes URL-fetchable (unguessable UUID — the same
  exposure media-detail unfurls already accept).
- **OG unfurl** for `/admin/latents/<slug>` (og:image + og:title), modeled on
  `media_detail_with_og`.
- Punted: slot-tile quick action, bookmarks surfaces, batched `items_added`
  rollup images, `theme-color` meta.

## Phasing

1. **PR 0 (this)** — plan only.
2. **PR 1 — backend**: columns + ALTER guards, `pick_accent_color`, PATCH
   validation (style, hex, image-only hero), auto-extraction wiring, summary
   fields, and `tests/test_latents_api.py` (first test coverage for this router).
3. **PR 2 — index cards**: hero rendering + three treatments + accent border.
   Functional via API-set heroes before PR 3 exists.
4. **PR 3 — detail controls**: `LatentHero.svelte`, `selectMode`, loose-files
   quick action, header accent carry.
5. **PR 4 (future)**: Slack image blocks, OG unfurl, optional header backdrop.

## Testing

`tests/test_latents_api.py` (conftest already provides `client` / `auth_headers` /
`make_media_item`): image-only hero enforcement; style accept/reject; hex accept/
normalize/reject (including injection strings like `#abcdef; }`); override
precedence and `""` reset; re-extraction on hero change with override persistence;
hero clear nulls auto; non-admin 403 regression. Unit tests for
`pick_accent_color`: vivid-minority-beats-gray, monochrome stays neutral, clamp
bounds.

Manual browser checklist: three treatments × light/dark; hero-less card visually
unchanged; view-transition away/back re-init; picker single-select flow;
quick action; color input + reset-to-auto; deleting the hero media item in Stacks
(FK `SET NULL` self-heal → plain card); mobile single-column; `npm run format` +
design-lint pass.

## Risks / gotchas

- Hex regex is the sole injection guard for the accent — client must never write
  an unvalidated string into a `style` attribute.
- Treatment CSS must use overlay tokens / `color-mix` on tokens; hardcoded rgba
  fails the design-lint pytest.
- Image uploaded seconds before being set as hero may lack `dominant_colors` and
  thumbnails — live-compute fallback covers it; worst case accent is `None`.
- Plate strip uses negative margins to counteract card padding — verify at 260px
  and single-column mobile.
- Dark theme: scrim/treat text rides overlay tokens (theme-proof); plate text is
  theme-native — check both explicitly.

## Open questions for review

1. Header hero **backdrop** (phase 3b): in or out?
2. Slack image blocks: comfortable with hero art on the unauthenticated og-thumb
   URL, or hold until a signed-URL story exists?
3. Naming/feel of the third treatment (`treat`, duotone via `mix-blend-mode:
   color`) — happy with the darkroom framing, or prefer plain dim-only?
