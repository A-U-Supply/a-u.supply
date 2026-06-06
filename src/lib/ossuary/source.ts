// Ossuary — source acquisition.
//
// Phase 1: pick a clip to carve. A clip can come from the samples-bored index
// (search or random pull) or a local file upload. Everything here just gets us
// a decoded AudioBuffer + a name; the RAVE pass and carving come in later phases.

const SAMPLES_INDEX = 'samples-bored';

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const key = import.meta.env.VITE_AU_API_KEY as string | undefined;
  if (key) headers['Authorization'] = `Bearer ${key}`;
  return headers;
}

export interface SearchHit {
  id: string;
  filename: string;
  durationSeconds: number | null;
}

export interface LoadedClip {
  name: string;
  /** Media item id when the clip came from the library; null for uploads. */
  sourceId: string | null;
  buffer: AudioBuffer;
}

/** Search the samples-bored index. Returns lightweight hits for the picker list. */
export async function searchSamples(query: string): Promise<SearchHit[]> {
  const res = await fetch('/api/search', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      query,
      filters: { output_index: [SAMPLES_INDEX] },
      page: 1,
      per_page: 40,
    }),
  });
  if (!res.ok) throw new Error(`search failed: HTTP ${res.status}`);
  const data = await res.json();
  const hits: any[] = data.hits ?? data.results?.hits ?? [];
  return hits.map((h) => ({
    id: h.id,
    filename: h.filename ?? h.id,
    durationSeconds: h.duration_seconds ?? null,
  }));
}

/** Fetch a specific library clip by media id and decode it. */
export async function fetchClipById(
  id: string,
  name: string,
  ctx: AudioContext,
): Promise<LoadedClip> {
  const res = await fetch(`/api/media/${encodeURIComponent(id)}/file`, {
    credentials: 'include',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`fetch clip failed: HTTP ${res.status}`);
  const buffer = await decode(await res.arrayBuffer(), ctx);
  return { name, sourceId: id, buffer };
}

/** Pull one random matching clip straight from /api/serve (no id). */
export async function fetchRandomClip(
  query: string,
  ctx: AudioContext,
): Promise<LoadedClip> {
  const url = `/api/serve?output_index=${SAMPLES_INDEX}&query=${encodeURIComponent(
    query,
  )}&sort=random`;
  const res = await fetch(url, {
    credentials: 'include',
    redirect: 'follow',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`random pull failed: HTTP ${res.status}`);
  const disposition = res.headers.get('content-disposition') ?? '';
  const name =
    (disposition.match(/filename="([^"]+)"/)?.[1] ?? query) || 'random';
  const buffer = await decode(await res.arrayBuffer(), ctx);
  return { name, sourceId: null, buffer };
}

/** Decode an uploaded local file. */
export async function loadLocalFile(
  file: File,
  ctx: AudioContext,
): Promise<LoadedClip> {
  const buffer = await decode(await file.arrayBuffer(), ctx);
  return { name: file.name, sourceId: null, buffer };
}

async function decode(
  data: ArrayBuffer,
  ctx: AudioContext,
): Promise<AudioBuffer> {
  // decodeAudioData detaches the buffer; callers pass a fresh ArrayBuffer.
  return await ctx.decodeAudioData(data);
}

/** Peak (min/max) pairs per pixel column — the data a waveform canvas draws. */
export function computePeaks(
  buffer: AudioBuffer,
  width: number,
): Array<[number, number]> {
  const channel = buffer.getChannelData(0);
  const samplesPerColumn = Math.max(1, Math.floor(channel.length / width));
  const peaks: Array<[number, number]> = [];
  for (let x = 0; x < width; x++) {
    const start = x * samplesPerColumn;
    const end = Math.min(channel.length, start + samplesPerColumn);
    let min = 1.0;
    let max = -1.0;
    for (let i = start; i < end; i++) {
      const v = channel[i];
      if (v < min) min = v;
      if (v > max) max = v;
    }
    if (end <= start) {
      min = 0;
      max = 0;
    }
    peaks.push([min, max]);
  }
  return peaks;
}
