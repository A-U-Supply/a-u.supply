/**
 * Arranging the latent detail page's sections — visibility and order.
 *
 * The promise this feature makes is the one nothing static can check:
 * **hiding a section does not touch its content.** So the load-bearing check
 * writes a paragraph into Documents, hides Documents, reloads, shows it again,
 * and compares the text character for character. A version that unmounted the
 * island, or that cleared anything server-side, passes every other assertion
 * here and fails that one.
 *
 * Also covered, each because it is silent when broken:
 *  - **The chip.** A hidden section's map chip has to go, or clicking it
 *    scrolls to something that isn't there. Which is why the head states a
 *    hidden count — once the chip is gone, Arrange is the only way back.
 *  - **Persistence.** The layout is a PATCH plus a broadcast; a version that
 *    only repaints looks identical until you reload.
 *  - **Order is `order:`, not DOM position.** Assertions therefore compare
 *    on-screen geometry, not `children` order — they'd both pass on the DOM
 *    and only one of them is what a reader sees.
 *  - **Slots can't leave their block.** The nested list is a separate Sortable
 *    with no shared group, so this is structural — but structure is exactly
 *    what a later refactor breaks by adding a `group` for convenience.
 *  - **Slots travel WITH their section.** The other half of that, and the half
 *    this suite originally missed: the only section it ever dragged was
 *    `marginalia`, which has no nested block to leave behind. The block was a
 *    sibling <li> of the Slots row, and Sortable moves `.arrange-row` and
 *    nothing else, so dragging the section walked out from over its own slots.
 *    It now lives inside the row; the check asserts parentage after a real
 *    drag of the Slots row itself.
 *  - **Getting out.** Escape comes free with `showModal()`; the × and the
 *    backdrop click did not, and neither existed. The one that matters is the
 *    negative — a drag released over the backdrop must NOT close the window,
 *    which is why the handler asks where the gesture began rather than where
 *    it ended.
 *  - **The phone.** The arrows are the mobile path and the keyboard path.
 *
 * Self-cleaning: creates its own latent, slots and document, removes them.
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
const PORT = 9389;
const PREFIX = 'zz-arrange';
const PARAGRAPH =
  'The take from the second night, before the amp started buzzing.';

const dir = mkdtempSync(join(tmpdir(), 'arrange-'));
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

/** Poll until truthy — the islands mount well after readyState completes, and
 * a harness that sleeps eventually reports a cold dev server as a bug. */
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

/** What a reader actually sees, top to bottom. `order:` moves boxes without
 * moving nodes, so DOM position would answer a different question. */
const visualOrder = () =>
  ev(`JSON.stringify(Array.from(document.querySelectorAll('#islands .latent-section'))
      .filter(el => !el.hidden)
      .map(el => ({ k: el.dataset.section, top: el.getBoundingClientRect().top }))
      .sort((a, b) => a.top - b.top)
      .map(x => x.k))`).then(JSON.parse);

const chipNames = () =>
  ev(`JSON.stringify(Array.from(document.querySelectorAll('.map__chip:not(.map__chip--slot) .map__name'))
      .map(e => e.textContent.trim()))`).then(JSON.parse);

const slotCardOrder = () =>
  ev(`JSON.stringify(Array.from(document.querySelectorAll('.slot[data-slot-id]'))
      .map(el => el.dataset.slotId))`).then(JSON.parse);

const openArrange = async () => {
  await ev(`document.querySelector('.map__arrange')?.click()`);
  return waitFor(`document.querySelector('.arrange')?.open === true`, 6000);
};

const rowToggle = (label) =>
  `Array.from(document.querySelectorAll('.arrange-row')).find(
     r => r.querySelector('.arrange-row__name')?.textContent.trim() === ${JSON.stringify(label)}
   )?.querySelector('input[type=checkbox]')`;

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
  await sleep(1600);
  return true;
}

