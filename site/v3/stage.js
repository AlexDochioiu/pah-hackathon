/* Sword & Servo · stage.js
   The animated robot-arm centrepiece: two rim-lit 2D arms (forward kinematics) in a hazy
   spotlight, a screenplay typewriter, LiveKit-style voice waveforms, a slate/timecode HUD and
   live interrupts that freeze the arms and rewrite the last sentence.
   Usage: <div class="stage" data-stage></div>
     data-story="1"     start with story #1
     data-beat="2"      freeze frame on that beat (no autoplay; for cards/stills)
     data-caption="off" hide the console (waveforms + script)
   Respects prefers-reduced-motion (starts paused, no typing or tweening) and pauses off-screen. */
(() => {
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const esc = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

// [shoulder, elbow, wrist, gripper-gap]
const POSES = {
  rest: [6, 70, 40, 3], raise: [-6, 40, -26, 2], hold: [12, 44, 8, 2], thrust: [40, 46, 4, 2],
  cower: [-28, 100, 14, 2], loom: [8, 24, 98, 16], hide: [-16, 128, 34, 4], bow: [50, 62, 52, 2],
  nuzzle: [38, 56, 40, 7], plant: [34, 58, 22, 2], cheer: [0, 10, -6, 2]
};
const STORIES = [
  { title: 'Midnight Duel', set: 'bridge', ask: 'a knight duels a dragon at midnight', beats: [
    { say: 'EXT. RUINED BRIDGE, MIDNIGHT. Rain. A lone knight raises her sword.', L: ['raise', 'sword'], R: ['hide', ''], spot: 'L' },
    { say: 'Out of the fog rises a dragon, eyes like furnaces.', L: ['hold', 'sword', 'tremble'], R: ['loom', 'eyes'], spot: 'R' },
    { say: 'Steel meets scale. She lunges, and the dragon opens its jaws to—', L: ['thrust', 'sword'], R: ['loom', 'eyes', 'snap'], spot: 'C', interrupt: 'make the dragon friendly!' },
    { say: 'Steel meets scale. She lunges… and the dragon catches the blade, gently, and bows.', rewrite: true, L: ['hold', 'sword'], R: ['bow', 'eyes'], spot: 'C' },
    { say: 'It only wanted someone to talk to. They talk until sunrise.', L: ['hold', 'sword', 'sway'], R: ['nuzzle', 'eyes', 'sway'], spot: 'C' }
  ]},
  { title: 'The Last Robot on the Moon', set: 'moon', ask: 'the last robot on the moon', beats: [
    { say: 'INT. MOON BASE, NIGHT. The last robot plants a flag nobody will ever see.', L: ['plant', 'flag'], R: ['hide', ''], spot: 'L' },
    { say: 'Every night it signals Earth. Every night: static.', L: ['raise', 'flag', 'sway'], R: ['hide', ''], spot: 'L' },
    { say: 'Then the dust stirs. Something crawls out of the crater, claws first, and—', L: ['cower', 'flag', 'tremble'], R: ['loom', 'eyes', 'snap'], spot: 'R', interrupt: 'make it a long-lost friend!' },
    { say: 'Then the dust stirs. A second robot climbs out of the crater, wearing a tin crown.', rewrite: true, L: ['hold', 'flag'], R: ['cheer', 'crown'], spot: 'C' },
    { say: 'Two lonely machines. One flag. They share it.', L: ['hold', 'flag', 'sway'], R: ['nuzzle', 'crown', 'sway'], spot: 'C' }
  ]}
];

const SCENE = id => `
<defs>
  <linearGradient id="${id}bk" x1="0" x2="1"><stop offset="0" stop-color="#22324a"/><stop offset=".2" stop-color="#0c0c11"/><stop offset=".72" stop-color="#0a0a0e"/><stop offset="1" stop-color="#47301a"/></linearGradient>
  <linearGradient id="${id}wh" x1="0" x2="1"><stop offset="0" stop-color="#8fa6c6"/><stop offset=".22" stop-color="#5a5d68"/><stop offset=".68" stop-color="#6a6970"/><stop offset="1" stop-color="#c79a62"/></linearGradient>
  <linearGradient id="${id}rim" x1="0" x2="1"><stop offset="0" stop-color="#8cc6ff"/><stop offset=".3" stop-color="#6fb7ff" stop-opacity="0"/><stop offset=".7" stop-color="#ffb35c" stop-opacity="0"/><stop offset="1" stop-color="#ffc98a"/></linearGradient>
  <linearGradient id="${id}blade" x1="0" x2="1"><stop offset="0" stop-color="#5d7fa8"/><stop offset=".45" stop-color="#1b1e26"/><stop offset=".8" stop-color="#9aa6b8"/><stop offset="1" stop-color="#fff1dc"/></linearGradient>
  <linearGradient id="${id}gold" x1="0" x2="1"><stop offset="0" stop-color="#7a4a1c"/><stop offset=".55" stop-color="#d7954a"/><stop offset="1" stop-color="#ffe0b0"/></linearGradient>
  <linearGradient id="${id}floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#101018"/><stop offset="1" stop-color="#030305"/></linearGradient>
  <linearGradient id="${id}fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset=".55" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <mask id="${id}refl" maskUnits="userSpaceOnUse" x="0" y="352" width="960" height="70"><rect x="0" y="352" width="960" height="70" fill="url(#${id}fade)"/></mask>
  <filter id="${id}glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<g class="set" data-set="bridge">
  <g fill="#cfe6ff" opacity=".35"><circle cx="140" cy="60" r="1"/><circle cx="820" cy="44" r="1.2"/><circle cx="700" cy="110" r=".9"/></g>
  <circle cx="770" cy="92" r="46" fill="#cfe6ff" opacity=".07"/><circle cx="770" cy="92" r="22" fill="#cfe6ff" opacity=".12"/>
  <path d="M0 330 V262 H70 V330 Z M70 262 Q130 200 190 262 V330 H170 V284 Q130 236 90 284 V330 Z M190 262 H230 V330 H190Z M230 262 Q290 206 350 262 V330 H330 V286 Q290 244 250 286 V330 H230Z" fill="#11131b"/>
  <path d="M610 330 V270 Q670 214 730 270 V330 H710 V292 Q670 252 630 292 V330Z M730 270 H780 V250 H790 V270 H800 V330 H730Z M800 270 Q860 230 920 274 L960 274 V330 H800Z" fill="#0f1118"/>
  <path d="M0 262 H350 M610 270 H960" stroke="#6fb7ff" stroke-opacity=".18" stroke-width="1"/>
</g>
<g class="set" data-set="moon">
  <g fill="#ece8df"><circle cx="90" cy="50" r="1.1" opacity=".7"/><circle cx="230" cy="96" r=".8" opacity=".5"/><circle cx="330" cy="36" r="1.2" opacity=".8"/><circle cx="520" cy="70" r=".8" opacity=".5"/><circle cx="610" cy="30" r="1" opacity=".7"/><circle cx="880" cy="160" r="1" opacity=".6"/><circle cx="60" cy="180" r=".9" opacity=".5"/><circle cx="440" cy="140" r=".7" opacity=".4"/></g>
  <circle cx="790" cy="96" r="34" fill="#2c6fb8"/><path d="M776 74 q14 6 10 18 q-8 6 -2 14 q10 4 6 14" stroke="#4fae7a" stroke-width="7" fill="none" stroke-linecap="round" opacity=".8"/>
  <circle cx="776" cy="88" r="34" fill="#07070a" opacity=".82"/>
  <circle cx="790" cy="96" r="40" fill="none" stroke="#6fb7ff" stroke-opacity=".18" stroke-width="5"/>
  <path d="M0 312 Q480 262 960 312 V330 H0Z" fill="#14141b"/>
  <path d="M0 312 Q480 262 960 312" stroke="#cfe6ff" stroke-opacity=".22" fill="none"/>
  <ellipse cx="170" cy="306" rx="40" ry="5" fill="#0b0b10"/><ellipse cx="820" cy="312" rx="56" ry="6" fill="#0b0b10"/>
</g>
<rect x="0" y="330" width="960" height="120" fill="url(#${id}floor)"/>
<path d="M0 330 H960" stroke="#6fb7ff" stroke-opacity=".16"/>
<use href="#${id}arms" transform="matrix(1 0 0 -1 0 704)" opacity=".28" mask="url(#${id}refl)"/>
<g id="${id}arms"></g>`;

/* ---------- arm rig ---------- */
function makeArm(parent, o, id) {
  const L1 = 96, L2 = 86, W = 20;
  const body = `url(#${id}${o.white ? 'wh' : 'bk'})`, rim = `url(#${id}rim)`, dot = o.white ? '#ece8df' : '#6fb7ff';
  const g = el('g', { transform: `translate(${o.x},${o.y}) scale(${o.flip ? -1 : 1},1)` }, parent);
  const part = (tag, a, p) => el(tag, Object.assign({ fill: body, stroke: rim, 'stroke-width': 1.6 }, a), p);
  part('rect', { x: -38, y: -2, width: 76, height: 14, rx: 3 }, g);
  part('rect', { x: -21, y: -34, width: 42, height: 34, rx: 8 }, g);
  const s = el('g', { transform: 'translate(0,-34)' }, g), j1 = el('g', {}, s);
  part('rect', { x: -W / 2, y: -L1, width: W, height: L1 + 6, rx: W / 2 }, j1);
  part('circle', { r: W * .72 }, j1); el('circle', { r: 2.6, fill: dot }, j1);
  const e = el('g', { transform: `translate(0,${-L1})` }, j1), j2 = el('g', {}, e);
  part('rect', { x: -W * .4, y: -L2, width: W * .8, height: L2 + 4, rx: W * .4 }, j2);
  part('circle', { r: W * .56 }, j2); el('circle', { r: 2.2, fill: dot }, j2);
  const w = el('g', { transform: `translate(0,${-L2})` }, j2), j3 = el('g', {}, w);
  part('circle', { r: W * .44 }, j3);
  part('rect', { x: -14, y: -29, width: 28, height: 21, rx: 4 }, j3);
  const prop = el('g', { class: 'prop' }, j3);
  const fl = part('rect', { x: -4, y: -54, width: 5, height: 27, rx: 2 }, j3);
  const fr = part('rect', { x: -1, y: -54, width: 5, height: 27, rx: 2 }, j3);
  const eyes = el('g', { opacity: 0, filter: `url(#${id}glow)` }, j3);
  for (const x of [-7, 7]) el('ellipse', { class: 'eye', cx: x, cy: -19, rx: 4, ry: 2.4, fill: '#ff7a3d' }, eyes);
  return { j: [j1, j2, j3], fl, fr, prop, eyes, cur: [...POSES.rest], vel: [0, 0, 0, 0], tgt: [...POSES.rest], ph: Math.random() * 6, mode: '', frozen: false, propName: null, id };
}
function drawProp(a, name, animate) {
  if (a.propName === name) return; a.propName = name;
  const p = a.prop, id = a.id, rim = `url(#${id}rim)`; p.innerHTML = ''; p.classList.remove('is-new');
  if (name === 'sword') {
    el('rect', { x: -3, y: -56, width: 6, height: 28, fill: '#2a1d12' }, p);
    el('rect', { x: -14, y: -61, width: 28, height: 5, rx: 2.5, fill: `url(#${id}gold)` }, p);
    el('path', { d: 'M-4.5 -61 V-112 L0 -124 L4.5 -112 V-61Z', fill: `url(#${id}blade)` }, p);
    el('path', { d: 'M4.5 -63 V-112 L0 -124', stroke: '#ffe2b8', 'stroke-width': 1, fill: 'none', opacity: .9 }, p);
  } else if (name === 'shield') {
    el('path', { d: 'M-30 -112 H30 V-86 Q30 -60 0 -48 Q-30 -60 -30 -86Z', fill: '#101118', stroke: rim, 'stroke-width': 2.4 }, p);
    el('path', { d: 'M0 -104 V-58 M-22 -88 H22', stroke: '#ffb35c', 'stroke-width': 3, opacity: .8 }, p);
  } else if (name === 'flag') {
    el('rect', { x: -2, y: -168, width: 4, height: 140, rx: 2, fill: `url(#${id}gold)` }, p);
    const c = el('g', { class: 'flag-cloth' }, p);
    el('path', { d: 'M2 -166 Q28 -174 54 -164 T104 -164 V-118 Q80 -128 54 -118 T2 -120Z', fill: '#14161f', stroke: rim, 'stroke-width': 1.4 }, c);
    el('circle', { cx: 50, cy: -143, r: 9, fill: 'none', stroke: '#ffb35c', 'stroke-width': 2 }, c);
  } else if (name === 'crown') {
    el('path', { d: 'M-22 -55 V-84 L-11 -70 L0 -90 L11 -70 L22 -84 V-55Z', fill: `url(#${id}gold)`, stroke: '#ffe0b0', 'stroke-width': 1 }, p);
    el('circle', { cx: 0, cy: -64, r: 3.2, fill: '#ff4d5e' }, p);
  }
  a.eyes.setAttribute('opacity', name === 'eyes' ? 1 : 0);
  if (animate && !RM) requestAnimationFrame(() => p.classList.add('is-new'));
}

const ALL = [];
let n = 0;
function mount(root) {
  const id = 'ss' + (n++) + '-';
  const still = root.dataset.beat !== undefined, consoleOn = root.dataset.caption !== 'off';
  const bars = k => '<i></i>'.repeat(k);
  root.innerHTML = `
    <div class="stage__screen">
      <div class="stage__pool"></div>
      <div class="stage__rain"></div>
      <svg class="stage__svg" viewBox="100 -40 760 430" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Two rim-lit robot arms, one white and one black, act out an improvised story in a hazy spotlight.">${SCENE(id)}</svg>
      <div class="stage__beam" style="mix-blend-mode:screen"></div>
      <div class="stage__dust">${Array.from({ length: 14 }, () => `<i style="left:${30 + Math.random() * 40}%;animation-duration:${7 + Math.random() * 7}s;animation-delay:-${Math.random() * 12}s"></i>`).join('')}</div>
      <div class="vignette"></div>
      <div class="stage__fade"></div>
      <div class="stage__hud" aria-hidden="true"><span>SC <b class="sc">01</b> · TK <b class="tk">01</b> · <span class="ttl"></span></span><span><span class="stage__rec">Live</span> &nbsp;<b class="tc">00:00:00:00</b></span></div>
      <i class="stage__corner stage__corner--tl"></i><i class="stage__corner stage__corner--tr"></i><i class="stage__corner stage__corner--bl"></i><i class="stage__corner stage__corner--br"></i>
      <div class="stage__alert" aria-hidden="true"></div>
      ${still ? '' : '<button class="chip stage__pause" type="button" aria-pressed="false">❚❚ Pause</button>'}
    </div>
    ${consoleOn ? `<div class="stage__console" aria-hidden="true">
      <div class="stage__voices">
        <div class="stage__voice"><p class="label label--tungsten">Narrator · AI</p><div class="wave wave--idle n">${bars(26)}</div></div>
        <div class="stage__voice"><p class="label">Audience</p><div class="wave wave--alarm wave--idle a">${bars(26)}</div></div>
      </div>
      <div class="stage__script"></div>
    </div>` : ''}
    <p class="sr-only">Example: someone asks for “a knight duels a dragon at midnight”. The AI narrator improvises it out loud while the arms act: the knight raises her sword, a dragon rises from the fog. Mid-sentence an audience member shouts “make the dragon friendly!”, the arms freeze, the narrator deletes the last sentence and rewrites it: the dragon gently catches the blade and bows.</p>`;
  const q = s => root.querySelector(s);
  const armsG = root.querySelector(`[id="${id}arms"]`);
  const S = {
    root, still, paused: RM && !still, visible: true, t: 12.2, take: 0,
    L: makeArm(armsG, { x: 372, y: 352, white: true }, id), R: makeArm(armsG, { x: 588, y: 352, flip: true }, id),
    beam: q('.stage__beam'), pool: q('.stage__pool'), fade: q('.stage__fade'), alert: q('.stage__alert'), rec: q('.stage__rec'),
    tc: q('.tc'), sc: q('.sc'), tk: q('.tk'), ttl: q('.ttl'), script: q('.stage__script'),
    wn: q('.wave.n'), wa: q('.wave.a'), nTalk: false, aTalk: false, sets: [...root.querySelectorAll('.set')]
  };
  S.arms = [S.L, S.R];
  S.barsN = S.wn ? [...S.wn.children] : []; S.barsA = S.wa ? [...S.wa.children] : [];
  S.lvN = S.barsN.map(() => 0); S.lvA = S.barsA.map(() => 0);
  ALL.push(S);
  const storyIx = +(root.dataset.story || 0);

  const cue = (a, [pose, prop, mode = ''], instant) => { a.tgt = [...POSES[pose]]; a.mode = mode; if (prop !== undefined) drawProp(a, prop, !instant); if (instant || RM) { a.cur = [...a.tgt]; a.vel = [0, 0, 0, 0]; } };
  const spotTo = w => { const x = w === 'L' ? -30 : w === 'R' ? 30 : 0; S.beam.style.transform = `translateX(${x}%)`; S.pool.style.transform = `translateX(${x * 1.05}%)`; };
  const setScene = st => { S.sets.forEach(s => s.classList.toggle('is-on', s.dataset.set === st.set)); root.dataset.set = st.set; S.ttl.textContent = st.title; };
  const slate = (sc, tk) => { S.sc.textContent = String(sc).padStart(2, '0'); S.tk.textContent = String(tk).padStart(2, '0'); };
  const interrupt = (text, on) => {
    root.classList.toggle('is-interrupt', on);
    if (on) S.alert.textContent = `Interrupt — “${text}”`;
    S.rec.textContent = on ? 'Freeze' : 'Live';
    S.arms.forEach(a => { a.frozen = on; if (on) a.vel = [0, 0, 0, 0]; });
  };
  // script lines
  function line(kind, who) {
    if (!S.script) return { txt: { data: '' }, p: null };
    [...S.script.children].forEach(c => c.classList.add('is-old'));
    while (S.script.children.length > 5) S.script.firstChild.remove();
    const p = document.createElement('p');
    if (who) p.innerHTML = `<span class="snippet__who ${kind === 'end' ? 'snippet__who--narrator' : ''}">${esc(who)}</span>`;
    const txt = document.createTextNode(''); p.append(txt);
    S.script.append(p);
    return { txt, p };
  }
  const caret = (p, on) => { if (!p) return; const c = p.querySelector('.caret'); if (on && !c) { const s = document.createElement('span'); s.className = 'caret'; p.append(s); } if (!on && c) c.remove(); };
  const tag = (p, t) => { if (!p) return; const s = document.createElement('span'); s.className = 'snippet__tag'; s.textContent = t; p.append(s); };

  if (still) {
    const st = STORIES[storyIx], bi = +root.dataset.beat;
    setScene(st); slate(storyIx + 1, 3);
    let b = null; for (let i = bi; i >= 0 && !b; i--) b = st.beats[i];
    cue(S.L, b.L, true); cue(S.R, b.R, true); spotTo(b.spot);
    S.tc.textContent = '00:00:12:04';
    line('ask', 'Audience ▸').txt.data = '“' + st.ask + '”';
    for (let i = Math.max(0, bi - 1); i <= bi; i++) { const l = line('n'); l.txt.data = st.beats[i].say; if (st.beats[i].rewrite) tag(l.p, 'rewritten'); }
    if (st.beats[bi].interrupt) { interrupt(st.beats[bi].interrupt, true); S.aTalk = true; }
    S.nTalk = !st.beats[bi].interrupt;
    render(S, 0, true); return;
  }

  const wait = ms => new Promise(res => { let left = ms, prev = performance.now(); (function tick(now) { if (!S.paused && S.visible) left -= now - prev; prev = now; left <= 0 ? res() : requestAnimationFrame(tick); })(prev); });
  async function type(l, text, speed = 30) {
    if (RM || !S.script) { l.txt.data += text; if (!S.script) await wait(text.length * speed); return; }
    caret(l.p, true);
    for (const ch of text) { l.txt.data += ch; await wait(/[.,:!?…—]/.test(ch) ? 190 : speed + Math.random() * 18); }
    caret(l.p, false);
  }
  async function erase(l, keep) {
    caret(l.p, true);
    while (l.txt.data.length > keep) { l.txt.data = l.txt.data.slice(0, -1); if (!RM) await wait(16); }
  }
  async function run() {
    for (let i = storyIx; ; i = (i + 1) % STORIES.length) {
      const st = STORIES[i]; S.take++;
      slate(i + 1, S.take); setScene(st);
      await type(line('ask', 'Audience ▸'), '“' + st.ask + '”', 38); await wait(800);
      let cur = null;
      for (const b of st.beats) {
        if (b.rewrite && cur) {
          const typed = cur.txt.data; let c = 0; while (c < typed.length && typed[c] === b.say[c]) c++;
          const cut = typed.lastIndexOf('. ', Math.max(0, c - 1)); const keep = cut >= 0 ? cut + 2 : 0;
          await erase(cur, keep); await wait(250);
          interrupt('', false); S.aTalk = false;
          cue(S.L, b.L); cue(S.R, b.R); spotTo(b.spot);
          S.nTalk = true; await type(cur, b.say.slice(keep)); S.nTalk = false; tag(cur.p, 'rewritten');
          await wait(1500); continue;
        }
        cue(S.L, b.L); cue(S.R, b.R); spotTo(b.spot);
        cur = line('n'); S.nTalk = true; await type(cur, b.say); S.nTalk = false;
        if (b.interrupt) {
          interrupt(b.interrupt, true); S.aTalk = true; await wait(1500); S.aTalk = false; await wait(900);
        } else await wait(1500);
      }
      const end = line('end', 'Fade out.'); await wait(1400);
      S.fade.style.opacity = 1; await wait(1200);
      if (S.script) S.script.innerHTML = '';
      cue(S.L, ['rest', '']); cue(S.R, ['hide', '']); spotTo('C');
      await wait(300); S.fade.style.opacity = 0;
    }
  }
  const btn = q('.stage__pause');
  const setPaused = p => { S.paused = p; btn.setAttribute('aria-pressed', p); btn.textContent = p ? '▶ Play' : '❚❚ Pause'; btn.setAttribute('aria-label', p ? 'Play the stage animation' : 'Pause the stage animation'); root.classList.toggle('is-paused', p); };
  setPaused(S.paused); btn.addEventListener('click', () => setPaused(!S.paused));
  new IntersectionObserver(es => { S.visible = es[0].isIntersecting; }).observe(q('.stage__screen'));
  setScene(STORIES[storyIx]);
  cue(S.L, ['rest', ''], true); cue(S.R, ['hide', ''], true);
  if (RM) {
    const st = STORIES[storyIx], b = st.beats[0];
    cue(S.L, b.L, true); cue(S.R, b.R, true); spotTo(b.spot);
    line('ask', 'Audience ▸').txt.data = '“' + st.ask + '”'; line('n').txt.data = b.say;
    render(S, 0, true);
    // wait for the visitor to press play, then run (typing is instant under reduced motion)
    const go = () => { btn.removeEventListener('click', go); if (S.script) S.script.innerHTML = ''; run(); };
    btn.addEventListener('click', go);
  } else run();
}

function render(S, dt, force) {
  if (!force && (S.paused || !S.visible)) return;
  S.t += dt; const t = S.t;
  for (const a of S.arms) {
    if (!a.frozen && dt) for (let i = 0; i < 4; i++) { a.vel[i] += ((a.tgt[i] - a.cur[i]) * 34 - a.vel[i] * 9) * dt; a.cur[i] += a.vel[i] * dt; }
    const o = [0, 0, 0, 0];
    if (!a.frozen && !RM && !S.still) {
      o[0] = Math.sin(t * 1.1 + a.ph) * 1.2; o[1] = Math.sin(t * 1.5 + a.ph) * 1.6; o[2] = Math.sin(t * 1.9 + a.ph) * 2.2;
      if (a.mode === 'tremble') { o[2] += Math.sin(t * 47) * 3; o[0] += Math.sin(t * 31) * 1; }
      if (a.mode === 'snap') o[3] = Math.sin(t * 6.5) > 0 ? 12 : -2;
      if (a.mode === 'sway') { o[0] += Math.sin(t * 1.8) * 4; o[2] += Math.sin(t * 1.8 + 1) * 7; }
    }
    const v = a.cur.map((x, i) => x + o[i]);
    a.j.forEach((j, i) => j.setAttribute('transform', `rotate(${v[i].toFixed(2)})`));
    const g = Math.max(1, v[3]);
    a.fl.setAttribute('x', (-4 - g).toFixed(1)); a.fr.setAttribute('x', (-1 + g).toFixed(1));
  }
  // timecode at 24 fps
  if (!S.still) { const f = Math.floor(t * 24), p = x => String(x).padStart(2, '0'); S.tc.textContent = `${p(Math.floor(f / 86400))}:${p(Math.floor(f / 1440) % 60)}:${p(Math.floor(f / 24) % 60)}:${p(f % 24)}`; }
  // voice waveforms
  const wave = (bars, lv, talk, cls, seed, gain) => {
    if (!bars.length) return;
    cls.classList.toggle('wave--idle', !talk);
    const n = bars.length;
    for (let i = 0; i < n; i++) {
      const env = Math.sin(Math.PI * (i + .5) / n);
      const noise = Math.abs(Math.sin(t * 11 + i * 1.7 + seed) * Math.sin(t * 6.3 + i * .55 + seed * 2)) * .8 + .2;
      const target = talk ? env * noise * gain : 0;
      lv[i] += (target - lv[i]) * (dt ? Math.min(1, dt * 18) : 1);
      bars[i].style.height = (3 + lv[i] * 21).toFixed(1) + 'px';
    }
  };
  wave(S.barsN, S.lvN, S.nTalk, S.wn, 0, 1);
  wave(S.barsA, S.lvA, S.aTalk, S.wa, 3, 1.15);
}
let last = performance.now();
function frame(now) { const dt = Math.min(.05, (now - last) / 1000); last = now; for (const S of ALL) if (!S.still) render(S, dt); requestAnimationFrame(frame); }

document.querySelectorAll('[data-stage]').forEach(mount);
requestAnimationFrame(frame);
})();
