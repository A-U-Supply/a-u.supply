# Persistent Audio Player

The audio player is a Svelte 5 island at `src/components/Player.svelte`. It's mounted in both `layouts/Base.astro` (public) and `layouts/Admin.astro` (admin), so it persists across navigation inside ViewTransitions.

## Queuing tracks

Dispatch a `player:queue` custom event from any page or script:

```js
document.dispatchEvent(
  new CustomEvent('player:queue', {
    detail: {
      tracks: [
        {
          track_id,         // string or number — unique id
          title,            // track title
          release_title,    // parent release title
          release_code,     // URL-safe product code
          stream_url,       // audio stream URL (usually /api/releases/{code}/tracks/{id}/stream)
          cover_url,        // cover art URL (small/thumbnail variant)
          duration,         // seconds
          entity_name,      // artist / manufacturer
          media_type,       // optional; 'video' → PiP video mode; presence → Marginalia enabled
        },
      ],
      startIndex: 0,
      start_time: 0,        // optional — seconds; seek here once metadata loads (one-shot, not on replay)
    },
  }),
);
```

The player appends the tracks to its queue and starts playback at `startIndex` (at `start_time`, when given).

`player:add` appends without replacing the queue (`detail: { tracks }`, no `startIndex`).

## Marginalia (timestamped comments + markers)

When the current track is a **media item** (`media_type` set — catalog release tracks skip this), the player enables the Marginalia UI:

- **Chevron toggle** in the bar opens the now-playing panel: a waveform canvas (peaks from `GET /api/media/{id}/peaks`, with a plain progress strip when peaks 404) with comment avatars and cue ticks at their timeline positions.
- **Click** the waveform to seek; **click a marker** to seek + open its card (reply, resolve/unresolve, edit, delete).
- **Composer** posts a comment (or text-less marker) at the current time via `POST /api/media/{id}/annotations`.
- **Session cues** (inherited from a parent session bundle) render outlined and can be toggled off.
- Collapsed, the seek bar shows tick dots at annotation positions.

Annotation reads/writes go through `GET|POST /api/media/{id}/annotations`, `PATCH /api/annotations/{id}`, `POST /api/annotations/{id}/resolve`, `DELETE /api/annotations/{id}` — see [`api.md`](api.md) and the [plan](plans/2026-07-22-latents-sessions-marginalia.md).

## Additional events

| Event | Detail | Behavior |
|---|---|---|
| `player:seek` | `{ seconds }` | Seeks the current track without reloading (used by Marginalia seek links when the item is already playing). |
| `player:time-request` | — | Player synchronously re-dispatches `player:time` with `{ currentTime, duration, track_id }` so other islands can read position without owning state. |

## Keyboard

`Space` play/pause · `←`/`→` prev/next track · `M` mute · `[` / `]` jump to previous/next marker · `c` comment at current time · `Esc` close card/composer/panel. All ignored while typing in inputs. Track changes and comment actions are announced via an `aria-live` polite region; transport and sliders carry `aria-label`s/`aria-valuetext`.

## Notes

- The player owns its own state (current track, playback time, queue). Pages should *not* read or write player state directly — only dispatch events.
- **Don't put `src=` on the `<audio>`/`<video>` element, and don't `bind:paused`.** `loadTrack()` sets `src` imperatively (see `applySrc`) and `paused` is one-way, updated from the element's `play`/`pause` events. Both rules exist because Svelte's write-backs land a microtask *after* `loadTrack()` has already set the source and called `play()`: re-setting the `src` attribute — even to the same URL — re-runs the media load algorithm and aborts that `play()`, and `bind:paused` sees its stale `true` (from the `pause` at end of track) and pauses the track that just started. Either one alone leaves the next track loaded but silent — i.e. autoplay looks like it needs a manual Play.
- URL-encode `release_code` anywhere it appears in a URL (see [`api.md`](api.md#special-characters-in-codes)).
- The same event works from Astro pages and from the Svelte component — both are just DOM.

## Related

- [`frontend.md`](frontend.md#event-bus-pattern) — the broader event-bus convention this fits into
- [`architecture.md`](architecture.md) — where the Player sits in the runtime
