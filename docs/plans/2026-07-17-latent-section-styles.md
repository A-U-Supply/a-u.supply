# Latent section styles — backgrounds + color coding for the detail page

**Branch:** `latent-section-styles` (implementation in stacked follow-up branches)
**Status:** plan-only

## Goal

Make the Latent detail page (`/admin/latents/<slug>`) parseable at a glance. The
page is info-dense — header, repo strip, links, documents, slots, loose files,
threads stacked in one long column — and finding the section you're looking for
means reading headings. This plan gives every area a customizable visual identity
(accent color, optional background image or solid color) under one shared visual
grammar, plus a click-to-scroll section map, so sections are recognizable by color
and reachable in one click. Mobile is a first-class requirement. Follow-up to the
shipped hero cards (`2026-07-11-latent-hero-cards.md`); reuses its conventions and
machinery throughout.

## Decisions (from brainstorm, fixed)

1. **One visual grammar everywhere.** Six fixed sections (repo, links, docs,
   slots, loose, threads) and every individual slot card share the same three
   elements: a 4px accent left-border spine (the existing header-spine pattern),
   a tinted head band (low-strength `color-mix` of the accent into
   `--color-surface`), and an optional quiet background.
2. **Fixed sections: global default palette + per-latent overrides.** The same
   six theme-tuned hues on every latent by default (cross-latent muscle memory —
   docs is always docs-colored), each overridable per latent. Fixed sections get
   the **full control set** — accent, background (`image | solid | none`; no
   `auto`, sections have no starred image), border, text, head-band tint.
3. **Slot accent: auto from the ★ starred primary image, manually overridable.**
   Server-extracted via the existing `pick_accent_color()`; free-picker override;
   `""` resets to auto — the exact hero-accent contract. Slots with no image are
   still colorable.
4. **Slot backgrounds: four modes.** `auto` (inherit the ★ starred file's image —
   the zero-effort default) / `image` (explicit pick via the existing
   `PullFromIndex` select flow, incl. upload-from-computer) / `solid` (color
   picker) / `none`. Images and solids always render as low-strength washes
   (~12–15% via `color-mix` / low opacity), never full-strength behind text.
5. **Extended controls, additive storage.** Border color (defaults to accent),
   text color (defaults to auto contrast-computed; free override with a live
   both-themes contrast warning — warns, never blocks), head-band tint. All
   user-authored style lives in one whitelisted JSON object per slot / per
   section, so future per-zone keys are schema-free additions.
6. **The style affordance is a labeled button**: the word **Style** plus a small
   swatch square showing the current accent (`[▪ Style]`), accent-tinted border,
   at the right end of every section head band and slot head. Explicit labels —
   the UI must accommodate someone who doesn't already know the feature. No
   icon-only or dot-only affordances.
7. **One shared Style panel.** Anchored popover on desktop, bottom sheet <640px.
   Identical control stack for sections and slots; the only difference is the
   bg-mode row (slots add `auto`). Both scopes get a "Reset all".
8. **Section map.** A strip under the header labeled "Section map": one labeled
   chip (colored swatch + short name) per fixed section and per slot, in page
   order, click-to-scroll. Chip names come from section titles / slot labels
   (truncated; full name as `title` tooltip + `aria-label`, live-updating on
   rename). Sticky on mobile only. Live-updates on every color change and on
   slot add/remove/reorder.
9. **Header backdrop (phase 3b from the hero plan): in.** The latent's hero as
   an absolutely positioned `md` thumbnail at ~0.15 opacity under a
   `--color-overlay-soft` wash, confined to the header block. Kept on mobile.
10. **Mobile <640px**: slot and section background imagery drops; header
    backdrop, spines, tints, and the sticky map remain.
11. **Live sync everywhere.** Everything wearing a color (spine, band, Style
    swatch, map chip) reads shared CSS custom properties; cross-island sync via
    a `latent-style-changed` window CustomEvent (the `latent-hero-changed`
    pattern).
12. **Un-styled stays plain.** A slot with no starred image and no manual style
    renders byte-identical to today — same invariant as hero-less cards.

Scale check: 6 latents, ~5–15 slots each, 4 admins; per-request bulk queries are
fine, no caching needed.

## Data model

Two nullable TEXT columns on `project_slots`, one on `projects` (ALTER guards in
`main.py`, matching the existing `_project_cols` / `_slot_cols_v2` loops):

| column | table | meaning |
|---|---|---|
| `accent_auto` | `project_slots` | server-extracted `#rrggbb` from the slot's ★ starred primary image; recomputed on star changes; NULL when none |
| `style_json` | `project_slots` | JSON dict of user style overrides (whitelist below); NULL when empty |
| `section_styles` | `projects` | JSON dict `{section_key: <style object>}`, keys in `{repo, links, docs, slots, loose, threads}` |

