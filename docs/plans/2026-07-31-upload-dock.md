# Uploads that survive navigation — a persistent upload dock

## Context

David, in Slack (2026-07-29):

> not sure its possible but it would be nice if you start uploading something
> and while your waiting you can go to other things on the admin page while it
> uploads?

Tube: *"this would be sick, but i don't have any tech knowledge to get
started."* Brendan's shape: a small bar pinned to the bottom of the window like
the audio player, sitting **above** the player when both are up.

It is possible, and most of it already exists:

1. **The persistence mechanism is in production.** `Admin.astro:314` wraps the
   player in `<div class="player-slot" transition:persist="audio-player">`
   inside `<ViewTransitions />`. That is exactly "an island that survives a page
   swap" — it's why music keeps playing as you click around.
2. **The server already treats an upload as fire-and-forget.**
   `POST /api/media/upload` (`server/search_api.py:1525`) takes `project_id` /
   `slot_id` as form fields, creates the `ProjectItem` attachment itself
   (`:1701`), commits, calls `meili_sync`, then hands off to
   `run_extraction_async`. Thumbnails, durations, tags and indexing all land
   with no browser involved.
3. **So the only casualty of navigating away is the XHR.** `Uploader.svelte`
   is mounted *inside pages*; ViewTransitions swaps the body, the island dies,
   its in-flight `XMLHttpRequest`s abort mid-transfer — **silently**, since
   there is no `beforeunload` guard anywhere today.

Outcome: uploads keep running while you work elsewhere in the admin, with a
visible bar and an honest failure story.

## Decisions taken (do not re-litigate)

| Decision | Choice |
|---|---|
| Scope | **Every upload in the admin** — Tribute, latent slots, loose files, pull-from-index picker, session bundles included |
| On clean finish | **Stay until dismissed.** Nothing disappears on its own |
| On failure | Bar **stays red with Retry**, *and* posts to Slack |
| Reload / tab close | **`beforeunload` warning** while transfers are live |
| Public site | Admin only. `Base.astro` doesn't upload |

## Architecture

**New: `src/components/UploadDock.svelte`**, mounted in `layouts/Admin.astro`
beside the player with its own `transition:persist="upload-dock"` key. It owns
the queue, the transfers, and the bar UI.

**New: `src/lib/uploadQueue.ts`** — the transfer machinery lifted out of
`Uploader.svelte` wholesale: `uploadWithProgress` (`:270`), the bundle part
uploader (`:479`), `activeXhrs`, cancel/abort, speed smoothing, the
`Bundle`/`Item` types. The dock is a thin view over it. Moving rather than
rewriting keeps the session-bundle staging flow intact.

**Pages hand off via an event**, mirroring the documented `player:queue` bus
(`docs/player.md`, `docs/frontend.md#event-bus-pattern`):

```js
document.dispatchEvent(new CustomEvent('upload:start', { detail: {
  files, destination, projectId, slotId, tags, description, forceSession, tool,
}}));
```

The dock emits `upload:progress`, `upload:done`, `upload:failed` back out.
Live islands listen; departed ones simply miss them — which is the whole point.

**`Uploader.svelte` keeps** its dropzone, browse button, shared-fields box,
`detectSessionTool`, and validation. It **loses** the XHRs, the queue state and
the per-item progress rows, and instead dispatches `upload:start`.

Its four call sites become pick-and-hand-off:
`upload.astro` (currently a plain `fetch` loop at `:405` with no progress at
all — this is an upgrade, not a port), `LatentSlots:2194`,
`LatentLooseFiles:283`, `PullFromIndex:394`. `PullFromIndex`'s `onUploaded`
(`:296`, re-fetch + auto-select) becomes an `upload:done` listener; if you've
navigated away you lose the auto-selection, never the file.

**Positioning — already solved, don't invent anything.** `Player.svelte:327`
publishes the bar's measured height as `--player-h` on `<html>` and removes it
when the player goes (`:974`, `:991`). So:

```css
.upload-dock {
  position: fixed;
  bottom: var(--player-h, 0px);
  left: 0; right: 0;
  z-index: var(--z-player, 9999);
}
```

Above the player when there's music, on the floor when there isn't, with zero
coordination between the two. Copy the `.player__spacer` pattern (`:1970`) so
the dock doesn't cover the bottom of every page, and publish
`--upload-dock-h` the same way in case anything else needs to clear it.

**Failure → Slack.** No client-error endpoint exists, so add a small one:
`POST /api/media/upload/report-failure` → `notify_immediate("upload.failed", …)`
with `_format_upload_failed` registered in `_IMMEDIATE_FORMATTERS`
(`server/slack_notifier.py:642`, posts to `#supply-side`). Guards: report
**terminal** failures only, **once per queue** (batched, not per file),
**never** user cancels — otherwise a flaky connection floods the channel.

**`beforeunload`** registers only while transfers are live and **must be removed
when the queue goes idle**, or every later navigation prompts for nothing.

## Hazards

- **Reload cannot be survived.** An in-flight XHR dies with the document; that
  would need a resumable/chunked protocol. Out of scope — the warning is the
  answer.
- **Session bundles carry the most state** (staging `bundleId`, per-part
  retries, `activeXhrs`, cancel). Move them last, and keep the cancel path
  working — it deletes the server-side staging record (`:706`).
- **Two bars on a phone is a lot of screen.** The player is measured rather than
  fixed-height for exactly this reason; the dock should collapse to one line on
  narrow viewports.
- `scripts/lint-design.mjs` guards `position: fixed` and `z-index ≥ 100` — use
  the tokens, expect it to check.
- Neither upload path sends `source_type`/`output_index` today, so both default
  to `manual_upload`. **Keep it that way** — changing it silently reroutes files
  to a different Meilisearch index, which is invisible until someone can't find
  their upload.

## Tests

- **The load-bearing browser check** (`tests/browser/upload_dock.mjs` + a pytest
  wrapper, following `latent_delete.mjs`): start an upload, navigate to another
  admin page mid-transfer, assert the transfer **continues and completes** and
  the item exists server-side afterwards. Nothing else can prove the feature.
  Prove it can fail by mounting the dock without `transition:persist`.
- Dock sits **above** the player: compare bounding rects with the player active,
  and assert it drops to the floor when `--player-h` is absent.
- pytest: the report-failure endpoint (auth, payload, one Slack event) and
  `_format_upload_failed`, mirroring `TestShippedStatus`.
- Existing upload tests must stay green — the server contract doesn't change.

## Verification

1. `.venv/bin/python -m pytest` (`uv run pytest` is broken here; ~2.5 min).
2. `npm run format` · `node scripts/lint-design.mjs` · `npm run build`.
3. Drive it: start a large upload from Tribute, navigate to Latents, watch it
   finish; repeat into a latent slot and confirm the file is attached when you
   return; kill the API mid-transfer and confirm red + Retry + one Slack post;
   confirm the warning appears only while transfers are live.
4. Both themes, phone width, with the player running.
5. Per `AGENTS.md`: worktree off `origin/master`, plan doc committed to
   `docs/plans/2026-07-31-upload-dock.md` before implementing.

## Deliberately out of scope

- Resuming after a reload or tab close.
- Uploads on the public site.
- Any change to the upload endpoint's contract or index routing.
