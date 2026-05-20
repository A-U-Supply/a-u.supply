<!--
  VoteChip — per-user up/down vote on a media item (issue #318).

  Two variants:
    - size="lg" (default for this component): two labeled brutalist
      buttons side-by-side with a voter list below. Used on the detail
      page in its own "Acclaim / Disavow" section.
    - size="sm": compact two-button pair for embedded contexts (the
      grid/list/feed in /admin/search render this style via vanilla
      HTML rather than mounting an island per tile; this size exists
      for symmetry / future single-instance embeds).

  Optimistic local state: clicking updates `myVote` + counts
  immediately, then POSTs. A failed POST reverts and surfaces an
  inline error.

  Styling uses the project's brutalist tokens (2px borders, hard
  shadow, mono uppercase, hover lift, aria-pressed inversion) — see
  `src/styles/tailwind.css` `.brutalist-control`.
-->
<script lang="ts">
  type Voter = { user_id: number; name: string };

  type Props = {
    mediaId: string;
    upCount: number;
    downCount: number;
    myVote?: -1 | 0 | 1;
    upvoters?: Voter[];
    downvoters?: Voter[];
    currentUserId?: number | null;
    size?: 'sm' | 'lg';
    onUpdate?: (aggregates: {
      up_count: number;
      down_count: number;
      vote_score: number;
      my_vote: number;
    }) => void;
  };

  let {
    mediaId,
    upCount,
    downCount,
    myVote = 0,
    upvoters = [],
    downvoters = [],
    currentUserId = null,
    size = 'lg',
    onUpdate,
  }: Props = $props();

  let up = $state(upCount);
  let down = $state(downCount);
  let mine = $state<-1 | 0 | 1>(myVote);
  let votersUp = $state<Voter[]>(upvoters);
  let votersDown = $state<Voter[]>(downvoters);
  let busy = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    up = upCount;
  });
  $effect(() => {
    down = downCount;
  });
  $effect(() => {
    mine = myVote;
  });
  $effect(() => {
    votersUp = upvoters;
  });
  $effect(() => {
    votersDown = downvoters;
  });

  async function cast(target: 1 | -1) {
    // Clicking the same arrow retracts.
    const next: -1 | 0 | 1 = mine === target ? 0 : target;
    const priorUp = up;
    const priorDown = down;
    const priorMine = mine;
    const priorVotersUp = votersUp;
    const priorVotersDown = votersDown;

    // Optimistic update
    if (mine === 1) up -= 1;
    if (mine === -1) down -= 1;
    if (next === 1) up += 1;
    if (next === -1) down += 1;
    mine = next;
    if (currentUserId != null) {
      votersUp = votersUp.filter((v) => v.user_id !== currentUserId);
      votersDown = votersDown.filter((v) => v.user_id !== currentUserId);
      if (next === 1)
        votersUp = [...votersUp, { user_id: currentUserId, name: 'you' }];
      if (next === -1)
        votersDown = [...votersDown, { user_id: currentUserId, name: 'you' }];
    }

    busy = true;
    error = null;
    try {
      const r = await fetch(`/api/search/${encodeURIComponent(mediaId)}/vote`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: next }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      up = body.up_count;
      down = body.down_count;
      mine = body.my_vote as -1 | 0 | 1;
      onUpdate?.(body);
    } catch (e: any) {
      up = priorUp;
      down = priorDown;
      mine = priorMine;
      votersUp = priorVotersUp;
      votersDown = priorVotersDown;
      error = e?.message || 'Vote failed';
    } finally {
      busy = false;
    }
  }

  function joinNames(voters: Voter[]): string {
    if (voters.length === 0) return '—';
    return voters.map((v) => v.name).join(', ');
  }
</script>

<div class="vote-block vote-block--{size}" class:is-busy={busy}>
  <div class="vote-block__row">
    <button
      type="button"
      class="brutalist-control vote-btn vote-btn--up"
      class:vote-btn--active={mine === 1}
      aria-pressed={mine === 1}
      aria-label="Acclaim this item"
      disabled={busy}
      onclick={(e) => {
        e.stopPropagation();
        void cast(1);
      }}
    >
      <span class="vote-btn__arrow" aria-hidden="true">▲</span>
      <span class="vote-btn__label">Acclaim</span>
      <span class="vote-btn__count">{up}</span>
    </button>

    <button
      type="button"
      class="brutalist-control vote-btn vote-btn--down"
      class:vote-btn--active={mine === -1}
      aria-pressed={mine === -1}
      aria-label="Disavow this item"
      disabled={busy}
      onclick={(e) => {
        e.stopPropagation();
        void cast(-1);
      }}
    >
      <span class="vote-btn__arrow" aria-hidden="true">▼</span>
      <span class="vote-btn__label">Disavow</span>
      <span class="vote-btn__count">{down}</span>
    </button>
  </div>

  {#if size === 'lg'}
    <dl class="vote-block__voters">
      <div class="vote-block__voters-row">
        <dt>Acclaimed by</dt>
        <dd>{joinNames(votersUp)}</dd>
      </div>
      <div class="vote-block__voters-row">
        <dt>Disavowed by</dt>
        <dd>{joinNames(votersDown)}</dd>
      </div>
    </dl>
  {/if}

  {#if error}
    <div class="vote-block__error" role="alert">{error}</div>
  {/if}
</div>

<style>
  .vote-block {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .vote-block__row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-sm);
  }
  .vote-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.6rem 1rem;
    font-size: var(--text-base, 0.95rem);
    line-height: 1;
    cursor: pointer;
    min-width: 9.5rem;
    justify-content: flex-start;
    /* brutalist-control already paints: 2px border, hard shadow, mono
       uppercase, hover lift, aria-pressed inversion. We only add the
       arrow/label/count layout + size. */
  }
  .vote-btn:disabled {
    cursor: progress;
    opacity: 0.7;
  }
  .vote-btn__arrow {
    font-size: 1.15em;
    line-height: 1;
  }
  .vote-btn__label {
    flex: 1;
  }
  .vote-btn__count {
    font-variant-numeric: tabular-nums;
    min-width: 2ch;
    text-align: right;
  }

  /* Compact variant for embedded contexts (small surfaces). */
  .vote-block--sm .vote-btn {
    padding: 0.25rem 0.55rem;
    min-width: 0;
    font-size: 0.75rem;
    gap: 0.35rem;
  }
  .vote-block--sm .vote-btn__label {
    display: none;
  }

  .vote-block__voters {
    margin: 0;
    font-size: var(--text-sm, 0.85rem);
    color: var(--color-muted);
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
  }
  .vote-block__voters-row {
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
  }
  .vote-block__voters dt {
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.5pt;
    color: var(--color-muted);
    min-width: 9.5rem;
    flex-shrink: 0;
  }
  .vote-block__voters dd {
    margin: 0;
    color: var(--color-text, var(--color-fg));
    word-break: break-word;
  }
  .vote-block__error {
    color: #c00;
    font-size: var(--text-sm, 0.85rem);
  }
</style>
