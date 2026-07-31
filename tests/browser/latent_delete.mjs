/**
 * Latent detail — delete pass.
 *
 * Four relationships here are invisible to `lint-design.mjs` and to pytest:
 *
 *  1. **The DELETE button's ground.** `.action-btn--danger` hardcodes a red
 *     tuned for a light page, and this header can carry the latent's cover art
 *     under a black veil — a MID-tone ground in light mode, where that red
 *     falls under AA, and `--color-danger-on-overlay` (built for the dark slot
 *     faces) reads worse still. The fix is an opaque plate on the button, so
 *     the guard is: the button's own background must be fully OPAQUE and its
 *     ink must clear AA against it, in both themes. Drop the plate and the
 *     artwork shows through — visibly fine on a dark photo, unreadable on a
 *     pale one, and green in every static check. Sixth instance of this class.
 *
 *  2. **The dialog must actually be on top.** `showModal()` puts it in the
 *     browser's top layer, which is exactly why it's used here: the header
 *     sets `isolation: isolate` for its backdrop, and a hand-rolled overlay
 *     inside that stacking context is the trap that cost four fixes
 *     (#573/#574/#575/#581). If someone "simplifies" this to a div, the dialog
 *     still opens — it just renders somewhere useless.
 *
 *  3. **The typed-name gate.** The DELETE button's `disabled` is driven by a
 *     string comparison in one place. Break it in either direction and nothing
 *     errors: either the gate never opens (dead feature) or it never closes
 *     (an irreversible action one stray click away).
 *
 *  4. **The whole path.** Nothing else clicks this button end to end.
 *
 * Self-contained: creates its own throwaway latents, exercises them, deletes
 * what survives. Needs `npm run dev` (4321) and the API (5000).
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
const PORT = 9358;

/** WCAG AA for normal text — the same bar the Style panel warns at. */
const AA = 4.5;
const PREFIX = 'zz-delete-probe';

const dir = mkdtempSync(join(tmpdir(), 'latdel-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${dir}`,
  '--window-size=1440,1200',
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
 * `readyState === 'complete'` only means the DOCUMENT is done — this header is
 * fetched and rendered by an inline script afterwards. Sleeping a fixed couple
 * of seconds and asserting is how the faced-head pass came to report a
 * still-compiling dev server as a product failure. Wait for the thing.
 */
const waitFor = async (expr, ms = 20000) => {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await ev(expr)) return true;
    } catch {}
    await sleep(150);
  }
  return false;
};

/** The header has finished its fetch and painted the real controls. */
const HEADER_READY = `!!document.getElementById('delete-latent')`;

const results = [];
const check = (name, pass, detail) =>
  results.push({ name, pass: !!pass, detail: detail ?? '' });

const box = (sel) => ev(`(() => {
  const el = document.querySelector(${JSON.stringify(sel)});
  if (!el) return null; const r = el.getBoundingClientRect();
  return { x: r.left + r.width/2, y: r.top + r.height/2, w: r.width, h: r.height }; })()`);

const click = async (b) => {
  for (const type of ['mousePressed', 'mouseReleased']) {
    await send('Input.dispatchMouseEvent', {
      type,
      x: b.x,
      y: b.y,
      button: 'left',
      clickCount: 1,
      buttons: type === 'mousePressed' ? 1 : 0,
    });
    await sleep(40);
  }
  await sleep(250);
};

const typeText = async (text) => {
  for (const ch of text) {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', text: ch });
    await sleep(12);
  }
  await sleep(150);
};

/** Contrast maths, mirroring src/lib/latentStyles.ts. */
const CONTRAST_FN = `(() => {
  const parse = (c) => (c.match(/[\\d.]+/g) || []).map(Number);
  const lum = (rgb) => {
    const [r,g,b] = rgb.map(v => { v/=255;
      return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
    return 0.2126*r + 0.7152*g + 0.0722*b;
  };
  window.__alpha = (c) => { const p = parse(c); return p.length > 3 ? p[3] : 1; };
  window.__ratio = (fg, bg) => {
    const l1 = lum(parse(fg).slice(0,3)), l2 = lum(parse(bg).slice(0,3));
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (hi + 0.05) / (lo + 0.05);
  };
  return true;
})()`;

