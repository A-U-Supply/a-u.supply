/*
 * Small helper for pages that mount BrutalPagination with useEvents=true.
 * Wires every `data-pagination-page` element under `root` to dispatch a
 * `pagination:go` CustomEvent with `{ page }` detail, and prevent default
 * navigation so the page's own JS can refetch.
 *
 *   import { wirePagination } from '../lib/pagination';
 *   wirePagination(document.querySelector('.pagination')!, (page) => {
 *     loadPage(page);
 *   });
 *
 * Returns the cleanup function.
 */
export function wirePagination(
  root: HTMLElement,
  onGo: (page: number) => void,
): () => void {
  const handler = (e: Event) => {
    const t = e.target as HTMLElement;
    const a = t.closest?.('[data-pagination-page]') as HTMLAnchorElement | null;
    if (!a || !root.contains(a)) return;
    if (a.getAttribute('aria-disabled') === 'true') {
      e.preventDefault();
      return;
    }
    const page = Number(a.dataset.paginationPage);
    if (!Number.isFinite(page) || page < 1) return;
    e.preventDefault();
    root.dispatchEvent(
      new CustomEvent('pagination:go', { detail: { page }, bubbles: true }),
    );
    onGo(page);
  };
  root.addEventListener('click', handler);
  return () => root.removeEventListener('click', handler);
}

/*
 * Render windowed pagination markup as a string into an existing
 * .pagination root. Used by pages that update pagination from JS
 * (e.g. as they re-fetch results) and don't want to re-mount the Astro
 * partial. The markup is the same shape as BrutalPagination.astro.
 */
export function renderPagination(opts: {
  current: number;
  total: number;
  windowSize?: number;
  hrefFor?: (page: number) => string;
}): string {
  const { current, total, windowSize = 1, hrefFor = () => '#' } = opts;
  if (total <= 0) return '';
  const items: (number | 'ellipsis')[] = [];
  const pages = new Set<number>();
  pages.add(1);
  pages.add(total);
  for (let i = current - windowSize; i <= current + windowSize; i++) {
    if (i >= 1 && i <= total) pages.add(i);
  }
  const sorted = [...pages].sort((a, b) => a - b);
  let last = 0;
  for (const p of sorted) {
    if (last && p - last > 1) items.push('ellipsis');
    items.push(p);
    last = p;
  }
  const prevDisabled = current <= 1;
  const nextDisabled = current >= total;

  const navBtn = (
    page: number,
    label: string,
    text: string,
    disabled: boolean,
  ) =>
    `<a class="pagination__page pagination__nav" ` +
    `href="${disabled ? '#' : hrefFor(page)}" ` +
    `aria-label="${label}" title="${label}" ` +
    (disabled ? `aria-disabled="true" tabindex="-1" ` : '') +
    `data-pagination-page="${page}">${text}</a>`;

  const pageBtn = (p: number | 'ellipsis') => {
    if (p === 'ellipsis')
      return '<li class="pagination__ellipsis" aria-hidden="true">…</li>';
    const isCurrent = p === current;
    return (
      `<li><a class="pagination__page${isCurrent ? ' is-current' : ''}" ` +
      `href="${isCurrent ? '#' : hrefFor(p)}" ` +
      (isCurrent ? `aria-current="page" ` : '') +
      `data-pagination-page="${p}">${p}</a></li>`
    );
  };

  return [
    navBtn(1, 'First page', '«', prevDisabled),
    navBtn(current - 1, 'Previous page', '‹', prevDisabled),
    `<ol class="pagination__pages">${items.map(pageBtn).join('')}</ol>`,
    navBtn(current + 1, 'Next page', '›', nextDisabled),
    navBtn(total, 'Last page', '»', nextDisabled),
  ].join('');
}
