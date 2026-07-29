/**
 * Faced-slot head contrast pass.
 *
 * The bug this exists for: `.slot__head` sets its own colour for every face
 * treatment, but `.action-btn` (admin.css) declares
 * `color: var(--color-text)`, and an explicit declaration does not inherit.
 * So the head action buttons stayed theme-coloured on a darkened image —
 * dark on dark. It was reported four times before anyone fixed it.
 *
 * Why this can't be a lint: the mistake is a RELATIONSHIP. `.action-btn` is
 * declared in a file that has no idea it will ever render inside a faced
 * head, and `LatentSlots.svelte` never names `.action-btn`'s colour. Neither
 * file is wrong on its own. Only the computed style of the rendered pair
 * shows it, which means a browser.
 *
 * Preconditions (the pytest wrapper checks these and skips):
 *   - Astro on :4321 and FastAPI on :5000
 *   - the `viewer-audit-0001` fixture latent, with at least one image
 *   - Chrome at the standard macOS path
 *
 * Emits one JSON line: {"results": [{name, pass, detail}, ...]}
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = 'http://localhost:4321';
const API = 'http://localhost:5000';
const PROJ = 'viewer-audit-0001';
const CHROME =
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9355;

/** The ground a scrim/treat face guarantees. Mirrors OVERLAY_GROUND in
 *  src/lib/latentStyles.ts, which the contrast warnings already use. */
const OVERLAY_GROUND = '#262626';
/** WCAG AA for normal text. Same threshold the Style panel warns at. */
const AA = 4.5;

const dir = mkdtempSync(join(tmpdir(), 'faced-'));
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

