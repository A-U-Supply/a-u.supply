/**
 * filterTranslator — shared utility for translating the SearchFilterBar's
 * Filters type into API request bodies.
 *
 * Consumers:
 *   - /admin/search/index.astro (Stacks) — search body
 *   - /admin/hecatomb.astro (Hecatomb) — batch shuffle spec
 *   - PullFromIndex.svelte (Latents) — search body
 */

export type Filters = {
  types: string[];
  outputIndexes: string[];
  channels: string[];
  poster: string;
  jobApp: string;
  colorGroups: string[];
  preservedMultiColors: string[];
  dateFrom: string;
  dateTo: string;
  tagsText: string;
  reactionsMin: number;
  reactionsMax: number | null;
  tagsMin: number;
  tagsMax: number | null;
  hasTranscript: '' | 'yes' | 'no';
  hasText: '' | 'yes' | 'no';
  sortBy: string;
  includeEmulsion: boolean;
  voteScoreMin: number | null;
  voteScoreMax: number | null;
  upMin: number | null;
  upMax: number | null;
  downMin: number | null;
  downMax: number | null;
  myVotes: '' | 'up' | 'down' | 'any' | 'none';
  aiVibe: string[];
  aiColorTemperature: string[];
  aiColorCharacter: string[];
  hasAiDescription: '' | 'yes' | 'no';
  isScreenshot: '' | 'yes' | 'no';
  isMeme: '' | 'yes' | 'no';
  isPhoto: '' | 'yes' | 'no';
  isArtwork: '' | 'yes' | 'no';
  isAiGenerated: '' | 'yes' | 'no';
  hasHuman: '' | 'yes' | 'no';
  hasFace: '' | 'yes' | 'no';
  hasTextOverlay: '' | 'yes' | 'no';
  isNsfw: '' | 'yes' | 'no';
};

export const SORT_MAP: Record<string, string> = {
  newest: 'created_at:desc',
  oldest: 'created_at:asc',
  random: 'random',
  most_reactions: 'total_reaction_count:desc',
  acclaim: 'vote_score:desc',
  largest: 'file_size_bytes:desc',
  longest: 'duration_seconds:desc',
};

/**
 * Translate bar {@link Filters} → POST /api/search body.
 * The caller supplies `query` separately (owned by the page, not the bar).
 */
