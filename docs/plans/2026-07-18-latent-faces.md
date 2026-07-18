# Latent faces — full-strength card identity

**Branch:** `latent-faces` (single PR)
**Status:** implemented
**Revises:** [`2026-07-17-latent-section-styles.md`](2026-07-17-latent-section-styles.md) — several of that plan's decisions are deliberately reversed here after a day of real use.

## Goal

The shipped section-styles round was too quiet to matter: background images
rendered at 0.12 opacity under an 82% surface veil (invisible in practice),
the color surfaces (spine / head band / wash) were small and fragmented, and
two real bugs surfaced (title wrap on styled cards; style PATCHes zeroing
visible counts). This revision makes a card's visual identity **one thing**
— the *face* — rendered the way the latents index cards already render:
full-strength, edge to edge, treatments carrying legibility.

## Decisions (from the field-feedback brainstorm, fixed)

1. **The face is the whole card.** Accent + background + head band merge
   into a single identity zone spanning the entire slot card / section:
   image or solid color at full strength, cropped by the card border —
   the index-card recipe, not a wash. Solid color is first-class.
2. **Treatments = the hero vocabulary.** New `bg_style` style key
   (`scrim | plate | treat`, default scrim) for image faces. The scrim
   gradient anchors **top** on cards (heads sit at the top; index cards
   anchor bottom content) — deliberate deviation, not drift. Solid faces
   ignore treatment and use auto-contrast text computed for the active
   theme (recomputed live on theme toggle via a data-theme observer).
3. **Accent is derived**: manual override > solid face color > extracted
   `accent_auto`. Still drives the section-map chips, the Style-button
   swatch, and the spine. `head_tint` is retired — removed from the
   whitelist (PATCHes now 400) and scrubbed from stored styles on any
   write.
4. **Border means linework.** A border pick redefines `--color-border`
   across the card subtree, recoloring the box line, dashed dividers, file
   rows, the upload dropzone, inputs — everything already tokenized.
   Survivors by design: status pills (semantic `--c`), error reds, the
   Slack-branded link border. A section-level pick cascades into its slot
   cards until a card sets its own.
5. **Faces stay on mobile.** The <640px imagery-hide rules are gone; faces
   are how you recognize a card at any size.
6. **Two-row heads, both breakpoints.** Slot card: title row (drag · #pos ·
   label at full width) over a controls row (status pills · actions ·
   Style). Latent header: name input alone, then slug/kind/status/nav.
   This also fixes the wrap bug (the styled border used to squeeze the
   min-width:80px label into multiple lines).
7. **Counts are always real.** `_slot_count_maps()` feeds every slot
   summary a mutation endpoint returns, so client merges can't zero the
   visible file/thread counts.
8. Un-styled cards keep the byte-identical-plain contract; the only
   universal change is the two-row layout.

## Data model / API

No schema changes. `_STYLE_HEX_KEYS = {accent, bg_color, border, text}`;
`bg_style` validated against `VALID_HERO_STYLES` (legal in any mode so
single-key PATCHes stay order-independent). Effective accent computed in
`_slot_summary`; sections mirror it client-side via
`effectiveAccent()` in `src/lib/latentStyles.ts`. The hex regex + enum
membership remain the entire style-injection defense, server and client.

## Rendering

- **Slots** (`LatentSlots.svelte`): `slotFace()` resolves
  auto-starred-image / picked image / solid; face classes
  `slot--faced` + `--face-{scrim|plate|treat|solid}`; `.slot__bg` full
  opacity; treatment CSS mirrors `index.astro`'s `.card--hero` recipes.
  Inner opaque boxes (file list, repo strip, textareas, runs) are the body
  legibility layer. Head-band tint survives only on face-less accented
  cards.
- **Sections** (`detail.astro` `applySectionStyles`): same face classes on
  the `.latent-section` wrappers; faced sections gain a full card frame
  (accent color-mix border + padding); solid text via
  `autoTextColor(bg_color, currentTheme())`, re-applied by `watchTheme`.
  `.latent-band` goes transparent under a face (global.css).
- **Panel** (`LatentStylePanel.svelte`): Face group (mode chips + treatment
  chips + solid color input) · Accent (derived, "from face color" note) ·
  Lines (recolors linework) · Text with grounds-aware contrast warnings
  (overlay field for scrim/treat, full-strength color for solid, per-theme
  surfaces for plate/none) · Reset all. Head-band row deleted.

## Testing

Server: `TestFaceStyle` (bg_style accept/reject/injection/delete/orderless,
head_tint 400 + scrub), `TestSolidFaceAccent` (precedence + reset
fallback), `TestSlotCountsInResponses` (label-PATCH, style-PATCH — the
exact clobber path — reorder, create pin). Manual: treatments × themes at
full strength on slots and sections; solid auto-contrast across a live
theme toggle; mobile faces; linework recolor scope; two-row heads; legacy
head_tint/wash data renders sane; map chips track solid accents.

## Known accepted risks

Bare muted text sitting directly on a busy scrim image can dip below AA in
light theme — treat and plate are the guaranteed-legible treatments; a
micro-veil behind `.slot__panel` is the escape hatch if real art proves
noisy. `size=md` thumbnails may render soft across wide sections; bump to
`lg` there if it shows.