`accent_auto` is a dedicated column (not a JSON key) so server recomputes on star
toggles can never clobber-race a client JSON merge — same rationale as the hero's
auto/override split.

Style-object whitelist (shared by slots and sections):

| key | validation | meaning |
|---|---|---|
| `accent` | `^#[0-9a-fA-F]{6}$`, lowercased | manual accent; wins over `accent_auto` (slots) / palette default (sections) |
| `bg_mode` | slots `{auto,image,solid,none}`; sections `{image,solid,none}` | absent = `auto` (slots) / `none` (sections) |
| `bg_media_item_id` | must exist + `media_type == "image"` (404/400) | rendered only when `bg_mode == "image"` |
| `bg_color` | hex, lowercased | rendered only when `bg_mode == "solid"` |
| `border` | hex, lowercased | spine/border; defaults to effective accent |
| `text` | hex, lowercased | defaults to auto contrast |
| `head_tint` | hex, lowercased | head-band tint; defaults to effective accent |

**Merge semantics** (both columns): PATCH sends a partial dict; unknown key → 400;
`""` deletes the key (reset to auto/default); valid value replaces; empty result
stores NULL. For `section_styles`, unknown section key → 400 and an empty section
object deletes that section's entry. `bg_mode: "image"` without a stored id is
legal (renders as none until an image is picked) — keeps single-key PATCHes
order-independent. One shared validator, parameterized by allowed bg modes.

The hex regexes are the **entire style-injection defense** (hero precedent) —
every stored value lands in a client `style` attribute, so nothing outside that
grammar may be stored, and the client re-validates before writing vars.

`bg_media_item_id` lives in JSON so it has no FK `SET NULL` self-heal (unlike
`hero_media_item_id`); a deleted image is covered by the client `onerror` removal,
matching this repo's ALTER-added-columns-have-no-FKs reality.

## API surface (`server/latents_api.py`)

- `UpdateProjectBody` gains `section_styles: dict | None`; `update_project`
  merge-validates per above.
- `UpdateSlotBody` gains `style: dict | None`; `update_slot` merge-validates.
- `_project_summary` adds `section_styles` (parsed, `{}` default).
- `_slot_summary` adds `accent_auto`, `style` (parsed), effective
  `accent` (`style.accent or accent_auto` — mirrors `hero_accent`), and
  **`primary_image_media_id`** — the ★ starred image promoted into the summary so
  slot cards paint backgrounds at first load instead of waiting for the lazy
  per-slot items fetch. Bulk query (one join, grouped by slot, `added_at DESC`
  wins) in `get_project` / `list_slots`; small per-slot query elsewhere.
- `set_item_primary` response gains a `slot: _slot_summary(...)` key when the
  item has a slot (additive; existing client reads are unaffected) so a star
  toggle repaints the card's auto accent/background immediately.
- New constants next to `_HERO_ACCENT_RE`: `_STYLE_HEX_KEYS`,
  `VALID_SLOT_BG_MODES`, `VALID_SECTION_BG_MODES`, `VALID_SECTION_KEYS`.
- No Meilisearch changes — display-only fields.

## Slot accent auto-extraction

New `_recompute_slot_accent(db, slot)`: find the slot's starred primary image —
first `ProjectItem` with `is_primary` and image media, `added_at DESC` (matches
the UI list order) — and set `accent_auto = _compute_hero_accent(mi) or None`.
Reuses `_compute_hero_accent` verbatim (dominant-colors → live-extraction
fallback → `pick_accent_color`, best-effort, never raises, never blocks the
write). Hook sites:

- `set_item_primary` — after toggle, when the item is an image in a slot.
- `move_item` — when a starred image moves, recompute **both** old and new slot.
- `detach_item` — when the detached item was a starred image.
- `clear_slot_items` — `accent_auto = None` (nothing remains).

Out-of-band media deletion is deliberately not hooked: the cascade removes the
ProjectItem so `primary_image_media_id` self-heals at read time, and a stale
`accent_auto` is just a color.

## Rendering

### Global default palette (`src/styles/tailwind.css`)

Six theme-tuned hues in `@theme` + the dark block, deliberately avoiding the
brand ochre (`--color-accent` = interactive) and the status greens/reds:

| token | light | dark | |
|---|---|---|---|
| `--latent-sec-repo` | `#4a6fa5` | `#7a9cc6` | slate |
| `--latent-sec-links` | `#2f8f83` | `#4fb3a5` | teal |
| `--latent-sec-docs` | `#7d5ba6` | `#9b7fc0` | violet |
| `--latent-sec-slots` | `#b05c2f` | `#d07f4f` | rust |
| `--latent-sec-loose` | `#6b8e23` | `#8fb03e` | moss |
| `--latent-sec-threads` | `#8e4a6f` | `#b06a92` | plum |