// --- API helpers -----------------------------------------------------------
// The browser session carries the cookie; every write goes through the page so
// CSRF and auth are the real ones rather than a second, divergent client.
const api = (method, path, body) =>
  ev(`(async () => {
    const t = await (await fetch('/api/csrf', {credentials:'include'})).json();
    const r = await fetch(${JSON.stringify(path)}, {
      method: ${JSON.stringify(method)},
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': t.csrf_token },
      ${body === undefined ? '' : `body: JSON.stringify(${JSON.stringify(body)}),`}
    });
    return { status: r.status, body: r.status === 204 ? null : await r.json() };
  })()`);

let created = [];
try {
  await send('Page.enable');
  await send('Runtime.enable');

  // Log in against the API directly — cookies ignore port, so the session set
  // on :5000 is the one :4321 sends back.
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
  if (!(await waitFor(`!!document.querySelector('.latent-grid')`))) {
    console.log(
      JSON.stringify({ results: [], error: 'index never rendered — logged in?' }),
    );
    process.exit(3);
  }

  // Two probes: one furnished (so the ledger has real counts), one bare.
  const furnished = (
    await api('POST', '/api/projects', {
      name: `${PREFIX} Furnished`,
      kind: 'session',
    })
  ).body;
  const bare = (
    await api('POST', '/api/projects', { name: `${PREFIX} Bare` })
  ).body;
  created = [furnished.id, bare.id];
  for (let i = 0; i < 3; i++)
    await api('POST', `/api/projects/${furnished.id}/slots`, {});
  await api('POST', `/api/projects/${furnished.id}/documents`, {
    name: 'Liner notes',
  });
  await api('POST', `/api/projects/${furnished.id}/links`, {
    url: 'https://example.com/x',
  });

  // Give the probe a cover image, because the header backdrop is the whole
  // reason the button's plate exists. Without one this pass would measure the
  // easy case and call the hard one green.
  const anyImage = (await api('GET', '/api/media/random?count=1&media_types=image'))
    .body?.items?.[0]?.id;
  if (anyImage) {
    await api('PATCH', `/api/projects/${furnished.id}`, {
      hero_media_item_id: anyImage,
    });
  }

  await goto(`${BASE}/admin/latents/detail?id=${furnished.id}`);
  if (!(await waitFor(HEADER_READY))) {
    console.log(
      JSON.stringify({ results: [], error: 'header never rendered' }),
    );
    process.exit(3);
  }
  await ev(CONTRAST_FN);

  // --- 1. Placement --------------------------------------------------------
  const placement = await ev(`(() => {
    const btn = document.getElementById('delete-latent');
    const summary = btn.closest('.summary');
    const hero = document.getElementById('hero-island');
    const controls = document.querySelector('.header-controls-row');
    const br = btn.getBoundingClientRect(), sr = summary.getBoundingClientRect();
    return {
      inSummary: !!summary,
      isLastChild: summary.lastElementChild === btn,
      rightAligned: (sr.right - br.right) < 4,
      aboveHero: !!hero && (summary.compareDocumentPosition(hero) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
      belowControls: !!controls && (controls.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
      label: btn.textContent.trim(),
      hit: document.elementFromPoint(br.left + br.width/2, br.top + br.height/2) === btn,
      w: Math.round(br.width), h: Math.round(br.height),
    };
  })()`);
  check(
    'button sits at the right edge of the summary line',
    placement.inSummary && placement.isLastChild && placement.rightAligned,
    JSON.stringify(placement),
  );
  check(
    'summary line is below the controls row and above the card images',
    placement.belowControls && placement.aboveHero,
    `belowControls=${placement.belowControls} aboveHero=${placement.aboveHero}`,
  );
  check(
    'button says what it does and is clickable',
    /delete/i.test(placement.label) && placement.hit,
    `label=${JSON.stringify(placement.label)} hit=${placement.hit} ${placement.w}x${placement.h}`,
  );

  // --- 2. Legibility, both themes -----------------------------------------
  // The header on this latent carries cover art. The button must not depend on
  // what the art looks like — i.e. its own background must be OPAQUE.
  check(
    'the cover-art backdrop is actually in play for this pass',
    await ev(`document.querySelector('.latent-header').classList.contains('latent-header--backdrop')`),
    anyImage
      ? 'hero set but .latent-header--backdrop absent'
      : 'no image in the index to use as a hero — the hard case went untested',
  );

  for (const theme of ['light', 'dark']) {
    await ev(`document.documentElement.setAttribute('data-theme', '${theme}')`);
    await sleep(250);
    const m = await ev(`(() => {
      const btn = document.getElementById('delete-latent');
      const cs = getComputedStyle(btn);
      const header = document.querySelector('.latent-header');
      return {
        color: cs.color,
        bg: cs.backgroundColor,
        alpha: window.__alpha(cs.backgroundColor),
        ratio: window.__ratio(cs.color, cs.backgroundColor),
        backdrop: header.classList.contains('latent-header--backdrop'),
      };
    })()`);
    check(
      `${theme}: DELETE keeps its own opaque plate`,
      m.alpha === 1,
      `background=${m.bg} alpha=${m.alpha} (header backdrop: ${m.backdrop})`,
    );
    check(
      `${theme}: DELETE clears AA on its plate`,
      m.ratio >= AA,
      `${m.color} on ${m.bg} = ${m.ratio.toFixed(2)}:1 (need ${AA})`,
    );
  }
  await ev(`document.documentElement.setAttribute('data-theme', 'light')`);
  await sleep(200);

  // --- 3. The dialog opens, and opens ON TOP -------------------------------
  await click(await box('#delete-latent'));
  const opened = await ev(`(() => {
    const d = document.getElementById('delete-dialog');
    const r = d.getBoundingClientRect();
    const at = document.elementFromPoint(r.left + r.width/2, r.top + 8);
    return {
      open: d.open,
      isNativeModal: d.matches(':modal'),
      onTop: !!at && d.contains(at),
      name: document.getElementById('delete-name').textContent.trim(),
      ledger: document.getElementById('delete-ledger').textContent.replace(/\\s+/g, ' ').trim(),
      confirmDisabled: document.getElementById('delete-confirm-btn').disabled,
      focused: document.activeElement === document.getElementById('delete-confirm'),
    };
  })()`);
  check('clicking DELETE opens the dialog', opened.open, JSON.stringify(opened.open));
  check(
    'dialog is a real top-layer modal, not a div in the header stacking context',
    opened.isNativeModal && opened.onTop,
    `:modal=${opened.isNativeModal} elementFromPoint inside=${opened.onTop}`,
  );
  check(
    'dialog names the latent and focuses the confirm field',
    opened.name === `${PREFIX} Furnished` && opened.focused,
    `name=${JSON.stringify(opened.name)} focused=${opened.focused}`,
  );
  check(
    'ledger names what goes and that files stay',
    /3 slots/.test(opened.ledger) &&
      /1 document/.test(opened.ledger) &&
      /search index/i.test(opened.ledger),
    opened.ledger,
  );
  check('DELETE starts disabled', opened.confirmDisabled, '');

  // --- 4. The typed-name gate ---------------------------------------------
  await click(await box('#delete-confirm'));
  await typeText('not the name');
  check(
    'wrong name leaves DELETE disabled',
    await ev(`document.getElementById('delete-confirm-btn').disabled`),
    '',
  );
  await ev(`document.getElementById('delete-confirm').value = ''`);
  await click(await box('#delete-confirm'));
  // Mixed case + surrounding space: the gate is trimmed and case-insensitive
  // on purpose, so this must open it.
  await typeText(`  ${PREFIX.toUpperCase()} FURNISHED  `);
  check(
    'right name (trimmed, any case) enables DELETE',
    !(await ev(`document.getElementById('delete-confirm-btn').disabled`)),
    await ev(`JSON.stringify(document.getElementById('delete-confirm').value)`),
  );

  // --- 5. Escape backs out without deleting -------------------------------
  await send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Escape',
    code: 'Escape',
    windowsVirtualKeyCode: 27,
  });
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Escape',
    code: 'Escape',
    windowsVirtualKeyCode: 27,
  });
  await sleep(300);
  check(
    'Escape closes the dialog',
    !(await ev(`document.getElementById('delete-dialog').open`)),
    '',
  );
  check(
    'Escape deleted nothing',
    (await api('GET', `/api/projects/${furnished.id}`)).status === 200,
    '',
  );

  // --- 6. Reopening starts clean ------------------------------------------
  await click(await box('#delete-latent'));
  check(
    'reopening clears the typed name and re-locks DELETE',
    await ev(`(() => document.getElementById('delete-confirm').value === ''
      && document.getElementById('delete-confirm-btn').disabled)()`),
    '',
  );
  await ev(`document.getElementById('delete-cancel').click()`);
  await sleep(200);
  check(
    'Cancel closes the dialog',
    !(await ev(`document.getElementById('delete-dialog').open`)),
    '',
  );

  // --- 7. A failed save keeps the dialog open -----------------------------
  await click(await box('#delete-latent'));
  await click(await box('#delete-confirm'));
  await typeText(`${PREFIX} Furnished`);
  // Break the network the way a dead API breaks it, without stopping uvicorn.
  await ev(`window.__realFetch = window.fetch;
    window.fetch = () => Promise.reject(new Error('offline'))`);
  await ev(`document.getElementById('delete-confirm-btn').click()`);
  await sleep(600);
  const failed = await ev(`(() => ({
    open: document.getElementById('delete-dialog').open,
    error: document.getElementById('delete-error').hidden ? null
         : document.getElementById('delete-error').textContent.trim(),
    reEnabled: !document.getElementById('delete-confirm-btn').disabled,
  }))()`);
  // Put the network back before anything else tries to use it.
  await ev(`window.fetch = window.__realFetch`);
  check(
    'a failed delete keeps the dialog open and says so',
    failed.open && !!failed.error && failed.reEnabled,
    JSON.stringify(failed),
  );
  check(
    'the failed delete really did not delete',
    (await api('GET', `/api/projects/${furnished.id}`)).status === 200,
    '',
  );

  // --- 8. The whole path, for real ----------------------------------------
  await goto(`${BASE}/admin/latents/detail?id=${bare.id}`);
  await waitFor(HEADER_READY);
  const bareLedger = await ev(`(() => {
    document.getElementById('delete-latent').click();
    return document.getElementById('delete-ledger').textContent.replace(/\\s+/g,' ').trim();
  })()`);
  check(
    'an empty latent reads as empty, not as a row of zeroes',
    !/all 0 file/.test(bareLedger) &&
      !/0 slot/.test(bareLedger) &&
      !/0 document/.test(bareLedger),
    bareLedger,
  );
  await click(await box('#delete-confirm'));
  await typeText(`${PREFIX} Bare`);
  await ev(`document.getElementById('delete-confirm-btn').click()`);
  await waitFor(`window.location.pathname === '/admin/latents'`, 10000);
  check(
    'confirming deletes the latent and lands on the index',
    (await ev(`window.location.pathname`)) === '/admin/latents',
    await ev(`window.location.pathname`),
  );
  const gone = await api('GET', `/api/projects/${bare.id}`);
  check('the deleted latent is really gone', gone.status === 404, `HTTP ${gone.status}`);
  created = created.filter((c) => c !== bare.id);
} catch (e) {
  results.push({ name: 'harness', pass: false, detail: String(e?.message || e) });
} finally {
  for (const cid of created) {
    try {
      await api('DELETE', `/api/projects/${cid}`);
    } catch {}
  }
  try {
    ws.close();
  } catch {}
  chrome.kill();
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {}
}

console.log(JSON.stringify({ results }));
process.exit(results.every((r) => r.pass) ? 0 : 1);
