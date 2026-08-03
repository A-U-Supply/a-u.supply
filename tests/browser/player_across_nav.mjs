/**
 * The comment window must still clear the player after you navigate.
 *
 * Reported from a phone, 2026-08-02: "backed out of the latent with the player
 * still up… when it came back up it was partially behind the player."
 *
 * **What the page loses on every in-app navigation.** Astro's ClientRouter
 * calls `swapRootAttributes()`, which removes *every* attribute from `<html>`
 * (only `data-astro-transition*` survive), and `swapBodyElement()`, which
 * replaces `<body>`. The player is `transition:persist`, so it sails through:
 * no effect re-runs, and its `ResizeObserver` never fires because the bar
 * never changed size — only the document around it did. `--player-h` and
 * `body.player-active` are simply gone, and `.marginalia`'s
 * `bottom: var(--player-h, 72px)` falls back to a bar height from before the
 * phone breakpoint existed.
 *
 * Measured here: the bar is ~165px at 390px wide, so the comment window lands
 * ~93px behind it. The same wipe puts the video PiP back under the transport
 * (the bug #592 fixed) and stops the page's bottom padding clearing the bar.
 *
 * **The load-bearing assertion is geometric, not the variable.** A future fix
 * that stops using a custom property should still pass, so this compares the
 * comment window's bottom edge against the player's top edge. The variable is
 * checked too, separately, because it is what regresses first.
 *
 * The navigation is an anchor `.click()`: this is a test of what a *swap*
 * does, not of whether the link is reachable, and ClientRouter intercepts a
 * dispatched click the same as a tap.
 *
 * Self-cleaning: creates its own latent and file, removes both.
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
const PREFIX = 'zz-playernav';

const results = [];
const check = (name, pass, detail) =>
  results.push({ name, pass: !!pass, detail: detail ?? '' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

/** A real WAV — the player has to have something it can actually load. */
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