Values are a proposal — eyeball and tune in PR 2 (open question 1).

### Fixed-section chrome (`detail.astro`)

The six island mounts gain `class="latent-section" data-section=<key>`. CSS:
spine `border-left: 4px solid var(--sec-border, var(--sec-accent))`, per-section
defaults `.latent-section[data-section='docs'] { --sec-accent: var(--latent-sec-docs); }`
etc. A new `applySectionStyles()` (beside `applyAccent`) writes overrides as
inline `setProperty` of the `--sec-*` var set after client hex re-validation, and
manages background layers per mode (image: absolute `md` thumb ~0.12 opacity +
veil inside the wrapper, `onerror` removes; solid: 12% `color-mix` wash). Re-runs
on `latent-style-changed`; all new listeners removed in the existing
`astro:before-swap` cleanup.

Head bands: a global `.latent-band` class (`src/styles/global.css` — island
styles are scoped, this must be shared):
`background: color-mix(in srgb, var(--sec-head, var(--sec-accent, transparent)) 12%, var(--color-surface))`.
Islands opt in via a new optional `styleKey` prop (LatentSlots / LatentDocuments /
LatentLinks / LatentLooseFiles / Threads header elements; LatentRepoStrip's root
row). Threads and LatentLinks are reused on other pages — no prop there, no
change.

### Slot cards (`LatentSlots.svelte`)

`Slot` type gains `accent_auto / accent / style / primary_image_media_id`. A
`slotVars(slot)` helper emits re-validated inline `--slot-*` custom properties
(only those present). Chrome, all under `.slot--styled` (plain slots byte-identical):

- Spine: `border-left: 4px solid var(--slot-border, var(--slot-accent))`.
- Head band: 12% mix of `var(--slot-head, var(--slot-accent))` into surface.
- Solid bg: 12% mix of `--slot-bg-color` into surface — never full-strength.
- Image bg (`auto` + starred image, or explicit pick): the index-card layering
  recipe — `position: relative; isolation: isolate`, `<img class="slot__bg">`
  (`md` thumb, `inset: 0`, `object-fit: cover`, ~0.12 opacity, lazy, `onerror`
  removes), `.slot__veil` of `color-mix(in srgb, var(--color-surface) 82%,
  transparent)`, content z-raised.
- Text: `color: var(--slot-text, inherit)` — the auto default is simply "no
  var" (washes are clamped low enough that theme text stays legible).
- Mobile: `@media (max-width: 640px)` hides `.slot__bg` / `.slot__veil`.

Live sync: listens for `latent-style-changed` (replace matching slot);
`togglePrimary` consumes the response's new `slot` key so star toggles repaint
instantly.

## Controls

- **`LatentStyleButton.svelte`** (~40 lines): `[▪ Style]` — swatch reads the
  ancestor's `--sec-accent`/`--slot-accent` (zero-JS live sync), border via the
  accent `color-mix` border recipe. Dispatches a `latent-style-open` document
  event `{scope, key, slotId?, current, anchorRect}`. Right end of every band
  and slot head.
- **`LatentStylePanel.svelte`** (~300 lines): mounted once by `detail.astro`;
  listens for `latent-style-open`, positions as popover (desktop) / fixed bottom
  sheet (<640px); Escape/outside-click closes. Control stack: accent row
  (debounced `<input type="color">` + reset chip — "auto" for slots, "default"
  for sections, shown only when an override exists), bg-mode mono chips,
  `image` → `PullFromIndex` `selectMode` flow (incl. upload), `solid` → color
  input, then border / text / head-tint rows, then **Reset all** (clears the
  whole style object). Contrast: current text vs the *blended* background
  computed in **both** themes; ratio < 4.5 in either shows a non-blocking badge
  ("▲ low contrast in dark theme"). Every successful PATCH dispatches
  `latent-style-changed` with the fresh summary.
- **`src/lib/latentStyles.ts`** (~90 lines): `HEX_RE`, `relLuminance`, `blend`
  (JS mirror of the CSS `color-mix` strength so warning math matches rendering),
  `autoTextColor`, `contrastRatio`, section key/token maps, and `THEME_SURFACES`
  constants (duplicates tailwind tokens — you can't `getComputedStyle` the other
  theme; keep-in-sync comments on both sides).

## Section map

**`LatentSectionMap.svelte`** (~150 lines), mounted between header and sections.
Leading label "Section map" (small muted mono), then labeled chips — colored
swatch + short name, slot chips use the slot label (CSS-truncated), `title` +
`aria-label` carry the full name and live-update on rename. Click →
`scrollIntoView({behavior: 'smooth'})` on the section wrapper /
`[data-slot-id]`. Sticky under 640px only (`position: sticky; top: 0;
background: var(--color-bg)`); kept outside any overflow-clipped ancestor (iOS).

