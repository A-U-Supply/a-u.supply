# Litany — Handoff: /api/serve production outage + Stop bug

**Date:** 2026-06-03
**Context:** Brendan doesn't have Dokku access, so the primary issue (a server-side
outage) needs Tube to pick up — it requires server logs to diagnose.

---

## PRIORITY 1 — `/api/serve` is failing in production (sample loading dead)

### Symptoms (confirmed 2026-06-03)
- **In-browser (logged in, Chrome):** every `GET /api/serve?output_index=samples-bored&query=…`
  shows as **`(cancelled)`** in the Network tab after exactly **15.00s**, **0.0 kB**
  transferred. The 15s is Litany's own client abort (`src/lib/litany/pool.ts:54-55`,
  `AbortController` + `setTimeout(..., 15000)`) giving up on a request the server
  never answers. The frontend is behaving correctly — the server isn't responding.
- **Direct probe (bearer token):** `GET /api/serve?...` returns **HTTP 500**
  ("Internal Server Error", plain-text body).
- **`GET /api/media/<bogus-uuid>/file`** returns **500** (should be 404).
  This path is a DB lookup + FileResponse — it does NOT use Meilisearch. So the
  failure is broader than search alone, or there are two problems.
- The Litany page route (`/admin/atelier/litany`) returns **200** — FastAPI is up
  and serving static Astro; only certain API routes are broken.

### What this means
Not a frontend bug. The serve/media API is erroring or hanging server-side. No
frontend change fixes this. Until `/api/serve` returns audio again, Litany is
silent for everyone.

### Diagnostic commands (you have Dokku access)
```bash
# 1. What URL is the app using for Meilisearch?
ssh dokku config:get au-supply MEILISEARCH_URL

# 2. Recent logs — should contain the 500 traceback (the big one)
ssh dokku logs au-supply -n 200

# 3. Is Meilisearch alive from inside the running web container?
ssh dokku enter au-supply web curl -sS -m 5 http://127.0.0.1:7700/health
# (use the URL from #1 if it differs from 127.0.0.1:7700)
```

### Leading hypotheses (ranked)
1. **Meilisearch down / unreachable** → `multi_search()` hangs (browser 15s
   cancel) or throws (bearer 500). Fix is usually a service restart. BUT the
   `/api/media/<bogus>` 500 doesn't touch Meili, so this can't be the *whole* story.
2. **Regression from last night's ~20 merged PRs (#458–#477).** Something in the
   serve/search/media path or a shared dependency (DB session, an import) may have
   broken. Check the traceback first; `git log origin/master` shows the deploy history.
3. **Connection-pool / worker exhaustion.** Litany fires 8 fetches per voice on
   load; with 3+ voices and re-rolls, dozens of requests stack up. If each hangs
   15s server-side, workers/DB connections can saturate and cascade every endpoint
   to 500/timeout. The logs will show this (pool timeouts, "QueuePool limit").
4. **`get_media_file` regression** — a bogus ID should 404, not 500. An unhandled
   exception there is a real clue regardless of Meilisearch.

### Code-path references (current master)
- `server/search_api.py` → `serve_media()` (the `/api/serve` handler) →
  `multi_search()` in `server/search_client.py`
- `server/search_client.py:26` → `MEILISEARCH_URL` (default `http://127.0.0.1:7700`)
- `server/search_api.py` → `get_media_file` route (the `/api/media/{id}/file` handler)
- `manage.py` → `reindex` subcommand exists; there is NO meili-health subcommand
  (consider adding one: `manage.py meili-health` that pings `/health` and prints status)

---

## PRIORITY 2 — "Instrument won't stop on Stop" (reported by Brendan; still unfixed)

### Symptom
Press Stop and one instrument (a long one-shot sample, or a reverb/delay tail)
keeps playing after the others stop.

### Root cause (confirmed by reading current master)
`stop()` in `src/components/Litany.svelte` (~line 213) only does `scheduler?.stop()`
(clears the lookahead interval) + resets flags. It never:
- stops active `AudioBufferSourceNode`s already scheduled with a future
  `source.start(when)` / `source.stop(when+dur)` — long one-shots play to completion
- silences reverb/delay tails (they decay independently through the convolver/delay)

`Scheduler.stop()` (`src/lib/litany/scheduler.ts:49`) also only clears the interval.
`AudioEngine` (`src/lib/litany/audio.ts`) has no `stopAll()` or master-silence method.

### Suggested fix
Add to `AudioEngine` (`audio.ts`):
```ts
silenceOutput(): void {
  this.masterGain.gain.cancelScheduledValues(this.ctx.currentTime);
  this.masterGain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.01); // ~30ms de-click fade
}
restoreOutput(volume: number): void {
  this.masterGain.gain.cancelScheduledValues(this.ctx.currentTime);
  this.masterGain.gain.setTargetAtTime(volume, this.ctx.currentTime, 0.01);
}
stopAllSources(): void {
  this.activeSources.forEach((a) => {
    try { a.source.stop(); } catch {}
    a.source.disconnect();
    a.envelopeGain.disconnect();
  });
  this.activeSources.clear();
}
```
Then in `Litany.svelte`:
- `stop()` → call `engine?.silenceOutput()` then `engine?.stopAllSources()`
- `startPlaying()` / `handlePlay()` → call `engine.restoreOutput(masterVolume)` before `scheduler.start()`

### Related latent bug (worth fixing same time)
In `audio.ts`, `activeSources` is a `Map<string, ActiveSource>` keyed one-per-voice
and overwritten on every `playVoice`. Two issues:
- Overlapping one-shots: only the most recent source per voice is tracked; earlier
  ones aren't stopped by `stopAllSources` / `stopVoice`.
- `source.onended → cleanupSource(id)` looks up `activeSources.get(id)`, which may
  now be a *newer* source — so an old source ending can disconnect the current one
  (cutting it off / orphaning). Consider keying active sources by a unique trigger
  id (e.g. `${voiceId}:${counter}`) or storing an array per voice.

---

## State of the work
- **No fix branch/PR exists yet** for either issue. Priority 1 needs server logs
  first (can't write a fix blind). Priority 2 is fully specced above and ready to
  implement.
- Repo conventions (AGENTS.md): worktree off `origin/master`, never commit to
  master, `npm run format` before commit, PR per change, merge auto-deploys.

## Still open (ask Brendan)
Brendan mentioned "bugs I noticed this morning" but we pivoted to the outage before
he listed them. Get that list — some may be downstream of the /api/serve outage
(they'll vanish once the server is fixed); others may be real frontend bugs.
