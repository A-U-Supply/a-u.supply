/**
 * Latents index grid — reorder pass.
 *
 * Three relationships here are invisible to `lint-design.mjs` and to pytest,
 * and each one has a precedent for going wrong:
 *
 *  1. **The grip must win the hit test.** The whole card is covered by a
 *     stretched `.card__link` (z-index 3) so the card stays one click target.
 *     The grip sits at z-index 4. Get that backwards and the grip is still
 *     perfectly visible, still has a hover cursor, and simply cannot be
 *     grabbed — a dead control that every static check calls fine.
 *
 *  2. **The grip's colour.** It is the only card chrome outside
 *     `.card__content`, so it can't inherit the treatment's text colour, and
 *     the TOP of a hero card is bare photograph under every treatment (plate's
 *     opaque strip is pinned to the bottom). The same "child declares its own
 *     colour under a faced head" bug has been reported five times in Latents.
 *     Neither file is wrong alone; only the rendered pair is.
 *
 *  3. **The drop indicator exists at all.** `ghostClass: 'card--landing'` is a
 *     string agreement between the Sortable options and a CSS rule. Rename
 *     either and drags keep working perfectly with no indicator at all.
 *
 * Self-contained: creates its own throwaway latents, exercises them, and
 * deletes them. It does not need a fixture and leaves no residue. The rest of
 * the grid keeps its relative order throughout (positions renormalise, which
 * is by design).
 *
 * Emits one JSON line: {"results": [{name, pass, detail}, ...]}
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = 'http://localhost:4321';
const API = 'http://localhost:5000';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9357;

/** WCAG AA for normal text — the same bar the Style panel warns at. */
const AA = 4.5;
/** A hero card's grip sits on an ARBITRARY photograph, so there is no single
 *  worst case — there are two, and the grip has to clear AA on both. A pale
 *  photo is what breaks white ink; a dark photo is what breaks a theme token.
 *  Checking only one is how a fixed colour passes and still ships unreadable. */
const GROUNDS = { 'a white photo': 'rgb(255,255,255)', 'a black photo': 'rgb(0,0,0)' };

const PREFIX = 'zz-order-probe';

const dir = mkdtempSync(join(tmpdir(), 'idxorder-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${dir}`,
  '--window-size=1440,1400',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-gpu',
  'about:blank',
]);
chrome.stderr.on('data', () => {});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let page;
for (let i = 0; i < 40; i++) {
  try {
    const j = await (await fetch(`http://localhost:${PORT}/json/list`)).json();
    page = j.find((t) => t.type === 'page');
    if (page) break;
  } catch {}
  await sleep(250);
}
if (!page) {
  console.log(JSON.stringify({ results: [], error: 'chrome never came up' }));
  process.exit(3);
}

let id = 0;
const pend = new Map();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.onopen = res;
  ws.onerror = rej;
});
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) {
    const { resolve, reject } = pend.get(m.id);
    pend.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
};
const send = (m, p = {}) => {
  const i = ++id;
  return new Promise((resolve, reject) => {
    pend.set(i, { resolve, reject });
    ws.send(JSON.stringify({ id: i, method: m, params: p }));
  });
};
const ev = async (x) => {
  const r = await send('Runtime.evaluate', {
    expression: x,
    awaitPromise: true,
    returnByValue: true,
  });
  if (r.exceptionDetails)
    throw new Error(r.exceptionDetails.exception?.description);
  return r.result.value;
};
const goto = async (u) => {
  await send('Page.navigate', { url: u });
  for (let i = 0; i < 60; i++) {
    await sleep(250);
    if ((await ev('document.readyState')) === 'complete') return;
  }
};

/**
 * Poll until `expr` is truthy. Returns false on timeout.
 *
 * `readyState === 'complete'` only means the DOCUMENT is done — this grid is
 * fetched and rendered by an inline script afterwards. Sleeping a fixed
 * couple of seconds and then asserting is how the faced-head pass came to
 * report a still-compiling dev server as a product failure (see
 * tests/browser/faced_head_contrast.mjs). Wait for the thing.
 */
const waitFor = async (expr, ms = 15000) => {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await ev(expr)) return true;
    } catch {}
    await sleep(150);
  }
  return false;
};

