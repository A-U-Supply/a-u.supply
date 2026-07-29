/**
 * One place that knows how to ask for a media thumbnail.
 *
 * Two mistakes kept getting copy-pasted across the Latents tree, so both
 * answers live here now and `npm run lint:design` fails on a raw
 * `/thumbnail?size=` written anywhere else:
 *
 * 1. Every caller asked for `sm` — the 128px variant that
 *    `server/extraction.py` documents as "mobile grids / narrow strips",
 *    not the 400px `md` it documents for "desktop grid tiles". A 160px
 *    grid tile on a 2x display needs 320px and was being handed 128.
 * 2. Every caller gated the `<img>` on `media_type === 'image'`, so video
 *    fell through to an extension chip — even though the backend has
 *    grabbed a frame at ~10% duration since extraction shipped, and
 *    `_resolve_thumbnail_path()` returns it ahead of everything else.
 *
 * Sizes still vary a lot per call site (28px style chip → 160px+ tile), so
 * images get a srcset and let the browser pick; only the `sizes` hint
 * changes between callers.
 */

/** Thumbnail variants the media endpoint understands. */
export type ThumbSize = 'sm' | 'md' | 'lg';

/** Max edge of each variant, per `server/extraction.py`. */
const THUMB_WIDTH: Record<ThumbSize, number> = {
  sm: 128,
  md: 400,
  lg: 1600,
};

/** Media types the thumbnail endpoint can render a real picture for. */
export function hasThumb(mediaType?: string | null): boolean {
  return mediaType === 'image' || mediaType === 'video';
}

/**
 * URL for one variant, from an arbitrary endpoint base. Job outputs serve
 * their own `/thumbnail` (generated on first hit) until they're indexed and
 * get a MediaItem, so the base isn't always `/api/media/{id}`.
 */
export function thumbUrlFrom(base: string, size: ThumbSize = 'md'): string {
  return `${base}/thumbnail?size=${size}`;
}

/** URL for one variant. Prefer {@link thumbAttrs} unless you need a bare URL. */
export function thumbUrl(mediaId: string, size: ThumbSize = 'md'): string {
  return thumbUrlFrom(`/api/media/${encodeURIComponent(mediaId)}`, size);
}

export interface ThumbAttrs {
  src: string;
  srcset?: string;
  sizes?: string;
}

/**
 * Spreadable `<img>` attributes for a thumbnail.
 *
 * `sizes` should describe the rendered box — a CSS length like `'64px'`, or
 * a media-query list for a responsive grid. It only matters for images:
 * video thumbnails are a single 640px frame grab that the endpoint returns
 * whatever `size` you ask for, so offering the browser a choice would just
 * invite it to fetch the same file under a wrong width descriptor.
 */
export function thumbAttrs(
  mediaId: string,
  mediaType?: string | null,
  sizes?: string,
): ThumbAttrs {
  if (mediaType === 'video') {
    return { src: thumbUrl(mediaId, 'md') };
  }
  const variants: ThumbSize[] = ['sm', 'md'];
  return {
    src: thumbUrl(mediaId, 'md'),
    srcset: variants
      .map((s) => `${thumbUrl(mediaId, s)} ${THUMB_WIDTH[s]}w`)
      .join(', '),
    ...(sizes ? { sizes } : {}),
  };
}
