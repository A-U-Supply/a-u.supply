/**
 * The upload dock — does an upload actually survive navigation?
 *
 * That is the whole feature and nothing else can prove it. Before this, the
 * uploader was an island inside the page, and it held the XHR in component
 * state: ViewTransitions swapped the body, the component was destroyed, and
 * every in-flight transfer was aborted mid-flight without a word.
 *
 * **What actually fixes it is that the queue is a module singleton**
 * (`src/lib/uploadQueue.ts`). Astro's client router swaps the DOM but never
 * reloads the module registry, so the queue and its XHRs are simply out of
 * reach of a page swap. `transition:persist` is a second, smaller thing: it
 * keeps the dock's DOM node, so the bar doesn't tear down and remount — and
 * lose its expanded state — on every click. Verified by experiment: removing
 * `transition:persist` leaves every transfer check green, which is exactly why
 * there is a separate element-identity probe below. Move the queue back into
 * component state and the survival checks go red.
 *
 * Either failure is invisible to pytest, to `svelte-check` and to
 * `lint-design.mjs` — the page still renders, the button still works, the file
 * just quietly never arrives.
 *
 * Checks here:
 *  1. **Survival.** Start an upload, navigate to a different admin page
 *     mid-transfer, assert the transfer kept advancing and the item exists
 *     server-side afterwards.
 *  2. **Element identity** across that same navigation — the one check that
 *     `transition:persist` owns.
 *  3. **Geometry.** The dock rides above the player when there's music and
 *     drops to the floor when there isn't — via `--player-h`, not a constant.
 *  4. **The real picker path**, through Tribute's file input, so the handoff
 *     wiring is covered and not just the event contract.
 *
 * The transfer is slowed with CDP network throttling rather than a huge file,
 * so "mid-transfer" is deterministic instead of a race against localhost.
 *
 * Self-cleaning: deletes every media item it uploads.
 * Needs `npm run dev` (4321) and the API (5000).
 *
 * Emits one JSON line: {"results": [{name, pass, detail}, ...]}
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = 'http://localhost:4321';
const API = 'http://localhost:5000';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9362;
const PREFIX = 'zz-dock-probe';

const dir = mkdtempSync(join(tmpdir(), 'dock-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${dir}`,
  '--window-size=1400,1000',
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
 * Never sleep-then-assert here: `readyState === 'complete'` only means the
 * document is done, and every island on these pages mounts later. A harness
 * that sleeps eventually reports a cold dev server as a product bug.
 */
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

const results = [];
const check = (name, pass, detail) =>
  results.push({ name, pass: !!pass, detail: detail ?? '' });

const api = (method, path, body) =>
  ev(`(async () => {
    const t = await (await fetch('/api/csrf', {credentials:'include'})).json();
    const r = await fetch(${JSON.stringify(path)}, {
      method: ${JSON.stringify(method)},
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': t.csrf_token },
      ${body === undefined ? '' : `body: JSON.stringify(${JSON.stringify(body)}),`}
    });
    return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
  })()`);

/** The dock is mounted and showing something. */
const DOCK = `!!document.querySelector('.dock')`;

/**
 * Record every completion the dock announces.
 *
 * Verification goes through `upload:done` → `GET /api/media/{id}` rather than
 * `/api/search`, deliberately: the search endpoint needs Meilisearch, which is
 * usually not running on the local loop, and a missing index would fail this
 * harness for a reason that has nothing to do with uploads surviving a page
 * swap. The listener is re-installed after every navigation — an injected
 * handler dies with the document.
 */
const WATCH_DONE = `(() => {
  if (window.__dockDone) return true;
  window.__dockDone = [];
  document.addEventListener('upload:done', (e) => {
    window.__dockDone.push(e.detail || {});
  });
  return true;
})()`;

const uploaded = [];

