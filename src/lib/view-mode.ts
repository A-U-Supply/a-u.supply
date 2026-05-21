/*
 * View-mode helper for pages with a grid/feed/list toggle.
 *
 * Source of truth precedence: URL ?view= > localStorage > caller default.
 * On change, both localStorage and the URL (replaceState, no history
 * entry) are written so links remain shareable but the user's
 * across-page preference still survives a soft reload.
 *
 * Usage:
 *   import { initViewMode } from '../../lib/view-mode';
 *   const view = initViewMode({
 *     storageKey: 'au.searchView',
 *     fallback: 'grid',
 *     allowed: ['grid', 'feed', 'list'],
 *     onChange: (v) => setView(v),
 *   });
 *   document.getElementById('grid-view-btn')!.addEventListener('click', () => view.set('grid'));
 */
export type ViewMode = string;

export interface ViewModeOpts<T extends ViewMode> {
  storageKey: string;
  fallback: T;
  allowed: readonly T[];
  /** URL param name (default 'view') */
  param?: string;
  /** Called whenever the view changes. */
  onChange?: (next: T, previous: T) => void;
}

export interface ViewModeHandle<T extends ViewMode> {
  get(): T;
  set(next: T): void;
}

export function initViewMode<T extends ViewMode>(
  opts: ViewModeOpts<T>,
): ViewModeHandle<T> {
  const param = opts.param ?? 'view';
  const isAllowed = (v: unknown): v is T =>
    typeof v === 'string' && (opts.allowed as readonly string[]).includes(v);

  function readFromUrl(): T | null {
    try {
      const v = new URL(window.location.href).searchParams.get(param);
      return isAllowed(v) ? (v as T) : null;
    } catch {
      return null;
    }
  }
  function readFromStorage(): T | null {
    try {
      const v = localStorage.getItem(opts.storageKey);
      return isAllowed(v) ? (v as T) : null;
    } catch {
      return null;
    }
  }
  let current: T = readFromUrl() ?? readFromStorage() ?? opts.fallback;

  function writeUrl(v: T) {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set(param, v);
      window.history.replaceState({}, '', url.toString());
    } catch {}
  }
  function writeStorage(v: T) {
    try {
      localStorage.setItem(opts.storageKey, v);
    } catch {}
  }

  return {
    get: () => current,
    set: (next: T) => {
      if (!isAllowed(next) || next === current) return;
      const previous = current;
      current = next;
      writeUrl(next);
      writeStorage(next);
      opts.onChange?.(next, previous);
    },
  };
}
