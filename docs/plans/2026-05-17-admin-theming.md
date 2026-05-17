# Admin theming — dark mode + shell consistency

**Branch:** `feat/admin-theme` (plan-only PR first)
**Status:** in design, awaiting review

## Goal

A global light/dark/system toggle for the admin section that:

- Respects the OS preference by default (`prefers-color-scheme`).
- Persists the user's explicit choice in `localStorage` once they touch the toggle.
- Applies only to admin pages (not the public site).
- Does not change atelier tools (photism, punctum, bullethole, spectralize) — they keep their bespoke colored stages.

And — since we're already touching the chrome — unify the admin shell so the sidebar, headers, tables, forms, buttons, and generic pages share one consistent token-driven look. Pages that currently set their own hex colors get rewritten in terms of tokens; the atelier pages opt out.

## Why this is approachable

The brutalist token system in `src/styles/tailwind.css` already exposes the five colors that drive ~everything:

```
--color-bg     #fff   (page background)
--color-fg     #1a1a1a (ink)
--color-muted  #666   (secondary text)
--color-border #ccc
--color-accent #b8860b
```

Brutalist shadows derive from `--color-fg` so they auto-flip. Zero hardcoded Tailwind utilities like `bg-white` / `text-black` in admin pages or components (verified by grep). Almost all the friction is in `<style>` blocks that use hex literals directly — those just need to switch to the token variables.

## Mechanism

### Tokens

Add a dark palette under `[data-theme="dark"]` in `tailwind.css`:

```css
@theme {
  --color-bg: #fff;
  --color-fg: #1a1a1a;
  --color-muted: #666;
  --color-border: #ccc;
  --color-accent: #b8860b;
}

[data-theme="dark"] {
  --color-bg: #111;
  --color-fg: #ececec;
  --color-muted: #8c8c8c;
  --color-border: #333;
  --color-accent: #c89b1a;  /* slightly brighter amber to hold on dark */
}
```

(Exact dark palette is open for design tweaks — these are starting points, not finals.)

### Toggle

3-state segmented control in the sidebar footer (near the logout button):

```
[ ☀ ] [ ◐ ] [ ☾ ]
 light  auto  dark
```

- "auto" is the default and reads `matchMedia('(prefers-color-scheme: dark)')`.
- Light / dark are explicit overrides written to `localStorage.adminTheme`.
- Reading: a tiny inline script in `<head>` of `Admin.astro` sets `data-theme` on `<html>` *before* first paint, to avoid the flash.

```html
<script>
  (() => {
    const pref = localStorage.getItem('adminTheme') || 'auto';
    const dark = pref === 'dark' || (pref === 'auto' &&
      matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.documentElement.dataset.themePref = pref;
  })();
</script>
```

The toggle component updates `localStorage`, re-runs the resolve, and writes the new `data-theme`. A `change` listener on the media query keeps "auto" reactive when the OS preference changes.

### Atelier opt-out

Two clean options; recommending the first.

**A. CSS-only override (recommended).** Each atelier page already sets its own canvas color in a `<style>` block. We tag those pages with `data-atelier` on a root container and add:

```css
[data-atelier] {
  /* Force the page's own palette regardless of theme */
  background: var(--atelier-bg, #1a1200);
  color: var(--atelier-fg, #ececec);
}
```

The shell (sidebar, top bar) still respects theme — atelier just owns the main pane. No JS opt-out needed; nothing changes for atelier maintainers except a single `data-atelier` attribute.

**B. Astro-level prop.** Add `atelier={true}` to `Admin.astro` props, which suppresses `data-theme` on `<html>` for those routes. Heavier-handed; affects the sidebar too.

Going with A.

## Files touched

### Plumbing (3 files)
- `src/styles/tailwind.css` — dark palette under `[data-theme=dark]`
- `src/layouts/Admin.astro` — pre-paint script, toggle component, `data-atelier` plumbing
- `src/components/ThemeToggle.svelte` (new) — 3-state segmented control

### Shell unification (1 file)
- `src/styles/admin.css` — convert the 56 hardcoded hex colors (sidebar, footers, modals, tables) to token references. The sidebar's existing `#1a1a1a` becomes either `var(--color-bg)` in dark mode or a darker shade like `color-mix(in srgb, var(--color-bg) 90%, black)`; light mode picks an off-white or `var(--color-bg)`.

### Per-page audit (≈18 admin pages, atelier excluded)

Non-atelier admin pages with `<style>` block hex colors:

```
admin/dashboard.astro            (4)
admin/settings.astro             (4)
admin/api-keys.astro
admin/bookmarks.astro
admin/files.astro
admin/hecatomb.astro
admin/catalog/index.astro        (1)
admin/catalog/new.astro
admin/catalog/edit.astro
admin/jobs/index.astro
admin/jobs/detail.astro
admin/latents/index.astro
admin/latents/new.astro
admin/latents/detail.astro
admin/search/index.astro         (42)  ← largest
admin/search/detail.astro
admin/search/midden.astro
admin/search/slop.astro
admin/search/failures.astro
admin/search/nomenclator.astro
admin/search/upload.astro
admin/search/workspace.astro
```

Each gets a quick rewrite: hex literal → `var(--color-*)` (or `color-mix` for shades). Status pills, accent dots, and similar narrowly-scoped color uses can stay as-is (semantic colors aren't part of the light/dark axis).

### Atelier (opt out, no code change beyond `data-atelier`)

```
admin/atelier/punctum.astro
admin/atelier/photism.astro
admin/atelier/bullethole.astro
admin/atelier/spectralize.astro
```

Each gets `data-atelier` on its outer container. Existing colors untouched.

### Out of scope
- Public site (`Base.astro` and the catalog pages) — explicit user ask.
- Atelier internals.
- Semantic status colors (success/warn/error pills) — not part of the light/dark axis.
- Player component — already uses tokens; verifies fine on both themes.

## Verification

- Toggle smoke-test: light → dark → auto, refresh, no flash, OS preference flip reflected when in auto mode.
- Visual sweep of every admin page in both modes against a checklist.
- Atelier pages render identically before and after (their bespoke colors).
- Contrast: WCAG AA on body text in both modes (`#ececec` on `#111` = 14.4:1 ✓, `#1a1a1a` on `#fff` = 16.6:1 ✓).
- Lighthouse a11y pass.

## Open questions

1. Does the toggle live in the sidebar footer (recommended) or in `/admin/settings`? Sidebar footer makes it discoverable; settings page keeps the chrome cleaner.
2. Exact dark palette — `#111` for bg or warmer like `#1a1612`? Should the brutalist accent shift on dark, or stay constant? (Mocking both in the PR if asked.)
3. Should atelier-pages-when-in-light-mode lighten their canvases automatically, or stay dark always? Going with "stay always" — the atelier dark canvas is brand, not theme.

## Rollout

Plan-only PR first (this one). On approval:
- Implementation PR with shell unification + toggle + per-page rewrites.
- Manual visual QA pass on staging-equivalent (this repo deploys straight to prod, so the QA pass is the dev server before merge).
- Ship.
