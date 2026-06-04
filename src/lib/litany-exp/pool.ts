import type { Cadence, PickMode } from './state.ts';

const INITIAL_POOL_SIZE = 8;
const MAX_POOL_SIZE = 16;

export type PoolStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface PoolEntry {
  buffer: AudioBuffer;
  name: string;
  mediaId?: string;
  source: 'query' | 'manual';
}

const CADENCE_MOD: Record<string, number> = {
  hit: 0,
  bar: 1,
  '2bar': 2,
  '3bar': 3,
  '4bar': 4,
  '5bar': 5,
  '6bar': 6,
  '7bar': 7,
  '8bar': 8,
  '16bar': 16,
};

export class SamplePool {
  private ctx: AudioContext;
  private decodeCtx: OfflineAudioContext;
  entries: (PoolEntry | null)[] = [];
  private index = 0;
  pinnedIndexes: Set<number> = new Set();
  pins: string[] = [];
  private barCount = 0;
  private lastQuery = '';

  status: PoolStatus = 'idle';
  currentName = '';

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
    this.decodeCtx = new OfflineAudioContext(1, 256, ctx.sampleRate);
  }

  async fill(
    query: string,
    onUpdate?: () => void,
    pinnedNames?: string[],
  ): Promise<void> {
    this.status = 'loading';
    this.lastQuery = query;

    const kept = this.entries.filter(
      (e, i) => this.pinnedIndexes.has(i) && e !== null,
    ) as PoolEntry[];

    this.entries = [];
    this.pinnedIndexes = new Set();
    this.pins = [];
    onUpdate?.();

    const need = Math.max(0, INITIAL_POOL_SIZE - kept.length);
    const results = await Promise.allSettled(
      Array.from({ length: need }, () => this.fetchOne(query)),
    );

    this.entries = [
      ...kept,
      ...results.map((r) => (r.status === 'fulfilled' ? r.value : null)),
    ];
    this.index = 0;

    if (pinnedNames && pinnedNames.length > 0) {
      this.repinFromNames(pinnedNames);
    }

    const first = this.entries.find((e) => e !== null);
    this.currentName = first?.name ?? '';
    this.status = first ? 'ready' : 'error';
    onUpdate?.();
  }

  async fetchMore(count: number, onUpdate?: () => void): Promise<void> {
    const available = MAX_POOL_SIZE - this.entries.length;
    if (available <= 0) return;

    const toFetch = Math.min(count, available);
    this.status = 'loading';
    onUpdate?.();

    const results = await Promise.allSettled(
      Array.from({ length: toFetch }, () => this.fetchOne(this.lastQuery)),
    );

    for (const r of results) {
      if (r.status === 'fulfilled') {
        this.entries.push(r.value);
      }
    }

    if (this.entries.length > 0) this.status = 'ready';
    onUpdate?.();
  }

  async addFromSearch(
    hits: { id: string; filename: string }[],
    onUpdate?: () => void,
  ): Promise<void> {
    const available = MAX_POOL_SIZE - this.entries.length;
    if (available <= 0) return;

    const toAdd = hits.slice(0, available);
    this.status = 'loading';
    onUpdate?.();

    const results = await Promise.allSettled(
      toAdd.map((hit) => this.fetchById(hit.id, hit.filename)),
    );

    for (const r of results) {
      if (r.status === 'fulfilled') {
        this.entries.push(r.value);
      }
    }

    if (this.entries.length > 0) this.status = 'ready';
    onUpdate?.();
  }

  next(
    cadence: Cadence,
    pickMode: PickMode,
    isBarStart: boolean,
    barCount: number,
  ): AudioBuffer | null {
    if (this.entries.length === 0) return null;

    const mod = CADENCE_MOD[cadence] ?? 0;
    const shouldAdvance =
      cadence === 'hit' || (isBarStart && mod > 0 && barCount % mod === 0);

    if (shouldAdvance) {
      if (pickMode === 'seq') this.advanceIndex();
      else this.randomIndex();
    }

    const entry = this.currentEntry();
    if (entry) this.currentName = entry.name;
    return entry?.buffer ?? null;
  }

  private advanceIndex(): void {
    for (let i = 0; i < this.entries.length; i++) {
      this.index = (this.index + 1) % this.entries.length;
      if (this.entries[this.index] !== null) return;
    }
  }

  private randomIndex(): void {
    const valid = this.entries.reduce<number[]>((acc, e, i) => {
      if (e !== null) acc.push(i);
      return acc;
    }, []);
    if (valid.length === 0) return;
    this.index = valid[Math.floor(Math.random() * valid.length)];
  }

  private currentEntry(): PoolEntry | null {
    return this.entries[this.index] ?? null;
  }

  previewBuffer(index?: number): AudioBuffer | null {
    if (index != null) {
      return this.entries[index]?.buffer ?? null;
    }
    return this.currentEntry()?.buffer ?? null;
  }

  previewBufferByIdx(index: number): AudioBuffer | null {
    return this.entries[index]?.buffer ?? null;
  }

  togglePin(index: number): boolean {
    if (index < 0 || index >= this.entries.length) return false;
    if (!this.entries[index]) return false;

    if (this.pinnedIndexes.has(index)) {
      this.pinnedIndexes.delete(index);
      this.pins = this.pins.filter((n) => n !== this.entries[index]!.name);
      return false;
    } else {
      this.pinnedIndexes.add(index);
      this.pins.push(this.entries[index]!.name);
      return true;
    }
  }

  getPinnedNames(): string[] {
    return Array.from(this.pinnedIndexes)
      .map((i) => this.entries[i]?.name)
      .filter(Boolean) as string[];
  }

  getActiveIndex(): number {
    return this.index;
  }

  removeEntry(index: number): void {
    if (index < 0 || index >= this.entries.length) return;
    this.pinnedIndexes.delete(index);

    this.entries = this.entries.filter((_, i) => i !== index);

    const newPinned: Set<number> = new Set();
    for (const pi of this.pinnedIndexes) {
      if (pi > index) newPinned.add(pi - 1);
      else newPinned.add(pi);
    }
    this.pinnedIndexes = newPinned;
    this.pins = this.getPinnedNames();

    if (this.index >= this.entries.length) {
      this.index = this.entries.length > 0 ? 0 : 0;
    }
  }

  moveEntry(fromIndex: number, toIndex: number): void {
    if (
      fromIndex < 0 ||
      fromIndex >= this.entries.length ||
      toIndex < 0 ||
      toIndex >= this.entries.length ||
      fromIndex === toIndex
    )
      return;

    const entry = this.entries[fromIndex];
    this.entries = this.entries.filter((_, i) => i !== fromIndex);
    this.entries = [
      ...this.entries.slice(0, toIndex),
      entry,
      ...this.entries.slice(toIndex),
    ];

    const newPinned: Set<number> = new Set();
    for (const pi of this.pinnedIndexes) {
      let idx = pi;
      if (pi === fromIndex) {
        idx = toIndex;
      } else {
        if (fromIndex < toIndex) {
          if (pi > fromIndex && pi <= toIndex) idx = pi - 1;
        } else {
          if (pi >= toIndex && pi < fromIndex) idx = pi + 1;
        }
      }
      newPinned.add(idx);
    }
    this.pinnedIndexes = newPinned;
    this.pins = this.getPinnedNames();
  }

  repinFromNames(names: string[]): void {
    this.pinnedIndexes = new Set();
    this.pins = [];
    for (const name of names) {
      const idx = this.entries.findIndex((e) => e?.name === name);
      if (idx !== -1) {
        this.pinnedIndexes.add(idx);
        this.pins.push(name);
      }
    }
  }

  get entryNames(): string[] {
    return this.entries
      .filter((e): e is PoolEntry => e !== null)
      .map((e) => e.name);
  }

  private async fetchOne(query: string): Promise<PoolEntry> {
    const url = `/api/serve?output_index=samples-bored&query=${encodeURIComponent(query)}&sort=random`;
    const headers: Record<string, string> = {};
    const key = import.meta.env.VITE_AU_API_KEY as string | undefined;
    if (key) headers['Authorization'] = `Bearer ${key}`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

    let response: Response;
    try {
      response = await fetch(url, {
        credentials: 'include',
        redirect: 'follow',
        headers,
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timeout);
      console.error(`[litany-exp] fetch failed for query "${query}":`, err);
      throw err;
    }
    clearTimeout(timeout);

    if (!response.ok) {
      console.error(
        `[litany-exp] HTTP ${response.status} for query "${query}"`,
      );
      throw new Error(`HTTP ${response.status}`);
    }

    const disposition = response.headers.get('content-disposition') ?? '';
    const nameMatch = disposition.match(/filename="([^"]+)"/);
    const name = nameMatch?.[1] ?? query;

    const arrayBuffer = await response.arrayBuffer();
    const buffer = await this.decodeAudio(arrayBuffer);
    return { buffer, name, source: 'query' };
  }

  private async fetchById(
    mediaId: string,
    filename: string,
  ): Promise<PoolEntry> {
    const url = `/api/media/${encodeURIComponent(mediaId)}/file`;
    const headers: Record<string, string> = {};
    const key = import.meta.env.VITE_AU_API_KEY as string | undefined;
    if (key) headers['Authorization'] = `Bearer ${key}`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

    let response: Response;
    try {
      response = await fetch(url, {
        credentials: 'include',
        headers,
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timeout);
      console.error(`[litany-exp] fetch failed for id "${mediaId}":`, err);
      throw err;
    }
    clearTimeout(timeout);

    if (!response.ok) {
      console.error(`[litany-exp] HTTP ${response.status} for id "${mediaId}"`);
      throw new Error(`HTTP ${response.status}`);
    }

    const arrayBuffer = await response.arrayBuffer();
    const buffer = await this.decodeAudio(arrayBuffer);
    return { buffer, name: filename, mediaId, source: 'manual' };
  }

  private async decodeAudio(arrayBuffer: ArrayBuffer): Promise<AudioBuffer> {
    try {
      return await this.decodeCtx.decodeAudioData(arrayBuffer);
    } catch {
      return await this.ctx.decodeAudioData(arrayBuffer);
    }
  }

  onBarStart(): void {
    this.barCount++;
  }

  get barNumber(): number {
    return this.barCount;
  }
}
