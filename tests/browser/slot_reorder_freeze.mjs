/**
 * Rearranging the songs inside one slot, twice.
 *
 * David, 2026-08-02: "found a bug tonight when rearranging songs within a
 * slot, the page often freezes and throws a page unresponsive error."
 *
 * It was not intermittent — the second drag inside a slot did it every time,
 * and the first one already rendered the wrong order.
 *
 * **Why a browser is the only place this shows up.** Sortable rearranges the
 * DOM as you drag. The file list is a Svelte keyed `{#each}` whose body is a
 * row `<li>` *and* the `{#if}` block for a session's extracted children — two
 * top-level nodes, so Svelte tracks each item as a RANGE, `<li>` … anchor
 * comment. Sortable moves the `<li>` alone. The range breaks, and the next
 * update walks forward from the row looking for an end node that now sits
 * behind it: `move()` in svelte/internal/client/dom/blocks/each.js never
 * arrives, and cycles the nodes in front of its destination for ever. The tab
 * dies with Chrome's "Page Unresponsive". Nothing throws, nothing logs, and
 * the API is correct throughout — the server had the right order every time
 * the screen did not.
 *
 * So the two checks that matter are: **the page still answers**, and **the
 * screen agrees with the server**. Both after the SECOND drag — one drag alone
 * corrupts the node range but doesn't necessarily walk it, which is why this
 * survived the drag suite written in #604 and the four-bug pass in #606.
 *
 * The order checks read the rendered rows *before* any navigation: a reload
 * rebuilds the DOM from the server and would pass no matter what the drag did.
 *
 * Fixture setup and teardown go through node's own fetch, not the page — when
 * this test fails, the renderer is wedged and nothing evaluated in it will
 * ever come back, including the cleanup.
 *
 * Self-cleaning: creates its own latent and files, removes both.
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
const PORT = 9387;
const PREFIX = 'zz-reorderfreeze';
/** How long the page gets to answer before we call it frozen. */
const RESPONSIVE_MS = 10000;

const results = [];
const check = (name, pass, detail) =>
  results.push({ name, pass: !!pass, detail: detail ?? '' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* --------------------------------------------------------------- node API */
// A hand-rolled cookie jar: node's fetch doesn't keep one, and this half has
// to keep working after the page stops.
let cookies = '';
async function nodeApi(method, path, body, isForm = false) {
  const headers = { ...(cookies ? { Cookie: cookies } : {}) };
  if (body !== undefined && !isForm)
    headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, {
    method,
    headers,
    body: isForm ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  const set = res.headers.getSetCookie?.() || [];
  if (set.length) {
    cookies = [cookies, ...set.map((c) => c.split(';')[0])]
      .filter(Boolean)
      .join('; ');
  }
  return { status: res.status, body: await res.json().catch(() => null) };
}

async function nodeLogin() {
  const csrf = await nodeApi('GET', '/api/csrf');
  return nodeApi('POST', '/api/login', {
    email: 'dev@local',
    password: 'devpass',
    csrf_token: csrf.body?.csrf_token,
  });
}

/** A real (if tiny) WAV, so these are songs and not empty rows. */
function wav(seconds, freq) {
  const rate = 8000;
  const frames = Math.round(rate * seconds);
  const buf = Buffer.alloc(44 + frames * 2);
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + frames * 2, 4);
  buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write('data', 36);
  buf.writeUInt32LE(frames * 2, 40);
  for (let i = 0; i < frames; i++) {
    buf.writeInt16LE(
      Math.round(9000 * Math.sin((2 * Math.PI * freq * i) / rate)),
      44 + i * 2,
    );
  }
  return buf;
}

/* ----------------------------------------------------------------- chrome */
const dir = mkdtempSync(join(tmpdir(), 'reorderfreeze-'));
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
let dragData = null;
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.onopen = res;
  ws.onerror = rej;
});
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.method === 'Input.dragIntercepted') dragData = m.params.data;
  if (m.id && pend.has(m.id)) {
    const { resolve, reject } = pend.get(m.id);
    pend.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
};

/**
 * Every call is bounded. A frozen renderer answers nothing, so an unbounded
 * send here would hang the whole suite instead of reporting the bug.
 */
