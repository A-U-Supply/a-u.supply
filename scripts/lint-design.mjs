#!/usr/bin/env node
/**
 * Design lint — flags admin pages that drift from the brutalist tokens.
 *
 * What it catches (and refuses):
 *   - Hardcoded hex colors (#rgb / #rrggbb) inside <style> blocks
 *   - Hardcoded rgb()/rgba()/hsl()/hsla() colors inside <style> blocks
 *   - Named CSS colors (color: green/red/blue/...) inside <style> blocks
 *   - Removed legacy class names in markup (`.btn-sm`, `.btn-save`, etc)
 *     once we move past Phase 1
 *   - Svelte components that declare `position: fixed` without going
 *     through one of the two sanctioned overlay helpers (see below)
 *
 * What it ignores:
 *   - The Atelier — Punctum, Photism, Bullethole, Spectralize live on
 *     their own design language by design.
 *   - The dark/transparent overlay tokens themselves (`tailwind.css`
 *     defines them).
 *   - Log terminal colors (#0a0a0a + #e0e0e0 in jobs/detail.astro), as
 *     they're intentionally dark-on-light for terminal output in both
 *     modes — those lines are explicitly allowlisted by file+pattern.
 *   - `<script>` blocks — they sometimes legitimately set hex inline
 *     values for canvases, dataURIs, etc. Style blocks only.
 *
 * Run:
 *   node scripts/lint-design.mjs              # report and exit non-zero on findings
 *   node scripts/lint-design.mjs --json       # JSON output for tests / CI
 *
 * The overlay rule, and why it exists:
 *
 *   Latents sections set `isolation: isolate` AND style every direct child
 *   `position: relative; z-index: 2` at a specificity a component's scoped
 *   rule can't beat. An overlay that just says `position: fixed` in its own
 *   <style> therefore does one of two things, silently, with a green build:
 *     - renders in page flow at the bottom of its section, often below the
 *       fold with nothing to scroll to, or
 *     - stays fixed but is painted over by any later section.
 *
 *   This has been re-fixed at least four times (#573, #574, #575, and the
 *   Style panel). So: a component with `position: fixed` must import either
 *
 *     src/lib/portal.ts        — moves the node to <body>, escaping the
 *                                stacking context, or
 *     src/lib/anchoredPanel.ts — places a panel against a button's rect and
 *                                clamps it inside the viewport
 *
 *   ...or be allowlisted below with a reason. The point isn't the import;
 *   it's that there is no third way to write an overlay by hand.
 *
 * Exit status: 0 clean, 1 findings, 2 internal error.
 */
import { readdir, readFile, stat } from 'node:fs/promises';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
// Scope: admin pages only for now. Svelte component cleanup is a
// follow-up — they have years of bespoke styling we don't want to
// regress in a single PR. Once components are clean, add
// `{ dir: 'src/components', exts: ['.svelte', '.astro'] }` here.
const TARGETS = [{ dir: 'src/pages/admin', exts: ['.astro'] }];
const SKIP_PATHS = [
  // The Atelier section — Punctum, Photism, Bullethole, Spectralize —
  // is intentionally off the standard brutalist house style.
  'src/pages/admin/atelier',
  // The Stacks blueprint. Years of careful tuning. The polish pass
  // explicitly left this alone — don't lint-regress it as a side
  // effect of this script.
  'src/pages/admin/search/index.astro',
  'src/pages/admin/search/detail.astro',
];

// File + substring allowlist. Each entry: the relative path, and a
// fragment of the line that must contain the offending color. If the
// offending line contains the fragment, we ignore it.
const ALLOWLIST = [
  // jobs/detail.astro: terminal log block is intentionally dark-on-light
  // in both modes (mimics a console).
  ['src/pages/admin/jobs/detail.astro', '#0a0a0a'],
  ['src/pages/admin/jobs/detail.astro', '#e0e0e0'],
];

/**
 * Components allowed to hand-roll `position: fixed`, with the reason. These
 * are page furniture mounted at the document root, not overlays inside a
 * Latents section — the trap doesn't apply to them.
 */
const OVERLAY_ALLOWLIST = {
  'src/components/Player.svelte':
    'the persistent player bar, mounted by the layouts at document root',
  'src/components/Pukebox.svelte':
    'owns its own page (/pukebox); no section wrapper to escape',
  'src/components/litany-exp/ViMode.svelte':
    'Litany experiment chrome, mounted inside LitanyExp, not a Latents section',
  'src/components/litany-exp/SampleSearchModal.svelte':
    'Litany experiment, mounted inside LitanyExp, not a Latents section',
};

const FIXED_RE = /position:\s*fixed/;
const OVERLAY_HELPER_RE =
  /from\s+['"][^'"]*\/(portal|anchoredPanel)(\.ts)?['"]/;

const HEX_RE = /#([0-9a-fA-F]{3,8})\b/;
const RGB_RE = /\b(?:rgba?|hsla?)\(/;
const NAMED_RE =
  /\bcolor\s*:\s*(green|red|blue|yellow|black|white|orange|purple|pink)\b/i;

async function walk(dir, exts, out = []) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    const rel = relative(ROOT, full);
    if (SKIP_PATHS.some((s) => rel.startsWith(s))) continue;
    if (entry.isDirectory()) {
      await walk(full, exts, out);
    } else if (exts.some((e) => entry.name.endsWith(e))) {
      out.push(full);
    }
  }
  return out;
}

