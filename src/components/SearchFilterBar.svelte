<!--
  SearchFilterBar — the single source of truth for /api/search filters.
  Consumed by:
    - src/pages/admin/search/index.astro (Stacks) via data-* + CustomEvents.
    - src/components/PullFromIndex.svelte (Latents) via bind:filters.

  Dual contract:
    - data-filters="<json>" on the wrapper for vanilla-JS readers.
    - filters-change CustomEvent (bubbles, detail.filters) on every change.
    - filters-set CustomEvent inbound (detail.patch) for partial state
      pokes from outside (e.g. an insights chart click).
    - filters-reset CustomEvent inbound (detail.key) for clearing a single
      field via an active-filter chip × button, or 'all' to clear everything.

  URL sync is opt-in via syncUrl prop. On mount with syncUrl=true, the
  bar reads the existing ?output_index=&types=… params; the bar does
  NOT push URL updates — the page owns history (matches today's behavior).

  Selection invariants:
    - outputIndexes always contains ≥1 entry (empty snaps to ['__inputs__']).
    - types empty means "any" (sent as [] to the backend, which treats it
      as all types).
-->
<script lang="ts">
  import { Select } from 'bits-ui';
  import { onMount, tick, untrack } from 'svelte';

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
    // Range filters — min and max counterparts paired in the UI.
    // `null` = open-ended for that bound. Reactions / tag-count use
    // number with 0 as "not set" for backwards compat with the URL
    // params (?rxn=0 was treated as none); the new max counterparts
    // use number|null since they didn't exist before.
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
  };

  type HideKey =
    | 'sort'
    | 'colors'
    | 'reactions'
    | 'tags-min'
    | 'dates'
    | 'channels'
    | 'posters'
    | 'tags-text'
    | 'votes';

  type Props = {
    filters?: Filters;
    initial?: Partial<Filters>;
    syncUrl?: boolean;
    hide?: HideKey[];
    onChange?: (next: Filters) => void;
  };

  let {
    filters = $bindable<Filters>(defaults()),
    initial,
    syncUrl = false,
    hide = [],
    onChange,
  }: Props = $props();

  function defaults(): Filters {
    return {
      types: ['image', 'audio', 'video'],
      outputIndexes: ['__inputs__'],
      channels: [],
      poster: '',
      jobApp: '',
      colorGroups: [],
      preservedMultiColors: [],
      dateFrom: '',
      dateTo: '',
      tagsText: '',
      reactionsMin: 0,
      reactionsMax: null,
      tagsMin: 0,
      tagsMax: null,
      hasTranscript: '',
      hasText: '',
      sortBy: 'newest',
      includeEmulsion: false,
      voteScoreMin: null,
      voteScoreMax: null,
      upMin: null,
      upMax: null,
      downMin: null,
      downMax: null,
      myVotes: '',
    };
  }

  const SORT_OPTIONS = [
    { value: 'newest', label: 'Newest' },
    { value: 'oldest', label: 'Oldest' },
    { value: 'random', label: 'Random' },
    { value: 'most_reactions', label: 'Most Reactions' },
    { value: 'acclaim', label: 'Acclaim' },
    { value: 'largest', label: 'Largest' },
    { value: 'longest', label: 'Longest' },
  ];

  const COLOR_GROUPS = [
    'red',
    'orange',
    'yellow',
    'green',
    'teal',
    'blue',
    'purple',
    'pink',
    'brown',
    'beige',
    'gray',
    'black',
    'white',
  ];

  // Facets (loaded from /api/search/facets).
  let dynamicIndexes = $state<string[]>([]);
  let channelOptions = $state<string[]>([]);
  let posterOptions = $state<string[]>([]);
  let jobAppOptions = $state<string[]>([]);

  let allIndexValues = $derived<string[]>([
    '__inputs__',
    '__emulsion__',
    ...dynamicIndexes,
  ]);

  let host: HTMLDivElement | null = $state(null);
  let mounted = $state(false);

  // ---------------------------------------------------------------------
  // URL parsing
  // ---------------------------------------------------------------------

  function parseUrlFilters(): Partial<Filters> {
    if (typeof window === 'undefined') return {};
    const p = new URLSearchParams(window.location.search);
    const out: Partial<Filters> = {};

    const rawIndex = p.get('output_index');
    if (rawIndex) {
      const parts = rawIndex
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean);
      if (parts.length) out.outputIndexes = parts;
    } else if (p.get('app')) {
      // Legacy: ?app=X without explicit output_index implies "outputs".
      out.outputIndexes = ['outputs'];
    }

    if (p.get('types')) {
      out.types = p
        .get('types')!
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
    }
    if (p.get('channel')) {
      out.channels = p.get('channel')!.split(',').filter(Boolean);
    }
    if (p.get('poster')) out.poster = p.get('poster')!;
    if (p.get('app')) out.jobApp = p.get('app')!;
    if (p.get('from')) out.dateFrom = p.get('from')!;
    if (p.get('to')) out.dateTo = p.get('to')!;

    // Tags: merge ?tag= and ?tags= into one string.
    const tagVals: string[] = [];
    if (p.get('tag')) tagVals.push(p.get('tag')!);
    if (p.get('tags')) {
      tagVals.push(
        ...p
          .get('tags')!
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
      );
    }
    if (tagVals.length) out.tagsText = [...new Set(tagVals)].join(', ');

    if (p.get('rxn')) {
      const n = parseInt(p.get('rxn')!, 10);
      if (!Number.isNaN(n)) out.reactionsMin = n;
    }
    if (p.get('rxnmax')) {
      const n = parseInt(p.get('rxnmax')!, 10);
      if (!Number.isNaN(n)) out.reactionsMax = n;
    }
    if (p.get('mintags')) {
      const n = parseInt(p.get('mintags')!, 10);
      if (!Number.isNaN(n)) out.tagsMin = n;
    }
    if (p.get('maxtags')) {
      const n = parseInt(p.get('maxtags')!, 10);
      if (!Number.isNaN(n)) out.tagsMax = n;
    }
    if (p.get('sort')) out.sortBy = p.get('sort')!;

    if (p.get('colorgroup')) {
      const groups = p
        .get('colorgroup')!
        .split(',')
        .map((g) => g.trim())
        .filter(Boolean);
      // Color is now a multi-select dropdown, so URL multi-color goes
      // straight into colorGroups instead of the preservedMultiColors
      // shadow state (which only existed because the UI was
      // single-select).
      if (groups.length) out.colorGroups = groups;
    }

    const t = p.get('transcript');
    if (t === 'yes' || t === 'no') out.hasTranscript = t;
    const o = p.get('ocr');
    if (o === 'yes' || o === 'no') out.hasText = o;

    if (p.get('vscore')) {
      const n = parseInt(p.get('vscore')!, 10);
      if (!Number.isNaN(n)) out.voteScoreMin = n;
    }
    if (p.get('vscoremax')) {
      const n = parseInt(p.get('vscoremax')!, 10);
      if (!Number.isNaN(n)) out.voteScoreMax = n;
    }
    if (p.get('upmin')) {
      const n = parseInt(p.get('upmin')!, 10);
      if (!Number.isNaN(n)) out.upMin = n;
    }
    if (p.get('upmax')) {
      const n = parseInt(p.get('upmax')!, 10);
      if (!Number.isNaN(n)) out.upMax = n;
    }
    if (p.get('downmin')) {
      const n = parseInt(p.get('downmin')!, 10);
      if (!Number.isNaN(n)) out.downMin = n;
    }
    if (p.get('downmax')) {
      const n = parseInt(p.get('downmax')!, 10);
      if (!Number.isNaN(n)) out.downMax = n;
    }
    const mv = p.get('myvotes');
    if (mv === 'up' || mv === 'down' || mv === 'any' || mv === 'none') {
      out.myVotes = mv;
    }

    return out;
  }

  // ---------------------------------------------------------------------
  // Facet loading
  // ---------------------------------------------------------------------

  async function loadFacets() {
    try {
      const res = await fetch('/api/search/facets', {
        credentials: 'include',
      });
      if (!res.ok) return;
      const data = await res.json();
      dynamicIndexes = Array.isArray(data?.output_indexes)
        ? data.output_indexes
        : [];
      channelOptions = Array.isArray(data?.channels) ? data.channels : [];
      posterOptions = Array.isArray(data?.uploaders) ? data.uploaders : [];
      jobAppOptions = Array.isArray(data?.job_apps) ? data.job_apps : [];

      // If the URL referenced an unknown index/poster/app/channel, keep it
      // as a valid option so the link still filters.
      const unknownIdx = filters.outputIndexes.filter(
        (v) =>
          v !== '__inputs__' &&
          v !== '__emulsion__' &&
          !dynamicIndexes.includes(v),
      );
      if (unknownIdx.length) {
        dynamicIndexes = [...new Set([...dynamicIndexes, ...unknownIdx])];
      }
      if (filters.poster && !posterOptions.includes(filters.poster)) {
        posterOptions = [...posterOptions, filters.poster];
      }
      if (filters.jobApp && !jobAppOptions.includes(filters.jobApp)) {
        jobAppOptions = [...jobAppOptions, filters.jobApp];
      }
      const unknownChannels = filters.channels.filter(
        (c) => !channelOptions.includes(c),
      );
      if (unknownChannels.length) {
        channelOptions = [...channelOptions, ...unknownChannels];
      }
    } catch {
      /* offline / no admin auth — defaults are enough to render */
    }
  }

  // ---------------------------------------------------------------------
  // Cross-filter derived state
  // ---------------------------------------------------------------------

  let jobAppDisabled = $derived(
    filters.outputIndexes.length === 1 &&
      filters.outputIndexes[0] === '__inputs__',
  );

  let hasAV = $derived(
    filters.types.length === 0 ||
      filters.types.includes('audio') ||
      filters.types.includes('video'),
  );
  let hasImage = $derived(
    filters.types.length === 0 || filters.types.includes('image'),
  );
  let hasTranscriptDisabled = $derived(!hasAV);
  let hasTextDisabled = $derived(!hasImage);

  // Force-clear disabled filters so they don't leak into requests.
  $effect(() => {
    if (jobAppDisabled && filters.jobApp) filters.jobApp = '';
  });
  $effect(() => {
    if (hasTranscriptDisabled && filters.hasTranscript)
      filters.hasTranscript = '';
  });
  $effect(() => {
    if (hasTextDisabled && filters.hasText) filters.hasText = '';
  });

  // ---------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------

  // Media-type buttons are pure toggles — every click adds or removes
  // the type from the set. Empty set means "all types" (same default
  // behaviour as not passing the filter). Was previously
  // click=single-switch / shift-click=multi, but the modifier-required
  // multi was a desktop-only affordance — mobile users had no way to
  // multi-select.
  function toggleType(t: string) {
    if (filters.types.includes(t)) {
      filters.types = filters.types.filter((x) => x !== t);
    } else {
      filters.types = [...filters.types, t];
    }
  }

  function handleIndexChange(next: string[]) {
    filters.outputIndexes = next.length ? next : ['__inputs__'];
    filters.includeEmulsion = filters.outputIndexes.includes('__emulsion__');
  }

  function handleChannelChange(next: string[]) {
    filters.channels = next;
  }

  function handleColorGroupChange(v: string) {
    filters.colorGroups = v ? [v] : [];
    filters.preservedMultiColors = [];
  }

  function handleColorMultiChange(next: string[]) {
    filters.colorGroups = next;
    filters.preservedMultiColors = [];
  }

  // ---------------------------------------------------------------------
  // Public API: reset(key | 'all')
  // ---------------------------------------------------------------------

  export function reset(key: keyof Filters | 'all') {
    if (key === 'all') {
      filters = defaults();
      return;
    }
    const d = defaults();
    (filters as any)[key] = (d as any)[key];
    if (key === 'outputIndexes') filters.includeEmulsion = false;
    if (key === 'colorGroups') filters.preservedMultiColors = [];
  }

  function applyPatch(patch: Partial<Filters>) {
    Object.assign(filters, patch);
    if ('outputIndexes' in patch) {
      filters.includeEmulsion = filters.outputIndexes.includes('__emulsion__');
    }
  }

  // ---------------------------------------------------------------------
  // Outward sync: data-filters + filters-change
  // ---------------------------------------------------------------------

  $effect(() => {
    // Track every field so this effect re-runs on any change.
    const snapshot: Filters = {
      types: [...filters.types],
      outputIndexes: [...filters.outputIndexes],
      channels: [...filters.channels],
      poster: filters.poster,
      jobApp: filters.jobApp,
      colorGroups: [...filters.colorGroups],
      preservedMultiColors: [...filters.preservedMultiColors],
      dateFrom: filters.dateFrom,
      dateTo: filters.dateTo,
      tagsText: filters.tagsText,
      reactionsMin: filters.reactionsMin,
      reactionsMax: filters.reactionsMax,
      tagsMin: filters.tagsMin,
      tagsMax: filters.tagsMax,
      hasTranscript: filters.hasTranscript,
      hasText: filters.hasText,
      sortBy: filters.sortBy,
      includeEmulsion: filters.includeEmulsion,
      voteScoreMin: filters.voteScoreMin,
      voteScoreMax: filters.voteScoreMax,
      upMin: filters.upMin,
      upMax: filters.upMax,
      downMin: filters.downMin,
      downMax: filters.downMax,
      myVotes: filters.myVotes,
    };
    if (!mounted) return;
    const wrapper = host;
    if (wrapper) {
      wrapper.dataset.filters = JSON.stringify(snapshot);
      wrapper.dispatchEvent(
        new CustomEvent('filters-change', {
          bubbles: true,
          detail: { filters: snapshot },
        }),
      );
    }
    untrack(() => onChange?.(snapshot));
  });

  // ---------------------------------------------------------------------
  // Mount
  // ---------------------------------------------------------------------

  onMount(async () => {
    const seed: Partial<Filters> = {
      ...(syncUrl ? parseUrlFilters() : {}),
      ...(initial ?? {}),
    };
    if (Object.keys(seed).length) applyPatch(seed);
    await tick();
    if (host) {
      host.addEventListener('filters-set', (e: Event) => {
        const patch = (e as CustomEvent).detail?.patch;
        if (patch && typeof patch === 'object') applyPatch(patch);
      });
      host.addEventListener('filters-reset', (e: Event) => {
        const key = (e as CustomEvent).detail?.key as
          | keyof Filters
          | 'all'
          | undefined;
        if (key) reset(key);
      });
    }
    mounted = true;
    loadFacets();
  });

  // ---------------------------------------------------------------------
  // Labels
  // ---------------------------------------------------------------------

  function indexLabel(v: string): string {
    if (v === '__inputs__') return 'Inputs';
    if (v === '__emulsion__') return 'Emulsion (Latents)';
    return v;
  }

  function indexTriggerLabel(sel: string[]): string {
    if (!sel.length) return 'Inputs';
    if (sel.length === 1) return indexLabel(sel[0]);
    if (sel.length === 2) return sel.map(indexLabel).join(', ');
    return `${indexLabel(sel[0])} +${sel.length - 1}`;
  }

  function channelTriggerLabel(sel: string[]): string {
    if (!sel.length) return 'All channels';
    if (sel.length === 1) return sel[0];
    if (sel.length === 2) return sel.join(', ');
    return `${sel[0]} +${sel.length - 1}`;
  }

  function colorTriggerLabel(sel: string[]): string {
    if (!sel.length) return 'Any color';
    const pretty = (s: string) => s[0].toUpperCase() + s.slice(1);
    if (sel.length === 1) return pretty(sel[0]);
    if (sel.length === 2) return sel.map(pretty).join(', ');
    return `${pretty(sel[0])} +${sel.length - 1}`;
  }

  // ---------------------------------------------------------------------
  // hide-section helpers
  // ---------------------------------------------------------------------
  let show = $derived({
    sort: !hide.includes('sort'),
    colors: !hide.includes('colors'),
    reactions: !hide.includes('reactions'),
    tagsMin: !hide.includes('tags-min'),
    dates: !hide.includes('dates'),
    channels: !hide.includes('channels'),
    posters: !hide.includes('posters'),
    tagsText: !hide.includes('tags-text'),
    votes: !hide.includes('votes'),
  });
