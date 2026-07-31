# Arrange sections — visibility toggle + reordering

## Context

Brendan: *"the title 'section map' becomes a header… to the right we add a
button… a user can toggle the visibility of sections in a latent on and off…
so that if a latent is only utilizing a handful of sections a user can make it
so only those sections are visible… the content doesn't disappear from the
memory saved in the latent"* — plus, in the same popup, drag-to-reorder the
sections, with arrow buttons for mobile and a ⠿ grip to advertise the drag.
Slots stay corralled inside their own group.

**Answered:** state is **server-side and shared** (a property of the latent,
like `section_styles` — not a per-browser preference like `latentCollapse.ts`).
**Marginalia is in the list.**

## What already exists — do not rebuild

| Thing | Where |
|---|---|
| The page is a **flex column** | `detail.astro:120` `.islands { display:flex; flex-direction:column }` → order is `order:`, hiding is `hidden`. No DOM moves, no remounts, no refetch |
| **⠿ grip + ↑/↓ arrows** as a component | `RowMove.svelte` — arrows currently phone-only |
| **Modal dialog** pattern | `.delete-dialog` in `detail.astro` — native `<dialog>` + `showModal()`, top layer, Escape and focus-trap free |
| **"slots changed elsewhere, reload"** signal | `document` event `latent:slots-changed` → `LatentSlots.load()` (`LatentSlots:1473`, fired by `LatentRepoStrip`) |
| **Slot reorder endpoint** | `POST /api/projects/{id}/slots/reorder` `{order:[ids]}` |
| **Idempotent migrations** | `ALTER TABLE … ADD COLUMN` guarded by `_sa_inspect(engine).get_columns(...)` in `main.py` |

## Decisions taken (do not re-litigate)

| Decision | Choice |
|---|---|
| Where state lives | **New `projects.section_layout` column**, mirroring `section_styles`. Not `metadata_json` — that column is documented as arbitrary user metadata and is PATCHable wholesale; a reserved key inside it would be squatting |
| Stored shape | `{"order": [key…], "hidden": [key…]}` — both optional, absent = default |
| Marginalia | **In the layout list**, and it gains a **map chip** (borrowing the threads accent — it has no styleable colour of its own). A map listing 8 of 9 sections while the dialog lists 9 would be the odd thing |
| `SECTION_KEYS` | **Unchanged.** It means *styleable* sections and `applySectionStyles` must keep skipping marginalia. A new `LAYOUT_KEYS = [...SECTION_KEYS, 'marginalia']` is the arrange list |
| Hidden sections | **Still mount and fetch.** Toggling is instant and no section can be blank-because-it-never-loaded. Skipping the mount is a real phone win but a bigger change; revisit with a measurement, not a guess |
| Individual slots | **Reorder only, no hide.** A section is page furniture; a slot is content, and hiding one is how a take goes missing |
| Button label | **`Arrange`** — covers order *and* visibility, and it's a word, not an icon (explicit affordances over minimalism) |

## 1. Backend

**`server/models.py`** — `section_layout = Column(String, nullable=True)` on
`Project`, commented like its neighbour: JSON `{order, hidden}` for the
detail-page section arrangement.

**`main.py`** — one more guarded block: `ALTER TABLE projects ADD COLUMN
section_layout TEXT`. No backfill; NULL already means "default order, nothing
hidden", which is the right starting state for every existing latent.

**`server/latents_api.py`**

- `VALID_LAYOUT_KEYS = VALID_SECTION_KEYS | {"marginalia"}` — with a comment
  saying why the two sets differ (marginalia is arrangeable but not styleable).
- `_project_summary` gains `"section_layout": _parse_metadata(p.section_layout)`.
- `UpdateProjectBody.section_layout: dict | None` — **whole-object replace**,
  `{}` clears. Deliberately *not* the merge grammar `section_styles` uses:
  these are two short arrays the client always holds in full, and "what does a
  partial order mean?" is exactly the ambiguity that breeds bugs.
- `_validate_section_layout()`: `order` and `hidden` must be lists of unique
  strings from `VALID_LAYOUT_KEYS`; anything else 400s with the offending key
  named. `order` may be **partial** — that's what makes the format survive a
  future tenth section.

**The resolution rule** (documented once, in `latents_api.py` and mirrored in
the client helper): take the stored `order`, drop unknown keys, then append
every known key it didn't mention in default order. A key absent from `hidden`
is visible. So a layout saved today keeps working when section ten arrives —
it appears at the end, visible — and forgetting an entry always fails safe to
*shown*, the same stance `latentCollapse.ts` takes.

## 2. `src/lib/latentLayout.ts` (new)

`LAYOUT_KEYS`, `LAYOUT_LABELS` (`SECTION_LABELS` + `marginalia: 'Comments &
markers'`), `LAYOUT_EVENT = 'latent-layout-changed'`, and one
`resolveLayout(raw) → { order: LayoutKey[]; hidden: Set<LayoutKey> }` — the JS
mirror of the server rule. The map, the dialog and `detail.astro` all call it,
so the three cannot disagree about what a stored layout means.

