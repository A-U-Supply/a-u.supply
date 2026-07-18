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
  'loose',
  'threads',
] as const;
export type SectionKey = (typeof SECTION_KEYS)[number];

export const SECTION_LABELS: Record<SectionKey, string> = {
  repo: 'Repo',
  links: 'Links',
  docs: 'Documents',
  slots: 'Slots',
  loose: 'Loose files',
  threads: 'Threads',
};

/** Default palette tokens (defined in src/styles/tailwind.css, both themes). */
export const SECTION_TOKENS: Record<SectionKey, string> = {
  repo: 'var(--latent-sec-repo)',
  links: 'var(--latent-sec-links)',
  docs: 'var(--latent-sec-docs)',
  slots: 'var(--latent-sec-slots)',
  loose: 'var(--latent-sec-loose)',
  threads: 'var(--latent-sec-threads)',
};

/** Background/head-band wash strength. Must match the color-mix percentages
 * in global.css (.latent-band), detail.astro, and LatentSlots.svelte so the
 * contrast warnings compute against what actually renders. */
export const WASH_STRENGTH = 0.12;

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

function hexToRgb(hex: string): Rgb {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

function rgbToHex([r, g, b]: Rgb): string {
  const c = (n: number) =>
    Math.round(Math.max(0, Math.min(255, n)))
      .toString(16)
      .padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
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
