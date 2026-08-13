# Quodlibet — a YouTube tape mixer for the Atelier

**Branch:** `feature/quodlibet` → PR TBD
**Status:** plan-only

## Context

Tube, via Brendan: *"make this an app on our site (youtube mixer)"* — a TikTok showing
a page that loads four YouTube videos and plays them back simultaneously, each with an
independent player. Brendan: *"one thing I would really like for this app to have is a
slider that adjusts the playback speed of the video… secondly I want the primary focus
of this app to be the mixing of YouTube videos."* Then Tube again: *"can we use
teenage engineerings tape fx/manipulation tools as inspiration? can we run some kinda
buffer to rewind, pitch up/down, loop, etc?"*

The plain four-video grid is well-trodden — ViewGrid, several Chrome extensions, and
ViewSync (which already does per-video time offsets and shareable URLs) all exist. All
of them are built for **watching**: sports feeds, multi-angle concert footage, speedrun
races. None are built for **mixing**. That gap is the whole justification for building
rather than linking, so this is the instrument: a four-channel mixer feeding a tape
machine, modelled on the OP-1.

**The architecture in one sentence: YouTube gives us four faders, and a tab-audio
capture gives us one tape.** Every scope question below resolves against that.

## The constraints that shape the design

Each of these was verified, not assumed. Three came from the API docs, four from a
spike against a real Chrome (`scratchpad/capture-spike{,2,3}.html`, 2026-08-13).

**1. `setPlaybackRate()` is discrete.** The IFrame API honours only the eight values
`getAvailablePlaybackRates()` returns (`0.25 … 2.0`) and **silently ignores** anything
else — no error, no `onPlaybackRateChange`. There is no continuous varispeed on a
YouTube embed and no cross-origin route to the underlying `<video>`. Brendan accepted
this: *"i guess its ok if the playback speed slider doesn't slide exactly."*

**2. No audio leaves a YouTube iframe.** Cross-origin, so no `AnalyserNode` on the
embed. Everything — BPM detection and every tape effect — runs off a
`getDisplayMedia({audio:true})` capture, which is the **summed mix, never per-slot**.

**3. Varispeed cannot be sustained on a live stream.** Slow it and the buffer grows
without bound; speed it and you starve. Continuous speed is a *spring-return gesture*,
not a setting. Physics, not an API limit.

**4. Self-capture feeds back.** Emitting 440 Hz to `ctx.destination` while capturing
the *current* tab returned it at −22 dB against a silent floor. `restrictOwnAudio:
true` is accepted by Chrome and reported back as applied, and changes nothing
(−25.7 dB). **`preferCurrentTab` is unusable.**

**5. A second browsing context works, and is the only thing that does.** Players in a
same-origin `window.open()` context, captured from the mixer tab: hears it at −64 dB
broadband against a −150 dB floor, and our own tone moves the reading by **−0.6 dB** —
nothing. Distinct `deviceId` surfaces (`web-contents-media-stream://7:8` vs `7:1`)
confirm separate capture targets.

Three single-window escapes were measured and **all three failed** — do not retry them:

| Route | Result |
|---|---|
| **Self-subtraction** (we authored the signal, so subtract it) | **−2.0 dB** cancellation, against the ≈−25 dB that would be useful. The loop delay drifts **1027 samples in 25 s** (≈0.09% clock skew) and there is an async sample-rate conversion in the path (48 kHz capture vs 44.1 kHz context), so it is not linear or time-invariant. Re-estimating from scratch still only reached −4.5 dB. A *stale* estimate scores **+1.4 dB** — subtracting with a slightly wrong delay adds energy. |
| **`setSinkId`** | No escape. With the sink set to `{type:'none'}` — play to no device at all — the tone read **−25.69 dB before and −25.69 dB after**. Tab capture taps the web-contents graph *upstream of the output device*, so no sink trick can work. |
| **Chrome's `echoCancellation`** | Applied and confirmed in settings; our tone still returned at −25.9 dB. Chrome does not treat a tab's own `AudioContext` output as an echo reference. Also pushes latency 2.9 ms → 10 ms. |

**6. Chrome's default capture is voice-tuned and unusable for music.** It hands you a
**mono** stream with `echoCancellation`, `noiseSuppression` and `autoGainControl` all
**on**. AGC alone would pump the master bus. All four override, and doing so also
drops capture latency from 10 ms to 2.9 ms.

**7. Chrome/Edge only.** Safari and Firefox support no audio in `getDisplayMedia`. See
"Degradation" below.

## Decisions taken (do not re-litigate)

