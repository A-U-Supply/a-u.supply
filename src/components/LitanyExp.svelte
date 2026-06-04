<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Toolbar from './litany-exp/Toolbar.svelte';
  import VoiceCard from './litany-exp/VoiceCard.svelte';
  import MasterSection from './litany-exp/MasterSection.svelte';
  import SampleSearchModal from './litany-exp/SampleSearchModal.svelte';
  import ViMode from './litany-exp/ViMode.svelte';
  import { AudioEngine } from '../lib/litany-exp/audio.ts';
  import { Scheduler } from '../lib/litany-exp/scheduler.ts';
  import { SamplePool, type PoolStatus } from '../lib/litany-exp/pool.ts';
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
    type Cadence,
    type PickMode,
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

  let viMode = $state(false);
  let viSubmode = $state<'normal' | 'euclid' | 'pool' | 'fx' | 'env'>('normal');
  let viVoiceIdx = $state(0);
  let viStepCursor = $state<number | null>(null);
  let viPoolCursor = $state(0);
  let viPending = $state<string | null>(null);
  let viPanelPool = $state(false);
  let viPanelFx = $state(false);
  let viPanelEnv = $state(false);

  const CADENCE_CYCLE: Cadence[] = [
    'hit',
    'bar',
    '2bar',
    '3bar',
    '4bar',
    '5bar',
    '6bar',
    '7bar',
    '8bar',
    '16bar',
  ];

  function toggleVi() {
    viMode = !viMode;
    viSubmode = 'normal';
    viStepCursor = null;
    viPoolCursor = 0;
    viPending = null;
    try {
      localStorage.setItem('litany-vi', String(viMode));
    } catch {}
  }

  function viClampVoiceIdx() {
    if (viVoiceIdx >= voices.length)
      viVoiceIdx = Math.max(0, voices.length - 1);
  }

  function viCurrentVoice(): Voice | undefined {
    return voices[viVoiceIdx];
  }

  function viVoiceId(): string | undefined {
    return viCurrentVoice()?.id;
  }

  const viVoicesById = $derived(
    voices.reduce<Record<string, number>>((acc, v, i) => {
      acc[v.id] = i;
      return acc;
    }, {}),
  );

  $effect(() => {
    void viVoiceIdx;
    viStepCursor = null;
  });

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
    const cadences: Cadence[] = [
      'hit',
      'bar',
      '2bar',
      '3bar',
      '4bar',
      '5bar',
      '6bar',
      '7bar',
      '8bar',
      '16bar',
    ];
    const pickModes: PickMode[] = ['seq', 'rnd'];
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
        cadence: pick(cadences),
        pickMode: pick(pickModes),
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

  // ── Vi-mode keyboard handler ─────────────────────────────────────────

  function viKeydown(e: KeyboardEvent) {
    if (!viMode) return;
    const tag = (e.target as HTMLElement).tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;

    const key = e.key;
    const shift = e.shiftKey;
    const ctrl = e.ctrlKey || e.metaKey;

    // Let Ctrl/Cmd combos pass through to browser (cmd-f, cmd-c, etc.)
    if (ctrl) {
      if (key === 'r') {
        e.preventDefault();
        redo();
      }
      return;
    }

    // Global: play/stop — Space and Shift+P always work
    if (key === ' ' || key === 'P') {
      e.preventDefault();
      playing ? stop() : handlePlay();
      return;
    }

    if (viSubmode === 'euclid') {
      viEuclidKey(key, shift);
      return;
    }
    if (viSubmode === 'pool') {
      viPoolKey(key);
      return;
    }
    if (viSubmode === 'fx') {
      viFxKey(key);
      return;
    }
    if (viSubmode === 'env') {
      viEnvKey(key);
      return;
    }

    // Normal mode
    e.preventDefault();

    // Pending (dd, gg)
    if (viPending) {
      if (viPending === 'd' && key === 'd') {
        viDeleteVoice();
        viPending = null;
        return;
      }
      if (viPending === 'g' && key === 'g') {
        viVoiceIdx = 0;
        viPending = null;
        return;
      }
      viPending = null;
    }

    if (key === 'd') {
      viPending = 'd';
      return;
    }
    if (key === 'g') {
      viPending = 'g';
      return;
    }
    if (key === 'Escape') {
      viStepCursor = null;
      viPending = null;
      return;
    }
    if (ctrl && key === 'r') {
      redo();
      return;
    }

    if (key === 'j') {
      viVoiceIdx = Math.min(viVoiceIdx + 1, voices.length - 1);
      viStepCursor = null;
      return;
    }
    if (key === 'k') {
      viVoiceIdx = Math.max(viVoiceIdx - 1, 0);
      viStepCursor = null;
      return;
    }
    if (shift && key === 'H') {
      viVoiceIdx = 0;
      viStepCursor = null;
      return;
    }
    if (shift && key === 'L') {
      viVoiceIdx = voices.length - 1;
      viStepCursor = null;
      return;
    }
    if (key === 'h') {
      viStepLeft();
      return;
    }
    if (key === 'l') {
      viStepRight();
      return;
    }
    if (key === '0') {
      viStepCursor = 0;
      return;
    }
    if (shift && key === '4') {
      viStepToLast();
      return;
    }

    if (key >= '1' && key <= '9') {
      const idx = parseInt(key) - 1;
      if (idx < voices.length) {
        viVoiceIdx = idx;
        viStepCursor = null;
      }
      return;
    }

    const voice = viCurrentVoice();
    if (!voice) return;

    if (key === 'i') {
      viToggleStep();
      return;
    }
    if (key === 'x') {
      viClearStep();
      return;
    }
    if (key === '%') {
      viCycleProbability();
      return;
    }
    if (key === 'm') {
      updateVoice({ ...voice, muted: !voice.muted }, true);
      return;
    }
    if (key === 'z') {
      updateVoice({ ...voice, soloed: !voice.soloed }, true);
      return;
    }
    if (key === 'r') {
      reRollPool(voice);
      return;
    }
    if (key === 'o') {
      pushHistory();
      viAddVoice(shift);
      return;
    }
    if (key === 'c') {
      viCycleCadence(1);
      return;
    }
    if (key === 'C') {
      viCycleCadence(-1);
      return;
    }
    if (key === 'y') {
      updateVoice({
        ...voice,
        pickMode: voice.pickMode === 'seq' ? 'rnd' : 'seq',
      });
      return;
    }
    if (key === '-') {
      viAdjustVolume(-0.05);
      return;
    }
    if (key === '=') {
      viAdjustVolume(0.05);
      return;
    }
    if (key === '_') {
      viAdjustPitch(-1);
      return;
    }
    if (key === '+') {
      viAdjustPitch(1);
      return;
    }
    if (key === 'w') {
      viPanelPool = !viPanelPool;
      viStepCursor = null;
      return;
    }
    if (key === 'f') {
      viSubmode = 'fx';
      viPanelFx = true;
      viStepCursor = null;
      return;
    }
    if (key === 'v') {
      viSubmode = 'env';
      viPanelEnv = true;
      viStepCursor = null;
      return;
    }
    if (key === 'p') {
      viPanelPool = true;
      viPanelFx = false;
      viPanelEnv = false;
      viEnterPool();
      return;
    }
    if (key === 'e') {
      viSubmode = 'euclid';
      viStepCursor = null;
      return;
    }
    if (key === 'f') {
      viSubmode = 'fx';
      viPanelFx = true;
      viPanelPool = false;
      viPanelEnv = false;
      viStepCursor = null;
      return;
    }
    if (key === 'v') {
      viSubmode = 'env';
      viPanelEnv = true;
      viPanelPool = false;
      viPanelFx = false;
      viStepCursor = null;
      return;
    }
    if (key === 'e') {
      viSubmode = 'euclid';
      viStepCursor = null;
      return;
    }
    if (key === 'u') {
      undo();
      return;
    }
    if (key === '[') {
      bpm = Math.max(bpm - 1, 40);
      return;
    }
    if (key === ']') {
      bpm = Math.min(bpm + 1, 240);
      return;
    }
    if (shift && key === '{') {
      bpm = Math.max(bpm - 5, 40);
      return;
    }
    if (shift && key === '}') {
      bpm = Math.min(bpm + 5, 240);
      return;
    }
  }

  function viStepLeft() {
    const v = viCurrentVoice();
    if (v) {
      if (viStepCursor === null) viStepCursor = 0;
      else if (viStepCursor > 0) viStepCursor--;
    }
  }

  function viStepRight() {
    const v = viCurrentVoice();
    if (v) {
      if (viStepCursor === null) viStepCursor = 0;
      else if (viStepCursor < v.stepCount - 1) viStepCursor++;
    }
  }

  function viStepToLast() {
    const v = viCurrentVoice();
    if (v) viStepCursor = v.stepCount - 1;
  }

  function viToggleStep() {
    const v = viCurrentVoice();
    if (!v || viStepCursor === null) return;
    const steps = [...v.steps];
    steps[viStepCursor] = !steps[viStepCursor];
    updateVoice({ ...v, steps });
  }

  function viClearStep() {
    const v = viCurrentVoice();
    if (!v || viStepCursor === null) return;
    const steps = [...v.steps];
    steps[viStepCursor] = false;
    updateVoice({ ...v, steps });
  }

  function viCycleProbability() {
    const v = viCurrentVoice();
    if (!v || viStepCursor === null) return;
    const overrides = [...(v.stepOverrides || [])];
    const probCycle = [null, 100, 75, 50, 25, 0];
    const cur = overrides[viStepCursor]?.probability ?? null;
    const idx = probCycle.indexOf(cur);
    const next = probCycle[(idx + 1) % probCycle.length];
    if (next == null) {
      overrides[viStepCursor] = overrides[viStepCursor]
        ? { ...overrides[viStepCursor], probability: undefined }
        : null;
    } else {
      overrides[viStepCursor] = {
        ...(overrides[viStepCursor] || {}),
        probability: next,
      };
    }
    updateVoice({ ...v, stepOverrides: overrides }, true);
  }

  function viDeleteVoice() {
    const v = viCurrentVoice();
    if (!v) return;
    pushHistory();
    removeVoice(v.id);
    viClampVoiceIdx();
  }

  function viAddVoice(above: boolean) {
    const v = createVoice('VOICE', 'perc');
    const insertAt = above ? viVoiceIdx : viVoiceIdx + 1;
    const next = [...voices];
    next.splice(insertAt, 0, v);
    voices = next;
    viVoiceIdx = Math.min(insertAt, voices.length - 1);
    if (engine) {
      engine.createVoiceChain(v.id);
      fillPool(v);
    }
  }

  function viCycleCadence(dir: number) {
    const v = viCurrentVoice();
    if (!v) return;
    const idx = CADENCE_CYCLE.indexOf(v.cadence);
    const next = (idx + dir + CADENCE_CYCLE.length) % CADENCE_CYCLE.length;
    updateVoice({ ...v, cadence: CADENCE_CYCLE[next] });
  }

  function viAdjustVolume(delta: number) {
    const v = viCurrentVoice();
    if (!v) return;
    const vol =
      Math.round(
        (Math.max(0, Math.min(1, v.volume + delta)) + Number.EPSILON) * 100,
      ) / 100;
    updateVoice({ ...v, volume: vol }, true);
  }

  function viAdjustPitch(delta: number) {
    const v = viCurrentVoice();
    if (!v) return;
    const pitch = Math.max(-12, Math.min(12, v.pitch + delta));
    updateVoice({ ...v, pitch }, true);
  }

  function viEnterPool() {
    viStepCursor = null;
    const v = viCurrentVoice();
    if (!v) return;
    const info = poolInfo[v.id];
    if (!info || info.entryCount === 0) return;
    viSubmode = 'pool';
    viPoolCursor = info.activeIndex;
  }

  function viEuclidKey(key: string, shift: boolean) {
    if (key === 'Escape' || key === 'Enter') {
      viSubmode = 'normal';
      return;
    }
    const v = viCurrentVoice();
    if (!v) return;
    const e = { ...v.euclidean };
    let changed = false;

    if (key === 'k') {
      e.pulses = Math.min(e.length, e.pulses + 1);
      changed = true;
    }
    if (key === 'j') {
      e.pulses = Math.max(1, e.pulses - 1);
      changed = true;
    }
    if (key === 'l') {
      e.length = Math.min(32, e.length + 1);
      e.pulses = Math.min(e.pulses, e.length);
      changed = true;
    }
    if (key === 'h') {
      e.length = Math.max(1, e.length - 1);
      e.pulses = Math.min(e.pulses, e.length);
      changed = true;
    }
    if (key === ',') {
      e.offset = (e.offset + 1) % (e.length || 1);
      changed = true;
    }
    if (key === '.') {
      e.offset = (e.offset - 1 + (e.length || 1)) % (e.length || 1);
      changed = true;
    }

    if (changed) updateVoice({ ...v, euclidean: e }, true);
  }

  function viPoolKey(key: string) {
    if (key === 'Escape') {
      viSubmode = 'normal';
      viPanelPool = false;
      return;
    }
    const v = viCurrentVoice();
    if (!v) return;
    const info = poolInfo[v.id];
    if (!info) return;

    if (key === 'j') {
      viPoolCursor = (viPoolCursor + 1) % Math.max(1, info.entryCount);
    }
    if (key === 'k') {
      viPoolCursor =
        (viPoolCursor - 1 + info.entryCount) % Math.max(1, info.entryCount);
    }
    if (key === 'l') {
      togglePoolPin(v.id, viPoolCursor);
    }
    if (key === 'Enter') {
      previewVoice(v.id, viPoolCursor);
    }
    if (key === 'x') {
      removePoolEntry(v.id, viPoolCursor);
      viPoolCursor = Math.min(
        viPoolCursor,
        Math.max(0, (poolInfo[v.id]?.entryCount ?? 1) - 1),
      );
    }
    if (key === '/') {
      openSearchForVoice(v.id);
    }
    if (key === 'r') {
      fetchMorePool(v.id, 4);
    }
  }

  function viFxKey(key: string) {
    if (key === 'Escape') {
      viSubmode = 'normal';
      return;
    }
    const v = viCurrentVoice();
    if (!v) return;
    const fx = { ...v.fx };
    let changed = false;

    if (key === 'j') {
      fx.delayTime =
        Math.round((Math.min(1, fx.delayTime + 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === 'k') {
      fx.delayTime =
        Math.round(
          (Math.max(0.01, fx.delayTime - 0.05) + Number.EPSILON) * 100,
        ) / 100;
      changed = true;
    }
    if (key === 'h') {
      fx.delayFeedback =
        Math.round(
          (Math.min(0.95, fx.delayFeedback + 0.05) + Number.EPSILON) * 100,
        ) / 100;
      changed = true;
    }
    if (key === 'l') {
      fx.delayFeedback =
        Math.round(
          (Math.max(0, fx.delayFeedback - 0.05) + Number.EPSILON) * 100,
        ) / 100;
      changed = true;
    }
    if (key === '-') {
      fx.delayWet =
        Math.round((Math.min(1, fx.delayWet + 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === '=') {
      fx.delayWet =
        Math.round((Math.max(0, fx.delayWet - 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === '_') {
      fx.reverbWet =
        Math.round((Math.min(1, fx.reverbWet + 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === '+') {
      fx.reverbWet =
        Math.round((Math.max(0, fx.reverbWet - 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === 'J') {
      fx.filterFreq = Math.min(20000, fx.filterFreq + 500);
      changed = true;
    }
    if (key === 'K') {
      fx.filterFreq = Math.max(80, fx.filterFreq - 500);
      changed = true;
    }
    if (key === 'H') {
      fx.filterQ =
        Math.round((Math.min(20, fx.filterQ + 0.5) + Number.EPSILON) * 10) / 10;
      changed = true;
    }
    if (key === 'L') {
      fx.filterQ =
        Math.round((Math.max(0.1, fx.filterQ - 0.5) + Number.EPSILON) * 10) /
        10;
      changed = true;
    }
    if (key === 'c') {
      const types: BiquadFilterType[] = [
        'lowpass',
        'highpass',
        'bandpass',
        'notch',
      ];
      const idx = types.indexOf(fx.filterType);
      fx.filterType = types[(idx + 1) % types.length];
      changed = true;
    }

    if (changed) updateVoice({ ...v, fx }, true);
  }

  function viEnvKey(key: string) {
    if (key === 'Escape') {
      viSubmode = 'normal';
      return;
    }
    const v = viCurrentVoice();
    if (!v) return;
    const env = { ...v.envelope };
    let changed = false;

    if (key === 'j') {
      env.attack =
        Math.round((Math.min(2, env.attack + 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === 'k') {
      env.attack =
        Math.round((Math.max(0, env.attack - 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === 'l') {
      env.release =
        Math.round((Math.min(3, env.release + 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === 'h') {
      env.release =
        Math.round((Math.max(0, env.release - 0.05) + Number.EPSILON) * 100) /
        100;
      changed = true;
    }
    if (key === 'c') {
      env.attackCurve = env.attackCurve === 'linear' ? 'exp' : 'linear';
      changed = true;
    }
    if (key === 'C') {
      env.releaseCurve = env.releaseCurve === 'linear' ? 'exp' : 'linear';
      changed = true;
    }

    if (changed) updateVoice({ ...v, envelope: env }, true);
  }

  // ── End vi-mode handler ──────────────────────────────────────────────

  onMount(() => {
    try {
      viMode = localStorage.getItem('litany-vi') === 'true';
    } catch {}
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

<svelte:window onkeydown={viKeydown} />

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
    {viMode}
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
    onViToggle={toggleVi}
  />

  <div class="voice-grid" class:voice-grid--rows={layout === 'rows'}>
    {#each voices as voice, i}
      {@const info = poolInfo[voice.id]}
      <VoiceCard
        {voice}
        {globalTick}
        {layout}
        viActive={viMode && i === viVoiceIdx}
        viStepCursor={viMode && i === viVoiceIdx ? viStepCursor : null}
        viPoolOpen={viMode && i === viVoiceIdx && viPanelPool}
        viFxOpen={viMode && i === viVoiceIdx && viPanelFx}
        viEnvOpen={viMode && i === viVoiceIdx && viPanelEnv}
        viPoolCursor={viMode && i === viVoiceIdx && viSubmode === 'pool'
          ? viPoolCursor
          : -1}
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
    {#if viMode}
      <ViMode
        submode={viSubmode}
        voiceIndex={viVoiceIdx}
        voiceCount={voices.length}
      />
    {/if}
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
      audioContext={engine?.ctx}
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
