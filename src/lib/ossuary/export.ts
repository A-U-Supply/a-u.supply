// Ossuary — export.
//
// Phase 6: the real product is a tagged sample in samples-bored; ZIP is a
// convenience for external samplers. Both consume baked (rendered + WAV-encoded)
// hits.
//
// NOTE (server contract): `_index_for_media_item` routes by source_type, not
// output_index — a `manual_upload`-only item lands in *emulsion* no matter what
// output_index says. /api/media/upload takes `source_type=sample_library`
// exactly for us; without it every indexed sample is stranded (stored in
// emulsion, filtered as samples-bored — findable by neither). Uploads made
// before this was sent can be repaired with `manage.py reroute-sample-uploads`.

import { zipSync } from 'fflate';
import { authHeaders } from './source.ts';

const SAMPLES_INDEX = 'samples-bored';

export function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'ossuary'
  );
}

/** Upload one baked sample to the library. */
export async function indexSample(
  bytes: Uint8Array,
  filename: string,
  tags: string[],
  description: string,
): Promise<void> {
  const fd = new FormData();
  fd.append('file', new Blob([bytes], { type: 'audio/wav' }), filename);
  fd.append('tags', tags.join(','));
  fd.append('description', description);
  fd.append('output_index', SAMPLES_INDEX);
  // Routes the item to the samples index — see NOTE at the top of this file.
  fd.append('source_type', 'sample_library');
  // Don't set Content-Type — the browser adds the multipart boundary.
  const res = await fetch('/api/media/upload', {
    method: 'POST',
    credentials: 'include',
    headers: authHeaders(),
    body: fd,
  });
  if (!res.ok) {
    throw new Error(`upload failed (${filename}): HTTP ${res.status}`);
  }
}

/** Bundle named WAV byte-arrays into a ZIP blob. */
export function zipSamples(files: Record<string, Uint8Array>): Blob {
  return new Blob([zipSync(files)], { type: 'application/zip' });
}

/**
 * Standard Ossuary tag set for a carved hit. Dry hits (carved from the raw
 * source clip, no brain pass) honestly carry no model/rave lineage — `dry` and
 * `interpreted` are flat searchable discriminators.
 */
export function sampleTags(
  slot: string,
  model: string,
  kit: string,
  origin: 'source' | 'interpreted' = 'interpreted',
): string[] {
  const base = [
    'source:ossuary',
    // Bare slot name matches the existing library's flat-tag convention, so
    // `query=phrase` / `query=kick` filter the same way everywhere (issue #514).
    slot,
    `slot:${slot}`,
    `kit:${kit}`,
    'carved',
  ];
  return origin === 'source'
    ? [...base, 'dry']
    : [...base, `model:${model}`, 'interpreted', 'rave', 'rotten', 'rgz-9'];
}