</script>

<div bind:this={host} class="filter-bar" data-filters>
  <!-- Index -->
  <div class="fg">
    <label class="fg__label">Index</label>
    <Select.Root
      type="multiple"
      value={filters.outputIndexes}
      onValueChange={handleIndexChange}
    >
      <Select.Trigger
        class="fb-select__trigger brutalist-control"
        aria-label="Index filter"
      >
        <span class="fb-select__label"
          >{indexTriggerLabel(filters.outputIndexes)}</span
        >
        <span class="fb-select__caret" aria-hidden="true">▾</span>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content class="fb-select__content" sideOffset={6} align="start">
          <Select.Viewport class="fb-select__viewport">
            {#each allIndexValues as v (v)}
              <Select.Item
                class="fb-select__item"
                value={v}
                label={indexLabel(v)}
              >
                {#snippet children({ selected })}
                  <span class="fb-select__check" aria-hidden="true"
                    >{selected ? '✓' : ''}</span
                  >
                  <span class="fb-select__item-label">{indexLabel(v)}</span>
                {/snippet}
              </Select.Item>
            {/each}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  </div>

  <!-- Media Type -->
  <div class="fg">
    <label class="fg__label">Media Type</label>
    <div class="type-switches" role="group" aria-label="Media types">
      {#each ['image', 'audio', 'video'] as t (t)}
        <button
          class="type-switch"
          class:active={filters.types.includes(t)}
          type="button"
          aria-pressed={filters.types.includes(t)}
          onclick={() => toggleType(t)}
          title={`Toggle ${t}`}
        >
          {t}
        </button>
      {/each}
    </div>
  </div>

  {#if show.channels}
    <div class="fg">
      <label class="fg__label">Source Channel</label>
      <Select.Root
        type="multiple"
        value={filters.channels}
        onValueChange={handleChannelChange}
      >
        <Select.Trigger
          class="fb-select__trigger brutalist-control"
          aria-label="Channel filter"
        >
          <span class="fb-select__label"
            >{channelTriggerLabel(filters.channels)}</span
          >
          <span class="fb-select__caret" aria-hidden="true">▾</span>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            class="fb-select__content"
            sideOffset={6}
            align="start"
          >
            <Select.Viewport class="fb-select__viewport">
              {#if channelOptions.length === 0}
                <div class="fb-select__empty">No channels indexed.</div>
              {/if}
              {#each channelOptions as ch (ch)}
                <Select.Item class="fb-select__item" value={ch} label={ch}>
                  {#snippet children({ selected })}
                    <span class="fb-select__check" aria-hidden="true"
                      >{selected ? '✓' : ''}</span
                    >
                    <span class="fb-select__item-label">{ch}</span>
                  {/snippet}
                </Select.Item>
              {/each}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  {/if}

  {#if show.dates}
    <div class="fg fg--wide">
      <label class="fg__label">Date Range</label>
      <div class="fg__range">
        <input type="date" bind:value={filters.dateFrom} aria-label="From" />
        <span class="fg__range-sep" aria-hidden="true">–</span>
        <input type="date" bind:value={filters.dateTo} aria-label="To" />
      </div>
    </div>
  {/if}

  {#if show.colors}
    <div class="fg">
      <label class="fg__label">Color</label>
      <Select.Root
        type="multiple"
        value={filters.colorGroups}
        onValueChange={handleColorMultiChange}
      >
        <Select.Trigger
          class="fb-select__trigger brutalist-control"
          aria-label="Color filter"
        >
          <span class="fb-select__label"
            >{colorTriggerLabel(filters.colorGroups)}</span
          >
          <span class="fb-select__caret" aria-hidden="true">▾</span>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            class="fb-select__content"
            sideOffset={6}
            align="start"
          >
            <Select.Viewport class="fb-select__viewport">
              {#each COLOR_GROUPS as g (g)}
                <Select.Item class="fb-select__item" value={g} label={g}>
                  {#snippet children({ selected })}
                    <span class="fb-select__check" aria-hidden="true"
                      >{selected ? '✓' : ''}</span
                    >
                    <span class="fb-select__item-label"
                      >{g[0].toUpperCase()}{g.slice(1)}</span
                    >
                  {/snippet}
                </Select.Item>
              {/each}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  {/if}

  {#if show.posters}
    <div class="fg">
      <label class="fg__label">Posted By</label>
      <Select.Root
        type="single"
        value={filters.poster}
        onValueChange={(v) => (filters.poster = v ?? '')}
      >
        <Select.Trigger
          class="fb-select__trigger brutalist-control"
          aria-label="Posted by filter"
        >
          <span class="fb-select__label">{filters.poster || 'All'}</span>
          <span class="fb-select__caret" aria-hidden="true">▾</span>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            class="fb-select__content"
            sideOffset={6}
            align="start"
          >
            <Select.Viewport class="fb-select__viewport">
              <Select.Item class="fb-select__item" value="" label="All">
                {#snippet children({ selected })}
                  <span class="fb-select__check" aria-hidden="true"
                    >{selected ? '✓' : ''}</span
                  >
                  <span class="fb-select__item-label">All</span>
                {/snippet}
              </Select.Item>
              {#each posterOptions as u (u)}
                <Select.Item class="fb-select__item" value={u} label={u}>
                  {#snippet children({ selected })}
                    <span class="fb-select__check" aria-hidden="true"
                      >{selected ? '✓' : ''}</span
                    >
                    <span class="fb-select__item-label">{u}</span>
                  {/snippet}
                </Select.Item>
              {/each}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  {/if}

  <div class="fg" class:fg--disabled={jobAppDisabled}>
    <label class="fg__label">Job App</label>
    <Select.Root
      type="single"
      value={filters.jobApp}
      disabled={jobAppDisabled}
      onValueChange={(v) => (filters.jobApp = v ?? '')}
    >
      <Select.Trigger
        class="fb-select__trigger brutalist-control"
        aria-label="Job app filter"
      >
        <span class="fb-select__label">{filters.jobApp || 'All'}</span>
        <span class="fb-select__caret" aria-hidden="true">▾</span>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          class="fb-select__content"
          sideOffset={6}
          align="start"
        >
          <Select.Viewport class="fb-select__viewport">
            <Select.Item class="fb-select__item" value="" label="All">
              {#snippet children({ selected })}
                <span class="fb-select__check" aria-hidden="true"
                  >{selected ? '✓' : ''}</span
                >
                <span class="fb-select__item-label">All</span>
              {/snippet}
            </Select.Item>
            {#each jobAppOptions as a (a)}
              <Select.Item class="fb-select__item" value={a} label={a}>
                {#snippet children({ selected })}
                  <span class="fb-select__check" aria-hidden="true"
                    >{selected ? '✓' : ''}</span
                  >
                  <span class="fb-select__item-label">{a}</span>
                {/snippet}
              </Select.Item>
            {/each}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
    {#if jobAppDisabled}
      <div class="fg__hint">Outputs only — switch Index to Outputs.</div>
    {/if}
  </div>

  {#if show.tagsText}
    <div class="fg fg--wide">
      <label class="fg__label">Tags</label>
      <input
        type="text"
        bind:value={filters.tagsText}
        placeholder="Comma-separated tags"
      />
    </div>
  {/if}

  {#if show.reactions}
    <div class="fg">
      <label class="fg__label">Reactions</label>
      <div class="fg__range">
        <input
          type="number"
          min="0"
          placeholder="min"
          aria-label="Minimum reactions"
          bind:value={filters.reactionsMin}
        />
        <span class="fg__range-sep" aria-hidden="true">–</span>
        <input
          type="number"
          min="0"
          placeholder="max"
          aria-label="Maximum reactions"
          value={filters.reactionsMax ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.reactionsMax = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
      </div>
    </div>
  {/if}

  {#if show.tagsMin}
    <div class="fg">
      <label class="fg__label">Tag Count</label>
      <div class="fg__range">
        <input
          type="number"
          min="0"
          placeholder="min"
          aria-label="Minimum tag count"
          bind:value={filters.tagsMin}
        />
        <span class="fg__range-sep" aria-hidden="true">–</span>
        <input
          type="number"
          min="0"
          placeholder="max"
          aria-label="Maximum tag count"
          value={filters.tagsMax ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.tagsMax = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
      </div>
    </div>
  {/if}

  <div class="fg" class:fg--disabled={hasTranscriptDisabled}>
    <label class="fg__label">Transcript</label>
    <Select.Root
      type="single"
      value={filters.hasTranscript}
      disabled={hasTranscriptDisabled}
      onValueChange={(v) =>
        (filters.hasTranscript = (v ?? '') as '' | 'yes' | 'no')}
    >
      <Select.Trigger
        class="fb-select__trigger brutalist-control"
        aria-label="Transcript filter"
      >
        <span class="fb-select__label"
          >{filters.hasTranscript === 'yes'
            ? 'With transcript'
            : filters.hasTranscript === 'no'
              ? 'Without transcript'
              : 'Any'}</span
        >
        <span class="fb-select__caret" aria-hidden="true">▾</span>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          class="fb-select__content"
          sideOffset={6}
          align="start"
        >
          <Select.Viewport class="fb-select__viewport">
            {#each [{ v: '', l: 'Any' }, { v: 'yes', l: 'With transcript' }, { v: 'no', l: 'Without transcript' }] as opt (opt.v)}
              <Select.Item class="fb-select__item" value={opt.v} label={opt.l}>
                {#snippet children({ selected })}
                  <span class="fb-select__check" aria-hidden="true"
                    >{selected ? '✓' : ''}</span
                  >
                  <span class="fb-select__item-label">{opt.l}</span>
                {/snippet}
              </Select.Item>
            {/each}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
    {#if hasTranscriptDisabled}
      <div class="fg__hint">Audio/video only.</div>
    {/if}
  </div>

  <div class="fg" class:fg--disabled={hasTextDisabled}>
    <label class="fg__label">OCR Text</label>
    <Select.Root
      type="single"
      value={filters.hasText}
      disabled={hasTextDisabled}
      onValueChange={(v) =>
        (filters.hasText = (v ?? '') as '' | 'yes' | 'no')}
    >
      <Select.Trigger
        class="fb-select__trigger brutalist-control"
        aria-label="OCR text filter"
      >
        <span class="fb-select__label"
          >{filters.hasText === 'yes'
            ? 'With OCR text'
            : filters.hasText === 'no'
              ? 'Without OCR text'
              : 'Any'}</span
        >
        <span class="fb-select__caret" aria-hidden="true">▾</span>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          class="fb-select__content"
          sideOffset={6}
          align="start"
        >
          <Select.Viewport class="fb-select__viewport">
            {#each [{ v: '', l: 'Any' }, { v: 'yes', l: 'With OCR text' }, { v: 'no', l: 'Without OCR text' }] as opt (opt.v)}
              <Select.Item class="fb-select__item" value={opt.v} label={opt.l}>
                {#snippet children({ selected })}
                  <span class="fb-select__check" aria-hidden="true"
                    >{selected ? '✓' : ''}</span
                  >
                  <span class="fb-select__item-label">{opt.l}</span>
                {/snippet}
              </Select.Item>
            {/each}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
    {#if hasTextDisabled}
      <div class="fg__hint">Images only.</div>
    {/if}
  </div>

  {#if show.votes}
    <div class="fg">
      <label class="fg__label">Acclaim (net)</label>
      <div class="fg__range">
        <input
          type="number"
          placeholder="min"
          aria-label="Minimum net acclaim"
          value={filters.voteScoreMin ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.voteScoreMin = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
        <span class="fg__range-sep" aria-hidden="true">–</span>
        <input
          type="number"
          placeholder="max"
          aria-label="Maximum net acclaim"
          value={filters.voteScoreMax ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.voteScoreMax = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
      </div>
    </div>
    <div class="fg">
      <label class="fg__label">Upvotes</label>
      <div class="fg__range">
        <input
          type="number"
          min="0"
          placeholder="min"
          aria-label="Minimum upvotes"
          value={filters.upMin ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.upMin = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
        <span class="fg__range-sep" aria-hidden="true">–</span>
        <input
          type="number"
          min="0"
          placeholder="max"
          aria-label="Maximum upvotes"
          value={filters.upMax ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.upMax = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
      </div>
    </div>
    <div class="fg">
      <label class="fg__label">Downvotes</label>
      <div class="fg__range">
        <input
          type="number"
          min="0"
          placeholder="min"
          aria-label="Minimum downvotes"
          value={filters.downMin ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.downMin = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
        <span class="fg__range-sep" aria-hidden="true">–</span>
        <input
          type="number"
          min="0"
          placeholder="max"
          aria-label="Maximum downvotes"
          value={filters.downMax ?? ''}
          oninput={(e) => {
            const v = (e.target as HTMLInputElement).value;
            filters.downMax = v === '' ? null : (parseInt(v, 10) ?? null);
          }}
        />
      </div>
    </div>
    <div class="fg">
      <label class="fg__label">My votes</label>
      <Select.Root
        type="single"
        value={filters.myVotes}
        onValueChange={(v) =>
          (filters.myVotes = (v ?? '') as
            | ''
            | 'up'
            | 'down'
            | 'any'
            | 'none')}
      >
        <Select.Trigger
          class="fb-select__trigger brutalist-control"
          aria-label="My votes filter"
        >
          <span class="fb-select__label"
            >{({
              '': 'Any',
              up: 'I acclaimed',
              down: 'I disavowed',
              any: 'Either way',
              none: 'No votes yet',
            } as Record<string, string>)[filters.myVotes] || 'Any'}</span
          >
          <span class="fb-select__caret" aria-hidden="true">▾</span>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            class="fb-select__content"
            sideOffset={6}
            align="start"
          >
            <Select.Viewport class="fb-select__viewport">
              {#each [{ v: '', l: 'Any' }, { v: 'up', l: 'I acclaimed' }, { v: 'down', l: 'I disavowed' }, { v: 'any', l: 'Either way' }, { v: 'none', l: 'No votes yet' }] as opt (opt.v)}
                <Select.Item
                  class="fb-select__item"
                  value={opt.v}
                  label={opt.l}
                >
                  {#snippet children({ selected })}
                    <span class="fb-select__check" aria-hidden="true"
                      >{selected ? '✓' : ''}</span
                    >
                    <span class="fb-select__item-label">{opt.l}</span>
                  {/snippet}
                </Select.Item>
              {/each}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  {/if}

  {#if show.sort}
    <div class="fg">
      <label class="fg__label">Sort By</label>
      <Select.Root
        type="single"
        value={filters.sortBy}
        onValueChange={(v) => (filters.sortBy = v ?? 'newest')}
      >
        <Select.Trigger
          class="fb-select__trigger brutalist-control"
          aria-label="Sort filter"
        >
          <span class="fb-select__label"
            >{SORT_OPTIONS.find((o) => o.value === filters.sortBy)?.label ??
              'Newest'}</span
          >
          <span class="fb-select__caret" aria-hidden="true">▾</span>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            class="fb-select__content"
            sideOffset={6}
            align="start"
          >
            <Select.Viewport class="fb-select__viewport">
              {#each SORT_OPTIONS as o (o.value)}
                <Select.Item
                  class="fb-select__item"
                  value={o.value}
                  label={o.label}
                >
                  {#snippet children({ selected })}
                    <span class="fb-select__check" aria-hidden="true"
                      >{selected ? '✓' : ''}</span
                    >
                    <span class="fb-select__item-label">{o.label}</span>
                  {/snippet}
                </Select.Item>
              {/each}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  {/if}
</div>

<style>
  /* Filter drawer is a 2-col responsive grid: each `.fg` is one cell,
     so short controls (selects, single numbers, range pairs) sit
     side-by-side. Long-form controls (Tags text, Date range) span
     both columns via `.fg--wide`. Collapses to single column below
     540px so phones still get every control full-width. */
  .filter-bar {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--space-sm) var(--space-md);
    align-items: start;
  }
  .fg--wide {
    grid-column: 1 / -1;
  }
  @media (max-width: 540px) {
    .filter-bar {
      grid-template-columns: 1fr;
    }
  }

  .fg {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .fg--disabled {
    opacity: 0.55;
  }
  .fg__label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
    font-weight: 700;
  }
  .fg__hint {
    font-size: 0.6rem;
    color: var(--color-muted);
    margin-top: 2px;
  }
  /* min/max paired inputs — sits inside .fg under the label. Each
     input is min-width: 0 so they don't overflow narrow viewports;
     the en-dash separator sits between them with light styling. */
  .fg__range {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .fg__range input {
    flex: 1 1 0;
    min-width: 0;
  }
  .fg__range-sep {
    color: var(--color-muted);
    font-weight: 700;
    flex-shrink: 0;
  }
  .fg input[type='text'],
  .fg input[type='number'],
  .fg input[type='date'],
  .fg select {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font: inherit;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .fg input[type='date'] + input[type='date'] {
    margin-top: 4px;
  }

  /* Brutalist switch buttons for media type — 2px border + drop shadow,
     plain click = switch, modifier-click = multi-select. */
  .type-switches {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .type-switch {
    background: var(--color-bg);
    color: var(--color-text);
    border: 2px solid var(--color-text);
    box-shadow: 2px 2px 0 var(--color-text);
    padding: 6px 12px;
    font: inherit;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1pt;
    cursor: pointer;
  }
  .type-switch:hover {
    transform: translate(-1px, -1px);
    box-shadow: 3px 3px 0 var(--color-text);
  }
  .type-switch:active {
    transform: translate(1px, 1px);
    box-shadow: 1px 1px 0 var(--color-text);
  }
  .type-switch.active {
    background: var(--color-text);
    color: var(--color-bg);
  }

  /* bits-ui Select styling shared across Index + Channel dropdowns. */
  :global(.fb-select__trigger) {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 10px;
    font-size: 0.75rem;
    cursor: pointer;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    font-family: var(--font-mono);
  }
  :global(.fb-select__caret) {
    font-size: 0.7rem;
    line-height: 1;
  }
  :global(.fb-select__content) {
    z-index: 1000;
    min-width: var(--bits-select-anchor-width);
    background: var(--color-bg);
    border: 2px solid var(--color-text);
    box-shadow: 3px 3px 0 var(--color-text);
    font-family: var(--font-mono);
    max-width: min(92vw, 360px);
    max-height: 60vh;
  }
  :global(.fb-select__viewport) {
    padding: 4px;
    overflow-y: auto;
    max-height: 60vh;
  }
  :global(.fb-select__item) {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    color: var(--color-text);
    cursor: pointer;
    user-select: none;
    outline: none;
    min-height: 36px;
  }
  :global(.fb-select__item[data-highlighted]),
  :global(.fb-select__item[data-selected]) {
    background: var(--color-text);
    color: var(--color-bg);
  }
  :global(.fb-select__check) {
    width: 14px;
    text-align: center;
    font-weight: 900;
  }
  :global(.fb-select__item-label) {
    flex: 1;
  }
  :global(.fb-select__empty) {
    padding: 8px 10px;
    color: var(--color-muted);
    font-size: 0.7rem;
  }
</style>
