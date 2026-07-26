/**
 * Opening the media viewer from inside a Latent.
 *
 * Latents was the only admin surface that never opened the lightbox — an
 * image was a 32px thumbnail, and clicking it navigated you away to Stacks.
 * Every other surface (search, workspace, bookmarks, midden, slop, jobs) has
 * had `openImageViewer` since v1.
 *
 * The Latent flavour differs from those in two ways, both deliberate:
 *
 * 1. **No actions.** `ViewerActions` are all optional and each one adds a
 *    toolbar button. Latents passes none, so the toolbar is just zoom,
 *    fullscreen and download. Bookmark/workspace/index/discard belong to the
 *    triage surfaces, not to a workspace you already curated by hand.
 * 2. **Auto-hiding chrome**, so a still frame is nothing but the picture.
 *
 * A Latent item carries images, video, audio, MIDI, sessions and documents.
 * Only images and video are worth a lightbox — audio has the persistent
 * Player, and the rest have no visual form — so `isViewable` gates both the
 * reel's membership and whether a thumbnail is clickable at all.
 */

import { mediaItemToViewerItem } from './admin-viewer';
import { openImageViewer } from './image-viewer';

/** The media types a Latent slideshow shows. */
export function isViewable(mediaType?: string | null): boolean {
  return mediaType === 'image' || mediaType === 'video';
}

/** The subset of a Latent item this module needs. Both callers already have it. */
export type LatentViewable = {
  media_item_id: string;
  media?: {
    filename?: string | null;
    media_type?: string | null;
  } | null;
};

/**
 * Open the viewer over `items`, starting on `startMediaId`.
 *
 * `items` is passed in whatever order the caller shows on screen — file order
 * today, a saved slideshow order once that ships — and non-viewable entries
 * are dropped, so the reel matches what the eye expects to arrow through.
 * A no-op if nothing viewable survives.
 */
export function openLatentViewer(
  items: LatentViewable[],
  startMediaId: string,
): void {
  const reel = items.filter((it) => isViewable(it.media?.media_type));
  if (reel.length === 0) return;
  const start = Math.max(
    0,
    reel.findIndex((it) => it.media_item_id === startMediaId),
  );
  openImageViewer(
    reel.map((it) =>
      mediaItemToViewerItem({
        id: it.media_item_id,
        filename: it.media?.filename || undefined,
        media_type: it.media?.media_type || undefined,
      }),
    ),
    start,
    {}, // no actions — see the header
    { chrome: 'auto-hide' },
  );
}