| Decision | Choice |
|---|---|
| Server-side BPM (yt-dlp + offline beat tracking) | **No.** Brendan: *"way too heavy handed for such a thing."* `yt-dlp` already being a dep (`server/slack_scraper.py:259`) is not a reason to revisit |
| `preferCurrentTab` / single-window | **No.** Measured to feed back; `restrictOwnAudio`, self-subtraction, `setSinkId` and Chrome's EC all fail to save it |
| Where the players live | **A same-origin background tab** — `window.open(url, name)` with *no* feature string. Architecture, not a convenience. A tab rather than a floating window, per Tube |
| Video | **Audio only in v1.** Tube: *"I would personally keep it audio only, at least at first."* The captured video track is requested (the API demands one) and never rendered |
| Punch-in tape (record and output never overlap, to dodge the loop in one window) | **Rejected.** It would forbid continuous master-bus FX. Tube: *"continuous master-bus effects like looping, delays, etc, are essential to this IMO"* |
| Per-slot pan / EQ / sidechain ducking | **Impossible.** The capture is the sum. Per-channel is volume/mute/solo/speed/seek; everything else is master-bus. Do not propose these later as if cheap |
| Speed control | Eight fixed positions, always visible per slot, **labelled in BPM** rather than multipliers |
| BPM acquisition | **Live analysis of the capture**, armed per slot, auto-soloing. Tap survives only as the downbeat marker |
| Mode structure | **FREE ⇄ BEAT MATCH** (OP-1 §10.4/10.5). Supersedes an earlier MIX/PERFORM framing |
| Library media | Present because `docs/atelier.md` requires a Library picker, kept deliberately thin. Brendan: *"I don't want the playback of library media to take precedent"* |
| v1 persistence | **None** beyond the beat-grid cache. Share links and Album recording are v2 |

## Approach

### The signal chain

```
 background tab                        mixer tab
 ┌──────────────────┐
 │ YT1 ─[vol]─┐     │                 ┌─ analyser → BPM
 │ YT2 ─[vol]─┤     │   capture       │
 │ YT3 ─[vol]─┼─ tab audio ──────── TAPE ──── master FX ──▸ out
 │ YT4 ─[vol]─┘     │  (local playback  (60s buffer,
 └──────────────────┘   suppressed)     varispeed, loop)
   never looked at,
   never rendered
```

The deck tab is pure infrastructure: audio-only means nothing in it is ever displayed,
so it can sit backgrounded and occluded for the whole session. Chrome does not
throttle a tab that is playing audio, and suspended compositing costs us nothing when
we never render the picture. Faders act *before* the capture point, so riding a
channel and hitting a tape gesture compose correctly — channels feeding a tape
machine, which is the OP-1 layout.

### Free vs costly gestures

OP-1 tape trick 4, Break: *"Stops the tape. If a loop is active it will continue in
the background."* That is why the videos never chase the tape, and it splits the
effects cleanly:

- **Free — latching, zero drift:** loop in, loop out, loop toggle, break, reverse,
  M1, M2. Chop is free too but needs a grid (*"a tempo locked repeat type of effect"*).
- **Costly — spring-return:** scrub, and continuous varispeed. These spend buffer, and
  the standing latency is the rope.

### Turning constraint 1 into a feature

In FREE mode the selector shows plain multipliers. In BEAT MATCH, once a slot has a
grid, each of the eight positions shows the **resulting BPM** — a 136 BPM video reads
`0.75 → 102`, not `0.75×` — with the nearest-to-master position flagged and the
residual stated honestly (`+6 BPM · drifts a beat every ~11 bars`). Half and double
time are exact and marked as true locks. The limitation becomes the matching aid.

## Surface

```
src/pages/admin/atelier/quodlibet.astro        route (Admin layout + island)
src/pages/admin/atelier/quodlibet-deck.astro   the deck tab — bare, NO Admin layout,
                                               stable <title> (the capture picker
                                               and the test flag select by title)
src/components/Quodlibet.svelte                the island
src/components/quodlibet/                      SlotCard, TapeDeck, TrickKeys,
                                               ScrubWheel, SoundPath
src/lib/quodlibet/deckTab.ts                   deck tab lifecycle + player handles
src/lib/quodlibet/slots.ts                     SlotPlayer iface (YouTube | library)
src/lib/quodlibet/rates.ts                     8 steps, BPM labelling, drift maths
src/lib/quodlibet/capture.ts                   getDisplayMedia + constraint block
src/lib/quodlibet/tape.ts                      60s ring buffer (AudioWorklet)
src/lib/quodlibet/tricks.ts                    the eight keys
src/lib/quodlibet/grid.ts                      BeatGrid store + localStorage cache
src/styles/atelier/quodlibet.css               scoped theme
```

Plus registration: sidebar `<li>` and an `applyPageBackground()` branch in
`src/layouts/Admin.astro`, a row in `docs/glossary.md`, a section in `docs/atelier.md`.

