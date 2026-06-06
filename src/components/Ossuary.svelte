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

  // ── Source picker state ───────────────────────────────────────────────────
  let query = $state('');
  let results = $state<SearchHit[]>([]);
  let searching = $state(false);
  let loading = $state(false);
  let error = $state('');

  // ── Loaded clip ───────────────────────────────────────────────────────────
  let clip = $state<LoadedClip | null>(null);
  let canvas: HTMLCanvasElement | undefined;

  // ── Audition ────────────────────────────────────────────────────────────--
  let ctx: AudioContext | null = null;
  let preview: AudioBufferSourceNode | null = null;
  let auditioning = $state(false);

  function audioCtx(): AudioContext {
    if (!ctx) ctx = new AudioContext();
    return ctx;
  }

  const duration = $derived(clip ? clip.buffer.duration : 0);

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
    stopAudition();
    try {
      clip = await promise;
      requestAnimationFrame(drawWaveform);
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

  function toggleAudition() {
    if (auditioning) {
      stopAudition();
    } else if (clip) {
      const c = audioCtx();
      if (c.state === 'suspended') c.resume();
      preview = c.createBufferSource();
      preview.buffer = clip.buffer;
      preview.connect(c.destination);
      preview.onended = () => {
        auditioning = false;
        preview = null;
      };
      preview.start();
      auditioning = true;
    }
  }

  function stopAudition() {
    if (preview) {
      try {
        preview.stop();
      } catch {}
      preview.disconnect();
      preview = null;
    }
    auditioning = false;
  }

  function drawWaveform() {
    if (!canvas || !clip) return;
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth || 800;
    const cssHeight = canvas.clientHeight || 160;
    canvas.width = Math.floor(cssWidth * dpr);
    canvas.height = Math.floor(cssHeight * dpr);
    const g = canvas.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, cssWidth, cssHeight);

    const mid = cssHeight / 2;
    const peaks = computePeaks(clip.buffer, Math.floor(cssWidth));

    // zero line
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
    resizeObserver = new ResizeObserver(() => clip && drawWaveform());
    if (canvas) resizeObserver.observe(canvas);
  });

  onDestroy(() => {
    stopAudition();
    resizeObserver?.disconnect();
    ctx?.close();
  });
</script>

<div class="ossuary">
  <!-- ── Source picker ──────────────────────────────────────────────────── -->
  <section class="oss-panel oss-source">
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
          title="Pull a random match (or any random sample if the query is empty)"
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

  <!-- ── Clip preview ───────────────────────────────────────────────────── -->
  <section class="oss-panel oss-preview">
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
        <canvas bind:this={canvas}></canvas>
      </div>
      <div class="oss-preview__actions">
        <button class="brutalist-control" onclick={toggleAudition}>
          {auditioning ? '■ Stop' : '▶ Audition'}
        </button>
      </div>
    {:else}
      <div class="oss-empty">
        No clip loaded. Search the library, pull a random sample, or upload a
        file.
      </div>
    {/if}
  </section>
</div>
