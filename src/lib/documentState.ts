/**
 * State that a persistent island publishes onto `<html>` or `<body>`.
 *
 * **Astro's ClientRouter erases both on every navigation.**
 * `swapRootAttributes()` removes *every* attribute from `<html>` and copies
 * the incoming document's over — only `data-astro-transition` and
 * `data-astro-transition-fallback` survive, so an inline custom property does
 * not. `swapBodyElement()` replaces `<body>` outright, taking its classes.
 *
 * An island marked `transition:persist` sails straight through that. Its
 * effects don't re-run, and a `ResizeObserver` on it never fires — the element
 * never changed size, only the document around it did. So what it published
 * about itself is gone, and nothing puts it back.
 *
 * Measured on a 390px viewport, 2026-08-02: `--player-h` is `165px` with a
 * track playing; one navigation later it is gone, and the comment window —
 * `bottom: var(--player-h, 72px)` — sits **93px behind the player bar**. That
 * is the bug reported from a phone that night ("backed out of the latent with
 * the player still up… the comment window came back partially behind the
 * player"). The same wipe silently reverts #592 for video (the PiP slides
 * under the transport again) and drops `body.player-active`, so the page's
 * bottom padding stops clearing the bar.
 *
 * Publish through here and it is re-applied after every swap. A value is
 * re-measured rather than remembered: the point of `--player-h` is that a
 * measurement can't drift out of sync with the layout, and restoring a
 * remembered number would hand that back.
 */

type Republish = () => void;

const republishers = new Set<Republish>();
let wired = false;

/**
 * Re-apply after the swap, not after the load: `astro:after-swap` fires with
 * the new document in place and before it is painted, so nothing is ever shown
 * with the fallback value. (`astro:page-load` also fires on a full load, where
 * nothing was lost in the first place.)
 */
function wire(): void {
  if (wired || typeof document === 'undefined') return;
  wired = true;
  document.addEventListener('astro:after-swap', () => {
    for (const fn of republishers) fn();
  });
}

export type RootVar = {
  /** Re-measure and publish. Safe to call as often as you like. */
  publish: () => void;
  /**
   * Stop publishing and take the property off `<html>`.
   *
   * Not permanent: publishing again resumes surviving navigations. The player
   * clears on the way through a track change and republishes on the far side,
   * and a one-way `clear()` would leave it correct now and wrong after the
   * next swap — the failure this module exists to end, one step removed.
   */
  clear: () => void;
};

/**
 * Keep a measured pixel value on `<html>` as a custom property.
 *
 * `measure` is called on publish and again after every navigation; return
 * a falsy value (an unmounted element, a zero height) to leave the previous
 * value alone rather than publish a nonsense one.
 */
export function rootPx(
  name: string,
  measure: () => number | null | undefined,
): RootVar {
  const apply = () => {
    const value = measure();
    if (!value) return;
    document.documentElement.style.setProperty(name, `${value}px`);
  };
  wire();
  return {
    publish() {
      republishers.add(apply);
      apply();
    },
    clear() {
      republishers.delete(apply);
      document.documentElement.style.removeProperty(name);
    },
  };
}

export type BodyClass = {
  /** Turn the class on or off; the choice survives navigation. */
  set: (on: boolean) => void;
  /** Stop maintaining it, and take it off. */
  clear: () => void;
};

/** Keep a class on `<body>`, across the `<body>` being replaced. */
export function bodyClass(name: string): BodyClass {
  let on = false;
  const apply = () => document.body.classList.toggle(name, on);
  wire();
  return {
    set(next: boolean) {
      on = next;
      republishers.add(apply);
      apply();
    },
    clear() {
      republishers.delete(apply);
      document.body.classList.remove(name);
    },
  };
}