/** The grid has finished a fetch and painted real cards (not skeletons). */
const GRID_READY = `document.querySelector('.latent-grid')?.getAttribute('aria-busy') === 'false'
  && !!document.querySelector('.latent-grid .card')`;

const results = [];
const check = (name, pass, detail) =>
  results.push({ name, pass: !!pass, detail: detail ?? '' });

/** Titles of the probe cards, in grid order. */
const ALL_NAMES = `Array.from(document.querySelectorAll('.latent-grid .card .card__title'))
  .map(e=>e.textContent.trim())`;
const PROBE_NAMES = `${ALL_NAMES}.filter(n=>n.startsWith(${JSON.stringify(PREFIX)}))`;

const box = (sel, nth = 0) => ev(`(() => {
  const el = document.querySelectorAll(${JSON.stringify(sel)})[${nth}];
  if (!el) return null; const r = el.getBoundingClientRect();
  return { x: r.left + r.width/2, y: r.top + r.height/2, w: r.width, h: r.height }; })()`);

async function dragMouse(from, to, onMid) {
  const steps = 18;
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: from.x, y: from.y,
    button: 'left', clickCount: 1, buttons: 1,
  });
  await sleep(60);
  for (let i = 1; i <= steps; i++) {
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x: from.x + ((to.x - from.x) * i) / steps,
      y: from.y + ((to.y - from.y) * i) / steps,
      button: 'left', buttons: 1,
    });
    await sleep(30);
    if (onMid && i === Math.floor(steps / 2)) await onMid();
  }
  await sleep(80);
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: to.x, y: to.y, button: 'left', buttons: 0,
  });
  await sleep(500);
}

/** Contrast maths, mirroring src/lib/latentStyles.ts. Composites the grip's
 *  semi-transparent backing over `under` before measuring, because a token
 *  like --color-overlay-soft is only meaningful once it is ON something. */
const CONTRAST_FN = `(() => {
  const parse = (c) => (c.match(/[\\d.]+/g) || []).map(Number);
  const over = (fg, bg) => {
    const f = parse(fg), b = parse(bg);
    const a = f.length > 3 ? f[3] : 1;
    return [0,1,2].map(i => f[i]*a + b[i]*(1-a));
  };
  const lum = (rgb) => {
    const [r,g,b] = rgb.map(v => { v/=255;
      return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
    return 0.2126*r + 0.7152*g + 0.0722*b;
  };
  window.__ratio = (fg, bg) => {
    const l1 = lum(parse(fg).slice(0,3)), l2 = lum(bg);
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (hi + 0.05) / (lo + 0.05);
  };
  window.__over = over;
  return true;
})()`;

/** Read every probe card's grip: what it is, what it sits on, and whether the
 *  grip — not the stretched link — is what a click at its centre would hit. */
const PROBE = `(() => {
  return Array.from(document.querySelectorAll('.latent-grid .card'))
    .filter(c => c.querySelector('.card__title').textContent.trim().startsWith(${JSON.stringify(PREFIX)}))
    .map(card => {
      const grip = card.querySelector('.card__grip');
      const cs = getComputedStyle(grip);
      const r = grip.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
      const treatment = ['scrim','plate','treat'].find(t => card.classList.contains('card--' + t)) || 'plain';
      const hero = card.classList.contains('card--hero');
      // A hero grip lands on a photo, so measure BOTH extremes. A plain
      // card's grip sits on the card's own solid background — one ground.
      const grounds = hero ? ${JSON.stringify(GROUNDS)}
                           : { 'the card background': getComputedStyle(card).backgroundColor };
      const contrast = {};
      for (const [label, under] of Object.entries(grounds)) {
        contrast[label] = +window.__ratio(cs.color, window.__over(cs.backgroundColor, under)).toFixed(2);
      }
      return {
        name: card.querySelector('.card__title').textContent.trim(),
        treatment, hero,
        color: cs.color,
        backing: cs.backgroundColor,
        contrast,
        w: Math.round(r.width), h: Math.round(r.height),
        hitsGrip: !!hit && (hit === grip || grip.contains(hit)),
        hitTag: hit ? (hit.className || hit.tagName) : null,
      };
    });
})()`;

let made = [];
const del = async (pid) =>
  ev(`(async()=>(await fetch('/api/projects/${pid}',{method:'DELETE',credentials:'include'})).status)()`);