const send = (m, p = {}, timeout = RESPONSIVE_MS) => {
  const i = ++id;
  return new Promise((resolve, reject) => {
    pend.set(i, { resolve, reject });
    ws.send(JSON.stringify({ id: i, method: m, params: p }));
    setTimeout(() => {
      if (pend.has(i)) {
        pend.delete(i);
        const err = new Error(`page did not answer ${m} within ${timeout}ms`);
        err.frozen = true;
        reject(err);
      }
    }, timeout);
  });
};

const ev = async (x, timeout = RESPONSIVE_MS) => {
  const r = await send(
    'Runtime.evaluate',
    { expression: x, awaitPromise: true, returnByValue: true },
    timeout,
  );
  if (r.exceptionDetails)
    throw new Error(r.exceptionDetails.exception?.description);
  return r.result.value;
};

/** True if the main thread answers at all. */
async function responsive() {
  try {
    return (await ev('1 + 1', RESPONSIVE_MS)) === 2;
  } catch (e) {
    if (e.frozen) return false;
    throw e;
  }
}

const goto = async (u) => {
  await send('Page.navigate', { url: u });
  for (let i = 0; i < 60; i++) {
    await sleep(250);
    if ((await ev('document.readyState')) === 'complete') return;
  }
};

const waitFor = async (expr, ms = 25000) => {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await ev(expr)) return true;
    } catch {}
    await sleep(180);
  }
  return false;
};

const boxOf = (sel, nth) =>
  ev(`(() => {
  const el = document.querySelectorAll(${JSON.stringify(sel)})[${nth}];
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
})()`);

/**
 * What the rows say, top to bottom, right now — or null if asking is what
 * finds the freeze. A `1 + 1` can slip through in the gap between the drop and
 * the update that walks the broken range, so the read itself has to be able to
 * report a dead page rather than take the whole run down with it.
 */
async function screenOrder() {
  try {
    return JSON.parse(
      await ev(`JSON.stringify(Array.from(document.querySelectorAll('.slot .file-row'))
        .map(r => (r.querySelector('.file-row__name')?.textContent || '?').trim()))`),
    );
  } catch (e) {
    if (e.frozen) return null;
    throw e;
  }
}

/** Compare what is on screen with what the server has, or say it froze. */
async function checkOrder(name, server) {
  const screen = await screenOrder();
  if (screen === null) {
    check(name, false, `the page stopped answering when asked what it shows`);
    return false;
  }
  check(
    name,
    screen.join() === server.join(),
    `screen ${screen.join(' ')} / server ${server.join(' ')}`,
  );
  return true;
}

/**
 * One real drag between two rows of the same list. Returns false when the
 * gesture never started, so a caller never asserts on a drag that didn't
 * happen — the most likely way a drag test rots into decoration.
 */
async function dragRow(from, to) {
  const src = await boxOf('.slot .file-row .file-row__drag', from);
  const dst = await boxOf('.slot .file-row', to);
  if (!src || !dst) return false;
  dragData = null;
  const step = to > from ? 1 : -1;
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: src.x,
    y: src.y,
    button: 'left',
    clickCount: 1,
    buttons: 1,
  });
  for (const dy of [3, 10, 24]) {
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x: src.x,
      y: src.y + dy * step,
      button: 'left',
      buttons: 1,
    });
    await sleep(60);
  }
  await sleep(350);
  if (!dragData) return false;
  const mid = Math.round((src.y + dst.y) / 2);
  for (const [type, x, y] of [
    ['dragEnter', src.x, src.y + 20 * step],
    ['dragOver', src.x, mid],
    ['dragOver', dst.x, dst.y],
    ['dragOver', dst.x, dst.y],
  ]) {
    await send('Input.dispatchDragEvent', { type, x, y, data: dragData });
    await sleep(110);
  }
  await send('Input.dispatchDragEvent', {
    type: 'drop',
    x: dst.x,
    y: dst.y,
    data: dragData,
  });
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x: dst.x,
    y: dst.y,
    button: 'left',
    buttons: 0,
  });
  await sleep(1400);
  return true;
}

