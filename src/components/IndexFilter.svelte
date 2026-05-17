<!--
  IndexFilter — multi-select dropdown for the search page's "Index" filter.
  Built on bits-ui Select (type="multiple") so we get accessible keyboard nav,
  focus management, and mobile-friendly popover behavior for free.

  Self-contained:
    - Reads `?output_index=foo,bar` from window.location on mount.
    - Fetches /api/search/facets for the dynamic option list.
    - Mirrors the current selection to the wrapper element (#filter-output-index)
      via `data-selected="..."` so the page's getFilters() / updateCrossFilterState
      can read it synchronously.
    - Dispatches a bubbling `index-change` CustomEvent (detail.value) when the
      selection changes — the page listens and runs doSearch().

  Selection always contains ≥1 entry — deselecting the last item snaps back
  to ["__inputs__"] so we never enter a zero-state.
-->
<script lang="ts">
  import { Select } from 'bits-ui';
  import { onMount, tick } from 'svelte';

  type Props = {
    initial?: string[];
  };

  let { initial }: Props = $props();

  // Always include the synthetic Inputs option first, and Emulsion (private
  // Latents index) right after — Emulsion lives in its own Meilisearch
  // index, but admins want to select it like any other index.
  let dynamicOptions = $state<string[]>([]);
  let allValues = $derived<string[]>([
    '__inputs__',
    '__emulsion__',
    ...dynamicOptions,
  ]);

  let value = $state<string[]>(['__inputs__']);

  let host: HTMLDivElement | null = $state(null);
  let mounted = $state(false);

  function labelFor(v: string): string {
    if (v === '__inputs__') return 'Inputs';
    if (v === '__emulsion__') return 'Emulsion (Latents)';
    return v;
  }

  function triggerLabel(selected: string[]): string {
    if (!selected.length) return 'Inputs';
    if (selected.length === 1) return labelFor(selected[0]);
    if (selected.length === 2) return selected.map(labelFor).join(', ');
    return `${labelFor(selected[0])} +${selected.length - 1}`;
  }

  function findWrapper(): HTMLElement | null {
    return host?.closest('#filter-output-index') ?? null;
  }

  // Sync selection outward whenever it changes (skip the initial mount).
  $effect(() => {
    const sel = value;
    if (!mounted) return;
    const wrapper = findWrapper();
    if (wrapper) {
      wrapper.dataset.selected = sel.join(',');
      wrapper.dispatchEvent(
        new CustomEvent('index-change', {
          bubbles: true,
          detail: { value: [...sel] },
        }),
      );
    }
  });

  function readInitialFromUrl(): string[] {
    if (initial && initial.length) return initial;
    if (typeof window === 'undefined') return ['__inputs__'];
    const p = new URLSearchParams(window.location.search);
    const raw = p.get('output_index');
    if (!raw) {
      // ?app=X without an explicit output_index implies "outputs" — keep the
      // legacy behavior so admin links from elsewhere still filter properly.
      if (p.get('app')) return ['outputs'];
      return ['__inputs__'];
    }
    const parts = raw
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);
    return parts.length ? parts : ['__inputs__'];
  }

  async function loadOptions() {
    try {
      const res = await fetch('/api/search/facets', {
        credentials: 'include',
      });
      if (!res.ok) return;
      const data = await res.json();
      dynamicOptions = Array.isArray(data?.output_indexes)
        ? data.output_indexes
        : [];
      // If the URL referenced an unknown output index, keep it as a valid
      // option so the link still filters and the user can deselect it.
      const unknown = value.filter(
        (v) => v !== '__inputs__' && !dynamicOptions.includes(v),
      );
      if (unknown.length) {
        dynamicOptions = [...new Set([...dynamicOptions, ...unknown])];
      }
    } catch {
      /* ignore — admin auth missing or offline; the synthetic Inputs option
         is enough to render. */
    }
  }

  onMount(async () => {
    value = readInitialFromUrl();
    await tick();
    const wrapper = findWrapper();
    if (wrapper) {
      wrapper.dataset.selected = value.join(',');
      // External resets (e.g. the active-filters "× Index" chip) dispatch
      // `index-set` on the wrapper with detail.value to override our state.
      const onSet = (e: Event) => {
        const next = (e as CustomEvent).detail?.value;
        if (Array.isArray(next)) value = next.length ? next : ['__inputs__'];
      };
      wrapper.addEventListener('index-set', onSet);
    }
    mounted = true;
    loadOptions();
  });

  function handleChange(next: string[]) {
    // Never allow zero — empty selection snaps back to Inputs.
    value = next.length ? next : ['__inputs__'];
  }
</script>

<div bind:this={host} class="index-filter">
  <Select.Root type="multiple" bind:value onValueChange={handleChange}>
    <Select.Trigger
      class="index-filter__trigger brutalist-control"
      aria-label="Index filter"
    >
      <span class="index-filter__label">{triggerLabel(value)}</span>
      <span class="index-filter__caret" aria-hidden="true">▾</span>
    </Select.Trigger>
    <Select.Portal>
      <Select.Content
        class="index-filter__content"
        sideOffset={6}
        align="start"
      >
        <Select.Viewport class="index-filter__viewport">
          {#each allValues as v (v)}
            <Select.Item
              class="index-filter__item"
              value={v}
              label={labelFor(v)}
            >
              {#snippet children({ selected })}
                <span class="index-filter__check" aria-hidden="true"
                  >{selected ? '✓' : ''}</span
                >
                <span class="index-filter__item-label">{labelFor(v)}</span>
              {/snippet}
            </Select.Item>
          {/each}
        </Select.Viewport>
      </Select.Content>
    </Select.Portal>
  </Select.Root>
</div>

<style>
  .index-filter {
    display: block;
    width: 100%;
  }

  :global(.index-filter__trigger) {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 10px;
    font-size: 0.75rem;
    cursor: pointer;
  }

  :global(.index-filter__caret) {
    font-size: 0.7rem;
    line-height: 1;
  }

  :global(.index-filter__content) {
    z-index: 1000;
    min-width: var(--bits-select-anchor-width);
    background: var(--color-bg);
    border: 2px solid var(--color-text);
    box-shadow: 3px 3px 0 var(--color-text);
    font-family: var(--font-mono);
    /* Prevent the popover from getting wider than the viewport on mobile. */
    max-width: min(92vw, 360px);
    max-height: 60vh;
  }

  :global(.index-filter__viewport) {
    padding: 4px;
    overflow-y: auto;
    max-height: 60vh;
  }

  :global(.index-filter__item) {
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
    /* Big tap targets on touch — Apple HIG min is 44px, this is close enough. */
    min-height: 36px;
  }
  :global(.index-filter__item[data-highlighted]),
  :global(.index-filter__item[data-selected]) {
    background: var(--color-text);
    color: var(--color-bg);
  }

  :global(.index-filter__check) {
    width: 14px;
    text-align: center;
    font-weight: 900;
  }
  :global(.index-filter__item-label) {
    flex: 1;
  }
</style>
