/**
 * Dragging a file from one slot card to another.
 *
 * The feature exists to solve one specific thing, and it is the thing no
 * static check can see: **slot cards render collapsed.** A card's file list
 * only exists when the card is open, on the Files tab, and already holding
 * something — so for most cards most of the time a shared Sortable group has
 * nothing to drop into. `.slot__dropzone` covers the whole card to fix that,
 * and if it regresses the feature still "works" in the one arrangement a
 * developer happens to have open while testing by hand.
 *
 * So the load-bearing check here drops a row onto a card that is **shut**.
 *
 * Also covered, each because it is silent when broken:
 *  - **The no-op.** Dropping a row back on its own card must change nothing. A
 *    short sloppy drag produces this constantly, and `put` refuses it from the
 *    DOM rather than from component state.
 *  - **The pin.** `SlotPrimaryPin` is keyed (slot_id, media_type), so a pinned
 *    file that leaves would otherwise strand a thumbnail on the old card for
 *    something it no longer holds.
 *  - **Persistence.** The move is a PATCH plus a refetch of both cards; a
 *    version that only reorders the DOM looks identical until you refresh.
 *  - **The other direction, which is how this shipped broken.** A version that
 *    only reaches the *server* also looks identical until you refresh, and the
 *    first cut of this file could not see it: arrival in the destination was
 *    asserted with `serverItems()` for the drag, and *only* that way for the
 *    menu. The API was right the whole time the screen was wrong. Every move
 *    check now asks the rendered card, before any `goto()` — a reload rebuilds
 *    the DOM from the server and would pass regardless of what the move did.
 *  - **The menu path.** Dragging is desktop-only — a phone shows one collapsed
 *    tab at a time — so `Move to slot ▸` is the only route on a phone, the
 *    only keyboard route, and the only way anyone discovers this exists.
 *
 * Driving a real drag needs CDP drag interception: SortableJS uses native
 * HTML5 drag-and-drop here (`DRAG_OPTS` sets no `forceFallback`), which
 * synthetic mouse events cannot start. `Input.setInterceptDrags` + the
 * `Input.dragIntercepted` payload replayed through `Input.dispatchDragEvent`
 * is the only way. The viewport is deliberately tall: coordinates past the
 * viewport edge land nowhere and the drag silently never starts.
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
const PORT = 9383;
const PREFIX = 'zz-slotdrag';

const dir = mkdtempSync(join(tmpdir(), 'slotdrag-'));
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
 * Poll until truthy. Never sleep-then-assert: the detail page's islands mount
 * well after `readyState === 'complete'`, and a harness that sleeps eventually
 * reports a cold dev server as a product failure.
 */
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

const box = (sel) => ev(`(() => {
  const el = document.querySelector(${JSON.stringify(sel)});
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
})()`);

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

const rowsIn = (slotId) =>
  ev(`document.querySelectorAll('.slot[data-slot-id="${slotId}"] .file-row').length`);

const serverItems = async (latentId, slotId) =>
  ((await api('GET', `/api/projects/${latentId}/items?slot_id=${slotId}`)).body
    ?.items || []).length;

/**
 * One real drag: press the grip, jiggle to make Chrome start a native drag,
 * then replay the intercepted payload over the target and drop.
 * Returns false if the drag never started, so a caller doesn't assert on a
 * gesture that didn't happen.
 */
async function dragOnto(gripSel, targetSel) {
  const grip = await box(gripSel);
  const target = await box(targetSel);
  if (!grip || !target) return false;
  dragData = null;
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: grip.x,
    y: grip.y,
    button: 'left',
    clickCount: 1,
    buttons: 1,
  });
  for (const dy of [3, 10, 24]) {
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x: grip.x,
      y: grip.y + dy,
      button: 'left',
      buttons: 1,
    });
    await sleep(60);
  }
  await sleep(350);
  if (!dragData) return false;
  for (const type of ['dragEnter', 'dragOver', 'dragOver']) {
    await send('Input.dispatchDragEvent', {
      type,
      x: target.x,
      y: target.y,
      data: dragData,
    });
    await sleep(110);
  }
  await send('Input.dispatchDragEvent', {
    type: 'drop',
    x: target.x,
    y: target.y,
    data: dragData,
  });
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x: target.x,
    y: target.y,
    button: 'left',
    buttons: 0,
  });
  await sleep(2200);
  return true;
}