### The capture constraint block — non-negotiable

```js
navigator.mediaDevices.getDisplayMedia({
  video: true,
  audio: {
    suppressLocalAudioPlayback: true,
    echoCancellation: false,   // all three OFF or the master bus is wrecked
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: 2,           // default is MONO
  },
  // never preferCurrentTab — it feeds back
});
```

The API demands a video track, so we request one and **never render it** — v1 is audio
only. Stopping the video track outright risks tearing down the capture, so keep it and
ignore it. (A `<video>` fed from `stream.getVideoTracks()[0]` is the obvious v2
preview, but it would also make the deck tab's rendering state start to matter, which
right now it does not.)

### Beat grids

`{ bpm, anchor, source: 'auto' | 'tap' }`, cached by YouTube video ID in
`localStorage` (the repo already uses it for small preferences —
`src/lib/view-mode.ts`, `src/lib/workspace.ts`). The shape leaves room for a
server-side filler later without a migration.

Detection: `realtime-bpm-analyzer` (npm), which refines a BPM estimate progressively
from a live `AudioNode`. The per-slot arm button **auto-solos** — mandatory, since the
capture is the sum and anything else audible poisons the read — and fills as
confidence builds rather than sitting blank. It yields tempo and beat phase but **not
the downbeat**, which is musical rather than signal-level; a "mark one" tap fixes bar
alignment.

Fallback if the library disappoints: `detectOnsets()` at `src/lib/ossuary/carve.ts:60`
is a working spectral-flux onset detector, and onsets + autocorrelation is the whole
algorithm.

## Reuse

- **Master FX** — `src/lib/litany/audio.ts` has the delay → reverb → filter chain and
  `buildIR()` (`:353`) for the convolution impulse. Note `buildIR` is **not exported**
  and the chain lives inside a class, so this is "lift the topology and export the
  helper", not a straight import.
- **Library picker** — mount `src/components/PullFromIndex.svelte`; don't rebuild it.
- **Thumbnails** — `thumbAttrs()` from `src/lib/mediaThumb.ts` (lint-enforced).
- **Any drag** — `createSortable()` from `src/lib/dragOptions.ts` only; raw
  `Sortable.create()` fails the lint and hangs the tab (#608).
- **Anything written to `<html>`/`<body>`** — `src/lib/documentState.ts`.

## Degradation

The page opens with four loadable slots, working faders, working speed and working
phase sync — complete and useful, no permission, any browser. The tape panel is
**visible but dark**, with the Sound Path diagram (OP-1 §9.2) showing which link is
broken and one labelled button to fix it. **Never prompt for capture on load.** Safari
and Firefox therefore degrade to a competent four-source mixer rather than a broken
page.

Sound Path must distinguish three failure states, because they have different fixes:
deck tab closed · tab audio not shared · wrong surface picked (no audio track).

The **help key** (OP-1 §11.1 — *"hold down the Help Key and pressing any key you get
the Key name and function of that specific key"*) covers the eight cryptic trick keys
without cluttering them.

## Verification

`tests/test_quodlibet_browser.py` + `tests/browser/quodlibet.mjs`, following the house
two-file CDP pattern (`slot_reorder_freeze` is the model). Chrome flags proven in the
spike: `--auto-select-tab-capture-source-by-title=<deck title>` for the deck tab, and
`--auto-accept-this-tab-capture` for the current-tab case.

Cover: the eight rate positions apply and report back; the BPM button solos and
restores the prior mute state; **break leaves playhead position untouched** (the whole
drift argument); loop in/out survive a rate change; M1/M2 blend interpolates; and the
deck tab keeps feeding the capture **while backgrounded and occluded**, which is the
one production condition the spikes have not yet reproduced.

## Open questions

1. **`AudioWorklet` under Vite.** Verified loading from a blob URL; the build path for
   a real worklet *file* (`?url` import vs a `public/` asset) is unproven, and this is
   the classic works-in-dev-breaks-in-build trap. There is **no existing worklet
   anywhere in `src/`** — this would be the first. Settle it before Step 5.
2. **Deck tab lifecycle.** Blocked by the popup blocker, or closed mid-session. Needs
   a real gesture to open and a visible recovery path; is silent auto-reopen right, or
   should it wait to be asked?
3. **Standing tape latency.** How far behind live should the tape sit? Too little and
   scrub has no rope; too much and it drags noticeably. Wants tuning by ear, not by
   argument.
4. **Audio-only is a narrowing of the original brief.** The source TikTok was four
   *videos* playing at once, and Brendan's framing was visual. Tube's *"at least at
   first"* defers the picture rather than dropping it, and v2 has a clear route back
   (render the captured video track). Worth Brendan confirming he is happy for v1 to
   be an instrument you listen to rather than watch.
