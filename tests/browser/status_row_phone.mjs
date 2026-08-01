/**
 * The latent detail header's status row, at phone widths.
 *
 * There are FIVE statuses — forming, developing, fixing, shipped, abandoned —
 * and `.status-row` is a single flex item inside a container that wraps its
 * items rather than their contents. Give the row no `flex-wrap` of its own and
 * its minimum width becomes all five buttons unbroken, so it runs off the right
 * edge: `shipped` clipped at the viewport, `abandoned` off-screen entirely. A
 * latent could not be marked abandoned from a phone at all.
 *
 * The check that matters is REACHABILITY, not overflow. Two reasons:
 *
 *  - `document.documentElement.scrollWidth` does not grow — the overflow is
 *    clipped further up the tree, so the "no horizontal overflow at 390px"
 *    assertions in the neighbouring suites stayed green through the whole bug.
 *    Measured, not assumed: they pass with the fix reverted.
 *  - A button inside the viewport can still be unusable. So each one is
 *    hit-tested with `elementFromPoint` at its own centre, and the last one is
 *    actually CLICKED and the status read back from the server. That is the
 *    difference between "it is laid out" and "you can mark this abandoned".
 *
 * 320px is deliberate. Five chips cannot fit one line there at a legible size,
 * and they are not supposed to — the point of the wrap is that the row breaks
 * instead of clipping. So that width asserts every button is still reachable
 * while expecting more than one line.
 *
 * **Proved able to fail.** Restoring `flex-wrap: nowrap` on `.status-row` turns
 * the 320px reachability and line-count checks red; removing the media query as
 * well takes 390 and 360 with them.
 *
 * Self-cleaning: creates its own latent, removes it.
 * Needs `npm run dev` (4321) and the API (5000).
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
const PORT = 9397;
const PREFIX = 'zz-statusrow';
const STATUSES = ['forming', 'developing', 'fixing', 'shipped', 'abandoned'];

const dir = mkdtempSync(join(tmpdir(), 'statusrow-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${dir}`,
  '--window-size=1200,1200',
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

/** Poll until truthy — the header renders client-side, well after readyState. */
const waitFor = async (expr, ms = 20000) => {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await ev(expr)) return true;
    } catch {}
    await sleep(180);
  }
  return false;
};

const api = (method, path, body) =>
  ev(`(async () => {
    const t = await (await fetch('/api/csrf', {credentials:'include'})).json();
    const r = await fetch(${JSON.stringify(path)}, {
      method: ${JSON.stringify(method)}, credentials: 'include',
      headers: {'Content-Type':'application/json','X-CSRF-Token': t.csrf_token},
      ${body === undefined ? '' : `body: JSON.stringify(${JSON.stringify(body)}),`}
    });
    return { status: r.status, body: await r.json().catch(() => null) };
  })()`);

const results = [];
const check = (name, pass, detail) =>
  results.push({ name, pass: !!pass, detail: detail ?? '' });

/**
 * Every status button: where it is, whether it is inside the viewport, and
 * whether a tap at its own centre would actually land on it. `elementFromPoint`
 * is viewport-relative and returns null off-screen, which is exactly the
 * failure being tested for — but it also returns null for anything scrolled out
 * of view vertically, so the row is scrolled into view first.
 */
const measure = () =>
  ev(`(() => {
    const vw = document.documentElement.clientWidth;
    const btns = Array.from(document.querySelectorAll('.status-btn'));
    return JSON.stringify({
      vw,
      count: btns.length,
      lines: new Set(btns.map(b => Math.round(b.getBoundingClientRect().top))).size,
      buttons: btns.map(b => {
        const r = b.getBoundingClientRect();
        const hit = document.elementFromPoint(
          Math.round(r.left + r.width / 2),
          Math.round(r.top + r.height / 2),
        );
        return {
          label: b.textContent.trim(),
          inside: r.left >= -0.5 && r.right <= vw + 0.5,
          hittable: !!(hit && (hit === b || b.contains(hit))),
          fontPx: parseFloat(getComputedStyle(b).fontSize),
          right: Math.round(r.right),
        };
      }),
    });
  })()`).then(JSON.parse);

let latentId = '';
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

  latentId = (
    await api('POST', '/api/projects', { name: `${PREFIX} probe`, kind: 'album' })
  ).body?.id;
  check('a latent to look at', !!latentId);
  if (!latentId) throw new Error('no fixture');

  for (const width of [390, 360, 320]) {
    await send('Emulation.setDeviceMetricsOverride', {
      width,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true,
    });
    await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
    await waitFor(`document.querySelectorAll('.status-btn').length === ${STATUSES.length}`);
    await ev(`document.querySelector('.status-row')?.scrollIntoView({block:'center'})`);
    await sleep(250);

    const m = await measure();
    check(
      `all ${STATUSES.length} statuses render at ${width}px`,
      m.count === STATUSES.length,
      `${m.count} buttons`,
    );
    const unreachable = m.buttons.filter((b) => !b.inside || !b.hittable);
    check(
      `every status is reachable at ${width}px`,
      unreachable.length === 0,
      unreachable.length
        ? unreachable
            .map((b) => `${b.label} (inside=${b.inside} hittable=${b.hittable} right=${b.right} vs ${m.vw})`)
            .join('; ')
        : `${m.lines} line(s)`,
    );
    // Condensing has a floor: shrinking type until it fits is not a fix if
    // nobody can read the result.
    const tiny = m.buttons.filter((b) => b.fontPx < 9);
    check(
      `the condensed labels stay legible at ${width}px`,
      tiny.length === 0,
      `smallest ${Math.min(...m.buttons.map((b) => b.fontPx)).toFixed(1)}px`,
    );
    if (width >= 360) {
      // The media query is meant to do the work at ordinary phone widths; the
      // wrap is the safety net, not the plan.
      check(
        `they still fit one line at ${width}px`,
        m.lines === 1,
        `${m.lines} line(s)`,
      );
    }
  }

  // Reachable means usable. `abandoned` is the one that was off-screen, and
  // it is last, so it is the one a clipped row loses.
  const before = (await api('GET', `/api/projects/${latentId}`)).body?.status;
  check('the fixture starts out forming', before === 'forming', String(before));
  const clicked = await ev(`(() => {
    const b = Array.from(document.querySelectorAll('.status-btn'))
      .find(e => e.dataset.status === 'abandoned');
    if (!b) return 'no abandoned button';
    const r = b.getBoundingClientRect();
    const hit = document.elementFromPoint(
      Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
    if (!(hit && (hit === b || b.contains(hit)))) return 'something else is at its centre';
    hit.click();
    return '';
  })()`);
  check('the last status can actually be tapped at 320px', clicked === '', clicked);
  await sleep(1200);
  const after = (await api('GET', `/api/projects/${latentId}`)).body?.status;
  check(
    'tapping it marks the latent abandoned',
    after === 'abandoned',
    `status is ${after}`,
  );
} catch (e) {
  check('harness completed', false, String(e && e.message));
} finally {
  try {
    if (latentId) await api('DELETE', `/api/projects/${latentId}`);
  } catch {}
  try {
    ws.close();
  } catch {}
  chrome.kill();
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {}
}
console.log(JSON.stringify({ results }));
