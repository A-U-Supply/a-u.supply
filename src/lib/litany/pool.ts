import type { Rotation } from './state.ts';

const POOL_SIZE = 8;

export type PoolStatus = 'idle' | 'loading' | 'ready' | 'error';

interface PoolEntry {
  buffer: AudioBuffer;
  name: string;
}

export class SamplePool {
  private ctx: AudioContext;
  private entries: (PoolEntry | null)[] = [];
  private index = 0;
  private pinnedEntry: PoolEntry | null = null;
  private barCount = 0;

  status: PoolStatus = 'idle';
  currentName = '';

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
  }

  async fill(query: string, onUpdate?: () => void): Promise<void> {
    this.status = 'loading';
    this.entries = [];
    onUpdate?.();

    const results = await Promise.allSettled(
      Array.from({ length: POOL_SIZE }, () => this.fetchOne(query)),
    );

    this.entries = results.map((r) =>
      r.status === 'fulfilled' ? r.value : null,
    );
    this.index = 0;

    const first = this.entries.find((e) => e !== null);
    this.currentName = first?.name ?? '';
    this.status = first ? 'ready' : 'error';
    onUpdate?.();
  }

  private async fetchOne(query: string): Promise<PoolEntry> {
    const url = `/api/serve?output_index=samples-bored&query=${encodeURIComponent(query)}&sort=random`;
    const headers: Record<string, string> = {};
    const key = (import.meta as { env?: Record<string, string> }).env
      ?.VITE_AU_API_KEY;
    if (key) headers['Authorization'] = `Bearer ${key}`;

    const response = await fetch(url, {
      credentials: 'include',
      redirect: 'follow',
      headers,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const disposition = response.headers.get('content-disposition') ?? '';
    const nameMatch = disposition.match(/filename="([^"]+)"/);
    const name = nameMatch?.[1] ?? query;

    const arrayBuffer = await response.arrayBuffer();
    const buffer = await this.ctx.decodeAudioData(arrayBuffer);
    return { buffer, name };
  }

  next(
    rotation: Rotation,
    isBarStart: boolean,
    is4BarStart: boolean,
  ): AudioBuffer | null {
    if (rotation === 'pinned') {
      return this.pinnedEntry?.buffer ?? this.currentEntry()?.buffer ?? null;
    }

    if (this.entries.length === 0) return null;

    const shouldAdvance =
      rotation === 'every-hit' ||
      (rotation === 'every-bar' && isBarStart) ||
      (rotation === 'every-4bars' && is4BarStart);

    if (shouldAdvance) {
      this.advanceIndex();
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

  private currentEntry(): PoolEntry | null {
    return this.entries[this.index] ?? null;
  }

  pin(): void {
    this.pinnedEntry = this.currentEntry();
  }

  unpin(): void {
    this.pinnedEntry = null;
  }

  onBarStart(): void {
    this.barCount++;
  }

  get barNumber(): number {
    return this.barCount;
  }
}
