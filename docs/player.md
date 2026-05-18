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
        },
      ],
      startIndex: 0,
    },
  }),
);
```

The player appends the tracks to its queue and starts playback at `startIndex`.

## Notes

- The player owns its own state (current track, playback time, queue). Pages should *not* read or write player state directly — only dispatch events.
- URL-encode `release_code` anywhere it appears in a URL (see [`api.md`](api.md#special-characters-in-codes)).
- The same event works from Astro pages and from the Svelte component — both are just DOM.

## Related

- [`frontend.md`](frontend.md#event-bus-pattern) — the broader event-bus convention this fits into
- [`architecture.md`](architecture.md) — where the Player sits in the runtime
