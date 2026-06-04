<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Toolbar from './litany-exp/Toolbar.svelte';
  import VoiceCard from './litany-exp/VoiceCard.svelte';
  import MasterSection from './litany-exp/MasterSection.svelte';
  import SampleSearchModal from './litany-exp/SampleSearchModal.svelte';
  import { AudioEngine } from '../lib/litany-exp/audio.ts';
  import { Scheduler } from '../lib/litany-exp/scheduler.ts';
  import { SamplePool, type PoolStatus } from '../lib/litany-exp/pool.ts';
  import type { PoolEntry } from '../lib/litany-exp/pool.ts';
  import {
    defaultVoices,
    createVoice,
    encodeState,
    decodeState,
    bjorklund,
    defaultFx,
    defaultEnvelope,
    type Voice,
    type PlayStyle,
    type Rotation,
    type PinnedRotation,
  } from '../lib/litany-exp/state.ts';
  import {
    randomizeBpm,
    randomizeVoices,
    randomizeVoiceSteps,
    randomizeVoiceQuery,
  } from '../lib/litany-exp/randomize.ts';
  import type { AppState } from '../lib/litany-exp/state.ts';

  interface VoicePoolInfo {
    status: PoolStatus;
    currentName: string;
    activeIndex: number;
    entryCount: number;
    pinnedCount: number;
    entries: { name: string; source: string; pinned: boolean }[];
  }

  let voices = $state<Voice[]>(defaultVoices());
  let bpm = $state(128);

  let playing = $state(false);
  let globalTick = $state(-1);
  let masterVolume = $state(0.8);
  let compressorThreshold = $state(-24);
  let compressorRatio = $state(4);
  let shareSuccess = $state(false);
  let layout = $state<'grid' | 'rows'>('grid');

  let sampleSearchOpen = $state(false);
  let searchTargetVoiceId = $state('');

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
    for (const v of voices) {
      if (!nextIds.has(v.id)) {
        engine?.removeVoiceChain(v.id);
        pools.delete(v.id);
        const info = { ...poolInfo };
        delete info[v.id];
        poolInfo = info;
      }
    }
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

  let poolInfo = $state<Record<string, VoicePoolInfo>>({});

  let engine: AudioEngine | null = null;
  let scheduler: Scheduler | null = null;
  const pools = new Map<string, SamplePool>();

  function buildPoolInfo(pool: SamplePool): VoicePoolInfo {
    return {
      status: pool.status,
      currentName: pool.currentName,
      activeIndex: pool.getActiveIndex(),
      entryCount: pool.entries.filter(Boolean).length,
      pinnedCount: pool.pinnedIndexes.size,
      entries: pool.entries.map((e, i) => ({
        name: e?.name ?? '(empty)',
        source: e?.source ?? 'query',
        pinned: pool.pinnedIndexes.has(i),
      })),
    };
  }

  function syncEngineParams() {
    if (!engine) return;
    engine.masterGain.gain.value = masterVolume;
    engine.compressor.threshold.value = compressorThreshold;
    engine.compressor.ratio.value = compressorRatio;
    for (const v of voices) {
      engine.updateVoiceChain(v.id, v.fx, v.volume);
    }
  }

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
    poolInfo[voice.id] = buildPoolInfo(pool);
    pool.fill(
      voice.query,
      () => {
        poolInfo[voice.id] = buildPoolInfo(pool!);
      },
      voice.pinned,
    );
  }

  function syncVoicePins(voiceId: string) {
    const voice = voices.find((v) => v.id === voiceId);
    if (!voice) return;
    const pool = pools.get(voiceId);
    if (!pool) return;
    const pinnedNames = pool.getPinnedNames();
    voices = voices.map((v) =>
      v.id === voiceId ? { ...v, pinned: pinnedNames } : v,
    );
    poolInfo[voiceId] = buildPoolInfo(pool);
  }

  function reRollPool(voice: Voice) {
    fillPool(voice);
  }

  function fetchMorePool(voiceId: string, count: number) {
    const pool = pools.get(voiceId);
    if (!pool) return;
    pool.fetchMore(count, () => {
      poolInfo[voiceId] = buildPoolInfo(pool);
    });
  }

  async function addSamplesFromSearch(
    voiceId: string,
    hits: { id: string; filename: string }[],
  ) {
    const pool = pools.get(voiceId);
    if (!pool) return;
    await pool.addFromSearch(hits, () => {
      poolInfo[voiceId] = buildPoolInfo(pool);
    });
  }

  function togglePoolPin(voiceId: string, index: number) {
    const pool = pools.get(voiceId);
    if (!pool) return;
    pool.togglePin(index);
    syncVoicePins(voiceId);
  }

  function removePoolEntry(voiceId: string, index: number) {
    const pool = pools.get(voiceId);
    if (!pool) return;
    pool.removeEntry(index);
    syncVoicePins(voiceId);
    poolInfo[voiceId] = buildPoolInfo(pool);
  }

  function movePoolEntry(voiceId: string, fromIndex: number, toIndex: number) {
    const pool = pools.get(voiceId);
    if (!pool) return;
    pool.moveEntry(fromIndex, toIndex);
    poolInfo[voiceId] = buildPoolInfo(pool);
  }

  function previewVoice(voiceId: string, entryIndex?: number) {
    const pool = pools.get(voiceId);
    if (!pool || pool.status !== 'ready') return;
    const buffer =
      entryIndex != null
        ? pool.previewBufferByIdx(entryIndex)
        : pool.previewBuffer();
    if (!buffer) return;
    const voice = voices.find((v) => v.id === voiceId);
    if (!voice) return;
    engine?.previewVoice(
      voiceId,
      buffer,
      voice.envelope.attack,
      voice.envelope.release,
      voice.envelope.attackCurve,
      voice.envelope.releaseCurve,
      voice.pitch,
    );
  }

  function openSearchForVoice(voiceId: string) {
    searchTargetVoiceId = voiceId;
    sampleSearchOpen = true;
  }

  async function handleSearchAdd(hits: { id: string; filename: string }[]) {
    if (!searchTargetVoiceId) return;
    await addSamplesFromSearch(searchTargetVoiceId, hits);
  }

  let poolsLoading = $derived(
    Object.values(poolInfo).some((p) => p.status === 'loading'),
  );

  function handlePlay() {
    const p = engine!.ctx.resume();
    startPlaying(p);
  }

  async function startPlaying(resumePromise: Promise<void>) {
    try {
      await resumePromise;
    } catch (err) {
      console.error('[litany-exp] failed to resume AudioContext', err);
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
    if (
      prev &&
      (prev.euclidean.pulses !== updated.euclidean.pulses ||
        prev.euclidean.length !== updated.euclidean.length ||
        prev.euclidean.offset !== updated.euclidean.offset)
    ) {
      const { pulses, length, offset } = updated.euclidean;
      updated = {
        ...updated,
        steps: bjorklund(pulses, length, offset),
        stepCount: length,
      };
    }

    voices = voices.map((v) => (v.id === updated.id ? updated : v));
    if (engine) {
      engine.updateVoiceChain(updated.id, updated.fx, updated.volume);
      if (prev && prev.query !== updated.query) {
        fillPool(updated);
      }
    }
  }

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
    const count = Math.floor(Math.random() * 5) + 2;
    const newVoices = randomizeVoices(count);
    for (const v of voices) {
      engine?.removeVoiceChain(v.id);
      pools.delete(v.id);
    }
    voices = newVoices;
    const info: Record<string, VoicePoolInfo> = {};
    for (const v of newVoices) {
      engine!.createVoiceChain(v.id);
      const pool = new SamplePool(engine!.ctx);
      pools.set(v.id, pool);
      info[v.id] = buildPoolInfo(pool);
      fillPool(v);
    }
    poolInfo = info;
  }

  function doRandomizeAll() {
    doRandomizeBpm();
    doRandomizeVoices();
  }

  function doChaos() {
    pushHistory();
    bpm = randomizeBpm();
    const rotations: Rotation[] = ['every-hit', 'every-bar', 'every-4bars'];
    const pinnedRotations: PinnedRotation[] = [
      'every-hit',
      'every-bar',
      'every-4bars',
      'fixed',
    ];
    const styles: PlayStyle[] = ['one-shot', 'cut', 'gate', 'legato'];
    const filterTypes: BiquadFilterType[] = [
      'lowpass',
      'highpass',
      'bandpass',
      'notch',
    ];
    const pick = <T,>(arr: T[]): T =>
      arr[Math.floor(Math.random() * arr.length)];

    voices = voices.map((v) => {
      const fx = defaultFx();
      fx.delayTime = Math.round(Math.random() * 100) / 100;
      fx.delayFeedback = Math.round(Math.random() * 95) / 100;
      fx.delayWet = Math.round(Math.random() * 100) / 100;
      fx.reverbWet = Math.round(Math.random() * 100) / 100;
      fx.filterFreq =
        Math.random() < 0.5 ? 20000 : Math.round(80 + Math.random() * 19920);
      fx.filterQ = Math.round(Math.random() * 20 * 10) / 10;
      fx.filterType = pick(filterTypes);

      const env = defaultEnvelope();

      const len = 4 + Math.floor(Math.random() * 13);
      const pulses = 1 + Math.floor(Math.random() * len);
      return {
        ...v,
        steps: bjorklund(pulses, len, Math.floor(Math.random() * len)),
        stepCount: len,
        rotation: pick(rotations),
        pinnedRotation: pick(pinnedRotations),
        volume: Math.round((0.3 + Math.random() * 0.7) * 100) / 100,
        pitch: Math.floor(Math.random() * 13) - 6,
        fx,
        envelope: env,
        playStyle: pick(styles),
        euclidean: {
          pulses,
          length: len,
          offset: Math.floor(Math.random() * len),
        },
      };
    });

    for (const v of voices) {
      engine?.updateVoiceChain(v.id, v.fx, v.volume);
    }
  }

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

  function share() {
    const hash = encodeState({ voices, bpm });
    const url = `${window.location.origin}${window.location.pathname}#${hash}`;
    navigator.clipboard.writeText(url).then(() => {
      shareSuccess = true;
      setTimeout(() => (shareSuccess = false), 2000);
    });
  }

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
    initEngine();
  });

  onDestroy(() => {
    scheduler?.stop();
    engine?.destroy();
  });