export function filtersToSearchBody(
  filters: Filters,
  query: string,
  opts?: { perPage?: number },
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    query,
    media_types: filters.types,
    sort: SORT_MAP[filters.sortBy] || null,
    filters: {} as Record<string, unknown>,
  };

  if (filters.includeEmulsion) body.include_emulsion = true;
  if (filters.channels.length)
    (body.filters as Record<string, unknown>).source_channels =
      filters.channels;
  if (filters.poster)
    (body.filters as Record<string, unknown>).poster = filters.poster;
  if (filters.outputIndexes.length)
    (body.filters as Record<string, unknown>).output_index =
      filters.outputIndexes;
  if (filters.colorGroups.length)
    (body.filters as Record<string, unknown>).color_group = filters.colorGroups;
  if (filters.dateFrom || filters.dateTo) {
    (body.filters as Record<string, unknown>).date_range = {
      from: filters.dateFrom || undefined,
      to: filters.dateTo || undefined,
    };
  }
  if (filters.tagsText) {
    (body.filters as Record<string, unknown>).tags = filters.tagsText
      .split(',')
      .map((t: string) => t.trim())
      .filter(Boolean);
  }

  const rxnRange: { min?: number; max?: number } = {};
  if (filters.reactionsMin > 0) rxnRange.min = filters.reactionsMin;
  if (filters.reactionsMax !== null) rxnRange.max = filters.reactionsMax;
  if (Object.keys(rxnRange).length)
    (body.filters as Record<string, unknown>).reaction_count = rxnRange;

  const tagRange: { min?: number; max?: number } = {};
  if (filters.tagsMin > 0) tagRange.min = filters.tagsMin;
  if (filters.tagsMax !== null) tagRange.max = filters.tagsMax;
  if (Object.keys(tagRange).length)
    (body.filters as Record<string, unknown>).tag_count = tagRange;

  if (filters.jobApp)
    (body.filters as Record<string, unknown>).job_app = filters.jobApp;
  if (filters.hasTranscript)
    (body.filters as Record<string, unknown>).has_transcript =
      filters.hasTranscript === 'yes';
  if (filters.hasText)
    (body.filters as Record<string, unknown>).has_text =
      filters.hasText === 'yes';

  const vsRange: { min?: number; max?: number } = {};
  if (filters.voteScoreMin !== null) vsRange.min = filters.voteScoreMin;
  if (filters.voteScoreMax !== null) vsRange.max = filters.voteScoreMax;
  if (Object.keys(vsRange).length)
    (body.filters as Record<string, unknown>).vote_score = vsRange;

  const upRange: { min?: number; max?: number } = {};
  if (filters.upMin !== null) upRange.min = filters.upMin;
  if (filters.upMax !== null) upRange.max = filters.upMax;
  if (Object.keys(upRange).length)
    (body.filters as Record<string, unknown>).up_count = upRange;

  const downRange: { min?: number; max?: number } = {};
  if (filters.downMin !== null) downRange.min = filters.downMin;
  if (filters.downMax !== null) downRange.max = filters.downMax;
  if (Object.keys(downRange).length)
    (body.filters as Record<string, unknown>).down_count = downRange;

  if (filters.myVotes)
    (body.filters as Record<string, unknown>).my_votes = filters.myVotes;

  if (filters.hasAiDescription)
    (body.filters as Record<string, unknown>).has_ai_description =
      filters.hasAiDescription === 'yes';
  if (filters.aiVibe?.length)
    (body.filters as Record<string, unknown>).ai_vibe = filters.aiVibe;
  if (filters.aiColorTemperature?.length)
    (body.filters as Record<string, unknown>).ai_color_temperature =
      filters.aiColorTemperature;
  if (filters.aiColorCharacter?.length)
    (body.filters as Record<string, unknown>).ai_color_character =
      filters.aiColorCharacter;

  const boolFlagMap: Array<[string, string]> = [
    ['isScreenshot', 'is_screenshot'],
    ['isMeme', 'is_meme'],
    ['isPhoto', 'is_photo'],
    ['isArtwork', 'is_artwork'],
    ['isAiGenerated', 'is_ai_generated'],
    ['hasHuman', 'has_human'],
    ['hasFace', 'has_face'],
    ['hasTextOverlay', 'has_text_overlay'],
    ['isNsfw', 'is_nsfw'],
  ];
  for (const [uiKey, apiKey] of boolFlagMap) {
    const v = (filters as any)[uiKey];
    if (v === 'yes') (body.filters as Record<string, unknown>)[apiKey] = true;
    else if (v === 'no')
      (body.filters as Record<string, unknown>)[apiKey] = false;
  }

  if (opts?.perPage) body.per_page = opts.perPage;

  return body;
}

/**
 * Translate bar {@link Filters} → BatchShuffleSpec for POST /api/jobs/batch.
 * Only includes fields the batch endpoint understands. Fields like sort,
 * votes, and AI vision are silently dropped (the batch endpoint doesn't
 * support them — nor should it).
 */
export function filtersToBatchShuffle(
  filters: Filters,
  query: string,
  opts: {
    excludeProcessedByApp: boolean;
    excludeProcessedByRecipe: boolean;
  },
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    query,
    source_channels: filters.channels,
    tags: filters.tagsText
      ? filters.tagsText
          .split(',')
          .map((t: string) => t.trim())
          .filter(Boolean)
      : [],
    output_index: filters.outputIndexes[0] || null,
    reaction_count_min: filters.reactionsMin > 0 ? filters.reactionsMin : null,
    tag_count_min: filters.tagsMin > 0 ? filters.tagsMin : null,
    exclude_processed_by_app: opts.excludeProcessedByApp,
    exclude_processed_by_recipe: opts.excludeProcessedByRecipe,
  };

  if (filters.dateFrom || filters.dateTo) {
    payload.date_range = {
      from: filters.dateFrom || undefined,
      to: filters.dateTo || undefined,
    };
  }
  if (filters.poster) payload.poster = filters.poster;
  if (filters.colorGroups.length) payload.color_group = filters.colorGroups;
  if (filters.reactionsMax !== null)
    payload.reaction_count_max = filters.reactionsMax;
  if (filters.tagsMax !== null) payload.tag_count_max = filters.tagsMax;
  if (filters.hasTranscript)
    payload.has_transcript = filters.hasTranscript === 'yes';
  if (filters.hasText) payload.has_text = filters.hasText === 'yes';

  return payload;
}