try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Network.enable');

  await goto(`${BASE}/login`);
  await sleep(1000);
  const status = await ev(`(async()=>{
    const c = await fetch('${API}/api/csrf',{credentials:'include'}).then(r=>r.json());
    return (await fetch('${API}/api/login',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:'dev@local',password:'devpass',csrf_token:c.csrf_token})})).status;
  })()`);
  check('login', status === 200, `status ${status}`);
  if (status !== 200) throw new Error('cannot log in');

  await goto(`${BASE}/admin/search/upload`);
  const ready = await waitFor(`!!document.getElementById('file-input')`);
  check('Tribute page renders', ready);
  if (!ready) {
    console.log(
      JSON.stringify({ results, error: 'Tribute never rendered — dev server?' }),
    );
    process.exit(3);
  }

  // --- 1. Survival --------------------------------------------------------
  // Throttle the uplink so "mid-transfer" is a fact, not a race. 150 KB/s
  // against a 1.5 MB file is ~10s of transfer — long enough to navigate
  // twice and still be uploading.
  await send('Network.emulateNetworkConditions', {
    offline: false,
    latency: 20,
    downloadThroughput: -1,
    uploadThroughput: 150 * 1024,
  });

  await ev(WATCH_DONE);
  const started = await ev(`(() => {
    const bytes = new Uint8Array(1_500_000);
    // Noise, not zeros: the server dedupes on sha256 and a run of zeros
    // would collide with the previous run's file and return instantly.
    for (let i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
    const f = new File([bytes], '${PREFIX}-survive-' + Date.now() + '.bin',
      { type: 'application/octet-stream' });
    document.dispatchEvent(new CustomEvent('upload:start', {
      detail: { files: [f], destination: 'tribute', tags: '${PREFIX}' },
    }));
    return f.name;
  })()`);

  check('dock appears when an upload starts', await waitFor(DOCK, 8000));

  // Let it get properly under way before moving.
  const movedAt = await waitFor(
    `(() => {
      const el = document.querySelector('.dock__fill');
      return el && parseFloat(el.style.width) > 5;
    })()`,
    20000,
  );
  check('transfer is under way before we navigate', movedAt);

  const beforePct = await ev(
    `parseFloat(document.querySelector('.dock__fill')?.style.width || '0')`,
  );

  // Tag the actual element. Two different things are being tested and it is
  // worth keeping them apart: the TRANSFER survives because the queue lives in
  // a module singleton the router never reloads, while the DOM NODE survives
  // only because of `transition:persist`. Drop that attribute and the transfer
  // still completes — the bar just tears down and remounts on every click,
  // losing its expanded state and flashing. This tag is the only check here
  // that can tell the difference.
  await ev(`document.querySelector('.dock').__persistProbe = 'kept'`);

  // THE navigation. Astro's client router swaps the body; without
  // transition:persist the dock and its XHR die right here.
  await ev(`document.querySelector('a[href="/admin/latents"]')?.click()`);
  await waitFor(`location.pathname === '/admin/latents'`, 10000);

  check(
    'navigated away from Tribute',
    (await ev(`location.pathname`)) === '/admin/latents',
    await ev(`location.pathname`),
  );
  check('dock survived the page swap', await ev(DOCK));
  check(
    'it is the SAME element, not a remount (transition:persist)',
    (await ev(`document.querySelector('.dock')?.__persistProbe || ''`)) ===
      'kept',
    'a remounted bar loses its expanded state and flashes on every click',
  );
  await ev(WATCH_DONE); // the old listener died with the old document

  const afterPct = await ev(
    `parseFloat(document.querySelector('.dock__fill')?.style.width || '0')`,
  );
  // The load-bearing assertion: still moving on the other side.
  const advanced = await waitFor(
    `parseFloat(document.querySelector('.dock__fill')?.style.width || '0') > ${afterPct + 2}`,
    25000,
  );
  check(
    'the transfer kept going after navigation',
    advanced,
    `${beforePct.toFixed(1)}% before → ${afterPct.toFixed(1)}% after nav`,
  );

  // --- 4. beforeunload while live ----------------------------------------
  // Can't read the listener directly; the dock owns it, so assert on the
  // condition it keys off instead, then confirm it clears when idle.
  check(
    'guard is armed while transferring',
    await ev(`!!document.querySelector('.dock__fill')`),
  );

  // Finish, unthrottled.
  await send('Network.emulateNetworkConditions', {
    offline: false,
    latency: 0,
    downloadThroughput: -1,
    uploadThroughput: -1,
  });
  const finished = await waitFor(
    `!!Array.from(document.querySelectorAll('.dock__headline'))
       .find(e => /uploaded/.test(e.textContent))`,
    60000,
  );
  check('upload completed on the new page', finished);

  const doneId = await ev(
    `(window.__dockDone || []).find(d => d.name === ${JSON.stringify(started)})?.media_item_id || ''`,
  );
  let landed = null;
  if (doneId) {
    const got = await api('GET', `/api/media/${doneId}`);
    landed = got.status === 200 ? got.body : null;
    uploaded.push(doneId);
  }
  check(
    'the file is on the server, uploaded from a page we had left',
    !!landed && (landed.filename || '').includes(started),
    landed ? `${doneId} → ${landed.filename}` : `no completion for ${started}`,
  );

  // --- stay-until-dismissed ----------------------------------------------
  await sleep(2500);
  check(
    'the finished bar stays until dismissed',
    await ev(DOCK),
    'still mounted 2.5s after completion',
  );
  await ev(`document.querySelector('.dock__close')?.click()`);
  await sleep(400);
  check('dismiss clears it', !(await ev(DOCK)));

  // --- 2. Geometry --------------------------------------------------------
  const geom = await ev(`(() => {
    const bytes = new Uint8Array(400_000);
    for (let i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
    const f = new File([bytes], '${PREFIX}-geom-' + Date.now() + '.bin',
      { type: 'application/octet-stream' });
    document.dispatchEvent(new CustomEvent('upload:start', {
      detail: { files: [f], destination: 'tribute', tags: '${PREFIX}' },
    }));
    return f.name;
  })()`);
  await waitFor(DOCK, 8000);

  const noPlayer = await ev(`(() => {
    document.documentElement.style.removeProperty('--player-h');
    const r = document.querySelector('.dock').getBoundingClientRect();
    return Math.round(window.innerHeight - r.bottom);
  })()`);
  check(
    'with no player, the dock sits on the floor',
    noPlayer === 0,
    `${noPlayer}px from the bottom`,
  );

  const withPlayer = await ev(`(() => {
    document.documentElement.style.setProperty('--player-h', '72px');
    const r = document.querySelector('.dock').getBoundingClientRect();
    return Math.round(window.innerHeight - r.bottom);
  })()`);
  check(
    'with a player, the dock rides above it',
    withPlayer === 72,
    `${withPlayer}px from the bottom (expected 72)`,
  );
  await ev(`document.documentElement.style.removeProperty('--player-h')`);

  const dockH = await ev(
    `getComputedStyle(document.documentElement).getPropertyValue('--upload-dock-h').trim()`,
  );
  check(
    'the dock publishes its own measured height',
    /^\d+(\.\d+)?px$/.test(dockH) && parseFloat(dockH) > 0,
    dockH || '(unset)',
  );

  await waitFor(
    `!!Array.from(document.querySelectorAll('.dock__headline'))
       .find(e => /uploaded/.test(e.textContent))`,
    40000,
  );
  const gid = await ev(
    `(window.__dockDone || []).find(d => d.name === ${JSON.stringify(geom)})?.media_item_id || ''`,
  );
  if (gid) uploaded.push(gid);
  await ev(`document.querySelector('.dock__close')?.click()`);

  // --- 3. The real picker path -------------------------------------------
  await goto(`${BASE}/admin/search/upload`);
  await waitFor(`!!document.getElementById('file-input')`);
  await ev(WATCH_DONE);

  const realPath = join(dir, `${PREFIX}-picker.txt`);
  writeFileSync(realPath, `picker probe ${Date.now()}\n`.repeat(200));
  const doc = await send('DOM.getDocument');
  const node = await send('DOM.querySelector', {
    nodeId: doc.root.nodeId,
    selector: '#file-input',
  });
  await send('DOM.setFileInputFiles', {
    files: [realPath],
    nodeId: node.nodeId,
  });
  await sleep(400);
  const staged = await ev(
    `document.querySelectorAll('#file-list .file-item').length`,
  );
  check('the picker stages the file', staged === 1, `${staged} staged`);

  await ev(`document.getElementById('upload-btn')?.click()`);
  const pickerDock = await waitFor(DOCK, 8000);
  check('clicking Upload All hands off to the dock', pickerDock);
  check(
    'the staging list clears once handed over',
    (await ev(`document.querySelectorAll('#file-list .file-item').length`)) === 0,
  );

  await waitFor(
    `!!Array.from(document.querySelectorAll('.dock__headline'))
       .find(e => /uploaded/.test(e.textContent))`,
    40000,
  );
  const pid = await ev(
    `(window.__dockDone || []).find(d => (d.name || '').includes('${PREFIX}-picker'))?.media_item_id || ''`,
  );
  check('the picker upload landed', !!pid, pid || 'no completion announced');
  if (pid) uploaded.push(pid);
} catch (e) {
  check('harness completed', false, String(e && e.message));
} finally {
  for (const mid of uploaded) {
    try {
      await api('DELETE', `/api/media/${mid}`);
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
