/**
 * The latent detail page must render exactly once.
 *
 * Two separate defects made it render twice, and only a browser can see
 * either — the API was right both times, and every static check was green.
 *
 *  1. **Two module instances.** This script ships as a content-hashed chunk.
 *     A tab that has already opened a latent holds chunk H1, evaluated, with
 *     an astro:page-load listener and its own `mounted`. Deploy H2 under that
 *     open tab and click a latent: ClientRouter swaps in HTML pointing at H2,
 *     the browser evaluates it as a SECOND instance, both mount a full set of
 *     islands, and neither teardown can see the other's components —
 *     `unmount()` is keyed by a WeakMap private to each copy of Svelte. Every
 *     section renders twice; the header doesn't, because it is rebuilt with
 *     innerHTML rather than mounted. That asymmetry is the fingerprint.
 *     Re-importing the same chunk under a cache-busting query makes a second
 *     instance without a redeploy, which is what this file does.
 *
 *  2. **Two init() runs in one instance.** The module both calls `init()` as
 *     it evaluates and listens for astro:page-load; on a client-side nav both
 *     fire, because module evaluation happens with a resolved `__authReady`
 *     left over from the previous page. The guard sat before the by-slug
 *     lookup and the flag was set after it, so whether the second run walked
 *     through came down to whether /api/me or /api/projects/by-slug answered
 *     first — a coin flip on every page load. It self-repaired in the DOM,
 *     which is why it survived: the only visible symptom was the page
 *     fetching everything twice. Counting requests is the assertion.
 *
 * **The pretty URL is the one that matters.** `?id=` short-circuits
 * resolveProjectId with no fetch at all, which closes the window in (2)
 * entirely, so a suite that used the query-string form would pass through the
 * bug. `/admin/latents/<slug>` is a main.py fallback over the built output, so
 * this runs against `dist` on :5000 rather than the dev server.
 *
 * Self-cleaning: creates its own `zz-dblmount` latent and removes it.
 *
 * Emits one JSON line: {"results": [{name, pass, detail}, ...]}
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = 'http://localhost:5000';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9415;
const PREFIX = 'zz-dblmount';

const dir = mkdtempSync(join(tmpdir(), 'dblmount-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${dir}`,
  '--window-size=1500,1200',
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
  for (let i = 0; i < 80; i++) {
    await sleep(200);
    if ((await ev('document.readyState')) === 'complete') return;
  }
};

/** Poll until truthy — islands mount well after readyState completes, and a
 * harness that sleeps eventually reports a cold server as a product bug. */
const waitFor = async (expr, ms = 25000) => {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await ev(expr)) return true;
    } catch {}
    await sleep(150);
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

/** One entry per island, plus the header — which is innerHTML, so it stays at
 * one even when everything mounted doubles. Keeping it here is what tells a
 * reader which failure they are looking at. */
const census = () =>
  ev(`JSON.stringify({
    map: document.querySelectorAll('#map-island .map').length,
    repo: document.querySelectorAll('#repo-island > *').length,
    links: document.querySelectorAll('#links-island .links').length,
    docs: document.querySelectorAll('#docs-island > *').length,
    slots: document.querySelectorAll('#slots-island > *').length,
    playlists: document.querySelectorAll('#playlists-island > *').length,
    slideshow: document.querySelectorAll('#slideshow-island > *').length,
    loose: document.querySelectorAll('#loose-island > *').length,
    threads: document.querySelectorAll('#threads-island > *').length,
    marginalia: document.querySelectorAll('#marginalia-island > *').length,
    nameInputs: document.querySelectorAll('#header .name-input').length,
  })`).then(JSON.parse);

const overOne = (c) =>
  Object.entries(c).filter(([, v]) => v > 1).map(([k, v]) => `${k}=${v}`);
const underOne = (c) =>
  Object.entries(c).filter(([, v]) => v < 1).map(([k]) => k);

/**
 * Record every request the page makes, so a second init() is countable — and
 * hold the slug lookup back so that whether one happens is not a coin flip.
 *
 * The two init() runs raced: the second one walked past the guard only when
 * /api/me answered before /api/projects/by-slug. Left to a local server both
 * land in a millisecond and the outcome is luck, so an unguarded regression
 * would only sometimes turn this red. Slowing the lookup pins the losing
 * ordering, which is the one worth asserting against.
 */
const watchFetches = () =>
  ev(`(() => {
    const orig = window.__origFetch || window.fetch;
    window.__origFetch = orig;
    window.__seen = [];
    window.fetch = function (input, init) {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      window.__seen.push(url);
      if (url.includes('/api/projects/by-slug/')) {
        return new Promise((res) =>
          setTimeout(() => res(orig.call(window, input, init)), 600),
        );
      }
      return orig.call(window, input, init);
    };
    return true;
  })()`);

const seen = (pattern) =>
  ev(
    `(window.__seen || []).filter(u => u.includes(${JSON.stringify(pattern)})).length`,
  );