let latentId = '';
const mediaIds = [];
try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Input.setInterceptDrags', { enabled: true });
  // Tall on purpose: a coordinate past the viewport edge lands nowhere and the
  // drag silently never starts, with no error anywhere to explain it.
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1400,
    height: 2200,
    deviceScaleFactor: 1,
    mobile: false,
  });

  const login = await nodeLogin();
  check('login', login.status === 200, `status ${login.status}`);
  if (login.status !== 200) throw new Error('cannot log in');

  // --- fixture ------------------------------------------------------------
  const latent = (
    await nodeApi('POST', '/api/projects', {
      name: `${PREFIX} probe`,
      kind: 'album',
    })
  ).body;
  latentId = latent?.id || '';
  if (!latentId) throw new Error('could not create a latent');
  const slot = (await nodeApi('POST', `/api/projects/${latentId}/slots`, {}))
    .body;
  await nodeApi('PATCH', `/api/projects/${latentId}/slots/${slot.id}`, {
    label: 'Takes',
  });

  const names = ['one', 'two', 'three', 'four', 'five', 'six'];
  for (let i = 0; i < names.length; i++) {
    const fd = new FormData();
    fd.append(
      'file',
      new File([wav(0.4, 220 + i * 40)], `${PREFIX}-${names[i]}.wav`, {
        type: 'audio/wav',
      }),
    );
    fd.append('project_id', latentId);
    fd.append('slot_id', slot.id);
    const r = await nodeApi('POST', '/api/media/upload', fd, true);
    if (r.body?.id) mediaIds.push(r.body.id);
  }
  check(
    'six songs landed in the slot',
    mediaIds.length === 6,
    `${mediaIds.length}`,
  );

  const serverOrder = async () =>
    (
      (
        await nodeApi(
          'GET',
          `/api/projects/${latentId}/items?slot_id=${slot.id}`,
        )
      ).body?.items || []
    ).map((it) => it.media?.filename);

  // --- the page -----------------------------------------------------------
  await goto(`${BASE}/login`);
  await sleep(800);
  await ev(`(async()=>{
    const c = await fetch('${API}/api/csrf',{credentials:'include'}).then(r=>r.json());
    return (await fetch('${API}/api/login',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:'dev@local',password:'devpass',csrf_token:c.csrf_token})})).status;
  })()`);
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  // The detail page canonicalises ?id=<uuid> to a slug URL — act before that
  // settles and you are acting on a document about to be replaced.
  await waitFor(`/\\/admin\\/latents\\/[^/]+$/.test(location.pathname)`, 20000);
  if (!(await waitFor(`document.querySelectorAll('.slot').length === 1`))) {
    console.log(
      JSON.stringify({
        results,
        error: 'the slot card never rendered — dev server?',
      }),
    );
    process.exit(3);
  }
  await ev(`document.querySelector('.slot__summary')?.click()`);
  if (
    !(await waitFor(
      `document.querySelectorAll('.slot .file-row').length === 6`,
    ))
  ) {
    console.log(
      JSON.stringify({ results, error: 'the file rows never rendered' }),
    );
    process.exit(3);
  }

  // --- drag one -----------------------------------------------------------
  check('the first drag started', await dragRow(4, 0));
  check('the page still answers after one drag', await responsive());
  await checkOrder(
    'the screen shows what the first drag did',
    await serverOrder(),
  );

  // --- drag two: the one that hung ---------------------------------------
  const startedTwo = await dragRow(1, 5).catch((e) => {
    if (e.frozen) return 'frozen';
    throw e;
  });
  check('the second drag started', startedTwo === true, String(startedTwo));
  const alive = startedTwo === 'frozen' ? false : await responsive();
  check(
    'the page still answers after a second drag in the same slot',
    alive,
    alive
      ? ''
      : `no response within ${RESPONSIVE_MS}ms — Svelte is walking a node range Sortable broke`,
  );

  if (alive) {
    await checkOrder(
      'the screen shows what the second drag did',
      await serverOrder(),
    );

    // Four more, because "often" was the word in the report.
    let stillAlive = true;
    for (const [from, to] of [
      [0, 3],
      [5, 2],
      [2, 4],
      [3, 0],
    ]) {
      if (
        !(await dragRow(from, to).catch((e) => {
          if (e.frozen) return false;
          throw e;
        }))
      )
        break;
      if (!(await responsive())) {
        stillAlive = false;
        break;
      }
    }
    check('six drags in a row and the page is still alive', stillAlive);
    if (stillAlive) {
      await checkOrder(
        'the screen still agrees with the server after six drags',
        await serverOrder(),
      );
    }
  }
} catch (e) {
  check('harness completed', false, String(e && e.message));
} finally {
  try {
    for (const mid of mediaIds) await nodeApi('DELETE', `/api/media/${mid}`);
    if (latentId) await nodeApi('DELETE', `/api/projects/${latentId}`);
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
