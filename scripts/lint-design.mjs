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

  if (jsonMode) {
    process.stdout.write(JSON.stringify(allFindings, null, 2) + '\n');
  } else {
    if (allFindings.length === 0) {
      console.log(
        `lint-design: ✓ ${files.length} files clean — no hardcoded colors in style blocks.`,
      );
    } else {
      console.error(`lint-design: ✗ ${allFindings.length} issue(s):\n`);
      for (const f of allFindings) {
        console.error(`  ${f.file}:${f.line} (${f.kind}: ${f.match})`);
        console.error(`    ${f.source}`);
      }
      console.error(
        `\nReplace hardcoded colors with tokens (--color-*, --color-status-*, --color-overlay*).`,
      );
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
