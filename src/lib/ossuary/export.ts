// Ossuary — export.
//
// Phase 6: the real product is a tagged sample in samples-bored; ZIP is a
// convenience for external samplers. Both consume baked (rendered + WAV-encoded)
// hits.
//
// NOTE (server dependency): /api/media/upload records a `manual_upload` source,
// which `_index_for_media_item` routes to the *emulsion* index — NOT
// samples-bored (that needs a `sample_library` source). We send
// output_index=samples-bored + the right tags so the moment the server routes
// Ossuary uploads as samples (a small server hook), discovery in Litany works.

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

/** Standard Ossuary tag set for a carved hit. */
export function sampleTags(slot: string, model: string, kit: string): string[] {
  return [
    'source:ossuary',
    // Bare slot name matches the existing library's flat-tag convention, so
    // `query=phrase` / `query=kick` filter the same way everywhere (issue #514).
    slot,
    `slot:${slot}`,
    `model:${model}`,
    `kit:${kit}`,
    'carved',
    'rave',
    'rotten',
    'rgz-9',
  ];
}
