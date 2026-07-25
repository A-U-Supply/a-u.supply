/**
 * Building `player:queue` payloads for Emulsion media.
 *
 * Extracted from LatentSlots/marginalia so playlists can queue N tracks with
 * the same track shape a single ▶ button has always produced — the MIDI
 * preview branch in particular is easy to get wrong twice.
 *
 * See docs/player.md for the event contract.
 */

export interface QueueMedia {
  id: string;
  media_type: string;
  filename?: string | null;
  duration_seconds?: number | null;
}

export interface QueueTrack {
  track_id: string;
  title: string;
  release_title: string;
  release_code: string;
  media_type: string;
  stream_url: string;
  cover_url: string;
  duration: number;
  entity_name: string;
}

/** One media item as a player track. MIDI streams its synthesized preview. */
export function buildQueueTrack(media: QueueMedia): QueueTrack {
  const id = encodeURIComponent(media.id);
  return {
    track_id: media.id,
    title: media.filename || 'Untitled',
    release_title: '',
    release_code: '',
    media_type: media.media_type,
    stream_url:
      media.media_type === 'midi'
        ? `/api/media/${id}/audio`
        : `/api/media/${id}/file`,
    cover_url:
      media.media_type === 'image' || media.media_type === 'video'
        ? `/api/media/${id}/thumbnail`
        : '/assets/default-cover.jpg',
    duration: media.duration_seconds || 0,
    entity_name: '',
  };
}

/**
 * Queue tracks in the persistent player, starting at `startIndex`.
 * `startTime` seeks once metadata loads (used by Marginalia).
 */
export function queueMedia(
  media: QueueMedia[],
  startIndex = 0,
  startTime?: number,
): void {
  if (!media.length) return;
  document.dispatchEvent(
    new CustomEvent('player:queue', {
      detail: {
        tracks: media.map(buildQueueTrack),
        startIndex: Math.max(0, Math.min(startIndex, media.length - 1)),
        ...(startTime != null && startTime > 0
          ? { start_time: startTime }
          : {}),
      },
    }),
  );
}