let latentId = '';
const mediaIds = [];
try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Input.setInterceptDrags', { enabled: true });
  // Tall on purpose: two slot cards plus an open one runs past a normal
  // viewport, and a mouse event dispatched past the edge lands nowhere — the
  // drag simply never starts, with no error anywhere.
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1400,
    height: 2200,
    deviceScaleFactor: 1,
    mobile: false,
  });

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

  // --- fixture ------------------------------------------------------------
  const latent = (
    await api('POST', '/api/projects', { name: `${PREFIX} probe`, kind: 'album' })
  ).body;
  latentId = latent?.id || '';
  if (!latentId) throw new Error('could not create a latent');
  const slotA = (await api('POST', `/api/projects/${latentId}/slots`, {})).body;
  const slotB = (await api('POST', `/api/projects/${latentId}/slots`, {})).body;
  await api('PATCH', `/api/projects/${latentId}/slots/${slotA.id}`, {
    label: 'Alpha',
  });
  await api('PATCH', `/api/projects/${latentId}/slots/${slotB.id}`, {
    label: 'Beta',
  });

  const uploaded = JSON.parse(
    await ev(`(async () => {
      const ids = [];
      for (const n of ['${PREFIX}-one.wav', '${PREFIX}-two.wav', '${PREFIX}-three.wav']) {
        const b = new Uint8Array(2000);
        for (let i = 0; i < b.length; i++) b[i] = (Math.random()*256)|0;
        const fd = new FormData();
        fd.append('file', new File([b], n, { type: 'audio/wav' }));
        fd.append('project_id', ${JSON.stringify(latentId)});
        fd.append('slot_id', ${JSON.stringify(slotA.id)});
        const r = await fetch('/api/media/upload', { method:'POST', credentials:'include', body: fd });
        const j = await r.json().catch(() => null);
        ids.push(j && j.id);
      }
      return JSON.stringify(ids);
    })()`),
  );
  mediaIds.push(...uploaded.filter(Boolean));
  check('three files landed in slot A', mediaIds.length === 3, `${mediaIds.length}`);

  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  // The detail page canonicalises ?id=<uuid> to a slug URL — act before that
  // settles and you are acting on a document about to be replaced.
  await waitFor(`/\\/admin\\/latents\\/[^/]+$/.test(location.pathname)`, 20000);
  if (!(await waitFor(`document.querySelectorAll('.slot').length === 2`))) {
    console.log(
      JSON.stringify({ results, error: 'slot cards never rendered — dev server?' }),
    );
    process.exit(3);
  }

  // Open A. Leave B shut — that is the case under test.
  await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"] .slot__summary')?.click()`,
  );
  await waitFor(`document.querySelectorAll('.slot[data-slot-id="${slotA.id}"] .file-row').length === 3`);
  check('slot A shows its rows', (await rowsIn(slotA.id)) === 3);
  check(
    'slot B is SHUT — it has no file list to drop into',
    (await rowsIn(slotB.id)) === 0,
    'this is the premise: a shared Sortable group alone would have no target',
  );

  // --- 1. the no-op: drop a row back on its own card -----------------------
  await ev(`window.scrollTo(0, 0)`);
  await sleep(250);
  const selfDragged = await dragOnto(
    `.slot[data-slot-id="${slotA.id}"] .file-row:first-child .file-row__drag`,
    `.slot[data-slot-id="${slotA.id}"] .slot__summary`,
  );
  check('a drag starts at all', selfDragged, 'CDP drag interception');
  check(
    'dropping a row on its own card changes nothing',
    (await serverItems(latentId, slotA.id)) === 3,
    `${await serverItems(latentId, slotA.id)} items still in A`,
  );

  // --- 2. THE ONE: drop onto a collapsed card -----------------------------
  await ev(`window.scrollTo(0, 0)`);
  await sleep(250);
  const litSource = await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"]')?.classList.contains('slot--droppable')`,
  );
  check('the source card is not its own drop target', !litSource);

  const moved = await dragOnto(
    `.slot[data-slot-id="${slotA.id}"] .file-row:first-child .file-row__drag`,
    `.slot[data-slot-id="${slotB.id}"]`,
  );
  check('the drag onto the shut card started', moved);
  check(
    'the row left slot A',
    (await rowsIn(slotA.id)) === 2,
    `${await rowsIn(slotA.id)} rows left`,
  );
  check(
    'the server has it in slot B',
    (await serverItems(latentId, slotB.id)) === 1,
    `${await serverItems(latentId, slotB.id)} item(s) in B`,
  );

  // The destination card is what the person is looking at, and until this
  // check existed nothing here asked it anything: arrival was confirmed against
  // `serverItems`, i.e. the API, which was right the whole time the screen was
  // wrong. `load()` refills only slots it has never loaded, so a move between
  // two already-loaded cards left both stale until a refresh.
  // Open B WITHOUT reloading — a `goto()` here rebuilds the DOM from the server
  // and would pass no matter what the move did to it.
  await ev(`document.querySelector('.slot[data-slot-id="${slotB.id}"] .slot__summary')?.click()`);
  const landed = await waitFor(
    `document.querySelectorAll('.slot[data-slot-id="${slotB.id}"] .file-row').length === 1`,
    8000,
  );
  check(
    'the row is ON SCREEN in slot B, with no reload',
    landed,
    `${await rowsIn(slotB.id)} row(s) rendered in B`,
  );

  // ...and again into a card that is ALREADY OPEN, which is the case the bug
  // actually lived in and the one the check above cannot see. `load()` fills
  // `itemsBySlot` for slots it has never loaded, so a drop onto a card nobody
  // has opened repaints correctly by accident — measured: with the refetch
  // removed, the check above still passes and only these go red. B is open now
  // (the check above opened it), so both cards are loaded and stale state is
  // the only thing that could show.
  const bBefore = await rowsIn(slotB.id);
  const aBefore = await rowsIn(slotA.id);
  const secondDrag = await dragOnto(
    `.slot[data-slot-id="${slotA.id}"] .file-row:first-child .file-row__drag`,
    `.slot[data-slot-id="${slotB.id}"]`,
  );
  check('a second drag, into the OPEN card, started', secondDrag);
  check(
    'an already-open destination repaints too, with no reload',
    await waitFor(
      `document.querySelectorAll('.slot[data-slot-id="${slotB.id}"] .file-row').length === ${bBefore + 1}`,
      8000,
    ),
    `B rendered ${bBefore} → ${await rowsIn(slotB.id)}`,
  );
  check(
    'and the source card drops it, with no reload',
    (await rowsIn(slotA.id)) === aBefore - 1,
    `A rendered ${aBefore} → ${await rowsIn(slotA.id)}`,
  );

  // --- 3. it stuck --------------------------------------------------------
  // Relative to what the drags above actually left behind, not to literals —
  // two files have moved A→B by now, and a check that hard-codes the tally is
  // one someone has to re-derive every time a case is added above it.
  const aOnServer = await serverItems(latentId, slotA.id);
  const bOnServer = await serverItems(latentId, slotB.id);
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  await waitFor(`document.querySelectorAll('.slot').length === 2`);
  check(
    'the move survives a reload',
    (await serverItems(latentId, slotB.id)) === bOnServer &&
      (await serverItems(latentId, slotA.id)) === aOnServer &&
      bOnServer === 2 &&
      aOnServer === 1,
    `A=${await serverItems(latentId, slotA.id)} B=${await serverItems(latentId, slotB.id)}`,
  );

  // --- 4. a pinned file leaves its pin behind ------------------------------
  const aItems = (
    await api('GET', `/api/projects/${latentId}/items?slot_id=${slotA.id}`)
  ).body.items;
  await api('PUT', `/api/projects/${latentId}/slots/${slotA.id}/pin`, {
    media_type: 'audio',
    media_item_id: aItems[0].media_item_id,
  });
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  await waitFor(`document.querySelectorAll('.slot').length === 2`);
  await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"] .slot__summary')?.click()`,
  );
  await waitFor(`document.querySelectorAll('.slot[data-slot-id="${slotA.id}"] .file-row').length === ${aOnServer}`);
  const pinnedBefore = await ev(
    `(async () => { const r = await fetch('/api/projects/${latentId}', {credentials:'include'});
       const j = await r.json();
       return JSON.stringify((j.slots.find(s => s.id === '${slotA.id}') || {}).pinned || {}); })()`,
  );
  check('slot A has an audio pin to begin with', /audio/.test(pinnedBefore), pinnedBefore);

  await ev(`window.scrollTo(0, 0)`);
  await sleep(250);
  await dragOnto(
    `.slot[data-slot-id="${slotA.id}"] .file-row:first-child .file-row__drag`,
    `.slot[data-slot-id="${slotB.id}"]`,
  );
  const pinnedAfter = await ev(
    `(async () => { const r = await fetch('/api/projects/${latentId}', {credentials:'include'});
       const j = await r.json();
       return JSON.stringify((j.slots.find(s => s.id === '${slotA.id}') || {}).pinned || {}); })()`,
  );
  check(
    'the pin does not outlive the file it pointed at',
    !/audio/.test(pinnedAfter),
    pinnedAfter,
  );

  // --- 5. the menu path ----------------------------------------------------
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  await waitFor(`document.querySelectorAll('.slot').length === 2`);
  // Its own row, for the same reason the phone pass below gets one: every check
  // above moves another file out of slot A, and a section that asserts against
  // whatever survived passes standalone and fails once anything is added before
  // it. That is how this section broke when the open-destination drag landed.
  const menuFile = JSON.parse(
    await ev(`(async () => {
      const b = new Uint8Array(1500);
      for (let i = 0; i < b.length; i++) b[i] = (Math.random()*256)|0;
      const fd = new FormData();
      fd.append('file', new File([b], '${PREFIX}-menu.wav', { type: 'audio/wav' }));
      fd.append('project_id', ${JSON.stringify(latentId)});
      fd.append('slot_id', ${JSON.stringify(slotA.id)});
      const r = await fetch('/api/media/upload', { method:'POST', credentials:'include', body: fd });
      const j = await r.json().catch(() => null);
      return JSON.stringify(j && j.id);
    })()`),
  );
  if (menuFile) mediaIds.push(menuFile);
  check('a fresh file for the menu pass', !!menuFile);
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  await waitFor(`document.querySelectorAll('.slot').length === 2`);
  await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"] .slot__summary')?.click()`,
  );
  await waitFor(`!!document.querySelector('.slot[data-slot-id="${slotA.id}"] .file-row')`);
  const beforeMenu = await serverItems(latentId, slotB.id);
  const renderedABefore = await rowsIn(slotA.id);

  await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"] .file-row .row-actions__toggle')?.click()`,
  );
  await sleep(500);
  const hasMove = await ev(
    `!!Array.from(document.querySelectorAll('.row-actions__item'))
       .find(e => e.textContent.trim().startsWith('Move to slot'))`,
  );
  check('the row menu offers Move to slot', hasMove);
  await ev(`Array.from(document.querySelectorAll('.row-actions__item'))
      .find(e => e.textContent.trim().startsWith('Move to slot'))?.click()`);
  await sleep(400);
  const listed = await ev(
    `!!Array.from(document.querySelectorAll('.row-actions__item--nested'))
       .find(e => e.textContent.trim() === 'Beta')`,
  );
  check('it lists the other slot by name', listed);
  await ev(`Array.from(document.querySelectorAll('.row-actions__item--nested'))
      .find(e => e.textContent.trim() === 'Beta')?.click()`);
  await sleep(2200);
  check(
    'picking a slot from the menu moves the file',
    (await serverItems(latentId, slotB.id)) === beforeMenu + 1,
    `B went ${beforeMenu} → ${await serverItems(latentId, slotB.id)}`,
  );

  // This path does no DOM surgery at all — unlike the drag, which at least rips
  // the dragged node out — so before these two it could, and did, leave the row
  // sitting in the source card looking untouched, with the check above green
  // because the API had done its job. Both halves, on screen, before any reload.
  check(
    'the row LEAVES slot A on screen, with no reload',
    (await rowsIn(slotA.id)) === renderedABefore - 1,
    `A rendered ${renderedABefore} → ${await rowsIn(slotA.id)}`,
  );
  await ev(`document.querySelector('.slot[data-slot-id="${slotB.id}"] .slot__summary')?.click()`);
  const menuLanded = await waitFor(
    `document.querySelectorAll('.slot[data-slot-id="${slotB.id}"] .file-row').length === ${beforeMenu + 1}`,
    8000,
  );
  check(
    'and ARRIVES in slot B on screen, with no reload',
    menuLanded,
    `${await rowsIn(slotB.id)} row(s) rendered in B, expected ${beforeMenu + 1}`,
  );

  // --- 6. the menu on a PHONE ---------------------------------------------
  // The whole reason `Move to slot` exists — dragging needs two cards on
  // screen, and a phone shows one collapsed tab at a time. RowActions renders
  // a *different* presentation below 640px (inline accordion vs the portaled
  // panel exercised above), so the desktop pass says nothing about it.
  // Slot A is empty by now — every check above moved another file out of it.
  // Give the phone pass its own row rather than depending on what survived.
  const phoneFile = JSON.parse(
    await ev(`(async () => {
      const b = new Uint8Array(1500);
      for (let i = 0; i < b.length; i++) b[i] = (Math.random()*256)|0;
      const fd = new FormData();
      fd.append('file', new File([b], '${PREFIX}-phone.wav', { type: 'audio/wav' }));
      fd.append('project_id', ${JSON.stringify(latentId)});
      fd.append('slot_id', ${JSON.stringify(slotA.id)});
      const r = await fetch('/api/media/upload', { method:'POST', credentials:'include', body: fd });
      const j = await r.json().catch(() => null);
      return JSON.stringify(j && j.id);
    })()`),
  );
  if (phoneFile) mediaIds.push(phoneFile);
  check('a fresh file for the phone pass', !!phoneFile);

  await send('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 2,
    mobile: true,
  });
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  await waitFor(`document.querySelectorAll('.slot').length === 2`);
  check(
    'the phone breakpoint is actually on',
    await ev(`window.matchMedia('(max-width: 640px)').matches`),
  );
  await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"] .slot__summary')?.click()`,
  );
  await waitFor(`!!document.querySelector('.slot[data-slot-id="${slotA.id}"] .file-row')`);
  await ev(
    `document.querySelector('.slot[data-slot-id="${slotA.id}"] .file-row .row-actions__toggle')?.click()`,
  );
  await sleep(500);
  check(
    'the menu opens inline, not as the floating panel',
    await ev(
      `!!document.querySelector('.row-actions .row-actions__panel:not(.row-actions__panel--float)')`,
    ),
  );
  await ev(`document.querySelector('.row-actions__item--parent')?.click()`);
  await sleep(400);
  check(
    'the disclosure expands on a phone too',
    await ev(
      `!!Array.from(document.querySelectorAll('.row-actions__item--nested'))
         .find(e => e.textContent.trim() === 'Beta')`,
    ),
  );
  // 44px is the house minimum for a touch target; the nested rows inherit it
  // from .row-actions__item, and an indent must not have shrunk them.
  const shortRows = JSON.parse(
    await ev(`JSON.stringify(Array.from(document.querySelectorAll('.row-actions__item'))
      .map(e => Math.round(e.getBoundingClientRect().height)).filter(h => h < 44))`),
  );
  check('every menu row is still a 44px target', shortRows.length === 0,
    shortRows.length ? `${shortRows.length} too short` : '');
  const phoneBefore = await serverItems(latentId, slotB.id);
  await ev(`Array.from(document.querySelectorAll('.row-actions__item--nested'))
      .find(e => e.textContent.trim() === 'Beta')?.click()`);
  await sleep(2200);
  check(
    'the file moves from a phone',
    (await serverItems(latentId, slotB.id)) === phoneBefore + 1,
    `B went ${phoneBefore} → ${await serverItems(latentId, slotB.id)}`,
  );
  check(
    'no horizontal overflow at 390px',
    !(await ev(`document.documentElement.scrollWidth > window.innerWidth + 1`)),
    await ev(`document.documentElement.scrollWidth + ' vs ' + window.innerWidth`),
  );
} catch (e) {
  check('harness completed', false, String(e && e.message));
} finally {
  try {
    for (const mid of mediaIds) await api('DELETE', `/api/media/${mid}`);
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
