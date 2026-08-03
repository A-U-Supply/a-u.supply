<!--
  LatentArrange — the "Arrange" dialog behind the section map's head
  (docs/plans/2026-07-31-latent-section-arrange.md).

  Two jobs in one window: which sections are shown, and what order they come
  in. Hiding is presentation only — the island stays mounted with its content
  loaded, so a paragraph written in Documents is still there, unchanged, when
  Documents comes back.

  A native <dialog> + showModal(), like .delete-dialog on the detail page. The
  top layer means no `z-index`, no portal, and no way for a section's
  `isolation: isolate` to swallow it — the trap that cost #573/#574/#575/#581.

  Slots live in their own nested list with its own Sortable and no shared
  group. That is what keeps a slot inside the slots block: structure, rather
  than a rule that has to be re-checked on every drop. The list sits INSIDE the
  Slots row so that dragging the section carries its slots along. Slot rows reorder the
  real slots (the same endpoint the slot drag uses) and carry no visibility
  toggle — a section is page furniture, but a slot is content, and hiding one
  is how a take goes missing.
-->
<script lang="ts">
  import RowMove from './RowMove.svelte';
  import { DRAG_OPTS, createSortable } from '../lib/dragOptions.ts';
  import { safeHex } from '../lib/latentStyles.ts';
  import {
    LAYOUT_EVENT,
    LAYOUT_LABELS,
    resolveLayout,
    serializeLayout,
    type LayoutKey,
    type StoredLayout,
  } from '../lib/latentLayout.ts';

  type SlotChip = {
    id: string;
    label: string;
    position: number;
    accent: string | null;
  };
  type Props = {
    projectId: string;
    slots: SlotChip[];
    open: boolean;
    layout: StoredLayout;
    swatchFor: (key: LayoutKey) => string;
    onClose: () => void;
  };

  let { projectId, slots, open, layout, swatchFor, onClose }: Props = $props();

  let dialogEl = $state<HTMLDialogElement | null>(null);
  let order = $state<LayoutKey[]>([]);
  let hidden = $state<Set<LayoutKey>>(new Set());
  let slotRows = $state<SlotChip[]>([]);
  let error = $state<string | null>(null);

  // The stored layout is the source of truth; this component holds the working
  // copy. The parent re-broadcasts what we write, so this resync sees its own
  // value coming back and settles rather than fighting.
  $effect(() => {
    const r = resolveLayout(layout);
    order = r.order;
    hidden = r.hidden;
  });

  $effect(() => {
    slotRows = slots;
  });

  $effect(() => {
    const d = dialogEl;
    if (!d) return;
    // showModal() throws on an already-open dialog; close() on a shut one is a
    // no-op but the guard keeps the two paths symmetrical.
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
  });

  /* ----------------------------------------------------------------- close */

  // Escape comes free with showModal(), and `onclose` syncs the parent either
  // way. These two add the corner × and click-outside that every portaled
  // overlay in the tree already has (LatentStylePanel, LinkRepoModal,
  // MarginaliaBadge, PullFromIndex) but no native <dialog> here did.

  /**
   * A backdrop click reports the dialog itself as its target — but so does a
   * click on the dialog's own padding, which is inside the plate and must not
   * close it. Ask the pointer's position instead of trusting the target.
   */
  function outsidePlate(e: MouseEvent): boolean {
    const d = dialogEl;
    if (!d || e.target !== d) return false;
    const r = d.getBoundingClientRect();
    return (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    );
  }

  // A drag released over the backdrop fires a click on the dialog, and closing
  // the window out from under someone mid-arrange is the worst regression this
  // could ship with. Requiring the gesture to BEGIN outside rules it out
  // without a timer to tune: a drag always starts on a row.
  let pressedOutside = false;

  function onDialogPointerDown(e: PointerEvent) {
    pressedOutside = outsidePlate(e);
  }

  function onDialogClick(e: MouseEvent) {
    if (pressedOutside && outsidePlate(e)) dialogEl?.close();
    pressedOutside = false;
  }

  function slotSwatch(s: SlotChip): string {
    return `background:${safeHex(s.accent) || 'var(--color-muted)'}`;
  }

  /* ---------------------------------------------------------------- writes */

  let saveTimer: ReturnType<typeof setTimeout> | null = null;
  let saving = false;
  let pending = false;

  /** Paint the page now, persist a beat later. Arrow clicks arrive in bursts
   * and every one of them would otherwise be its own PATCH. */
  function commit() {
    error = null;
    broadcast();
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 250);
  }

  function broadcast() {
    window.dispatchEvent(
      new CustomEvent(LAYOUT_EVENT, {
        detail: {
          projectId,
          layout: serializeLayout({ order, hidden }),
        },
      }),
    );
  }

  async function save() {
    if (saving) {
      pending = true;
      return;
    }
    saving = true;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            section_layout: serializeLayout({ order, hidden }),
          }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
    } catch (e: any) {
      error = e?.message || 'Failed to save the arrangement';
      await resync();
    } finally {
      saving = false;
      if (pending) {
        pending = false;
        saveTimer = setTimeout(save, 250);
      }
    }
  }

  /** A failed write leaves the page showing something the server doesn't have.
   * Pull the truth back and repaint from it. */
  async function resync() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const p = await res.json();
      const r = resolveLayout(p.section_layout);
      order = r.order;
      hidden = r.hidden;
      broadcast();
    } catch {
      /* offline; the error line already says the save didn't land */
    }
  }

  function toggle(key: LayoutKey) {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    hidden = next;
    commit();
  }

  function nudge(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    order = next;
    commit();
  }

  function reset() {
    error = null;
    order = resolveLayout(null).order;
    hidden = new Set();
    broadcast();
    if (saveTimer) clearTimeout(saveTimer);
    // `{}` clears the column outright rather than storing a default-shaped
    // object, so a latent that was never arranged and one that was reset are
    // the same row again.
    void fetch(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: 'PATCH',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section_layout: {} }),
    }).then((res) => {
      if (!res.ok) {
        error = `Failed (${res.status})`;
        return resync();
      }
    });
  }

  /* ------------------------------------------------------------------ slots */

  async function persistSlots(ids: string[]) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/reorder`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order: ids }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slotRows = (body.slots || []).map((s: any) => ({
        id: s.id,
        label: s.label,
        position: s.position,
        accent: s.accent || null,
      }));
      // The real cards live in LatentSlots, which reloads on this.
      document.dispatchEvent(new CustomEvent('latent:slots-changed'));
    } catch (e: any) {
      error = e?.message || 'Failed to reorder slots';
      document.dispatchEvent(new CustomEvent('latent:slots-changed'));
    }
  }

  function nudgeSlot(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= slotRows.length) return;
    const next = [...slotRows];
    [next[i], next[j]] = [next[j], next[i]];
    slotRows = next;
    void persistSlots(next.map((s) => s.id));
  }

  /* ------------------------------------------------------------------ drag */

  function sortableSections(el: HTMLElement) {
    const s = createSortable(el, {
      ...DRAG_OPTS,
      handle: '.arrange-row__drag',
      // Only `.arrange-row` moves — and the slots block lives inside the Slots
      // row, so it travels with it. It used to be a sibling <li>, which is how
      // a dragged section left its slots behind.
      //
      // Note this does NOT keep slot rows out: `draggable` filters what a list
      // can ORIGINATE, not what can be put into it. Giving these two lists a
      // shared `group` lets a slot land here even with this selector in place
      // (measured — the browser suite's escape check goes red). The absence of
      // a shared group is the only thing holding that line.
      draggable: '.arrange-row',
      rows: '.arrange-row[data-key]',
      id: 'key',
      onDrop: (keys: string[]) => {
        if (keys.length !== order.length) return;
        order = keys as LayoutKey[];
        commit();
      },
    });
    return { destroy: () => s.destroy() };
  }

  function sortableSlots(el: HTMLElement) {
    const s = createSortable(el, {
      ...DRAG_OPTS,
      handle: '.arrange-slot__drag',
      draggable: '.arrange-slot-row',
      rows: '.arrange-slot-row[data-slot-id]',
      id: 'slotId',
      onDrop: (ids: string[]) => {
        if (ids.length !== slotRows.length) return;
        slotRows = ids
          .map((id) => slotRows.find((s2) => s2.id === id)!)
          .filter(Boolean);
        void persistSlots(ids);
      },
    });
    return { destroy: () => s.destroy() };
  }
</script>

<dialog
  class="arrange"
  bind:this={dialogEl}
  onclose={onClose}
  onpointerdown={onDialogPointerDown}
  onclick={onDialogClick}
>
  <div class="arrange__head">
    <h3 class="arrange__title">Arrange sections</h3>
    <button
      class="action-btn arrange__close"
      type="button"
      aria-label="Close"
      onclick={() => dialogEl?.close()}>×</button
    >
  </div>
  <p class="arrange__lede">
    Hiding a section only stops it showing on this page — everything in it stays
    saved, and comes back exactly as it was.
  </p>
  {#if error}
    <p class="arrange__error">{error}</p>
  {/if}

  <ul class="arrange__list" use:sortableSections>
    {#each order as key, i (key)}
      <li
        class="arrange-row"
        class:arrange-row--off={hidden.has(key)}
        data-key={key}
      >
        <div class="arrange-row__head">
          <RowMove
            label={LAYOUT_LABELS[key]}
            handleClass="arrange-row__drag"
            alwaysArrows
            upDisabled={i === 0}
            downDisabled={i === order.length - 1}
            onUp={() => nudge(i, -1)}
            onDown={() => nudge(i, 1)}
          />
          <i class="arrange-row__swatch" style={swatchFor(key)}></i>
          <span class="arrange-row__name">{LAYOUT_LABELS[key]}</span>
          <label class="arrange-row__vis">
            <input
              type="checkbox"
              checked={!hidden.has(key)}
              onchange={() => toggle(key)}
            />
            <span class="arrange-row__vis-text">
              {hidden.has(key) ? 'Hidden' : 'Shown'}
            </span>
          </label>
        </div>
        <!-- Inside the row, not beside it. Sortable moves `.arrange-row` and
             nothing else, so as a sibling this block stayed put while its
             section was dragged away — and it left the row's keyed fragment
             with non-contiguous start/end nodes, which is why re-reading the
             order afterwards couldn't repair it either. -->
        {#if key === 'slots' && slotRows.length > 0}
          <div class="arrange-slots">
            <ul class="arrange-slots__list" use:sortableSlots>
              {#each slotRows as s, j (s.id)}
                <li class="arrange-slot-row" data-slot-id={s.id}>
                  <RowMove
                    label={s.label}
                    handleClass="arrange-slot__drag"
                    alwaysArrows
                    upDisabled={j === 0}
                    downDisabled={j === slotRows.length - 1}
                    onUp={() => nudgeSlot(j, -1)}
                    onDown={() => nudgeSlot(j, 1)}
                  />
                  <i class="arrange-row__swatch" style={slotSwatch(s)}></i>
                  <span class="arrange-row__name">{s.label}</span>
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      </li>
    {/each}
  </ul>

  <div class="arrange__actions">
    <button class="action-btn" type="button" onclick={reset}>Reset</button>
    <button class="action-btn" type="button" onclick={() => dialogEl?.close()}>
      Done
    </button>
  </div>
</dialog>

<style>
  /* Follows .delete-dialog on the detail page: an opaque brutalist plate on a
     washed backdrop, with no position or stacking of its own — showModal()
     puts it in the browser's top layer. */
  .arrange {
    border: 2px solid var(--color-fg);
    box-shadow: 4px 4px 0 var(--color-fg);
    background: var(--color-bg);
    color: var(--color-fg);
    padding: var(--space-md);
    width: min(460px, 92vw);
    max-height: 86dvh;
    overflow-y: auto;
    font-family: var(--font-mono);
    /* The site's reset zeroes margin on everything, which takes out the UA's
       `margin: auto` and drops an open dialog into the top-left corner. */
    margin: auto;
  }
  .arrange::backdrop {
    background: var(--color-overlay);
  }
  .arrange__head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-sm);
    margin: 0 0 var(--space-xs) 0;
  }
  .arrange__title {
    margin: 0;
    font-size: var(--text-base);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .arrange__close {
    flex: 0 0 auto;
    /* The × glyph is small; give it the same 44px target every row here has
       rather than leaving a corner control only a mouse can hit. */
    min-width: 44px;
    min-height: 44px;
    margin-right: 0;
    font-size: var(--text-base);
    line-height: 1;
  }
  .arrange__lede {
    margin: 0 0 var(--space-sm) 0;
    font-size: var(--text-sm);
    color: var(--color-muted);
  }
  .arrange__error {
    margin: 0 0 var(--space-sm) 0;
    font-size: var(--text-sm);
    color: var(--color-status-fail);
  }
  .arrange__list,
  .arrange-slots__list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  /* The row is a bare column so it can carry the slots block; the plate
     (border, fill, 44px) belongs to its head, which is what still has to look
     like a row. */
  .arrange-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .arrange-row__head,
  .arrange-slot-row {
    display: flex;
    align-items: center;
    gap: var(--space-xs);
    min-height: 44px;
    padding: 4px 6px;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  /* Hidden rows keep their grip, so you can position something that isn't
     currently showing. Scoped to the head on purpose: slot rows reuse these
     two class names, and now that they sit INSIDE the Slots row an unscoped
     rule would dim every slot whenever the Slots section is switched off. */
  .arrange-row--off > .arrange-row__head .arrange-row__name,
  .arrange-row--off > .arrange-row__head .arrange-row__swatch {
    opacity: 0.45;
  }
  .arrange-row__swatch {
    width: 10px;
    height: 10px;
    flex: 0 0 10px;
    border: 1px solid var(--color-border);
  }
  .arrange-row__name {
    flex: 1 1 auto;
    font-size: 0.72rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .arrange-row__vis {
    display: flex;
    align-items: center;
    gap: 5px;
    flex: 0 0 auto;
    min-height: 44px;
    padding: 0 2px;
    cursor: pointer;
  }
  .arrange-row__vis-text {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
    width: 4.2em;
  }
  .arrange-row__vis input {
    width: 18px;
    height: 18px;
    accent-color: var(--color-accent);
    cursor: pointer;
  }
  /* The slots block reads as belonging to the Slots row above it — indented,
     and sharing its left edge with the section spine on the page. */
  .arrange-slots {
    margin: 0 0 0 var(--space-md);
    border-left: 2px solid var(--color-border);
    padding: 0 0 0 var(--space-xs);
  }
  .arrange-slot-row {
    margin-top: 4px;
  }
  .arrange__actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-xs);
    margin-top: var(--space-sm);
  }
</style>