let latentId = '';
try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Input.setInterceptDrags', { enabled: true });
  // Tall on purpose: a mouse event dispatched past the viewport edge lands
  // nowhere and the drag silently never starts, with no error anywhere.
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

  // --- the resolver, before any UI -----------------------------------------
  const resolved = JSON.parse(
    await ev(`(async () => {
      const m = await import('/src/lib/latentLayout.ts');
      const partial = m.resolveLayout({ order: ['threads'], hidden: ['docs'] });
      const junk = m.resolveLayout({ order: ['nope', 'loose', 'loose'], hidden: 'docs' });
      return JSON.stringify({
        keys: m.LAYOUT_KEYS.length,
        partialFirst: partial.order[0],
        partialAll: partial.order.length,
        partialHidden: [...partial.hidden],
        junkFirst: junk.order[0],
        junkAll: junk.order.length,
        junkHidden: [...junk.hidden],
        marginalia: m.LAYOUT_KEYS.includes('marginalia'),
      });
    })()`),
  );
  check('marginalia is arrangeable', resolved.marginalia, `${resolved.keys} keys`);
  check(
    'a partial order backfills the rest',
    resolved.partialFirst === 'threads' && resolved.partialAll === resolved.keys,
    `first ${resolved.partialFirst}, ${resolved.partialAll}/${resolved.keys}`,
  );
  check(
    'unknown and duplicate keys are dropped, not rendered',
    resolved.junkFirst === 'loose' && resolved.junkAll === resolved.keys,
    `first ${resolved.junkFirst}, ${resolved.junkAll}/${resolved.keys}`,
  );
  check(
    'a malformed hidden list fails safe to shown',
    resolved.junkHidden.length === 0,
    JSON.stringify(resolved.junkHidden),
  );

  // --- fixture -------------------------------------------------------------
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
  const doc = (
    await api('POST', `/api/projects/${latentId}/documents`, { name: 'Notes' })
  ).body;
  await api('PATCH', `/api/projects/${latentId}/documents/${doc.id}`, {
    content: PARAGRAPH,
  });

  const detail = `${BASE}/admin/latents/detail?id=${latentId}`;
  await goto(detail);
  await waitFor(`document.querySelectorAll('.slot[data-slot-id]').length === 2`);

  // --- the head ------------------------------------------------------------
  check(
    'the map head is a header with an Arrange button',
    await ev(`!!document.querySelector('.map__head .map__title') &&
              document.querySelector('.map__arrange')?.textContent.trim() === 'Arrange sections'`),
  );
  const names = await chipNames();
  check(
    'marginalia has a chip too',
    names.includes('Comments & markers'),
    names.join(', '),
  );

  // The button is a filled ochre plate with a FIXED near-black ink, because
  // --color-accent is light in both themes and the theme's own text colour
  // would be 2.4:1 on it in dark. Measure rather than trust the swatch.
  const btnContrast = (theme) => ev(`(() => {
    document.documentElement.dataset.theme = ${JSON.stringify(theme)};
    const el = document.querySelector('.map__arrange');
    if (!el) return 0;
    const cs = getComputedStyle(el);
    const parse = (s) => (s.match(/\\d+/g) || []).slice(0, 3).map(Number);
    const lum = (rgb) => {
      const [r, g, b] = rgb.map((v) => {
        const s = v / 255;
        return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const a = lum(parse(cs.color));
    const b = lum(parse(cs.backgroundColor));
    const [hi, lo] = a >= b ? [a, b] : [b, a];
    return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
  })()`);
  for (const theme of ['light', 'dark']) {
    const ratio = await btnContrast(theme);
    check(
      `the Arrange button clears AA in ${theme}`,
      ratio >= 4.5,
      `${ratio}:1`,
    );
  }
  await ev(`document.documentElement.dataset.theme = 'light'`);

  // --- the dialog ----------------------------------------------------------
  check('Arrange opens a modal dialog', await openArrange());
  check(
    'every section is listed',
    (await ev(`document.querySelectorAll('.arrange-row').length`)) ===
      resolved.keys,
    `${await ev(`document.querySelectorAll('.arrange-row').length`)} rows`,
  );
  check(
    'the slots sit in their own nested block',
    (await ev(`document.querySelectorAll('.arrange-slots__list .arrange-slot-row').length`)) === 2,
  );
  check(
    'slot rows carry no visibility toggle',
    (await ev(`document.querySelectorAll('.arrange-slot-row input[type=checkbox]').length`)) === 0,
  );

  // --- hide a section that has content -------------------------------------
  await ev(`(${rowToggle('Documents')})?.click()`);
  await sleep(1400);
  check(
    'hiding a section takes it off the page',
    await ev(`document.querySelector('.latent-section[data-section="docs"]')?.hidden === true`),
  );
  check(
    'its chip leaves the map',
    !(await chipNames()).includes('Documents'),
    (await chipNames()).join(', '),
  );
  check(
    'the head says how many are hidden',
    (await ev(`document.querySelector('.map__hidden')?.textContent.trim()`)) ===
      '1 hidden',
    await ev(`document.querySelector('.map__hidden')?.textContent.trim() || '(none)'`),
  );
  check(
    'the server has it',
    ((await api('GET', `/api/projects/${latentId}`)).body?.section_layout
      ?.hidden || []).includes('docs'),
  );

  // --- reload, then bring it back ------------------------------------------
  await goto(detail);
  await waitFor(`document.querySelectorAll('.slot[data-slot-id]').length === 2`);
  check(
    'it is still hidden after a reload',
    await ev(`document.querySelector('.latent-section[data-section="docs"]')?.hidden === true`),
  );
  await openArrange();
  await ev(`(${rowToggle('Documents')})?.click()`);
  await sleep(1400);
  await ev(`document.querySelector('.arrange')?.close()`);
  check(
    'un-hiding puts it back',
    await ev(`document.querySelector('.latent-section[data-section="docs"]')?.hidden === false`),
  );
  await ev(`document.querySelector('#docs-island .sec-summary')?.click()`);
  const kept = await waitFor(
    `document.querySelector('#docs-island textarea')?.value === ${JSON.stringify(PARAGRAPH)}`,
    8000,
  );
  check(
    'the writing survived, character for character',
    kept,
    await ev(`JSON.stringify(document.querySelector('#docs-island textarea')?.value || null)`),
  );

  // --- order, by arrow -----------------------------------------------------
  await openArrange();
  const before = await visualOrder();
  // Threads is second from the bottom by default; four taps puts it above the
  // sections it was under.
  for (let i = 0; i < 4; i++) {
    await ev(`Array.from(document.querySelectorAll('.arrange-row')).find(
        r => r.querySelector('.arrange-row__name')?.textContent.trim() === 'Threads'
      )?.querySelector('.row-move__arrow[title="Move up"]')?.click()`);
    await sleep(200);
  }
  await sleep(1200);
  const afterArrows = await visualOrder();
  check(
    'the arrows move a section up the page',
    afterArrows.indexOf('threads') === before.indexOf('threads') - 4,
    `${before.indexOf('threads')} → ${afterArrows.indexOf('threads')}`,
  );
  check(
    'the map chips follow the page order',
    (await chipNames())[afterArrows.indexOf('threads')] === 'Threads',
    (await chipNames()).join(', '),
  );
  await ev(`document.querySelector('.arrange')?.close()`);
  await goto(detail);
  await waitFor(`document.querySelectorAll('.slot[data-slot-id]').length === 2`);
  check(
    'the new order survives a reload',
    JSON.stringify(await visualOrder()) === JSON.stringify(afterArrows),
    (await visualOrder()).join(','),
  );

  // --- order, by drag ------------------------------------------------------
  await openArrange();
  const preDrag = await visualOrder();
  const dragged = await dragOnto(
    '.arrange-row:last-of-type .arrange-row__drag',
    '.arrange-row:first-of-type',
  );
  check('a section drag starts and drops', dragged);
  if (dragged) {
    await sleep(1200);
    const postDrag = await visualOrder();
    check(
      'dragging reorders the page',
      JSON.stringify(postDrag) !== JSON.stringify(preDrag),
      `${preDrag.join(',')} → ${postDrag.join(',')}`,
    );
  }

  // --- slots stay in their block -------------------------------------------
  const slotsBefore = await ev(
    `JSON.stringify(Array.from(document.querySelectorAll('.arrange-slots__list .arrange-slot-row')).map(r => r.dataset.slotId))`,
  );
  const rowsBefore = await ev(`document.querySelectorAll('.arrange-row').length`);
  const slotDragStarted = await dragOnto(
    '.arrange-slot-row:first-of-type .arrange-slot__drag',
    '.arrange-row:first-of-type',
  );
  // Recorded separately on purpose: "nothing moved" is only evidence if a
  // drag actually happened. Without this the check passes when the gesture
  // never started, which is the most likely way for it to rot.
  check('the escape attempt is a real drag', slotDragStarted);
  // The invariant is WHERE the rows are, not which ids exist. An escaped slot
  // is still in the document with the same id, so counting ids proves nothing
  // — it has to still be parented to the nested list.
  const strays = await ev(
    `Array.from(document.querySelectorAll('.arrange-slot-row'))
       .filter(r => !r.closest('.arrange-slots__list')).length`,
  );
  check(
    'a slot cannot be dragged out of the slots block',
    slotDragStarted &&
      strays === 0 &&
      (await ev(
        `JSON.stringify(Array.from(document.querySelectorAll('.arrange-slots__list .arrange-slot-row')).map(r => r.dataset.slotId))`,
      )) === slotsBefore &&
      (await ev(`document.querySelectorAll('.arrange-row').length`)) === rowsBefore,
    strays ? `${strays} slot row(s) escaped the block` : '',
  );

  // --- the slots block travels with its section ----------------------------
  // Sortable moves `.arrange-row` and nothing else. The block used to be a
  // sibling <li>, so dragging the Slots section walked out from over its own
  // slots — and left that row's keyed fragment with non-contiguous start/end
  // nodes, so re-reading the order afterwards couldn't repair it either. This
  // suite never caught it: the only section it dragged was `marginalia`, which
  // has nothing nested to leave behind.
  const beforeSlotsDrag = await visualOrder();
  const slotsSectionDragged = await dragOnto(
    '.arrange-row[data-key="slots"] .arrange-row__drag',
    '.arrange-row:first-of-type',
  );
  check('the Slots section drag starts and drops', slotsSectionDragged);
  if (slotsSectionDragged) {
    await sleep(1200);
    // Guard against a vacuous pass: "still nested" proves nothing if the drag
    // moved nothing.
    check(
      'dragging the Slots section reorders the page',
      JSON.stringify(await visualOrder()) !== JSON.stringify(beforeSlotsDrag),
      `${beforeSlotsDrag.join(',')} → ${(await visualOrder()).join(',')}`,
    );
    // Parentage, not existence — the block is still in the document either way,
    // which is the trap the escape check above already had to be rewritten for.
    const nesting = await ev(`(() => {
      const list = document.querySelector('.arrange-slots__list');
      if (!list) return 'no slots list at all';
      const owner = list.closest('.arrange-row');
      if (!owner) return 'slots list is not inside any section row';
      return owner.dataset.key === 'slots' ? '' : 'parented to ' + owner.dataset.key;
    })()`);
    check(
      'the slots stay nested under Slots after it moves',
      nesting === '',
      nesting,
    );
  }

  // --- getting out of the dialog -------------------------------------------
  const dialogOpen = () => ev(`document.querySelector('.arrange')?.open === true`);
  // Top-left of the viewport: the plate is centred and at most 460px wide, so
  // this is backdrop at any width this suite runs at.
  const clickBackdrop = async () => {
    for (const type of ['mousePressed', 'mouseReleased']) {
      await send('Input.dispatchMouseEvent', {
        type,
        x: 5,
        y: 5,
        button: 'left',
        clickCount: 1,
        buttons: type === 'mousePressed' ? 1 : 0,
      });
    }
    await sleep(300);
  };

  // The negative first, and it is the one that matters: a gesture that BEGAN
  // inside the window must not close it when it happens to end on the backdrop.
  // That is what a released drag looks like to a click handler, and closing the
  // window out from under someone mid-arrange is the worst thing this could do.
  //
  // Press on a row's NAME, not its grip. Pressing a grip starts a Sortable drag,
  // and with `Input.setInterceptDrags` on, an intercepted drag that is never
  // dropped leaves Chrome mid-gesture: no mouseup, no click, and every later
  // mouse event silently discarded. The first cut of this check did exactly
  // that and passed without a single event reaching the handler — vacuous, and
  // it took the backdrop check below down with it. The handler keys on where
  // the POINTERDOWN landed, so any press inside the plate exercises it.
  const insidePress = await box('.arrange-row:first-of-type .arrange-row__name');
  check('there is a row to press inside the window', !!insidePress);
  if (insidePress) {
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      x: insidePress.x,
      y: insidePress.y,
      button: 'left',
      clickCount: 1,
      buttons: 1,
    });
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x: 5,
      y: 5,
      button: 'left',
      buttons: 1,
    });
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      x: 5,
      y: 5,
      button: 'left',
      clickCount: 1,
      buttons: 0,
    });
    await sleep(400);
    check(
      'a gesture begun inside and released on the backdrop leaves it open',
      await dialogOpen(),
    );
  }

  await clickBackdrop();
  check('clicking the backdrop closes the dialog', !(await dialogOpen()));

  check('it reopens after a backdrop close', await openArrange());
  check(
    'the dialog has a labelled close button',
    await ev(`!!document.querySelector('.arrange__close[aria-label="Close"]')`),
  );
  await ev(`document.querySelector('.arrange__close')?.click()`);
  await sleep(300);
  check('the × closes the dialog', !(await dialogOpen()));
  await openArrange();

  // --- reordering a slot moves the real card -------------------------------
  const cardsBefore = await slotCardOrder();
  await ev(`document.querySelector('.arrange-slot-row:last-of-type .row-move__arrow[title="Move up"]')?.click()`);
  await sleep(2200);
  const cardsAfter = await slotCardOrder();
  check(
    'reordering a slot here moves the real slot card',
    cardsAfter.join(',') === [...cardsBefore].reverse().join(','),
    `${cardsBefore.join(',')} → ${cardsAfter.join(',')}`,
  );

  // --- reset ---------------------------------------------------------------
  await ev(`Array.from(document.querySelectorAll('.arrange .action-btn'))
      .find(b => b.textContent.trim() === 'Reset')?.click()`);
  await sleep(1600);
  check(
    'Reset restores the default arrangement',
    (await visualOrder()).join(',') ===
      'repo,links,docs,slots,playlists,slideshow,loose,threads,marginalia',
    (await visualOrder()).join(','),
  );
  check(
    'Reset clears the stored layout entirely',
    JSON.stringify(
      (await api('GET', `/api/projects/${latentId}`)).body?.section_layout,
    ) === '{}',
  );
  await ev(`document.querySelector('.arrange')?.close()`);

  // --- phone ---------------------------------------------------------------
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 2,
    mobile: true,
  });
  await goto(detail);
  await waitFor(`document.querySelectorAll('.slot[data-slot-id]').length === 2`);
  check('the Arrange button is reachable on a phone', await openArrange());
  const short = JSON.parse(
    await ev(`JSON.stringify(Array.from(document.querySelectorAll('.arrange-row, .arrange-slot-row'))
        .map(e => Math.round(e.getBoundingClientRect().height)).filter(h => h < 44))`),
  );
  check(
    'every row is at least a 44px target',
    short.length === 0,
    short.length ? `${short.length} too short` : '',
  );
  const phoneBefore = await visualOrder();
  await ev(`Array.from(document.querySelectorAll('.arrange-row')).find(
      r => r.querySelector('.arrange-row__name')?.textContent.trim() === 'Loose files'
    )?.querySelector('.row-move__arrow[title="Move up"]')?.click()`);
  await sleep(1400);
  check(
    'the arrows work on a phone',
    JSON.stringify(await visualOrder()) !== JSON.stringify(phoneBefore),
    `${phoneBefore.join(',')} → ${(await visualOrder()).join(',')}`,
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