try {
  await send('Page.enable');
  await send('Runtime.enable');

  await goto(`${BASE}/login`);
  await sleep(1200);
  const status = await ev(`(async()=>{
    const c = await fetch('${API}/api/csrf',{credentials:'include'}).then(r=>r.json());
    return (await fetch('${API}/api/login',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:'dev@local',password:'devpass',csrf_token:c.csrf_token})})).status;
  })()`);
  check('login', status === 200, `status ${status}`);
  if (status !== 200) throw new Error('cannot log in');

  await goto(`${BASE}/admin/latents`);
  if (!(await waitFor(GRID_READY))) {
    check('the grid rendered', false,
      'no cards within 15s — the dev server is still compiling or is in a bad ' +
      'state. This is NOT a reorder failure; restart astro dev.');
    throw new Error('grid never rendered');
  }

  // --- seed: one card per hero treatment, plus a plain one ----------------
  // Find any image already attached to any latent. Deliberately NOT via
  // /api/search: that is a POST through Meilisearch, which is usually not
  // running on a dev box, so it would fail for a reason that has nothing to do
  // with this pass. Walking the latents is slower and always available.
  const anyImage = await ev(`(async()=>{
    const ps = (await (await fetch('/api/projects',{credentials:'include'})).json()).projects || [];
    for (const p of ps) {
      if (p.hero_media_item_id) return p.hero_media_item_id;
      const r = await fetch('/api/projects/'+p.id+'/items',{credentials:'include'});
      if (!r.ok) continue;
      const items = (await r.json()).items || [];
      const im = items.find(i => i.media?.media_type === 'image');
      if (im) return im.media_item_id;
    }
    return null;
  })()`);
  // Informational, not a failure: a DB with no images anywhere is a thinner
  // pass, not a broken one.
  results.push({
    name: 'image available for hero treatments',
    pass: true,
    detail: anyImage
      ? `using ${anyImage}`
      : 'NONE in this DB — hero-treatment checks skipped, plain card still covered',
  });

  const plan = [
    ['plain', null, null],
    ['scrim', anyImage, 'scrim'],
    ['plate', anyImage, 'plate'],
    ['treat', anyImage, 'treat'],
  ].filter(([, img], i) => i === 0 || !!img);

  for (const [label, img, style] of plan) {
    const pid = await ev(`(async()=>{
      const r = await fetch('/api/projects',{method:'POST',credentials:'include',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:'${PREFIX} ${label}',kind:'other'})});
      const p = await r.json();
      ${img ? `await fetch('/api/projects/'+p.id,{method:'PATCH',credentials:'include',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({hero_media_item_id:'${img}',hero_style:'${style}'})});` : ''}
      return p.id;
    })()`);
    made.push(pid);
  }
  check('seeded probe latents', made.length >= 2, `${made.length}: ${plan.map((p) => p[0]).join(', ')}`);

  await goto(`${BASE}/admin/latents`);
  await waitFor(GRID_READY);
  await ev(CONTRAST_FN);

  const probes = await ev(PROBE);
  check('probe cards rendered', probes.length === made.length, `${probes.length}/${made.length}`);

  // --- 1. the grip wins the hit test over the stretched link --------------
  for (const p of probes) {
    check(
      `${p.treatment}: the grip is what a click at its centre hits`,
      p.hitsGrip,
      p.hitsGrip ? '' : `hit ${p.hitTag} instead — the grip is unreachable, drag is dead`,
    );
  }

  // --- 2. the grip is legible on whatever it lands on ---------------------
  for (const p of probes) {
    for (const [ground, ratio] of Object.entries(p.contrast)) {
      check(
        `${p.treatment}: grip clears AA over ${ground}`,
        ratio >= AA,
        `contrast ${ratio} — ${p.color} on ${p.backing || 'no backing'}`,
      );
    }
  }

  // --- the grip is actually a touch target --------------------------------
  const small = probes.filter((p) => p.w < 30 || p.h < 30);
  check('grips are at least 30x30', small.length === 0,
    small.map((p) => `${p.treatment} ${p.w}x${p.h}`).join(', ') || 'all >= 30');

  // --- 3. the drop indicator exists, and the drag works -------------------
  // Assert on the WHOLE grid, not just the probe cards: with only one probe
  // (a DB with no images) the probe list can't change and the check would be
  // vacuous. The probe prefix is for identifying cards to inspect, not for
  // measuring the drag.
  const before = await ev(ALL_NAMES);
  const g = await box('.latent-grid .card__grip', 0);
  const target = await box('.latent-grid .card', 2);
  let sawLanding = false;
  let landingStyled = null;
  await dragMouse(g, target, async () => {
    const l = await ev(`(() => {
      const el = document.querySelector('.latent-grid .card--landing');
      if (!el) return null;
      const cs = getComputedStyle(el);
      return { outline: cs.outlineWidth, outlineStyle: cs.outlineStyle,
               bg: cs.backgroundColor,
               childHidden: getComputedStyle(el.querySelector('.card__title')).visibility };
    })()`);
    sawLanding = !!l;
    landingStyled = l;
  });
  check('a landing slot appears while dragging', sawLanding,
    sawLanding ? JSON.stringify(landingStyled) : '.card--landing never appeared — ghostClass and the CSS rule disagree');
  if (landingStyled) {
    check('the landing slot is visibly marked, not just an empty box',
      landingStyled.outlineStyle !== 'none' && parseFloat(landingStyled.outline) > 0,
      JSON.stringify(landingStyled));
    check('the landing slot hides the dragged card\'s content',
      landingStyled.childHidden === 'hidden', `visibility: ${landingStyled.childHidden}`);
  }

  const after = await ev(ALL_NAMES);
  check('the drag reordered the grid', before.join() !== after.join(),
    `${before.join(' | ')}  ->  ${after.join(' | ')}`);

  await goto(`${BASE}/admin/latents`);
  await waitFor(GRID_READY);
  const reloaded = await ev(ALL_NAMES);
  check('the new order survives a reload', reloaded.join() === after.join(),
    `${reloaded.join(' | ')}`);

  // --- prove the checks can fail -----------------------------------------
  // A green run means nothing unless each guard responds to its own bug.
  await ev(CONTRAST_FN);

  // (a) Re-introduce the colour bug: the grip hardcoding the theme token
  //     instead of riding --color-on-overlay over imagery.
  // The bug is a THEME token on an arbitrary photo. In light mode that ink is
  // dark, so it reads fine on a pale image and vanishes on a dark one — which
  // is exactly why measuring a single ground lets it through. Assert the
  // worst of the two grounds goes under AA.
  const regressedColor = await ev(`(() => {
    const st = document.createElement('style');
    st.id = 'regress-grip-color';
    st.textContent = '.latent-grid .card--hero .card__grip{color:var(--color-text) !important;background:transparent !important;}';
    document.head.appendChild(st);
    const grip = document.querySelector('.latent-grid .card--hero .card__grip');
    const cs = grip ? getComputedStyle(grip) : null;
    const out = cs ? Math.min(...Object.values(${JSON.stringify(GROUNDS)}).map(
      under => window.__ratio(cs.color, window.__over(cs.backgroundColor, under)))) : null;
    st.remove();
    return out === null ? null : +out.toFixed(2);
  })()`);
  check('the contrast check responds to the bug it guards',
    regressedColor !== null && regressedColor < AA,
    regressedColor === null ? 'no hero card to regress' : `worst-ground contrast with the bug re-introduced: ${regressedColor} (must be < ${AA})`);

  // (b) Re-introduce the stacking bug: the link painted above the grip.
  const regressedHit = await ev(`(() => {
    const st = document.createElement('style');
    st.id = 'regress-grip-z';
    st.textContent = '.latent-grid .card__link{z-index:99 !important;}';
    document.head.appendChild(st);
    const grip = document.querySelector('.latent-grid .card__grip');
    const r = grip.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
    const stillGrip = hit === grip || grip.contains(hit);
    st.remove();
    return { stillGrip, hit: hit ? (hit.className || hit.tagName) : null };
  })()`);
  check('the hit-test check responds to the bug it guards',
    regressedHit.stillGrip === false,
    `with the link raised, a click hits ${regressedHit.hit}`);
} catch (e) {
  check('harness completed', false, String(e?.message || e));
} finally {
  // Leave nothing behind. The rest of the grid keeps its relative order;
  // positions renormalise on the next drag, which is by design.
  for (const pid of made) {
    try {
      await del(pid);
    } catch {}
  }
  console.log(JSON.stringify({ results }));
  try {
    ws.close();
  } catch {}
  chrome.kill();
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {}
}
