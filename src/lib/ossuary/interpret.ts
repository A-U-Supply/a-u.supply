// Ossuary — the interpreter (one rotten pass).
//
// Phase 2: submit a single-input single_pass job to the rottengenizdat bot,
// poll it to completion, and fetch the decoded wet WAV. One job at a time;
// the caller enforces that by disabling INTERPRET while a pass is in flight.

import { authHeaders } from './source.ts';

const APP_NAME = 'rottengenizdat';
const POLL_INTERVAL_MS = 2000;

export const MODELS = [
  'percussion',
  'vintage',
  'nasa',
  'VCTK',
  'musicnet',
  'isis',
  'sol_ordinario',
  'sol_full',
  'darbouka_onnx',
] as const;

export type Model = (typeof MODELS)[number];

// Glossary blurbs — adapted from the param descriptions in
// apps/rottengenizdat.toml (the bot manifest is the source of truth for what
// each checkpoint was trained on).
export const MODEL_NOTES: Record<Model, string> = {
  percussion:
    'Trained on drums; pushes everything toward rhythmic transients. The default brain for kit carving.',
  vintage: 'Warm analog color — tape saturation and old-console character.',
  nasa: 'Alien-transmission artifacts: static, telemetry, deep-space interference.',
  VCTK: 'A speech brain; makes anything babble.',
  musicnet: 'Classical chamber timbres — strings, winds, piano bleed.',
  isis: 'Middle Eastern string resonance; long ringing sustains.',
  sol_ordinario: 'Bowed strings, plain ordinario sustains only.',
  sol_full: 'The full bowed-string palette, extended techniques included.',
  darbouka_onnx: 'Hand drum — goblet-drum attack and slap.',
};

export interface InterpretParams {
  model: Model;
  temperature: number;
  noise: number;
  mix: number;
  dims: string;
  shuffle: number;
  quantize: number;
  reverse: boolean;
}

export function defaultParams(): InterpretParams {
  return {
    model: 'percussion',
    temperature: 1.0,
    noise: 0.0,
    mix: 1.0,
    dims: '',
    shuffle: 0.0,
    quantize: 0.0,
    reverse: false,
  };
}

export type InterpretPhase = 'submitting' | 'queued' | 'running' | 'fetching';

interface JobStatus {
  id: string;
  status: string;
  error_message: string | null;
  output_count: number;
}

async function submitJob(
  mediaItemId: string,
  params: InterpretParams,
): Promise<string> {
  const res = await fetch('/api/jobs', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      app_name: APP_NAME,
      media_item_ids: [mediaItemId],
      params: { processing_mode: 'single_pass', ...params },
    }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : detail;
    } catch {}
    throw new Error(`couldn't start interpret: ${detail}`);
  }
  return (await res.json()).id as string;
}

async function getJob(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/api/jobs/${jobId}`, {
    credentials: 'include',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`job status failed: HTTP ${res.status}`);
  const data = await res.json();
  return data.job as JobStatus;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function fetchWetBuffer(
  jobId: string,
  ctx: AudioContext,
): Promise<AudioBuffer> {
  const res = await fetch(`/api/jobs/${jobId}/outputs`, {
    credentials: 'include',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`couldn't list outputs: HTTP ${res.status}`);
  const outputs: Array<{ id: string; media_type: string }> = await res.json();
  const audio = outputs.find((o) => o.media_type === 'audio') ?? outputs[0];
  if (!audio) throw new Error('interpret produced no output');

  const fileRes = await fetch(
    `/api/jobs/${jobId}/outputs/${audio.id}/download`,
    { credentials: 'include', headers: authHeaders() },
  );
  if (!fileRes.ok)
    throw new Error(`couldn't fetch output: HTTP ${fileRes.status}`);
  return ctx.decodeAudioData(await fileRes.arrayBuffer());
}

export async function interpret(
  mediaItemId: string,
  params: InterpretParams,
  ctx: AudioContext,
  onPhase: (phase: InterpretPhase) => void,
  cancelled: () => boolean = () => false,
): Promise<AudioBuffer | null> {
  let jobId: string | null = null;

  onPhase('submitting');
  try {
    jobId = await submitJob(mediaItemId, params);
  } catch (e) {
    const msg = (e as Error).message;
    const detail = msg.replace("couldn't start interpret: ", '');
    throw new Error(
      `couldn't start interpret: ${detail}\nJob may have been created — check /admin/jobs`,
    );
  }

  onPhase('queued');
  while (!cancelled()) {
    try {
      const job = await getJob(jobId);
      if (job.status === 'completed') break;
      if (job.status === 'failed' || job.status === 'cancelled') {
        throw new Error(
          (job.error_message || `interpret ${job.status}`) +
            `\nJob: /admin/jobs/${jobId}`,
        );
      }
      onPhase(job.status === 'running' ? 'running' : 'queued');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes('HTTP 502') || msg.includes('Failed to fetch')) {
        await sleep(POLL_INTERVAL_MS * 3);
        continue;
      }
      throw new Error(`${msg}\nJob: /admin/jobs/${jobId}`);
    }
    await sleep(POLL_INTERVAL_MS);
  }
  if (cancelled()) return null;

  onPhase('fetching');
  try {
    return await fetchWetBuffer(jobId, ctx);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(`${msg}\nJob: /admin/jobs/${jobId}`);
  }
}