## 3. `detail.astro`

`applySectionLayout(layout)`, sitting beside `applySectionStyles` and called
from the same two places (initial load with `p.section_layout`, and on the
`latent-layout-changed` event):

- `el.style.order = String(i)` per resolved position;
- `el.hidden = hidden.has(key)`, plus an explicit
  `.latent-section[hidden] { display: none; }` rule so a future `display` on
  `.latent-section` can't quietly beat the UA stylesheet.

## 4. `LatentSectionMap.svelte`

- The strip becomes a **head row + chip row**: `<h2 class="map__title">Section
  map</h2>`, the hidden count, and the `Arrange` button on the right.
- **Hiding is never silent** — the head reads `Section map · 3 hidden`. Once a
  chip is gone, that button is the only way back, so the page has to say so.
- Chips render from `resolveLayout()`, in resolved order, skipping hidden ones;
  slot chips still nest after the `slots` chip.
- A `marginalia` chip joins the row.
- Third window listener, `latent-layout-changed`, same shape as the two it has.

## 5. `LatentArrange.svelte` (new) — the dialog

Native `<dialog>` + `showModal()`. `#map-island` is a top-level sibling of
`#islands` so there's no `isolation: isolate` to escape, and the top layer means
**no z-index and no portal at all** — the trap behind #573/#574/#575/#581 simply
doesn't apply. Sortable here uses native HTML5 drag (no `forceFallback`), so
there's no mirror element to get stranded under the top layer either.

- One row per layout key: `<RowMove>` (grip + arrows), a visibility toggle, the
  section's swatch, the label.
- **Slot rows are a nested `<ul>` with its own Sortable and no shared group.** A
  separate list is what keeps a slot inside the group — structure, not a rule
  that has to be enforced on every drop. Slot rows render without a visibility
  toggle.
- `RowMove` gains an additive `alwaysArrows` prop: in a dialog that exists to
  reorder, clicking ↑ beats dragging even with a mouse. Default keeps today's
  phone-only behaviour for every existing caller.
- Writes: sections → `PATCH /api/projects/{id}` `{section_layout}` then dispatch
  `latent-layout-changed`; slots → `POST /slots/reorder` then
  `document.dispatchEvent('latent:slots-changed')` so the real cards repaint.
  Trailing 250ms debounce with a single in-flight guard (arrow clicks come in
  bursts); a failed write resyncs from the server and shows an error line.
- Changes apply **live behind the dialog** — you watch the page rearrange.
- `Reset` sends `{}`: default order, nothing hidden.
- Hidden rows dim but keep their grip, so you can position something that isn't
  currently shown.

## 6. Tests

**pytest — `tests/test_latents_api.py::TestSectionLayout`** (new):
round-trip of `order` + `hidden` through the summary; a **partial** order is
stored as given; `marginalia` accepted; an unknown key 400s; a duplicate key
400s; a non-list 400s; `{}` clears the column; and the auth cases per
`TestLatentsAuth`.

**Browser — `tests/browser/section_arrange.mjs`** + a pytest wrapper. The
guarantee Brendan asked for is the headline assertion:

1. Type a paragraph into Documents → hide Documents → the section leaves the
   page **and** its chip leaves the map → reload → still gone → un-hide →
   **the same paragraph is there, character for character**.
2. Drag a section to the top: page order changes, map chip order matches,
   survives a reload.
3. The arrows do the same thing at 390px, and every row clears 44px.
4. A slot row cannot leave the slots group (drag it out; assert nothing moved).
5. Reordering a slot in the dialog moves the real slot card on the page.
6. Marginalia is listed, and can be hidden.
7. `resolveLayout` unit checks evaluated in-page (there's no JS test runner in
   this repo): partial order backfills, unknown keys are dropped, missing
   `hidden` fails safe to visible.
8. No horizontal overflow at 390px; screenshots of the dialog in both themes.

**Prove it can fail:** run the probe against a build where
`applySectionLayout` is a no-op, and confirm 1, 2 and 5 go red.

## Verification

1. `.venv/bin/python -m pytest` (`uv run pytest` is broken here).
2. `npm run format` · `node scripts/lint-design.mjs` · `npm run build`.
3. Drive it by hand: hide something with content and reload; reorder on a
   phone with the arrows; drag on desktop; confirm a hidden section's Style
   button is simply unreachable (expected — the head is what carries it).
4. Both themes, 390px and desktop. Screenshot the dialog — assertions confirm
   geometry, not legibility.
5. Per `AGENTS.md`: worktree off `origin/master`, plan doc committed to
   `docs/plans/2026-07-31-latent-section-arrange.md` before implementing.

## Deliberately out of scope

- **Per-user overrides** — this is the latent's arrangement, shared.
- **Hiding individual slots.**
- **Skipping the mount** of hidden sections.
- **The mobile map's sticky bug** (`#map-island` is only as tall as the map, so
  `position: sticky` has never done anything). We'll be rebuilding this element,
  so it's the cheapest it will ever be to fix — but it changes phone navigation
  behaviour, so it stays out unless Brendan says otherwise.