const results = [];
const check = (name, pass, detail) => {
  results.push({ name, pass: !!pass, detail: detail ?? '' });
};

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

  await goto(`${BASE}/admin/latents/detail?id=${PROJ}`);
  await sleep(2500);

  const imgId = await ev(`(async()=>{
    const items=(await (await fetch('/api/projects/${PROJ}/items',{credentials:'include'})).json()).items;
    const im=items.find(i=>i.media?.media_type==='image');
    return im?im.media_item_id:null;})()`);
  check('fixture has an image to face a slot with', !!imgId, imgId || 'none');

  const slotId = await ev(`(async()=>{
    const s=(await (await fetch('/api/projects/${PROJ}/slots',{credentials:'include'})).json()).slots||[];
    return s[0]?.id||null;})()`);
  check('fixture has a slot', !!slotId, slotId || 'none');
  if (!imgId || !slotId) throw new Error('fixture incomplete');

  /**
   * Read what a viewer actually sees: the head's colour, the plain buttons'
   * colour, DELETE's colour, and the contrast of each against the ground the
   * face guarantees.
   */
  const PROBE = `(() => {
    const srgb = (c) => { c/=255; return c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4); };
    const lum = ([r,g,b]) => 0.2126*srgb(r)+0.7152*srgb(g)+0.0722*srgb(b);
    const parse = (s) => {
      if (s.startsWith('#')) {
        const h=s.slice(1); const n=h.length===3?h.split('').map(c=>c+c):[h.slice(0,2),h.slice(2,4),h.slice(4,6)];
        return n.map(x=>parseInt(x,16));
      }
      const m=s.match(/[\\d.]+/g); return [ +m[0], +m[1], +m[2] ];
    };
    const ratio = (a,b) => { const l1=lum(parse(a)), l2=lum(parse(b));
      const [hi,lo]=l1>l2?[l1,l2]:[l2,l1]; return (hi+0.05)/(lo+0.05); };

    const card = document.querySelector('.slot--faced');
    if (!card) return { found:false };
    const head = card.querySelector('.slot__head');
    const btns = [...head.querySelectorAll('.action-btn')];
    const plain = btns.find(b => !b.classList.contains('action-btn--danger'));
    const danger = btns.find(b => b.classList.contains('action-btn--danger'));
    const cs = (el) => el ? getComputedStyle(el) : null;
    const themeText = getComputedStyle(document.documentElement)
      .getPropertyValue('--color-text').trim();
    return {
      found: true,
      classes: card.className,
      headColor: cs(head).color,
      plainColor: plain ? cs(plain).color : null,
      plainBorder: plain ? cs(plain).borderTopColor : null,
      dangerColor: danger ? cs(danger).color : null,
      themeText,
      contrast: {
        plain: plain ? ratio(cs(plain).color, GROUND) : null,
        danger: danger ? ratio(cs(danger).color, GROUND) : null,
      },
    };
  })()`;

  const TREATMENTS = [
    ['image · scrim', { bg_mode: 'image', bg_style: 'scrim' }, OVERLAY_GROUND],
    ['image · treat', { bg_mode: 'image', bg_style: 'treat' }, OVERLAY_GROUND],
    ['solid', { bg_mode: 'solid', bg_color: '#aa3355' }, '#aa3355'],
    // The negative control. plate's head sits on an opaque --color-bg plate,
    // so theme colours are CORRECT there — if the fix leaked into plate, the
    // buttons would be washed-out light text on a light plate.
    ['image · plate', { bg_mode: 'image', bg_style: 'plate' }, null],
  ];

  for (const [label, style, ground] of TREATMENTS) {
    const full =
      style.bg_mode === 'image'
        ? { ...style, bg_media_item_id: imgId }
        : style;
    await ev(`(async()=>{ await fetch('/api/projects/${PROJ}/slots/${slotId}',
      {method:'PATCH',credentials:'include',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({style:${JSON.stringify(full)}})}); return true})()`);
    await goto(`${BASE}/admin/latents/detail?id=${PROJ}`);
    await sleep(2400);

    const r = await ev(PROBE.replace(/GROUND/g, JSON.stringify(ground || '#000')));
    if (!r?.found) {
      check(`${label}: faced slot rendered`, false, 'no .slot--faced on page');
      continue;
    }

    if (ground === null) {
      // plate: buttons must still be theme-coloured.
      check(
        `${label}: buttons keep the THEME colour (plate is excluded)`,
        r.plainColor === r.themeText ||
          r.plainColor.replace(/\s/g, '') === r.themeText.replace(/\s/g, ''),
        `button=${r.plainColor} theme=${r.themeText}`,
      );
      continue;
    }

    check(
      `${label}: plain buttons ride the head's colour`,
      r.plainColor === r.headColor,
      `button=${r.plainColor} head=${r.headColor}`,
    );
    check(
      `${label}: plain buttons clear AA against the face`,
      r.contrast.plain >= AA,
      `contrast ${r.contrast.plain?.toFixed(2)} vs ${ground}`,
    );
    check(
      `${label}: DELETE is NOT the theme colour and clears AA`,
      r.dangerColor !== r.themeText && r.contrast.danger >= AA,
      `delete=${r.dangerColor} contrast ${r.contrast.danger?.toFixed(2)}`,
    );
  }

  // --- Prove the pass can actually fail -------------------------------------
  // A green run means nothing unless the check responds to the bug it guards.
  // Re-introduce the exact defect (buttons hardcoding the theme colour) and
  // assert the contrast assertion flips.
  await ev(`(async()=>{ await fetch('/api/projects/${PROJ}/slots/${slotId}',
    {method:'PATCH',credentials:'include',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({style:{bg_mode:'image',bg_style:'scrim',bg_media_item_id:'${imgId}'}})}); return true})()`);
  await goto(`${BASE}/admin/latents/detail?id=${PROJ}`);
  await sleep(2400);
  const regressed = await ev(`(()=>{
    const st=document.createElement('style');
    st.id='regress-probe';
    st.textContent='.slot--faced .slot__head .action-btn{color:var(--color-text) !important;}';
    document.head.appendChild(st);
    const card=document.querySelector('.slot--faced');
    const btn=card?.querySelector('.slot__head .action-btn:not(.action-btn--danger)');
    const head=card?.querySelector('.slot__head');
    const out={ btn:btn?getComputedStyle(btn).color:null,
                head:head?getComputedStyle(head).color:null };
    st.remove();
    return out;
  })()`);
  check(
    'the check responds to the bug (re-introducing it breaks the match)',
    regressed.btn !== null && regressed.btn !== regressed.head,
    `regressed button=${regressed.btn} head=${regressed.head}`,
  );

  // Leave the fixture as we found it.
  await ev(`(async()=>{ await fetch('/api/projects/${PROJ}/slots/${slotId}',
    {method:'PATCH',credentials:'include',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({style:{}})}); return true})()`);
} catch (e) {
  check('harness completed', false, String(e?.message || e));
} finally {
  console.log(JSON.stringify({ results }));
  try {
    ws.close();
  } catch {}
  chrome.kill();
  rmSync(dir, { recursive: true, force: true });
}
