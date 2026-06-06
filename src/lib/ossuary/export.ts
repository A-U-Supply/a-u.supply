// Ossuary — export.
//
// Phase 6: the real product is a tagged sample in samples-bored; ZIP is a
// convenience for external samplers. Both consume baked (rendered + WAV-encoded)
// hits.
//
// NOTE: /api/media/upload accepts source_type. We send "sample_library" so
// _index_for_media_item routes uploads to samples-bored, same as sounds-bored.

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

/** Standard Ossuary tag set for a carved hit. */
export function sampleTags(slot: string, model: string, kit: string): string[] {
  return [
    'source:ossuary',
    `slot:${slot}`,
    `model:${model}`,
    `kit:${kit}`,
    'carved',
    'rave',
    'rotten',
    'rgz-9',
  ];
}