function inStyleBlock(text, lineIdx) {
  // Count <style ...> and </style> tags before lineIdx. Odd count = we're
  // inside a style block at that line.
  const before = text.slice(0, lineIdx);
  const opens = (before.match(/<style\b[^>]*>/gi) || []).length;
  const closes = (before.match(/<\/style>/gi) || []).length;
  return opens > closes;
}

function lineOffsets(text) {
  const offsets = [0];
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) === 10) offsets.push(i + 1);
  }
  return offsets;
}

async function lintFile(filePath) {
  const rel = relative(ROOT, filePath);
  const text = await readFile(filePath, 'utf8');
  const offsets = lineOffsets(text);
  const lines = text.split('\n');
  const findings = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!inStyleBlock(text, offsets[i])) continue;
    if (line.trim().startsWith('//') || line.trim().startsWith('*')) continue;

    const allowed = ALLOWLIST.some(
      ([f, frag]) => rel === f && line.includes(frag),
    );
    if (allowed) continue;

    let m;
    if ((m = HEX_RE.exec(line))) {
      findings.push({
        file: rel,
        line: i + 1,
        kind: 'hex',
        match: m[0],
        source: line.trim(),
      });
    } else if ((m = RGB_RE.exec(line))) {
      findings.push({
        file: rel,
        line: i + 1,
        kind: 'rgba',
        match: m[0],
        source: line.trim(),
      });
    } else if ((m = NAMED_RE.exec(line))) {
      findings.push({
        file: rel,
        line: i + 1,
        kind: 'named',
        match: m[0],
        source: line.trim(),
      });
    }
  }

  return findings;
}

/**
 * The overlay-discipline pass. Separate from the colour pass because it is
 * per-FILE, not per-line: what matters is whether the component as a whole
 * reached for a sanctioned helper.
 */
async function lintOverlay(filePath) {
  const rel = relative(ROOT, filePath);
  if (rel in OVERLAY_ALLOWLIST) return [];
  const text = await readFile(filePath, 'utf8');

  const offsets = lineOffsets(text);
  const lines = text.split('\n');
  const hits = [];
  for (let i = 0; i < lines.length; i++) {
    if (!inStyleBlock(text, offsets[i])) continue;
    if (!FIXED_RE.test(lines[i])) continue;
    hits.push({ line: i + 1, source: lines[i].trim() });
  }
  if (hits.length === 0) return [];
  if (OVERLAY_HELPER_RE.test(text)) return [];

  // Report the first occurrence only — the finding is about the file.
  return [
    {
      file: rel,
      line: hits[0].line,
      kind: 'overlay',
      match: 'position: fixed',
      source: hits[0].source,
    },
  ];
}

async function main() {
  const jsonMode = process.argv.includes('--json');

  const files = [];
  for (const t of TARGETS) {
    const dir = join(ROOT, t.dir);
    try {
      await stat(dir);
    } catch {
      continue;
    }
    await walk(dir, t.exts, files);
  }

  const allFindings = [];
  for (const f of files) {
    const findings = await lintFile(f);
    allFindings.push(...findings);
  }

  // Overlay discipline runs over the components tree, which the colour pass
  // doesn't cover yet (see TARGETS).
  const componentFiles = [];
  await walk(join(ROOT, 'src/components'), ['.svelte'], componentFiles);
  for (const f of componentFiles) {
    allFindings.push(...(await lintOverlay(f)));
  }

  if (jsonMode) {
    process.stdout.write(JSON.stringify(allFindings, null, 2) + '\n');
  } else {
    if (allFindings.length === 0) {
      console.log(
        `lint-design: ✓ ${files.length} pages clean (colors) + ` +
          `${componentFiles.length} components clean (overlay discipline).`,
      );
    } else {
      console.error(`lint-design: ✗ ${allFindings.length} issue(s):\n`);
      for (const f of allFindings) {
        console.error(`  ${f.file}:${f.line} (${f.kind}: ${f.match})`);
        console.error(`    ${f.source}`);
      }
      if (allFindings.some((f) => f.kind !== 'overlay')) {
        console.error(
          `\nReplace hardcoded colors with tokens (--color-*, --color-status-*, --color-overlay*).`,
        );
      }
      if (allFindings.some((f) => f.kind === 'overlay')) {
        console.error(
          `\nAn overlay with 'position: fixed' must import src/lib/portal.ts ` +
            `(to escape a section's stacking context) or src/lib/anchoredPanel.ts ` +
            `(to stay inside the viewport) — or be allowlisted in this script ` +
            `with a reason. See the header comment for why.`,
        );
      }
      console.error(
        `See docs/frontend.md "Status colors" + "New admin page checklist".`,
      );
    }
  }

  process.exit(allFindings.length === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error('lint-design: internal error', err);
  process.exit(2);
});