const dir = mkdtempSync(join(tmpdir(), 'playernav-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${dir}`,
  '--window-size=430,900',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-gpu',
  '--mute-audio',
  '--autoplay-policy=no-user-gesture-required',
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
    setTimeout(() => {
      if (pend.has(i)) {
        pend.delete(i);
        reject(new Error(`timeout ${m}`));
      }
    }, 20000);
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

/** Everything the layout depends on, in one read. */
const layout = () =>
  ev(`(() => {
    const rect = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height) };
    };
    return {
      url: location.pathname,
      playerH: document.documentElement.style.getPropertyValue('--player-h') || '',
      playerActive: document.body.classList.contains('player-active'),
      player: rect('.player'),
      marginalia: rect('.marginalia'),
    };
  })()`);

/** The comment window must end where the bar begins, or above it. */
function clears(l) {
  if (!l.player || !l.marginalia) return null;
  return l.marginalia.bottom - l.player.top;
}

let latentId = '';
const mediaIds = [];
try {
  await send('Page.enable');
  await send('Runtime.enable');
  // A phone, where the bar wraps to several rows and is nothing like the 72px
  // fallback. At desktop widths the bar is short enough that the bug hides.
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 2,
    mobile: true,
  });

  const csrf = await nodeApi('GET', '/api/csrf');
  const login = await nodeApi('POST', '/api/login', {
    email: 'dev@local',
    password: 'devpass',
    csrf_token: csrf.body?.csrf_token,
  });
  check('login', login.status === 200, `status ${login.status}`);
  if (login.status !== 200) throw new Error('cannot log in');

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
  const fd = new FormData();
  fd.append(
    'file',
    new File([wav(2, 220)], `${PREFIX}-take.wav`, { type: 'audio/wav' }),
  );
  fd.append('project_id', latentId);
  fd.append('slot_id', slot.id);
  const up = await nodeApi('POST', '/api/media/upload', fd, true);
  if (up.body?.id) mediaIds.push(up.body.id);
  check('a track to play', mediaIds.length === 1, `${mediaIds.length}`);

  await goto(`${BASE}/login`);
  await sleep(800);
  await ev(`(async()=>{
    const c = await fetch('${API}/api/csrf',{credentials:'include'}).then(r=>r.json());
    return (await fetch('${API}/api/login',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:'dev@local',password:'devpass',csrf_token:c.csrf_token})})).status;
  })()`);
  await goto(`${BASE}/admin/latents/detail?id=${latentId}`);
  await waitFor(`/\\/admin\\/latents\\/[^/]+$/.test(location.pathname)`, 20000);
  if (!(await waitFor(`!!document.querySelector('.slot__summary')`))) {
    console.log(
      JSON.stringify({ results, error: 'the slot card never rendered' }),
    );
    process.exit(3);
  }

  // Play the track, then open the comment window over the bar.
  await ev(`document.querySelector('.slot__summary')?.click()`);
  await waitFor(`!!document.querySelector('.file-row__play')`);
  await ev(`document.querySelector('.file-row__play')?.click()`);
  if (!(await waitFor(`!!document.querySelector('.player')`))) {
    console.log(
      JSON.stringify({ results, error: 'the player never appeared' }),
    );
    process.exit(3);
  }
  await sleep(1200);
  await ev(`document.querySelector('.player__btn--marginalia')?.click()`);
  if (!(await waitFor(`!!document.querySelector('.marginalia')`))) {
    console.log(
      JSON.stringify({ results, error: 'the comment window never opened' }),
    );
    process.exit(3);
  }
  await sleep(800);

  const before = await layout();
  const gapBefore = clears(before);
  check(
    'the comment window clears the player to begin with',
    gapBefore !== null && gapBefore <= 0,
    `bar ${before.player?.h}px tall, overlap ${gapBefore}px`,
  );
  check(
    'the bar is taller than the 72px fallback on a phone',
    (before.player?.h || 0) > 72,
    `${before.player?.h}px — if this fails the fixture stopped exercising the bug`,
  );

  // A real in-app navigation, player still up.
  const href = await ev(`(() => {
    const a = [...document.querySelectorAll('a[href]')].find(
      a => (a.getAttribute('href') || '').replace(/\\/$/, '') === '/admin/latents');
    if (!a) return null;
    a.click();
    return a.getAttribute('href');
  })()`);
  check('there is a link out of the latent to click', !!href, String(href));
  await waitFor(`location.pathname.replace(/\\/$/, '') === '/admin/latents'`);
  await sleep(1500);

  const after = await layout();
  check(
    'the player survived the navigation',
    !!after.player,
    after.player ? '' : 'no .player — the fixture, not the bug',
  );
  check(
    'the comment window survived the navigation',
    !!after.marginalia,
    after.marginalia ? '' : 'no .marginalia',
  );
  const gapAfter = clears(after);
  check(
    'the comment window STILL clears the player after navigating',
    gapAfter !== null && gapAfter <= 0,
    gapAfter > 0
      ? `${gapAfter}px behind the bar — the root's inline style was wiped by the swap and nothing re-measured`
      : '',
  );
  check(
    '--player-h survived the navigation',
    !!after.playerH,
    `"${after.playerH}"`,
  );
  check(
    'body.player-active survived the navigation',
    after.playerActive,
    after.playerActive ? '' : 'the page lost the padding that clears the bar',
  );

  // ...and back into a latent, which is the round trip he described.
  await ev(`(() => {
    const a = [...document.querySelectorAll('a[href]')].find(
      a => /\\/admin\\/latents\\/[a-z0-9-]/i.test(a.getAttribute('href') || ''));
    if (a) a.click();
  })()`);
  await sleep(2000);
  const back = await layout();
  const gapBack = clears(back);
  check(
    'and after navigating a second time',
    gapBack !== null && gapBack <= 0,
    gapBack > 0 ? `${gapBack}px behind the bar` : '',
  );
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
