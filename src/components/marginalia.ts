/*
 * Marginalia — shared helpers for timestamped comments + cue markers.
 *
 * The Player panel, the media-detail Marginalia section, and the Latent
 * badge popovers all talk to the same /api/media/{id}/annotations endpoints
 * and queue seeks through the persistent player. Keeping the endpoint
 * wrappers + the player bridge here avoids three copies of the midi/seek
 * special-casing.
 */

export type AnnotationAuthor = { id: number; name: string } | null;

export type Annotation = {
  id: string;
  media_item_id: string;
  parent_id: string | null;
  kind: 'comment' | 'cue';
  source: string;
  position_seconds: number;
  label: string | null;
  body: string | null;
  author: AnnotationAuthor;
  resolved: boolean;
  resolved_at: string | null;
  touched_by_user: boolean;
  created_at: string | null;
  updated_at: string | null;
  replies: Annotation[];
};

export type AnnotationBundle = {
  annotations: Annotation[];
  inherited: Annotation[];
  parent: { id: string; filename: string } | null;
};

export type AnnotationCounts = Record<
  string,
  { comments: number; cues: number; unresolved: number }
>;

/** Marginalia search-index document (GET /api/annotations). */
export type MarginaliaDoc = {
  id: string;
  media_item_id: string;
  kind: 'comment' | 'cue';
  source: string;
  position_seconds: number;
  label: string | null;
  body: string | null;
  author_name: string | null;
  resolved: boolean;
  media_type: string | null;
  filename: string | null;
  created_at: number | string | null;
};

/* ── timestamps ────────────────────────────────────────────────────────── */

export function fmtTimestamp(secs: number | null | undefined): string {
  if (secs == null || !isFinite(secs)) return '0:00';
  const total = Math.max(0, Math.floor(secs));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** Parse "ss", "m:ss" or "h:mm:ss" into seconds. Null when unparseable. */
export function parseTimestamp(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  if (/^\d+(\.\d+)?$/.test(t)) return parseFloat(t);
  const m = t.match(/^(?:(\d+):)?([0-5]?\d):([0-5]\d)$/);
  if (!m) return null;
  const h = m[1] ? parseInt(m[1], 10) : 0;
  return h * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3], 10);
}

/* ── body [mm:ss] seek links ───────────────────────────────────────────── */

export type BodyPart = { text: string } | { seconds: number; label: string };

const TS_LINK_RE = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;

/** Split a comment body into plain text and [mm:ss] seek-link parts. */
export function linkifyTimestamps(body: string): BodyPart[] {
  const parts: BodyPart[] = [];
  let last = 0;
  for (const match of body.matchAll(TS_LINK_RE)) {
    const idx = match.index ?? 0;
    if (idx > last) parts.push({ text: body.slice(last, idx) });
    const seconds = parseTimestamp(match[1]);
    if (seconds == null) parts.push({ text: match[0] });
    else parts.push({ seconds, label: match[1] });
    last = idx + match[0].length;
  }
  if (last < body.length) parts.push({ text: body.slice(last) });
  return parts;
}

/* ── display labels ────────────────────────────────────────────────────── */

export function sourceLabel(source: string): string {
  return (source || 'cue').replace(/_/g, ' ');
}

/** Who to credit on a row: the author for human notes, the import source
 *  for harvested cues ("wav cue", "midi", "logic"). */
export function whoLabel(a: {
  kind: string;
  source: string;
  author?: AnnotationAuthor;
}): string {
  if (a.kind === 'comment') return a.author?.name || 'comment';
  if (a.source === 'user') return a.author?.name || 'marker';
  return sourceLabel(a.source);
}

export function excerpt(
  a: { label?: string | null; body?: string | null },
  n = 80,
): string {
  const text = (a.label || a.body || '').trim().replace(/\s+/g, ' ');
  return text.length > n ? text.slice(0, n).trimEnd() + '…' : text;
}

/** Relative time for index docs (unix seconds) and API rows (ISO strings). */
export function relTime(v: string | number | null | undefined): string {
  if (v == null) return '';
  const ms = typeof v === 'number' ? v * 1000 : new Date(v).getTime();
  if (!isFinite(ms)) return '';
  const mins = Math.round((Date.now() - ms) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return new Date(ms).toLocaleDateString();
}

/* ── API ───────────────────────────────────────────────────────────────── */

async function request(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(path, {
    credentials: 'include',
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers || {}),
    },
  });
}

