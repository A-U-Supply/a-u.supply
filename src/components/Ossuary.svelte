<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    searchSamples,
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
    type InterpretParams,
    type InterpretPhase,
  } from '../lib/ossuary/interpret.ts';

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

  // ── Audition ────────────────────────────────────────────────────────────--
  let ctx: AudioContext | null = null;
  let preview: AudioBufferSourceNode | null = null;
  let playingKey = $state<'source' | 'wet' | null>(null);
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

  // ── Source loading ──────────────────────────────────────────────────────--
  async function runSearch() {
    if (searching) return;
    searching = true;
    error = '';
    try {
      results = await searchSamples(query.trim());
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
    interpretError = '';
    try {
      clip = await promise;
      requestAnimationFrame(() => draw(sourceCanvas, clip?.buffer));
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  const pickResult = (hit: SearchHit) =>
    load(fetchClipById(hit.id, hit.filename, audioCtx()));
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
        requestAnimationFrame(() => draw(wetCanvas, wetBuffer));
      }
    } catch (e) {
      interpretError = (e as Error).message;
    } finally {
      interpreting = false;
      interpretPhase = null;
    }
  }

  // ── Audition ────────────────────────────────────────────────────────────--
  function toggle(key: 'source' | 'wet', buffer: AudioBuffer | null) {
    if (playingKey === key) {
      stop();
    } else if (buffer) {
      stop();
      const c = audioCtx();
      if (c.state === 'suspended') c.resume();
      preview = c.createBufferSource();
      preview.buffer = buffer;
      preview.connect(c.destination);
      preview.onended = () => {
        playingKey = null;
        preview = null;
      };
      preview.start();
      playingKey = key;
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
    playingKey = null;
  }

  // ── Waveform drawing ──────────────────────────────────────────────────────
  function draw(
    canvas: HTMLCanvasElement | undefined,
    buffer?: AudioBuffer | null,
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
  }

  function formatDuration(s: number): string {
    if (!s) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  let resizeObserver: ResizeObserver | undefined;
  onMount(() => {
    resizeObserver = new ResizeObserver(() => {
      draw(sourceCanvas, clip?.buffer);
      draw(wetCanvas, wetBuffer);
    });
    if (sourceCanvas) resizeObserver.observe(sourceCanvas);
    if (wetCanvas) resizeObserver.observe(wetCanvas);
  });

  onDestroy(() => {
    destroyed = true;
    stop();
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
      <div class="oss-actions">
        <button
          class="brutalist-control"
          onclick={() => toggle('source', clip!.buffer)}
        >
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

  <!-- ── Result (wet WAV) ───────────────────────────────────────────────── -->
  {#if interpreting || wetBuffer || interpretError}
    <section class="oss-panel">
      <header class="oss-panel__head">
        <h2>Interpreted</h2>
        {#if wetBuffer}
          <span class="oss-hint"
            >{params.model} · {formatDuration(wetBuffer.duration)}</span
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
        <div class="oss-waveform">
          <canvas bind:this={wetCanvas}></canvas>
        </div>
        <div class="oss-actions">
          <button
            class="brutalist-control"
            onclick={() => toggle('wet', wetBuffer)}
          >
            {playingKey === 'wet' ? '■ Stop' : '▶ Audition'}
          </button>
        </div>
      {/if}
    </section>
  {/if}
</div>
