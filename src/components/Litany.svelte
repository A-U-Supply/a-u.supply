<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Toolbar from './litany/Toolbar.svelte';
  import VoiceCard from './litany/VoiceCard.svelte';
  import MasterSection from './litany/MasterSection.svelte';
  import { AudioEngine } from '../lib/litany/audio.ts';
  import { Scheduler } from '../lib/litany/scheduler.ts';
  import { SamplePool, type PoolStatus } from '../lib/litany/pool.ts';
  import {
    defaultVoices,
    createVoice,
    encodeState,
    decodeState,
    type Voice,
  } from '../lib/litany/state.ts';
  import {
    randomizeBpm,
    randomizeVoices,
    randomizeVoiceSteps,
    randomizeVoiceQuery,
  } from '../lib/litany/randomize.ts';

  // ── Serialisable state ──────────────────────────────────────────────────
  let voices = $state<Voice[]>(defaultVoices());
  let bpm = $state(128);

  // ── Runtime state ───────────────────────────────────────────────────────
  let playing = $state(false);
  let globalTick = $state(-1);
  let masterVolume = $state(0.8);
  let compressorThreshold = $state(-24);
  let compressorRatio = $state(4);
  let shareSuccess = $state(false);

  // Pool reactive info (status + name) keyed by voice id
  let poolInfo = $state<Record<string, { status: PoolStatus; name: string }>>(
    {},
  );

  // Non-reactive runtime objects
  let engine: AudioEngine | null = null;
  let scheduler: Scheduler | null = null;
  const pools = new Map<string, SamplePool>();

  // ── Helpers ─────────────────────────────────────────────────────────────
  function syncEngineParams() {
    if (!engine) return;
    engine.masterGain.gain.value = masterVolume;
    engine.compressor.threshold.value = compressorThreshold;
    engine.compressor.ratio.value = compressorRatio;
    for (const v of voices) {
      engine.updateVoiceChain(v.id, v.fx, v.volume);
    }
  }

  // Called once on mount. Creates AudioContext (suspended is fine for decoding)
  // and starts filling pools immediately so they're ready when play is clicked.
  function initEngine() {
    engine = new AudioEngine();
    for (const v of voices) {
      engine.createVoiceChain(v.id);
    }
    syncEngineParams();
    for (const v of voices) {
      fillPool(v);
    }
  }

  function fillPool(voice: Voice) {
    let pool = pools.get(voice.id);
    if (!pool) {
      pool = new SamplePool(engine!.ctx);
      pools.set(voice.id, pool);
    }
    poolInfo[voice.id] = { status: 'loading', name: '' };
    pool.fill(voice.query, () => {
      poolInfo[voice.id] = { status: pool!.status, name: pool!.currentName };
    });
  }

  let poolsLoading = $derived(
    Object.values(poolInfo).some((p) => p.status === 'loading'),
  );

  // ── Play / Stop ──────────────────────────────────────────────────────────
  async function play() {
    await engine!.resume();
    scheduler = new Scheduler(
      engine!,
      pools,
      () => voices,
      (tick) => {
        globalTick = tick;
      },
      () => bpm,
    );
    scheduler.start();
    playing = true;
  }

  function stop() {
    scheduler?.stop();
    scheduler = null;
    playing = false;
    globalTick = -1;
  }

  // ── Voice management ─────────────────────────────────────────────────────
  function addVoice() {
    const v = createVoice('VOICE', 'perc');
    voices = [...voices, v];
    if (engine) {
      engine.createVoiceChain(v.id);
      fillPool(v);
    }
  }

  function removeVoice(id: string) {
    voices = voices.filter((v) => v.id !== id);
    engine?.removeVoiceChain(id);
    pools.delete(id);
    const info = { ...poolInfo };
    delete info[id];
    poolInfo = info;
  }

  function updateVoice(updated: Voice) {
    const prev = voices.find((v) => v.id === updated.id);
    voices = voices.map((v) => (v.id === updated.id ? updated : v));
    if (engine) {
      engine.updateVoiceChain(updated.id, updated.fx, updated.volume);
      // Refill pool if query changed
      if (prev && prev.query !== updated.query) {
        fillPool(updated);
      }
    }
  }

  // ── Randomize ────────────────────────────────────────────────────────────
  function doRandomizeSteps() {
    voices = voices.map((v) => randomizeVoiceSteps(v));
  }

  function doRandomizeQuery() {
    voices = voices.map((v) => {
      const updated = randomizeVoiceQuery(v);
      if (engine) fillPool(updated);
      return updated;
    });
  }

  function doRandomizeBpm() {
    bpm = randomizeBpm();
  }

  function doRandomizeVoices() {
    const count = Math.floor(Math.random() * 5) + 2; // 2–6 voices
    const newVoices = randomizeVoices(count);
    // Tear down old
    for (const v of voices) {
      engine?.removeVoiceChain(v.id);
      pools.delete(v.id);
    }
    voices = newVoices;
    const info: Record<string, { status: PoolStatus; name: string }> = {};
    for (const v of newVoices) {
      engine!.createVoiceChain(v.id);
      info[v.id] = { status: 'loading', name: '' };
      fillPool(v);
    }
    poolInfo = info;
  }

  function doRandomizeAll() {
    doRandomizeBpm();
    doRandomizeVoices();
  }

  // ── Pin / Unpin ──────────────────────────────────────────────────────────
  function pinVoice(id: string) {
    pools.get(id)?.pin();
  }

  function unpinVoice(id: string) {
    pools.get(id)?.unpin();
  }

  // ── Master section ────────────────────────────────────────────────────────
  function setMasterVolume(v: number) {
    masterVolume = v;
    if (engine) engine.masterGain.gain.value = v;
  }

  function setCompressorThreshold(v: number) {
    compressorThreshold = v;
    if (engine) engine.compressor.threshold.value = v;
  }

  function setCompressorRatio(v: number) {
    compressorRatio = v;
    if (engine) engine.compressor.ratio.value = v;
  }

  // ── URL sharing ───────────────────────────────────────────────────────────
  function share() {
    const hash = encodeState({ voices, bpm });
    const url = `${window.location.origin}${window.location.pathname}#${hash}`;
    navigator.clipboard.writeText(url).then(() => {
      shareSuccess = true;
      setTimeout(() => (shareSuccess = false), 2000);
    });
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  onMount(() => {
    if (window.location.hash) {
      try {
        const saved = decodeState(window.location.hash.slice(1));
        voices = saved.voices;
        bpm = saved.bpm;
      } catch {
        // malformed hash — use defaults
      }
    }
    // Create AudioContext and start filling pools immediately.
    // A suspended context can decode audio fine; resume() is called on play click.
    initEngine();
  });

  onDestroy(() => {
    scheduler?.stop();
    engine?.destroy();
  });
</script>

<div class="litany">
  <Toolbar
    {playing}
    {bpm}
    onPlay={play}
    onStop={stop}
    {poolsLoading}
    onBpmChange={(v) => (bpm = v)}
    onRandomizeSteps={doRandomizeSteps}
    onRandomizeQuery={doRandomizeQuery}
    onRandomizeBpm={doRandomizeBpm}
    onRandomizeVoices={doRandomizeVoices}
    onRandomizeAll={doRandomizeAll}
    onAddVoice={addVoice}
  />

  <div class="voice-grid">
    {#each voices as voice (voice.id)}
      <VoiceCard
        {voice}
        {globalTick}
        poolStatus={poolInfo[voice.id]?.status ?? 'idle'}
        currentSampleName={poolInfo[voice.id]?.name ?? ''}
        onChange={updateVoice}
        onRandomizeSteps={() => updateVoice(randomizeVoiceSteps(voice))}
        onRandomizeQuery={() => {
          const updated = randomizeVoiceQuery(voice);
          if (engine) fillPool(updated);
          voices = voices.map((v) => (v.id === updated.id ? updated : v));
        }}
        onRemove={() => removeVoice(voice.id)}
        onPin={() => pinVoice(voice.id)}
        onUnpin={() => unpinVoice(voice.id)}
      />
    {/each}

    <button class="brutalist-control add-card" onclick={addVoice}
      >+ ADD VOICE</button
    >
  </div>

  <MasterSection
    {masterVolume}
    {compressorThreshold}
    {compressorRatio}
    onVolumeChange={setMasterVolume}
    onThresholdChange={setCompressorThreshold}
    onRatioChange={setCompressorRatio}
    onShare={share}
    {shareSuccess}
  />
</div>

<style>
  .litany {
    padding: 16px;
    min-height: 100vh;
    color: #ccc;
    font-family: var(--font-mono, 'Courier New', monospace);
  }

  .voice-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px;
    padding: 16px 0;
  }

  .add-card {
    border: 2px dashed #333;
    background: transparent;
    color: #555;
    font-size: 0.75rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 120px;
    box-shadow: none;
  }

  .add-card:hover {
    border-color: #666;
    color: #888;
    box-shadow: none;
  }
</style>