async function errorDetail(res: Response): Promise<string> {
  const err = await res.json().catch(() => ({}));
  return err?.detail?.message || err?.detail || `Failed (${res.status})`;
}

export async function fetchAnnotations(
  mediaId: string,
): Promise<AnnotationBundle> {
  const res = await request(
    `/api/media/${encodeURIComponent(mediaId)}/annotations`,
  );
  if (!res.ok) throw new Error(await errorDetail(res));
  const body = await res.json();
  return {
    annotations: body.annotations || [],
    inherited: body.inherited || [],
    parent: body.parent || null,
  };
}

/** Badge counts for a batch of items. Fails soft — badges are decorative. */
export async function fetchAnnotationCounts(
  mediaIds: string[],
): Promise<AnnotationCounts> {
  const ids = [...new Set(mediaIds.filter(Boolean))];
  if (!ids.length) return {};
  try {
    const res = await request(
      `/api/media/annotations/counts?media_ids=${ids.map(encodeURIComponent).join(',')}`,
    );
    if (!res.ok) return {};
    const body = await res.json();
    return body.counts || {};
  } catch {
    return {};
  }
}

export type AnnotationWrite = {
  kind: 'comment' | 'cue';
  position_seconds: number;
  body?: string;
  label?: string;
  parent_id?: string;
};

export async function createAnnotation(
  mediaId: string,
  payload: AnnotationWrite,
): Promise<Annotation> {
  const res = await request(
    `/api/media/${encodeURIComponent(mediaId)}/annotations`,
    { method: 'POST', body: JSON.stringify(payload) },
  );
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function updateAnnotation(
  id: string,
  payload: { body?: string; label?: string; position_seconds?: number },
): Promise<Annotation> {
  const res = await request(`/api/annotations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function toggleResolveAnnotation(id: string): Promise<Annotation> {
  const res = await request(
    `/api/annotations/${encodeURIComponent(id)}/resolve`,
    { method: 'POST' },
  );
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function deleteAnnotation(id: string): Promise<void> {
  const res = await request(`/api/annotations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(await errorDetail(res));
}

/* ── player bridge ─────────────────────────────────────────────────────── */

export type PlayerState = {
  track_id: string | null;
  media_type: string | null;
  currentTime: number;
  duration: number;
};

/*
 * Synchronous request/response over the event bus: Player.svelte answers
 * `player:time-request` by dispatching `player:time` inside the same
 * dispatchEvent tick, so the listener below fires before dispatchEvent
 * returns. Returns null when no player is mounted.
 */
export function queryPlayerState(): PlayerState | null {
  let out: PlayerState | null = null;
  const handler = (e: Event) => {
    out = (e as CustomEvent).detail as PlayerState;
  };
  document.addEventListener('player:time', handler);
  document.dispatchEvent(new CustomEvent('player:time-request'));
  document.removeEventListener('player:time', handler);
  return out;
}

/**
 * Build the exact track payload the Latents islands use (midi streams its
 * synthesized WAV preview) and queue it in the persistent player, with a
 * start_time the player seeks to once metadata loads.
 */
export function queueMediaTrack(
  mediaId: string,
  mediaType: string,
  filename: string,
  startTime?: number,
): void {
  document.dispatchEvent(
    new CustomEvent('player:queue', {
      detail: {
        tracks: [
          {
            track_id: mediaId,
            title: filename || 'Untitled',
            release_title: '',
            release_code: '',
            media_type: mediaType,
            stream_url:
              mediaType === 'midi'
                ? `/api/media/${encodeURIComponent(mediaId)}/audio`
                : `/api/media/${encodeURIComponent(mediaId)}/file`,
            cover_url:
              mediaType === 'image' || mediaType === 'video'
                ? `/api/media/${encodeURIComponent(mediaId)}/thumbnail`
                : '/assets/default-cover.jpg',
            duration: 0,
            entity_name: '',
          },
        ],
        startIndex: 0,
        ...(startTime != null && startTime > 0
          ? { start_time: startTime }
          : {}),
      },
    }),
  );
}

/**
 * Seek to an annotation: a bare `player:seek` when the item is already the
 * player's current track (no reload), else queue it with a start_time.
 */
export function seekAnnotation(
  mediaId: string,
  mediaType: string,
  filename: string,
  seconds: number,
): void {
  const st = queryPlayerState();
  if (st && st.track_id === mediaId) {
    document.dispatchEvent(
      new CustomEvent('player:seek', { detail: { seconds } }),
    );
  } else {
    queueMediaTrack(mediaId, mediaType, filename, seconds);
  }
}
