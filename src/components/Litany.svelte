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
  import type { AppState } from '../lib/litany/state.ts';

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
  let layout = $state<'grid' | 'rows'>('grid');

  // ── Undo / Redo ──────────────────────────────────────────────────────────
  let undoStack = $state<AppState[]>([]);
  let redoStack = $state<AppState[]>([]);

  function pushHistory() {
    undoStack = [
      ...undoStack.slice(-49),
      { voices: $state.snapshot(voices), bpm },
    ];
    redoStack = [];
  }

  function restoreState(state: AppState) {
    const currentIds = new Set(voices.map((v) => v.id));
    const nextIds = new Set(state.voices.map((v) => v.id));
    // Remove chains for voices that disappear
    for (const v of voices) {
      if (!nextIds.has(v.id)) {
        engine?.removeVoiceChain(v.id);
        pools.delete(v.id);
        const info = { ...poolInfo };
        delete info[v.id];
        poolInfo = info;
      }
    }
    // Add chains / refill pools for new or query-changed voices
    for (const v of state.voices) {
      const current = voices.find((c) => c.id === v.id);
      if (!currentIds.has(v.id)) {
        engine?.createVoiceChain(v.id);
        fillPool(v);
      } else if (current && current.query !== v.query) {
        fillPool(v);
      }
      engine?.updateVoiceChain(v.id, v.fx, v.volume);
    }
    voices = state.voices;
    bpm = state.bpm;
  }

  function undo() {
    if (!undoStack.length) return;
    redoStack = [...redoStack, { voices: $state.snapshot(voices), bpm }];
    const prev = undoStack[undoStack.length - 1];
    undoStack = undoStack.slice(0, -1);
    restoreState(prev);
  }

  function redo() {
    if (!redoStack.length) return;
    undoStack = [...undoStack, { voices: $state.snapshot(voices), bpm }];
    const next = redoStack[redoStack.length - 1];
    redoStack = redoStack.slice(0, -1);
    restoreState(next);
  }

  // Pool reactive info (status + name + entryNames) keyed by voice id
  let poolInfo = $state<
    Record<string, { status: PoolStatus; name: string; entryNames: string[] }>
  >({});

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
    poolInfo[voice.id] = { status: 'loading', name: '', entryNames: [] };
    pool.fill(voice.query, () => {
      poolInfo[voice.id] = {
        status: pool!.status,
        name: pool!.currentName,
        entryNames: pool!.entryNames,
      };
    });
  }

  function reRollPool(voice: Voice) {
    fillPool(voice);
  }

  function selectSample(voiceId: string, name: string) {
    pools.get(voiceId)?.selectByName(name);
    const info = poolInfo[voiceId];
    if (info)
      poolInfo[voiceId] = {
        ...info,
        name,
        entryNames: pools.get(voiceId)!.entryNames,
      };
  }

  let poolsLoading = $derived(
    Object.values(poolInfo).some((p) => p.status === 'loading'),
  );

  // ── Play / Stop ──────────────────────────────────────────────────────────
  async function play() {
    try {
      await engine!.resume();
    } catch (err) {
      console.error('[litany] failed to resume AudioContext', err);
      return;
    }
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
    pushHistory();
    const v = createVoice('VOICE', 'perc');
    voices = [...voices, v];
    if (engine) {
      engine.createVoiceChain(v.id);
      fillPool(v);
    }
  }

  function removeVoice(id: string) {
    pushHistory();
    voices = voices.filter((v) => v.id !== id);
    engine?.removeVoiceChain(id);
    pools.delete(id);
    const info = { ...poolInfo };
    delete info[id];
    poolInfo = info;
  }

  function updateVoice(updated: Voice, skipHistory = false) {
    if (!skipHistory) pushHistory();
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
    pushHistory();
    voices = voices.map((v) => randomizeVoiceSteps(v));
  }

  function doRandomizeQuery() {
    pushHistory();
    voices = voices.map((v) => {
      const updated = randomizeVoiceQuery(v);
      if (engine) fillPool(updated);
      return updated;
    });
  }

  function doRandomizeBpm() {
    pushHistory();
    bpm = randomizeBpm();
  }

  function doRandomizeVoices() {
    pushHistory();
    const count = Math.floor(Math.random() * 5) + 2; // 2–6 voices
    const newVoices = randomizeVoices(count);
    // Tear down old
    for (const v of voices) {
      engine?.removeVoiceChain(v.id);
      pools.delete(v.id);
    }
    voices = newVoices;
    const info: Record<
      string,
      { status: PoolStatus; name: string; entryNames: string[] }
    > = {};
    for (const v of newVoices) {
      engine!.createVoiceChain(v.id);
      info[v.id] = { status: 'loading', name: '', entryNames: [] };
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
    undoCount={undoStack.length}
    redoCount={redoStack.length}
    {layout}
    onUndo={undo}
    onRedo={redo}
    onLayoutToggle={() => (layout = layout === 'grid' ? 'rows' : 'grid')}
    onBpmChange={(v) => {
      pushHistory();
      bpm = v;
    }}
    onRandomizeSteps={doRandomizeSteps}
    onRandomizeQuery={doRandomizeQuery}
    onRandomizeBpm={doRandomizeBpm}
    onRandomizeVoices={doRandomizeVoices}
    onRandomizeAll={doRandomizeAll}
    onAddVoice={addVoice}
  />

  <div class="voice-grid" class:voice-grid--rows={layout === 'rows'}>
    {#each voices as voice (voice.id)}
      <VoiceCard
        {voice}
        {globalTick}
        {layout}
        poolStatus={poolInfo[voice.id]?.status ?? 'idle'}
        currentSampleName={poolInfo[voice.id]?.name ?? ''}
        poolEntryNames={poolInfo[voice.id]?.entryNames ?? []}
        onChange={updateVoice}
        onBeforeDrag={pushHistory}
        onRandomizeSteps={() => updateVoice(randomizeVoiceSteps(voice))}
        onRandomizeQuery={() => {
          pushHistory();
          const updated = randomizeVoiceQuery(voice);
          if (engine) fillPool(updated);
          voices = voices.map((v) => (v.id === updated.id ? updated : v));
        }}
        onReRoll={() => reRollPool(voice)}
        onSelectSample={(name: string) => selectSample(voice.id, name)}
        onRemove={() => removeVoice(voice.id)}
        onPin={() => pinVoice(voice.id)}
        onUnpin={() => unpinVoice(voice.id)}
      />
    {/each}

    <button
      class="brutalist-control add-card"
      class:add-card--row={layout === 'rows'}
      onclick={addVoice}>+ ADD VOICE</button
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

  .voice-grid--rows {
    grid-template-columns: 1fr;
    gap: 4px;
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

  .add-card--row {
    min-height: 36px;
  }

  .add-card:hover {
    border-color: #666;
    color: #888;
    box-shadow: none;
  }
</style>
