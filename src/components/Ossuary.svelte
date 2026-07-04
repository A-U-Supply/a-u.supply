<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    searchInputs,
    fetchClipById,
    fetchRandomClip,
    loadLocalFile,
    computePeaks,
    type SearchHit,
    type LoadedClip,
  } from '../lib/ossuary/source.ts';
  import {
    interpret,
    defaultParams,
    MODELS,
    MODEL_NOTES,
    type InterpretParams,
    type InterpretPhase,
  } from '../lib/ossuary/interpret.ts';
  import {
    carve,
    resizeKeep,
    DEFAULT_KEEP_LIMIT,
    SLOTS,
    SLOT_COLOR,
    type Hit,
    type Slot,
  } from '../lib/ossuary/carve.ts';
  import { renderHit } from '../lib/ossuary/render.ts';
  import { AuditionLoop } from '../lib/ossuary/loop.ts';
  import { encodeWav } from '../lib/ossuary/wav.ts';
  import {
    indexSample,
    zipSamples,
    sampleTags,
    slugify,
  } from '../lib/ossuary/export.ts';
  import HitEditor from './ossuary/HitEditor.svelte';

  // ── Source picker state ───────────────────────────────────────────────────
  let query = $state('');
  let results = $state<SearchHit[]>([]);
  let searching = $state(false);
  let loading = $state(false);
  let error = $state('');

  // ── Loaded source clip ──────────────────────────────────────────────────--
  let clip = $state<LoadedClip | null>(null);
  let sourceCanvas: HTMLCanvasElement | undefined;

  // ── Interpreter ───────────────────────────────────────────────────────────
  let params = $state<InterpretParams>(defaultParams());
  let interpreting = $state(false);
  let interpretPhase = $state<InterpretPhase | null>(null);
  let interpretError = $state('');
  let wetBuffer = $state<AudioBuffer | null>(null);
  let wetCanvas: HTMLCanvasElement | undefined;

  // ── Carve ─────────────────────────────────────────────────────────────────
  let sensitivity = $state(0.5);
  let zoom = $state(1);
  let keepLimit = $state(DEFAULT_KEEP_LIMIT);
  let hits = $state<Hit[]>([]);
  let selectedHitId = $state<string | null>(null);
  const selectedHit = $derived(
    hits.find((h) => h.id === selectedHitId) ?? null,
  );
  let auditioningHitId = $state<string | null>(null);
  let auditionError = $state('');

  // ── Audition loop (one slot, minimal — Litany owns real sequencing) ────────
  let loop: AuditionLoop | null = null;
  let loopSlot = $state<Slot | null>(null);
  let looping = $state(false);
  let loopBpm = $state(120);
  let loopRate = $state(8);
  let loopLength = $state(16);
  let loopProb = $state(100);
  let plrTouched = $state(false); // onset-seeded default until the user engages PLR
  let loopPattern = $state<boolean[]>([]);
  let loopStep = $state(-1);

  const stepDurSec = () => (60 / loopBpm) * (4 / loopRate);

  function rebuildPattern() {
    if (!loopSlot || !wetBuffer) {
      loopPattern = [];
    } else if (plrTouched) {
      loopPattern = Array.from(
        { length: loopLength },
        () => Math.random() * 100 < loopProb,
      );
    } else {
      // Invisible default: seed steps from this slot's onset timing. Diagnostic
      // — it shows the rhythm the brain actually found. No UI, no toggle.
      const sr = wetBuffer.sampleRate;
      const dur = stepDurSec();
      const pat = new Array(loopLength).fill(false);
      for (const h of keptInSlot(loopSlot)) {
        pat[Math.round(h.start / sr / dur) % loopLength] = true;
      }
      loopPattern = pat;
    }
    if (loop) loop.pattern = loopPattern;
  }

  async function renderSlotBuffers(slot: Slot): Promise<AudioBuffer[]> {
    if (!wetBuffer) return [];
    return Promise.all(
      keptInSlot(slot).map((h) =>
        renderHit(wetBuffer!, h.start, h.end, $state.snapshot(h.edit)),
      ),
    );
  }

  async function startLoop(slot: Slot) {
    if (!wetBuffer) return;
    const c = audioCtx();
    if (c.state === 'suspended') await c.resume();
    stopLoop();
    loopSlot = slot;
    plrTouched = false;
    if (!loop) {
      loop = new AuditionLoop(c);
      loop.onStep = (i) => (loopStep = i);
    }
    loop.buffers = await renderSlotBuffers(slot);
    if (destroyed) return;
    loop.bpm = loopBpm;
    loop.rate = loopRate;
    rebuildPattern();
    loop.start();
    looping = true;
  }

  function stopLoop() {
    loop?.stop();
    looping = false;
    loopStep = -1;
  }

  const toggleLoop = (slot: Slot) =>
    looping && loopSlot === slot ? stopLoop() : startLoop(slot);

  // Loop-control handlers. BPM/Rate/Length re-seed the onset default; only
  // Probability flips to generated PLR.
  function onLoopBpm() {
    if (loop) loop.bpm = loopBpm;
    if (!plrTouched) rebuildPattern();
  }
  function onLoopRate() {
    if (loop) loop.rate = loopRate;
    if (!plrTouched) rebuildPattern();
  }
  const onLoopLength = () => rebuildPattern();
  function onLoopProb() {
    plrTouched = true;
    rebuildPattern();
  }

  // ── Export: bake hits → index into samples-bored / download a ZIP ──────────
  let kitName = $state('');
  let exporting = $state(false);
  let exportMsg = $state('');

  const kitSlug = () =>
    slugify(kitName || `${clip?.name ?? 'ossuary'}-${params.model}`);

  /** Bake every kept hit to a named WAV; returns [filename, bytes] pairs. */
  async function bakeAll(slug: string): Promise<Array<[string, Uint8Array]>> {
    const counters: Record<string, number> = {};
    const out: Array<[string, Uint8Array]> = [];
    for (const h of hits.filter((h) => h.kept)) {
      const rendered = await renderHit(
        wetBuffer!,
        h.start,
        h.end,
        $state.snapshot(h.edit),
      );
      counters[h.slot] = (counters[h.slot] ?? 0) + 1;
      out.push([
        `${slug}_${h.slot}_${counters[h.slot]}.wav`,
        encodeWav(rendered),
      ]);
    }
    return out;
  }

  async function indexAll() {
    if (!wetBuffer || exporting || !keptCount) return;
    exporting = true;
    exportMsg = '';
    try {
      const slug = kitSlug();
      let n = 0;
      for (const h of hits.filter((h) => h.kept)) {
        const rendered = await renderHit(
          wetBuffer,
          h.start,
          h.end,
          $state.snapshot(h.edit),
        );
        const bytes = encodeWav(rendered);
        const filename = `${slug}_${h.slot}_${n + 1}.wav`;
        await indexSample(
          bytes,
          filename,
          sampleTags(h.slot, params.model, slug),
          `Carved ${h.slot} from "${clip?.name ?? 'clip'}" via RGZ-9 ${params.model} brain`,
        );
        n++;
        exportMsg = `indexing… ${n}/${keptCount}`;
      }
      exportMsg = `✓ indexed ${n} samples (kit:${slug})`;
    } catch (e) {
      exportMsg = (e as Error).message;
    } finally {
      exporting = false;
    }
  }

  async function downloadZip() {
    if (!wetBuffer || exporting || !keptCount) return;
    exporting = true;
    exportMsg = '';
    try {
      const slug = kitSlug();
      const baked = await bakeAll(slug);
      const files: Record<string, Uint8Array> = {};
      for (const [name, bytes] of baked) files[`${slug}/${name}`] = bytes;
      const blob = zipSamples(files);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${slug}.zip`;
      a.click();
      URL.revokeObjectURL(a.href);
      exportMsg = `✓ downloaded ${baked.length} samples`;
    } catch (e) {
      exportMsg = (e as Error).message;
    } finally {
      exporting = false;
    }
  }

  // ── Audition ────────────────────────────────────────────────────────────--
  let ctx: AudioContext | null = null;
  let preview: AudioBufferSourceNode | null = null;
  let sourceAudio: HTMLAudioElement | undefined;
  let playingKey = $state<string | null>(null);
  let destroyed = false;

  function audioCtx(): AudioContext {
    if (!ctx) ctx = new AudioContext();
    return ctx;
  }

  const duration = $derived(clip ? clip.buffer.duration : 0);
  const canInterpret = $derived(!!clip?.sourceId && !interpreting && !loading);

  const PHASE_LABEL: Record<InterpretPhase, string> = {
    submitting: 'summoning the brain…',
    queued: 'waiting in the queue…',
    running: 'interpreting…',
    fetching: 'retrieving the bones…',
  };

  // Glossary blurbs for the single-pass knobs (brains live in MODEL_NOTES).
  const KNOB_NOTES: Array<[string, string]> = [
    [
      'Temperature',
      'How far the latent drifts from a faithful re-hearing. ~0.3 is smoothed and softened, 1.0 an honest reconstruction through the brain’s lens; above 1.5 it hallucinates, 2.0+ brings heavy artifacts.',
    ],
    [
      'Noise',
      'Random energy injected into the latent before decoding. 0.1–0.3 adds grain and texture; higher progressively dissolves the source into the brain’s noise floor.',
    ],
    [
      'Wet/Dry',
      '0 is your original, 1 is brain-only. 0.3–0.5 keeps the source recognizable under the neural coloring.',
    ],
    [
      'Shuffle',
      'Chops the latent timeline into chunks and deals them out at random. The value is chunk size: 2–3 granular chaos, 4–8 mild glitch, higher means longer coherent segments in random order.',
    ],
    [
      'Quantize',
      'Bit-crush, but in latent space. 0.1 subtle stepping, 0.5 audibly crunchy, 1.0 collapses the latent to a handful of values.',
    ],
    [
      'Dims',
      'Which latent dimensions get touched (e.g. 0,2,6). Untouched dimensions keep the source’s values, so the brain bleeds through only on specific axes. Blank means all.',
    ],
    [
      'Reverse',
      'Flips the latent trajectory, not the waveform — the audio plays forward while the brain remembers backward. Uncanny.',
    ],
  ];

  const hitsInSlot = (slot: Slot) => hits.filter((h) => h.slot === slot);
  const keptInSlot = (slot: Slot) =>
    hits.filter((h) => h.slot === slot && h.kept);
  const benchInSlot = (slot: Slot) =>
    hits.filter((h) => h.slot === slot && !h.kept);
  const keptCount = $derived(hits.filter((h) => h.kept).length);

  // ── Source loading ──────────────────────────────────────────────────────--
  async function runSearch() {
    if (searching) return;
    searching = true;
    error = '';
    try {
      results = await searchInputs(query.trim());
    } catch (e) {
      error = (e as Error).message;
      results = [];
    } finally {
      searching = false;
    }
  }

  async function load(promise: Promise<LoadedClip>) {
    if (loading) return;
    loading = true;
    error = '';
    stop();
    wetBuffer = null;
    hits = [];
    interpretError = '';
    try {
      clip = await promise;
      requestAnimationFrame(() => drawWaveform(sourceCanvas, clip?.buffer));
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  const pickResult = (hit: SearchHit) =>
    load(fetchClipById(hit.id, hit.filename, hit.mediaType, audioCtx()));
  const pullRandom = () => load(fetchRandomClip(query.trim(), audioCtx()));

  function onUpload(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (file) load(loadLocalFile(file, audioCtx()));
  }

  // ── Interpret pass ────────────────────────────────────────────────────────
  async function runInterpret() {
    if (!clip?.sourceId || interpreting) return;
    interpreting = true;
    interpretError = '';
    wetBuffer = null;
    hits = [];
    stop();
    try {
      const c = audioCtx();
      if (c.state === 'suspended') await c.resume();
      const buf = await interpret(
        clip.sourceId,
        $state.snapshot(params),
        c,
        (p) => (interpretPhase = p),
        () => destroyed,
      );
      if (buf) {
        wetBuffer = buf;
        hits = carve(buf, sensitivity, keepLimit); // auto-carve on arrival
      }
    } catch (e) {
      interpretError = (e as Error).message;
    } finally {
      interpreting = false;
      interpretPhase = null;
    }
  }

  function recarve() {
    if (wetBuffer) hits = carve(wetBuffer, sensitivity, keepLimit);
  }

  function reassign(hit: Hit, slot: Slot) {
    hit.slot = slot;
  }

  function dropHit(hit: Hit) {
    hits = hits.filter((h) => h.id !== hit.id);
  }

  // Keep/bench. The limit is only enforced at carve time and by the slider —
  // promote and reassign may push a slot over it, intentionally.
  const promote = (hit: Hit) => (hit.kept = true);
  const demote = (hit: Hit) => (hit.kept = false);
  const onKeepLimit = () => resizeKeep(hits, keepLimit);

  // ── Audition ────────────────────────────────────────────────────────────--
  async function toggleRange(
    key: string,
    buffer: AudioBuffer | null,
    offsetSec: number,
    durSec: number,
  ) {
    if (playingKey === key) {
      stop();
      return;
    }
    if (!buffer) return;
    stop();
    const c = audioCtx();
    if (c.state === 'suspended') await c.resume();
    preview = c.createBufferSource();
    preview.buffer = buffer;
    preview.connect(c.destination);
    preview.onended = () => {
      playingKey = null;
      preview = null;
    };
    preview.start(0, offsetSec, durSec);
    playingKey = key;
  }

  const playFull = (key: string, buffer: AudioBuffer | null) =>
    toggleRange(key, buffer, 0, buffer ? buffer.duration : 0);

  /** Source preview: native <audio> for video, Web Audio for audio. */
  function toggleSourcePlayback() {
    if (!clip) return;
    if (playingKey === 'source') {
      stop();
      return;
    }
    stop();
    if (clip.mediaType === 'video' && sourceAudio) {
      sourceAudio.src = clip.url;
      sourceAudio.currentTime = 0;
      sourceAudio
        .play()
        .then(() => {
          playingKey = 'source';
        })
        .catch(() => {
          playingKey = null;
        });
      sourceAudio.onended = () => {
        playingKey = null;
      };
    } else {
      playFull('source', clip.buffer);
    }
  }

  // Audition the *edited* hit: render its FX/envelope/trim offline, then play.
  async function auditionHit(h: Hit) {
    const key = `hit:${h.id}`;
    if (playingKey === key) {
      stop();
      return;
    }
    if (!wetBuffer) return;
    auditioningHitId = h.id;
    auditionError = '';
    try {
      const c = audioCtx();
      if (c.state === 'suspended') await c.resume();
      const rendered = await renderHit(
        wetBuffer,
        h.start,
        h.end,
        $state.snapshot(h.edit),
      );
      if (destroyed) return;
      await toggleRange(key, rendered, 0, rendered.duration);
    } catch (e) {
      auditionError = (e as Error).message;
    } finally {
      auditioningHitId = null;
    }
  }

  function stop() {
    if (preview) {
      try {
        preview.stop();
      } catch {}
      preview.disconnect();
      preview = null;
    }
    if (sourceAudio) {
      sourceAudio.pause();
      sourceAudio.currentTime = 0;
    }
    playingKey = null;
  }

  // ── Waveform drawing ──────────────────────────────────────────────────────
  function drawWaveform(
    canvas: HTMLCanvasElement | undefined,
    buffer?: AudioBuffer | null,
    overlayHits: Hit[] = [],
  ) {
    if (!canvas || !buffer) return;
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth || 800;
    const cssHeight = canvas.clientHeight || 120;
    canvas.width = Math.floor(cssWidth * dpr);
    canvas.height = Math.floor(cssHeight * dpr);
    const g = canvas.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, cssWidth, cssHeight);

    const mid = cssHeight / 2;
    const peaks = computePeaks(buffer, Math.floor(cssWidth));

    g.strokeStyle = 'rgba(255,255,255,0.08)';
    g.beginPath();
    g.moveTo(0, mid);
    g.lineTo(cssWidth, mid);
    g.stroke();

    g.strokeStyle = '#c9a227';
    g.lineWidth = 1;
    g.beginPath();
    for (let x = 0; x < peaks.length; x++) {
      const [min, max] = peaks[x];
      g.moveTo(x + 0.5, mid - max * mid);
      g.lineTo(x + 0.5, mid - min * mid);
    }
    g.stroke();

    // Slice boundaries from carving. Benched (unkept) hits draw dimmer.
    const len = buffer.length;
    for (const h of overlayHits) {
      const x0 = (h.start / len) * cssWidth;
      const x1 = (h.end / len) * cssWidth;
      const color = SLOT_COLOR[h.slot];
      g.globalAlpha =
        playingKey === `hit:${h.id}` ? 0.85 : h.kept ? 0.45 : 0.15;
      g.fillStyle = color;
      g.fillRect(x0, 0, Math.max(1, x1 - x0), 4);
      g.globalAlpha = h.kept ? 1 : 0.3;
      g.strokeStyle = color;
      g.beginPath();
      g.moveTo(x0, 0);
      g.lineTo(x0, cssHeight);
      g.stroke();
    }
    g.globalAlpha = 1;
  }

  const drawWet = () => drawWaveform(wetCanvas, wetBuffer, hits);

  /** Click on the carve waveform canvas to select the nearest hit. */
  function onCanvasClick(e: MouseEvent) {
    if (!wetCanvas || !wetBuffer || !hits.length) return;
    const rect = wetCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const frac = x / rect.width;
    const sample = Math.floor(frac * wetBuffer.length);
    // Find nearest hit by center distance
    let best: Hit | null = null;
    let bestDist = Infinity;
    for (const h of hits) {
      const center = (h.start + h.end) / 2;
      const dist = Math.abs(center - sample);
      if (dist < bestDist) {
        bestDist = dist;
        best = h;
      }
    }
    if (best) {
      selectedHitId = selectedHitId === best.id ? null : best.id;
    }
  }

  // Redraw the carve view whenever the buffer, hits (incl. trims/slots), zoom,
  // or playing state change.
  $effect(() => {
    for (const h of hits) {
      void h.start;
      void h.end;
      void h.slot;
      void h.kept;
    }
    void zoom;
    void wetBuffer;
    void playingKey;
    requestAnimationFrame(drawWet);
  });

  function formatDuration(s: number): string {
    if (!s) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  let resizeObserver: ResizeObserver | undefined;
  onMount(() => {
    resizeObserver = new ResizeObserver(() => {
      drawWaveform(sourceCanvas, clip?.buffer);
      drawWet();
    });
    if (sourceCanvas) resizeObserver.observe(sourceCanvas);
    if (wetCanvas) resizeObserver.observe(wetCanvas);
  });

  onDestroy(() => {
    destroyed = true;
    stop();
    loop?.stop();
    resizeObserver?.disconnect();
    ctx?.close();
  });
</script>

<div class="ossuary">
  <!-- ── Source picker ──────────────────────────────────────────────────── -->
  <section class="oss-panel">
    <header class="oss-panel__head">
      <h2>Source</h2>
      <span class="oss-hint">pick a clip to carve</span>
    </header>

    <div class="oss-source__controls">
      <form
        class="oss-search"
        onsubmit={(e) => {
          e.preventDefault();
          runSearch();
        }}
      >
        <input
          class="brutalist-control oss-search__input"
          type="search"
          placeholder="search samples-bored…"
          bind:value={query}
          aria-label="Search the sample library"
        />
        <button class="brutalist-control" type="submit" disabled={searching}>
          {searching ? '…' : 'Search'}
        </button>
        <button
          class="brutalist-control"
          type="button"
          onclick={pullRandom}
          disabled={loading}
          title="Load a random match (or any random sample if the query is empty)"
        >
          🎲 Random
        </button>
        <label class="brutalist-control oss-upload">
          Upload
          <input type="file" accept="audio/*" onchange={onUpload} hidden />
        </label>
      </form>

      {#if error}
        <p class="oss-error">{error}</p>
      {/if}

      {#if results.length}
        <ul class="oss-results">
          {#each results as hit (hit.id)}
            <li>
              <button
                class="oss-result"
                class:is-active={clip?.sourceId === hit.id}
                onclick={() => pickResult(hit)}
                disabled={loading}
              >
                <span class="oss-result__name">{hit.filename}</span>
                {#if hit.durationSeconds}
                  <span class="oss-result__dur"
                    >{formatDuration(hit.durationSeconds)}</span
                  >
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </section>

  <!-- ── Source clip preview ────────────────────────────────────────────── -->
  <section class="oss-panel">
    <header class="oss-panel__head">
      <h2>Clip</h2>
      {#if clip}
        <span class="oss-hint">{clip.name} · {formatDuration(duration)}</span>
      {/if}
    </header>

    {#if loading}
      <div class="oss-empty">decoding…</div>
    {:else if clip}
      <div class="oss-waveform">
        <canvas bind:this={sourceCanvas}></canvas>
      </div>
      <audio bind:this={sourceAudio} style="display:none;"></audio>
      <div class="oss-actions">
        <button class="brutalist-control" onclick={toggleSourcePlayback}>
          {playingKey === 'source' ? '■ Stop' : '▶ Audition'}
        </button>
      </div>
    {:else}
      <div class="oss-empty">
        No clip loaded. Search the library, pull a random sample, or upload a
        file.
      </div>
    {/if}
  </section>

  <!-- ── Interpreter ────────────────────────────────────────────────────── -->
  <section class="oss-panel">
    <header class="oss-panel__head">
      <h2>Interpreter</h2>
      <span class="oss-hint"
        >hear the clip through a one-track-minded brain</span
      >
    </header>

    <details class="oss-glossary">
      <summary>what do these knobs do?</summary>
      <div class="oss-glossary__body">
        <p class="oss-glossary__title">Brains</p>
        <dl>
          {#each MODELS as m}
            <dt>{m}</dt>
            <dd>{MODEL_NOTES[m]}</dd>
          {/each}
        </dl>
        <p class="oss-glossary__title">Knobs</p>
        <dl>
          {#each KNOB_NOTES as [name, note]}
            <dt>{name}</dt>
            <dd>{note}</dd>
          {/each}
        </dl>
      </div>
    </details>

    <div class="oss-interp">
      <div class="oss-knobs">
        <label class="oss-knob">
          <span>Brain</span>
          <select class="brutalist-control" bind:value={params.model}>
            {#each MODELS as m}
              <option value={m}>{m}</option>
            {/each}
          </select>
        </label>

        <label class="oss-knob">
          <span>Temperature <em>{params.temperature.toFixed(2)}</em></span>
          <input
            type="range"
            min="0.1"
            max="3"
            step="0.05"
            bind:value={params.temperature}
          />
        </label>

        <label class="oss-knob">
          <span>Noise <em>{params.noise.toFixed(2)}</em></span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            bind:value={params.noise}
          />
        </label>

        <label class="oss-knob">
          <span>Wet/Dry <em>{params.mix.toFixed(2)}</em></span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            bind:value={params.mix}
          />
        </label>

        <label class="oss-knob">
          <span>Shuffle <em>{params.shuffle.toFixed(0)}</em></span>
          <input
            type="range"
            min="0"
            max="32"
            step="1"
            bind:value={params.shuffle}
          />
        </label>

        <label class="oss-knob">
          <span>Quantize <em>{params.quantize.toFixed(2)}</em></span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            bind:value={params.quantize}
          />
        </label>

        <label class="oss-knob">
          <span>Dims</span>
          <input
            class="brutalist-control"
            type="text"
            placeholder="e.g. 0,2,6 (blank = all)"
            bind:value={params.dims}
          />
        </label>

        <label class="oss-knob oss-knob--toggle">
          <input type="checkbox" bind:checked={params.reverse} />
          <span>Reverse</span>
        </label>
      </div>

      <div class="oss-interp__action">
        <button
          class="brutalist-control oss-interpret-btn"
          onclick={runInterpret}
          disabled={!canInterpret}
          title={clip && !clip.sourceId
            ? 'Uploaded clips can’t be interpreted yet — pick or random-pull a library clip'
            : ''}
        >
          {interpreting ? 'INTERPRETING…' : 'INTERPRET'}
        </button>
        {#if clip && !clip.sourceId}
          <span class="oss-hint">uploads can’t be interpreted yet</span>
        {:else if !clip}
          <span class="oss-hint">load a clip first</span>
        {/if}
      </div>
    </div>
  </section>

  <!-- ── Interpreted + carve ────────────────────────────────────────────── -->
  {#if interpreting || wetBuffer || interpretError}
    <section class="oss-panel">
      <header class="oss-panel__head">
        <h2>Interpreted &amp; carved</h2>
        {#if wetBuffer}
          <span class="oss-hint"
            >{params.model} · {formatDuration(wetBuffer.duration)} · {hits.length}
            hits</span
          >
        {/if}
      </header>

      {#if interpreting}
        <div class="oss-loader">
          <div class="oss-loader__spine">
            {#each Array(6) as _, i}
              <span style={`--i:${i}`}></span>
            {/each}
          </div>
          <p class="oss-loader__label">
            {interpretPhase ? PHASE_LABEL[interpretPhase] : 'working…'}
          </p>
        </div>
      {:else if interpretError}
        <p class="oss-error oss-error--pad">{interpretError}</p>
      {:else if wetBuffer}
        <div class="oss-carve">
          <div class="oss-waveform-scroll">
            <div class="oss-waveform" style={`width:${zoom * 100}%`}>
              <canvas bind:this={wetCanvas} onclick={onCanvasClick}></canvas>
            </div>
          </div>

          <div class="oss-carve__controls">
            <button
              class="brutalist-control"
              onclick={() => playFull('wet', wetBuffer)}
            >
              {playingKey === 'wet' ? '■ Stop' : '▶ Whole'}
            </button>
            <label class="oss-knob">
              <span>Sensitivity <em>{sensitivity.toFixed(2)}</em></span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                bind:value={sensitivity}
              />
            </label>
            <label class="oss-knob">
              <span>Keep <em>{keepLimit}/slot</em></span>
              <input
                type="range"
                min="4"
                max="32"
                step="1"
                bind:value={keepLimit}
                oninput={onKeepLimit}
              />
            </label>
            <button class="brutalist-control" onclick={recarve}>Re-carve</button
            >
            <label class="oss-knob">
              <span>Zoom <em>{zoom}×</em></span>
              <input type="range" min="1" max="12" step="1" bind:value={zoom} />
            </label>
          </div>

          {#if auditionError}
            <p class="oss-error">{auditionError}</p>
          {/if}
          {#if hits.length}
            <p class="oss-hint" style="padding:0.3rem 0.85rem;margin:0;">
              Click a carve on the waveform or a hit name below to edit.
            </p>
            <div class="oss-slots">
              {#snippet hitRow(h: Hit, label: string)}
                <div
                  class="oss-hit"
                  class:is-playing={playingKey === `hit:${h.id}`}
                  class:is-selected={selectedHitId === h.id}
                >
                  <button
                    class="oss-hit__play"
                    onclick={() => auditionHit(h)}
                    disabled={!!auditioningHitId && auditioningHitId !== h.id}
                    title="Audition (edited)"
                  >
                    {#if auditioningHitId === h.id}
                      …
                    {:else}
                      {playingKey === `hit:${h.id}` ? '■' : '▶'}
                    {/if}
                  </button>
                  <button
                    class="oss-hit__name"
                    onclick={() =>
                      (selectedHitId = selectedHitId === h.id ? null : h.id)}
                    title="Edit this hit"
                  >
                    {label}
                  </button>
                  <select
                    class="oss-hit__slot"
                    value={h.slot}
                    onchange={(e) =>
                      reassign(
                        h,
                        (e.target as HTMLSelectElement).value as Slot,
                      )}
                    title="Reassign slot"
                  >
                    {#each SLOTS as s}
                      <option value={s}>{s}</option>
                    {/each}
                  </select>
                  {#if h.kept}
                    <button
                      class="oss-hit__keep"
                      onclick={() => demote(h)}
                      title="Demote to bench"
                    >
                      −
                    </button>
                  {:else}
                    <button
                      class="oss-hit__keep"
                      onclick={() => promote(h)}
                      title="Promote to kit"
                    >
                      +
                    </button>
                  {/if}
                  <button
                    class="oss-hit__drop"
                    onclick={() => dropHit(h)}
                    title="Drop"
                  >
                    ✕
                  </button>
                </div>
              {/snippet}
              {#each SLOTS as slot}
                {@const kept = keptInSlot(slot)}
                {@const bench = benchInSlot(slot)}
                <div
                  class="oss-slot"
                  style={`--slot-color:${SLOT_COLOR[slot]}`}
                >
                  <div class="oss-slot__head">
                    <span class="oss-slot__name">{slot}</span>
                    {#if kept.length}
                      <button
                        class="oss-slot__loop"
                        class:is-active={looping && loopSlot === slot}
                        onclick={() => toggleLoop(slot)}
                        title="Audition loop for this slot"
                      >
                        {looping && loopSlot === slot ? '◼' : '↻'}
                      </button>
                    {/if}
                    <span class="oss-slot__count"
                      >{kept.length}/{kept.length + bench.length}</span
                    >
                  </div>
                  {#each kept as h, idx (h.id)}
                    {@render hitRow(h, `${slot} ${idx + 1}`)}
                  {/each}
                  {#if bench.length}
                    <details class="oss-slot__bench">
                      <summary>+ {bench.length} more</summary>
                      {#each bench as h, idx (h.id)}
                        {@render hitRow(h, `${slot} ${kept.length + idx + 1}`)}
                      {/each}
                    </details>
                  {/if}
                </div>
              {/each}
            </div>
          {:else}
            <p class="oss-empty">
              No hits found. Nudge sensitivity up and re-carve.
            </p>
          {/if}

          {#if selectedHit && wetBuffer}
            <div class="oss-editor-wrap">
              <div class="oss-editor__head">
                <span>Editing <strong>{selectedHit.slot}</strong></span>
                <button
                  class="oss-editor__close"
                  onclick={() => (selectedHitId = null)}>close ✕</button
                >
              </div>
              <HitEditor
                hit={selectedHit}
                buffer={wetBuffer}
                playing={playingKey === `hit:${selectedHit.id}`}
                onAudition={() => auditionHit(selectedHit)}
              />
            </div>
          {/if}

          {#if looping && loopSlot}
            <div class="oss-loop">
              <div class="oss-loop__head">
                <button class="brutalist-control" onclick={stopLoop}
                  >◼ Stop</button
                >
                <span class="oss-loop__title"
                  >loop · <strong>{loopSlot}</strong></span
                >
              </div>
              <div class="oss-loop__steps">
                {#each loopPattern as on, i}
                  <span
                    class="oss-loop__step"
                    class:on
                    class:cur={i === loopStep}
                  ></span>
                {/each}
              </div>
              <div class="oss-knobs">
                <label class="oss-knob">
                  <span>BPM <em>{loopBpm}</em></span>
                  <input
                    type="range"
                    min="60"
                    max="200"
                    step="1"
                    bind:value={loopBpm}
                    oninput={onLoopBpm}
                  />
                </label>
                <label class="oss-knob">
                  <span>Rate <em>1/{loopRate}</em></span>
                  <input
                    type="range"
                    min="2"
                    max="16"
                    step="2"
                    bind:value={loopRate}
                    oninput={onLoopRate}
                  />
                </label>
                <label class="oss-knob">
                  <span>Length <em>{loopLength}</em></span>
                  <input
                    type="range"
                    min="4"
                    max="32"
                    step="1"
                    bind:value={loopLength}
                    oninput={onLoopLength}
                  />
                </label>
                <label class="oss-knob">
                  <span>Probability <em>{loopProb}%</em></span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    bind:value={loopProb}
                    oninput={onLoopProb}
                  />
                </label>
              </div>
            </div>
          {/if}

          {#if hits.length}
            <div class="oss-export">
              <input
                class="brutalist-control oss-export__name"
                type="text"
                placeholder="kit name (optional)"
                bind:value={kitName}
              />
              <button
                class="brutalist-control oss-interpret-btn"
                onclick={indexAll}
                disabled={exporting || !keptCount}
              >
                {exporting ? 'WORKING…' : `Index ${keptCount} to library`}
              </button>
              <button
                class="brutalist-control"
                onclick={downloadZip}
                disabled={exporting || !keptCount}
              >
                Download ZIP
              </button>
              {#if exportMsg}
                <span class="oss-export__msg">{exportMsg}</span>
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </section>
  {/if}
</div>