let latentId = '';
try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1400,
    height: 2600,
    deviceScaleFactor: 1,
    mobile: false,
  });

  await goto(`${BASE}/login`);
  await sleep(1200);
  const status = await ev(`(async()=>{
    const c = await fetch('/api/csrf',{credentials:'include'}).then(r=>r.json());
    return (await fetch('/api/login',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:'dev@local',password:'devpass',csrf_token:c.csrf_token})})).status;
  })()`);
  check('login', status === 200, `status ${status}`);
  if (status !== 200) throw new Error('cannot log in');

  const latent = (
    await api('POST', '/api/projects', { name: `${PREFIX} probe`, kind: 'album' })
  ).body;
  latentId = latent?.id || '';
  const slug = latent?.slug || '';
  if (!latentId || !slug) throw new Error('could not create a latent');
  const href = `/admin/latents/${slug}`;
  await api('POST', `/api/projects/${latentId}/slots`, {});
  await api('POST', `/api/projects/${latentId}/links`, {
    url: 'https://example.com/one',
    label: 'one',
  });

  // --- one init per client-side nav ---------------------------------------
  // A hard load can't show this: `__authReady` doesn't exist yet when the
  // module evaluates, so its own init() bails and only the astro:page-load
  // one runs. The second init only appears on a client-side nav, which is how
  // anyone actually opens a latent.
  await goto(`${BASE}/admin/latents`);
  const linked = await waitFor(`!!document.querySelector('a[href="${href}"]')`);
  check('the index links to the latent', linked, href);
  await watchFetches();
  await ev(
    `document.querySelector('a[href="${href}"]').click()`,
  );
  const arrived = await waitFor(
    `document.querySelectorAll('#map-island .map').length > 0`,
  );
  check('a client-side nav renders the latent', arrived, '');
  await sleep(3000);

  const slugLookups = await seen(`/api/projects/by-slug/${slug}`);
  check(
    'the slug is resolved once, not twice',
    slugLookups === 1,
    `${slugLookups} lookups`,
  );
  const projectLoads = await seen(`/api/projects/${latentId}?`);
  const projectLoadsPlain = await ev(
    `(window.__seen||[]).filter(u => u.endsWith('/api/projects/${latentId}')).length`,
  );
  check(
    'the project is loaded once, not twice',
    projectLoads + projectLoadsPlain === 1,
    `${projectLoads + projectLoadsPlain} loads`,
  );

  const afterNav = await census();
  check(
    'a client-side nav renders each section once',
    overOne(afterNav).length === 0 && underOne(afterNav).length === 0,
    JSON.stringify(afterNav),
  );

  // --- a second module instance -------------------------------------------
  // What a deploy under an open tab does. The chunk URL is re-imported with a
  // query the module graph has never seen, which is the whole mechanism: a
  // different URL is a different module, with its own `mounted` and its own
  // astro:page-load listener.
  const dup = await ev(`(async () => {
    const s = Array.from(document.scripts).find(s => s.src.includes('detail.astro'));
    if (!s) return 'no detail chunk on the page';
    const url = new URL(s.src);
    url.searchParams.set('zzdup', '1');
    await import(url.href);
    return '';
  })()`);
  check('a second instance of the chunk loads', dup === '', dup || 'ok');
  await sleep(3500);

  const afterDup = await census();
  check(
    'a second module instance does not double the page',
    overOne(afterDup).length === 0,
    JSON.stringify(afterDup),
  );
  check(
    'and does not blank it either',
    underOne(afterDup).length === 0,
    JSON.stringify(afterDup),
  );

  // The map is the loudest duplicate on a phone — it sits above every
  // section — and it is also the proof the takeover re-rendered rather than
  // just cleared.
  const chips = await ev(
    `document.querySelectorAll('#map-island .map__chip').length`,
  );
  check('the section map still has its chips', chips > 0, `${chips} chips`);

  // --- and the superseded instance stays down ------------------------------
  // Both instances still hold an astro:page-load listener. The older one must
  // not answer it.
  await ev(`document.dispatchEvent(new Event('astro:page-load'))`);
  await sleep(2500);
  const afterEvent = await census();
  check(
    'a later page-load does not re-mount on top',
    overOne(afterEvent).length === 0 && underOne(afterEvent).length === 0,
    JSON.stringify(afterEvent),
  );

  // A full swap cycle is where standing down earns its keep. before-swap
  // clears both instances' flags, so both are eligible again; emptying the
  // targets would still leave one visible copy, but the loser would have
  // mounted and orphaned a whole page of components — every navigation, for
  // as long as the tab stays open. Counting the fetch is how that shows up.
  await ev(`window.__seen = []`);
  await ev(`document.dispatchEvent(new Event('astro:before-swap'))`);
  await sleep(400);
  await ev(`document.dispatchEvent(new Event('astro:page-load'))`);
  await waitFor(`document.querySelectorAll('#map-island .map').length > 0`);
  await sleep(3000);
  const cycleLoads = await ev(
    `(window.__seen||[]).filter(u => u.endsWith('/api/projects/${latentId}')).length`,
  );
  check(
    'only the newest instance answers a swap cycle',
    cycleLoads === 1,
    `${cycleLoads} project loads`,
  );
  const afterCycle = await census();
  check(
    'the page survives a swap cycle intact',
    overOne(afterCycle).length === 0 && underOne(afterCycle).length === 0,
    JSON.stringify(afterCycle),
  );
} catch (e) {
  check('harness', false, String(e && e.message ? e.message : e));
} finally {
  try {
    if (latentId) await api('DELETE', `/api/projects/${latentId}`);
  } catch {}
  try {
    ws.close();
  } catch {}
  chrome.kill();
  await sleep(400);
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {}
  console.log(JSON.stringify({ results }));
  for (const r of results)
    console.error(`${r.pass ? 'ok  ' : 'FAIL'} ${r.name} — ${r.detail}`);
  process.exit(0);
}
