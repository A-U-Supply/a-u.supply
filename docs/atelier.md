# The Atelier

A sidebar section under `/admin/atelier/*` that holds interactive, browser-side creative tools. Each tool is a single page; the Atelier is just the grouping.

The Atelier is **not** the same as the bots / App Runner ([`bots.md`](bots.md)):

|  | Atelier | App Runner (bots) |
|--|---------|-------------------|
| Where it runs | In the browser, on the user's machine | In a Docker container on the server |
| How it's added | New `.astro` page under `src/pages/admin/atelier/` | New TOML manifest in `apps/` pointing at an external Docker image |
| Latency | Live, interactive (canvas, WebGL, Web Audio) | Async job submitted to the queue |
| Cost shape | Heavy client load, no server CPU | Server CPU / GPU |
| Inputs | Files picked from the user's machine or the Media Library | Media items pulled from the search indices into a workspace |

Some Atelier tools share code/concepts with bots (e.g. several Punctum modes are TypeScript ports of bot algorithms so the user can iterate live before queuing a real job), but the runtime stories are separate.

## What's in it today

### Punctum — `/admin/atelier/punctum`

Multi-bot image atelier. One canvas, one "Bot" dropdown that switches between ~40 image effects (Bullet Hole, Collage Frankenstein, Pixelate, Dream Dissolve, Double Exposure, Spectral Merge, Two-Face, X-Ray Composite, Neural Chimera, Voronoi Chimera, VHS Halation, etc.). Each bot has its own input count (1–9), parameter set, and seeded render. Outputs can be saved into the Media Library tagged `atelier,punctum,<bot-name>` and carry a JSON `description` recording the bot, source IDs, seed, and params for reproducibility.

`atelier/bullethole.astro` is a 301 redirect into Punctum with `?bot=bullethole`. The legacy `/admin/atelier/bullethole` URL still works.

The Punctum source lives at `src/pages/admin/atelier/punctum.astro`. Each bot is defined in the `BOTS` registry near the bottom of the file (`Record<string, BotDef>`); per-bot render code is inlined above. No standalone design doc.

### Photism — `/admin/atelier/photism`

Spectral audio editor. Two entry points: drop an audio file (MP3 / WAV / OGG / FLAC / M4A) to analyse it as a spectrogram, or switch to the **▦ Image** tab to synthesise audio directly from an image (additive synthesis, the former Spectralize workflow). From either starting point the user can paint on the spectrogram, stamp images, apply spectral FX, and export as image, video, or resynthesised audio (ISTFT/OLA). Source: `src/pages/admin/atelier/photism.astro`. Design doc: [`/photism.md`](../photism.md).

### Litany — `/admin/atelier/litany`

Sample step sequencer. Voices are configured by search query (e.g. `kick`, `snare`, `vocal chop`) and pull random samples from the sounds-bored index, rotating through them on a configurable schedule (every hit, every bar, every 4 bars, or pinned to one sample). A 16-step grid (configurable per voice: 8 / 16 / 32) controls which beats each voice fires on — different step counts enable polyrhythm. Per-voice FX chain: delay → reverb → filter. Full app state (voices, patterns, FX, BPM) is gzip+base64 encoded in the URL hash for sharing. Source: `src/components/Litany.svelte` + `src/components/litany/` + `src/lib/litany/`. Design doc: [`plans/2026-05-29-sequencer.md`](plans/2026-05-29-sequencer.md).

### Spectralize — deprecated

Folded into Photism (2026-05-18). The page at `/admin/atelier/spectralize` now shows a redirect notice. Source retained as `src/pages/admin/atelier/spectralize.astro` for the redirect.

## Page-level theming

Each Atelier page paints the document body its own background colour via JS in `src/layouts/Admin.astro` (look for `applyPageBackground` near the bottom). Punctum uses deep amber `#221c00`; Photism uses `#1a1200`. Add a new page, add its background there.

## Conventions to keep

- **Library picker integration.** Every Atelier tool should let the user pick inputs from the Media Library (the picker is a shared admin component). Don't force file-system uploads only.
- **Save-back tags.** When saving generated outputs into the Library, include the tag `atelier` plus the tool name, e.g. `atelier,punctum,bullethole`. The Stacks search depends on these for filtering.
- **Reproducibility metadata.** Save a JSON `description` on outputs recording the bot/tool name, source IDs, seed, params. Punctum does this — match the shape.
- **Seed control.** Anything that can be randomly seeded should expose a seed input and a "shuffle seed" button so the user can reproduce or iterate.
- **Don't deepen this doc.** Per-tool internals belong in the tool's source file (or its own design plan under `docs/plans/` if it's getting complex). This page is an index, not a manual.

## Related

- [`bots.md`](bots.md) — server-side App Runner counterpart
- [`frontend.md`](frontend.md) — UI kit + Svelte components used in Atelier pages
- [`glossary.md`](glossary.md) — what "Punctum", "Photism", etc. translate to
