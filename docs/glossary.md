# Glossary

The admin UI uses a deliberate, ritualistic vocabulary. Most labels in the sidebar are not the obvious word for what they do. This page maps each name to its function so you can navigate the codebase without first decoding the theming.

The naming system blends ecclesiastical, scientific-warehouse, and photographic-process metaphors. It's load-bearing — don't rename things to be plainer.

## Sidebar map

| Section | Hint | Entry | Slug (`current=`) | What it actually is |
|---------|------|-------|-------------------|----------------------|
| **Overture** | dashboard + bookmarks | Auspices | `dashboard` | Admin dashboard (`/admin/dashboard`) — site-wide stats, action queue, activity feed, "Altar of the Day" |
|  |  | Marginalia | `bookmarks` | Per-user bookmarks across releases / media items (`/admin/bookmarks`) |
| **The Canon** | releases catalog | Releases | `catalog` | Release catalog list + edit (`/admin/catalog`) |
|  |  | New Release | `catalog-new` | Create-release form (`/admin/catalog/new`) |
| **The Larder** | media library | The Stacks | `search` | Main media search UI over Meilisearch indices `images` / `audio` / `video` / `emulsion` (`/admin/search`) |
|  |  | Tribute | `upload` | User upload form. Routes uploads into the `emulsion` index (`/admin/search/upload`) |
|  |  | Nomenclator | `nomenclator` | Tag administration: rename, merge, retire tags (`/admin/search/nomenclator`) |
| **The Winnowing** | rejected + flagged media | The Fallen | `failures` | Media items where metadata extraction failed; retry / resolve (`/admin/search/failures`) |
|  |  | Slop Bucket | `slop` | Auto-tagged low-quality / duplicate output media awaiting triage (`/admin/search/slop`) |
|  |  | The Midden | `midden` | Soft-deleted / discarded items; recoverable trash pile (`/admin/search/midden`) |
| **The Altar** | workspace, jobs, bulk ops | Workspace | `workspace` | Ephemeral bag of media items selected for a bot job (`/admin/search/workspace`) |
|  |  | Hecatomb | `hecatomb` | Bulk job-submit UI — runs an app against many random inputs at once (`/admin/hecatomb`). "Hecatomb" is the dispatch UI, not an app itself. |
|  |  | The Queue | `jobs` | Job-queue browser + detail (`/admin/jobs`) |
| **The Darkroom** | pre-release workspace | Latents | `latents` | Private admin-only workspace for assembling pre-release works of any media type. See [`plans/2026-05-15-latents.md`](plans/2026-05-15-latents.md). |
| **The Atelier** | browser-side creative tools | Punctum | `punctum` | Multi-bot image atelier (`/admin/atelier/punctum`). See [`atelier.md`](atelier.md). |
|  |  | Photism | `photism` | Audio → image spectral editor (`/admin/atelier/photism`) |
|  |  | Litany | `litany` | Sample sequencer — voices fired from the sounds-bored index in step-pattern loops (`/admin/atelier/litany`). See [`plans/2026-05-29-sequencer.md`](plans/2026-05-29-sequencer.md). |
|  |  | Ossuary | `ossuary` | Sound-design workstation — feed a clip to a RAVE brain, carve the result into one-shots, shape them, index into samples-bored (`/admin/atelier/ossuary`). See [`plans/2026-06-06-ossuary.md`](plans/2026-06-06-ossuary.md). |
| **The Sacristy** | keys, settings | Keys | `api-keys` | API key generation + revocation (`/admin/api-keys`) |
|  |  | Settings | `settings` | User management, integrations, site settings (`/admin/settings`) |

The `current=` slug is what `<Admin current="…">` expects in each page's frontmatter — it controls which sidebar entry shows `aria-current="page"`.

## Other named concepts

| Name | What it is |
|------|------------|
| **Emulsion** | Fourth Meilisearch index, parallel to `images` / `audio` / `video`. Holds admin-uploaded media that isn't tied to a release — the pre-release WIP pool. Source: [`plans/2026-05-15-latents.md`](plans/2026-05-15-latents.md). |
| **Stacks** | (a) The sidebar entry that opens the search UI. (b) The global private Lemmy community that hosts media-item-anchored discussion threads across all search indices. |
| **Latent** | A single in-progress work inside the Latents section — album, video, zine, session, anything. Subdivided into ordered **slots** (Track N / Scene N / Spread N / Cut N / Part N) plus loose files, named documents, and threaded discussion. |
| **fold** | The private Lemmy instance at `fold.a-u.supply` that backs Latents discussion and the `stacks` community. Federation is disabled; communities are local-only. |
| **Altar of the Day** | A daily-rotating featured media item shown on the Auspices dashboard. Backed by `GET /api/admin/altar`. |
| **Supply-side** | The Slack channel where deploy and activity notifications go (`#supply-side`, ID `C0AUNJ6BMJT`). See [`plans/2026-04-24-slack-activity-log.md`](plans/2026-04-24-slack-activity-log.md). |
| **Tribute** | The act of uploading. The Tribute page is where user uploads enter Emulsion. |
| **Acclaim / Disavow** | Per-user up/down votes on Stacks items. Internal identifiers stay `vote`, `vote_score`, `up_count`, `down_count`. Sortable, filterable, and surfaced as `▲N▼M` chips with hover voter lists. Independent signal from passive Slack reactions. See [`plans/2026-05-20-search-votes.md`](plans/2026-05-20-search-votes.md). |
| **Punctum / Photism / Bullet Hole** | Atelier tools. See [`atelier.md`](atelier.md). Spectralize was folded into Photism (2026-05-18). |

## Project codes

Release product codes (`AU-001`, `BDO-#03`, etc.) can contain `#`, dots, spaces, and other URL-unsafe characters. Always URL-encode them in paths — `encodeURIComponent()` in JS, `quote(code, safe='')` in Python.

## Naming conventions for new pages

If you're adding a new admin page, give it a thematic name consistent with the surrounding section. Don't invent a new metaphor system — pick from the existing palette (ecclesiastical, photographic-process, scientific-warehouse). When in doubt, ask in the PR; reviewers care about the cohesion.

The one place to **not** use thematic naming is internal identifiers: `current=` slugs, route segments, database tables, API paths. Those stay descriptive (`dashboard`, `search`, `jobs`, `projects`).