Data: initial props from the `get_project` payload; updates from
`latent-style-changed` plus a new **`latent-slots-updated`** window event —
`LatentSlots` gains an `announceSlots()` (`{projectId, slots: [{id, label,
position, accent}]}`) fired after load / add / delete / reorder / label patch.
(Nothing existing covers this; `latent:slots-changed` flows the *other*
direction, RepoStrip → slots.)

## Header backdrop (phase 3b, now in)

In `detail.astro` `init()` when a hero exists: `#header` gets
`position: relative; overflow: hidden`, a prepended absolute `md`-thumb `<img>`
at ~0.15 opacity plus a `--color-overlay-soft` wash div, children z-raised.
Refreshes on `latent-hero-changed`; `onerror` removes. Kept on mobile.

## Phasing

1. **PR 0 (this)** — plan only.
2. **PR 1 — backend** (~250 + ~250 test lines): columns + ALTER guards, shared
   style validator + merge logic, `_recompute_slot_accent` + four hooks, summary
   additions, star-response `slot` key, tests.
3. **PR 2 — rendering** (~350): palette tokens, section wrappers + spines +
   `applySectionStyles` + section backgrounds, `.latent-band`, slot-card chrome
   (all four bg modes, mobile drop), `latentStyles.ts`. Fully functional via
   API-set styles before PR 3 exists.
4. **PR 3 — controls** (~470): StyleButton, StylePanel (+ contrast badge,
   PullFromIndex reuse), `styleKey` prop across the six islands, event wiring.
5. **PR 4 — map + backdrop** (~250): SectionMap, `latent-slots-updated`
   announcements, sticky mobile behavior, header backdrop.

## Testing

`tests/test_latents_api.py` (existing fixtures + `make_image_with_colors`,
parametrized accept/reject incl. injection strings like `#abcdef; }`):

- `section_styles`: per-key accept/normalize; unknown section 400; unknown
  subkey 400; bad hex 400; `bg_mode: auto` rejected for sections; section
  bg_media 404/400; `""` deletes subkey; merge preserves untouched sections;
  summary echo; member 403 regression.
- Slot `style`: each hex key accept/normalize/reject; bg_mode enum; bg_media
  404 / non-image 400; partial merge preserves other keys; style survives
  unrelated label/status PATCHes.
- `accent_auto` lifecycle: star image → computed (vivid-minority fixture);
  unstar → next starred or NULL; starring audio is a no-op; detach starred →
  recompute; move starred → both slots; `clear_slot_items` → NULL; effective
  `accent` precedence; deterministic pick with two starred images; extraction
  failure never blocks the toggle; `primary_image_media_id` present in
  `get_project` + `list_slots`.

Manual browser checklist: six spines/bands distinct × light/dark; section
override + reset + section image/solid washes legible; slot auto accent at first
paint (no lazy-fetch pop-in); star/unstar repaints live; four bg modes × themes;
text-override contrast badge fires in exactly the theme that's bad; live sync
across swatch/spine/band/chip while dragging a color input; panel as popover and
as bottom sheet; map label + labeled chips + truncation/tooltips; map updates on
slot add/delete/reorder/rename; sticky map on iOS Safari (device); header
backdrop legible over inputs, on/off with hero; <640px drops imagery, keeps
color; view-transition away/back re-init clean (no stacked listeners/panels);
`npm run format` + design-lint pass.

## Risks / gotchas

- Hex regex is the sole injection guard; the client must never write an
  unvalidated string into a `style` attribute (server + client validation both,
  every PR).
- Wash strengths are clamped constants (10–15%) so no user color can zero out
  contrast; the text warning informs but never blocks (admins may overrule).
- `THEME_SURFACES` in `latentStyles.ts` duplicates tailwind tokens — keep-in-sync
  comments on both sides.
- `astro:before-swap` must remove the new window/document listeners and panel
  mount or handlers stack across view transitions.
- `bg_media_item_id` has no FK self-heal — `onerror` removal mitigates; stale
  `accent_auto` after out-of-band media deletion is cosmetic only.
- Sticky positioning dies inside overflow-clipped ancestors on iOS Safari — the
  map must stay a sibling of (not inside) `#header`, which gains
  `overflow: hidden` for the backdrop. Test on device.
- Design lint: all new CSS rides tokens / `color-mix` on tokens; raw hexes
  appear only as server-validated inline custom-property values.
- Concurrent debounced single-key PATCHes are last-write-wins per key — the same
  exposure the header autosaves already accept.

## Open questions for review

1. Palette hue assignment — are slate/teal/violet/rust/moss/plum mapped to the
   right sections? Tune by eye in PR 2.
2. Map chip labels: short names are truncated by CSS — happy with truncation +
   tooltip, or should slot chips show only their position number when space is
   tight (mobile)?