</script>

<div class="litany-exp">
  <Toolbar
    {playing}
    {bpm}
    onPlay={handlePlay}
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
    onChaos={doChaos}
    onAddVoice={addVoice}
  />

  <div class="voice-grid" class:voice-grid--rows={layout === 'rows'}>
    {#each voices as voice (voice.id)}
      {@const info = poolInfo[voice.id]}
      <VoiceCard
        {voice}
        {globalTick}
        {layout}
        poolStatus={info?.status ?? 'idle'}
        currentSampleName={info?.currentName ?? ''}
        activeEntryIndex={info?.activeIndex ?? 0}
        poolEntries={info?.entries ?? []}
        poolEntryCount={info?.entryCount ?? 0}
        poolPinnedCount={info?.pinnedCount ?? 0}
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
        onRemove={() => removeVoice(voice.id)}
        onPreview={() => previewVoice(voice.id)}
        onPreviewEntry={(idx: number) => previewVoice(voice.id, idx)}
        onTogglePin={(idx: number) => togglePoolPin(voice.id, idx)}
        onRemoveEntry={(idx: number) => removePoolEntry(voice.id, idx)}
        onMoveEntry={(from: number, to: number) =>
          movePoolEntry(voice.id, from, to)}
        onOpenSearch={() => openSearchForVoice(voice.id)}
        onFetchMore={() => fetchMorePool(voice.id, 4)}
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

  {#if sampleSearchOpen}
    <SampleSearchModal
      onClose={() => (sampleSearchOpen = false)}
      onAdd={handleSearchAdd}
      poolEntryCount={searchTargetVoiceId
        ? (poolInfo[searchTargetVoiceId]?.entryCount ?? 0)
        : 0}
    />
  {/if}
</div>

<style>
  .litany-exp {
    padding: 16px;
    color: var(--lit-text);
    font-family: var(--lit-font);
  }

  .voice-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 10px;
    padding: 16px 0;
  }

  .voice-grid--rows {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .add-card {
    border: 1px dashed var(--lit-border-hover);
    background: transparent;
    color: var(--lit-text-faint);
    font-size: 0.75rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100px;
    box-shadow: none;
  }

  .add-card--row {
    min-height: 36px;
  }

  .add-card:hover {
    border-color: var(--lit-text-dim);
    color: var(--lit-text-dim);
    box-shadow: none;
  }
</style>
