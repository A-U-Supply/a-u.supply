/*
 * Shared helpers for latent section/slot styles
 * (docs/plans/2026-07-17-latent-section-styles.md).
 *
 * Colors here are all server-validated `#rrggbb`, but the client re-validates
 * with HEX_RE before writing anything into a `style` attribute — the regex is
 * the entire style-injection defense on both sides.
 */

export const HEX_RE = /^#[0-9a-f]{6}$/i;

export function safeHex(v: string | null | undefined): string | null {
  return v && HEX_RE.test(v) ? v.toLowerCase() : null;
}

/** Fixed detail-page sections, in page order. Keys match the server's
 * VALID_SECTION_KEYS and the `data-section` attributes on detail.astro. */
export const SECTION_KEYS = [
  'repo',
  'links',
  'docs',
  'slots',
  'playlists',
  'slideshow',
  'loose',
  'threads',
] as const;
export type SectionKey = (typeof SECTION_KEYS)[number];

export const SECTION_LABELS: Record<SectionKey, string> = {
  repo: 'Repo',
  links: 'Links',
  docs: 'Documents',
  slots: 'Slots',
  playlists: 'Playlists',
  slideshow: 'Slideshows',
  loose: 'Loose files',
  threads: 'Threads',
};

/** Default palette tokens (defined in src/styles/tailwind.css, both themes). */
export const SECTION_TOKENS: Record<SectionKey, string> = {
  repo: 'var(--latent-sec-repo)',
  links: 'var(--latent-sec-links)',
  docs: 'var(--latent-sec-docs)',
  slots: 'var(--latent-sec-slots)',
  playlists: 'var(--latent-sec-playlists)',
  slideshow: 'var(--latent-sec-slideshow)',
  loose: 'var(--latent-sec-loose)',
  threads: 'var(--latent-sec-threads)',
};

/** Head-band tint strength for face-less accented cards. Must match the
 * color-mix percentages in global.css (.latent-band) and the face-less
 * band rules in detail.astro / LatentSlots.svelte. */
export const WASH_STRENGTH = 0.12;

/** Image-face treatments — the hero-card vocabulary (server
 * VALID_HERO_STYLES). Solid faces ignore treatment. */
export const FACE_TREATMENTS = ['scrim', 'plate', 'treat'] as const;
export type FaceTreatment = (typeof FACE_TREATMENTS)[number];

/** Representative ground for contrast checks over scrim/treat faces — the
 * overlay/brightness clamps guarantee a dark field in both themes, so one
 * check against this stands in for per-theme math. */
export const OVERLAY_GROUND = '#262626';

/** Effective accent — JS mirror of the server's `_slot_summary` formula:
 * manual override > solid face color > extracted auto. Sections have no
 * server-computed accent, so they always go through this. */
export function effectiveAccent(
  style: Record<string, string> | null | undefined,
  accentAuto: string | null = null,
): string | null {
  const st = style || {};
  return (
    safeHex(st.accent) ||
    (st.bg_mode === 'solid' ? safeHex(st.bg_color) : null) ||
    safeHex(accentAuto)
  );
}

/** The viewer's active theme (Admin.astro stamps data-theme pre-paint). */
export function currentTheme(): ThemeName {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

/** Re-derive theme-dependent computed colors (solid-face text) when the
 * user toggles the theme. Returns a disposer — call it on unmount. */
export function watchTheme(cb: (t: ThemeName) => void): () => void {
  const mo = new MutationObserver(() => cb(currentTheme()));
  mo.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });
  return () => mo.disconnect();
}

/*
 * Theme grounds for cross-theme contrast math. You can't getComputedStyle
 * the theme that ISN'T active, so these duplicate --color-surface and
 * --color-fg/-text from src/styles/tailwind.css — keep in sync with the
 * token definitions there.
 */
export const THEME_SURFACES = {
  light: { surface: '#fafafa', text: '#1a1a1a' },
  dark: { surface: '#1a1a1a', text: '#ececec' },
} as const;
export type ThemeName = keyof typeof THEME_SURFACES;

type Rgb = [number, number, number];
/** Hue in [0, 360), saturation/value in [0, 1]. */
type Hsv = [number, number, number];

export function hexToRgb(hex: string): Rgb {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

export function rgbToHex([r, g, b]: Rgb): string {
  const c = (n: number) =>
    Math.round(Math.max(0, Math.min(255, n)))
      .toString(16)
      .padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
}

/** For ColorPicker.svelte's saturation/value square + hue slider. */
export function rgbToHsv([r, g, b]: Rgb): Hsv {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const d = max - min;
  let h = 0;
  if (d !== 0) {
    if (max === rn) h = 60 * (((gn - bn) / d) % 6);
    else if (max === gn) h = 60 * ((bn - rn) / d + 2);
    else h = 60 * ((rn - gn) / d + 4);
  }
  if (h < 0) h += 360;
  const v = max;
  const s = max === 0 ? 0 : d / max;
  return [h, s, v];
}

export function hsvToRgb([h, s, v]: Hsv): Rgb {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let rp = 0;
  let gp = 0;
  let bp = 0;
  if (h < 60) [rp, gp, bp] = [c, x, 0];
  else if (h < 120) [rp, gp, bp] = [x, c, 0];
  else if (h < 180) [rp, gp, bp] = [0, c, x];
  else if (h < 240) [rp, gp, bp] = [0, x, c];
  else if (h < 300) [rp, gp, bp] = [x, 0, c];
  else [rp, gp, bp] = [c, 0, x];
  return [(rp + m) * 255, (gp + m) * 255, (bp + m) * 255];
}

/** JS mirror of `color-mix(in srgb, a <t*100>%, b)` so warning math matches
 * the rendered wash. */
export function blend(a: string, b: string, t: number): string {
  const ar = hexToRgb(a);
  const br = hexToRgb(b);
  return rgbToHex([
    ar[0] * t + br[0] * (1 - t),
    ar[1] * t + br[1] * (1 - t),
    ar[2] * t + br[2] * (1 - t),
  ]);
}

/** WCAG relative luminance (linearized sRGB). */
export function relLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const s = v / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two hex colors, 1..21. */
export function contrastRatio(a: string, b: string): number {
  const la = relLuminance(a);
  const lb = relLuminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** The effective card ground once a wash color is blended into the theme
 * surface at WASH_STRENGTH. `washColor` null = plain surface. */
export function effectiveGround(
  washColor: string | null,
  theme: ThemeName,
): string {
  const surface = THEME_SURFACES[theme].surface;
  return washColor ? blend(washColor, surface, WASH_STRENGTH) : surface;
}

/** Pick a legible default text color for a ground: the theme's own text
 * color when it clears WCAG AA (4.5), else whichever of black/white
 * contrasts harder. Washes are clamped low, so this almost always returns
 * the theme text — the escape hatch exists for future stronger washes. */
export function autoTextColor(ground: string, theme: ThemeName): string {
  const themed = THEME_SURFACES[theme].text;
  if (contrastRatio(themed, ground) >= 4.5) return themed;
  return contrastRatio('#111111', ground) >= contrastRatio('#ffffff', ground)
    ? '#111111'
    : '#ffffff';
}
