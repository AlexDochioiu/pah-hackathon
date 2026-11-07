/* Once Upon a Bot · stage.js
   The animated robot-arm centrepiece in toy-box style: two chunky cartoon arms
   (white with sunshine joints, black with bubblegum joints, googly eyes),
   pop-in sets, chat-bubble narration and a starburst interrupt.
   Usage: <div class="stage" data-stage></div>
     data-story="1"     start with story #1
     data-beat="3"      freeze frame on that beat (no autoplay; for cards/stills)
     data-caption="off" hide the chat bubbles
   Respects prefers-reduced-motion (starts paused, no tweening) and pauses off-screen. */
(() => {
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const INK = '#1b1530';

const POSES = {
  rest: [6, 70, 40, 4], raise: [-6, 40, -26, 3], hold: [14, 44, 8, 3], thrust: [40, 44, 6, 3], reach: [34, 50, 30, 16],
  cower: [-30, 100, 14, 3], loom: [8, 22, 100, 16], hide: [-14, 128, 30, 5], bow: [38, 40, 70, 3],
  nuzzle: [40, 58, 36, 8], plant: [34, 58, 22, 3], cheer: [0, 8, -8, 3], sneak: [22, 92, 60, 18]
};
const STORIES = [
  { title: 'Pirates on Mars', set: 'mars', ask: 'Pirates on Mars, please!', beats: [
    { say: 'Captain Zip sailed her rocket-ship across the dusty red sea of Mars.', L: ['raise', 'flag', 'sway'], R: ['hide', ''] },
    { say: '“Arr! This whole planet be MINE!” she cried, and planted her flag.', L: ['plant', 'flag'], R: ['hide', ''] },
    { say: 'But a Martian King popped out of a crater, crown wobbling with rage—', L: ['cower', 'flag', 'tremble'], R: ['cheer', 'crown'] },
    { shout: 'Make the captain a cat!' },
    { say: 'POOF! Captain Zip was now a cat. She purred. The king melted.', L: ['nuzzle', 'flag'], R: ['hold', 'crown', 'sway'], cat: true },
    { say: 'They napped in a sunny crater forever after. Meow. The end.', L: ['bow', 'flag', 'sway'], R: ['bow', 'crown', 'sway'], cat: true }
  ]},
  { title: 'The Great Crown Heist', set: 'palace', ask: 'A heist! At a palace!', beats: [
    { say: 'Queen Bolt polished her crown: the shiniest in the whole kingdom.', L: ['hide', ''], R: ['raise', 'crown', 'sway'] },
    { say: 'Sneaky Sprocket tiptoed closer, grabby claws wide open…', L: ['sneak', '', 'tiptoe'], R: ['hold', 'crown'] },
    { say: 'SNATCH! The crown was gone, and the queen yelled for the guards—', L: ['raise', 'crown', 'sway'], R: ['cower', '', 'tremble'] },
    { shout: 'Make the queen join the heist!' },
    { say: '“Psst, take me with you,” whispered the queen. “Palace life is SO boring.”', L: ['hold', 'crown'], R: ['nuzzle', ''] },
    { say: 'Now they rob cake shops together and share every crumb. The end.', L: ['cheer', 'crown', 'dance'], R: ['cheer', 'shield', 'dance'] }
  ]}
];

const S_ = 'stroke="#1b1530" stroke-width="4" stroke-linejoin="round"';
const SCENE = id => `
<defs><clipPath id="${id}clip"><rect width="800" height="450"/></clipPath></defs>
<g clip-path="url(#${id}clip)">
<g class="set" data-set="mars">
  <rect width="800" height="450" fill="#ffb48f"/>
  <g fill="#fff6e5" opacity=".9"><circle cx="90" cy="60" r="4"/><circle cx="300" cy="40" r="3"/><circle cx="470" cy="90" r="4"/><circle cx="560" cy="34" r="3"/><circle cx="740" cy="150" r="4"/><circle cx="40" cy="200" r="3"/></g>
  <circle cx="120" cy="110" r="52" fill="#ff8fcf" ${S_}/><path d="M60 128 Q120 88 182 98" fill="none" ${S_}/>
  <circle cx="690" cy="80" r="26" fill="#ffc93c" ${S_}/>
  <g transform="translate(640 220) rotate(24)"><path d="M0 -58 Q22 -34 20 14 H-20 Q-22 -34 0 -58Z" fill="#fff" ${S_}/><circle cy="-16" r="9" fill="#4cc3ff" ${S_}/><path d="M-20 4 l-16 22 h16Z M20 4 l16 22 h-16Z" fill="#ff5a36" ${S_}/><path d="M-10 16 Q0 46 10 16Z" fill="#ffc93c" ${S_}/></g>
  <path d="M-10 372 Q90 300 200 330 T420 318 T640 326 T820 300 V460 H-10Z" fill="#ff5a36" ${S_}/>
  <ellipse cx="120" cy="400" rx="42" ry="10" fill="#e0441f" ${S_}/><ellipse cx="700" cy="410" rx="56" ry="12" fill="#e0441f" ${S_}/>
</g>
<g class="set is-away" data-set="palace">
  <rect width="800" height="450" fill="#c9b8ff"/>
  <path d="M0 26 Q200 70 400 26 T800 26" fill="none" ${S_}/>
  <g ${S_}><path d="M40 34 l16 34 l16 -30Z" fill="#ffc93c"/><path d="M120 46 l16 34 l16 -30Z" fill="#ff5a36"/><path d="M200 52 l16 34 l16 -32Z" fill="#4cc3ff"/><path d="M280 46 l16 34 l16 -30Z" fill="#3ddc97"/><path d="M480 46 l16 34 l16 -30Z" fill="#ff8fcf"/><path d="M560 52 l16 34 l16 -32Z" fill="#ffc93c"/><path d="M640 46 l16 34 l16 -30Z" fill="#ff5a36"/><path d="M720 34 l16 34 l16 -30Z" fill="#4cc3ff"/></g>
  <g ${S_}><path d="M90 300 V170 Q90 120 140 120 Q190 120 190 170 V300Z" fill="#4cc3ff"/><path d="M610 300 V170 Q610 120 660 120 Q710 120 710 170 V300Z" fill="#4cc3ff"/></g>
  <g class="cloud" fill="#fff"><ellipse cx="130" cy="190" rx="26" ry="12"/><ellipse cx="660" cy="210" rx="30" ry="12"/></g>
  <g ${S_}><path d="M90 300 V170 Q90 120 140 120 Q190 120 190 170 V300Z M610 300 V170 Q610 120 660 120 Q710 120 710 170 V300Z" fill="none"/><path d="M140 120 V300 M90 210 H190 M660 120 V300 M610 210 H710" fill="none"/></g>
  <rect x="-10" y="340" width="820" height="120" fill="#fff6e5" ${S_}/>
  <g fill="#ffc93c"><rect x="0" y="342" width="50" height="27"/><rect x="100" y="342" width="50" height="27"/><rect x="200" y="342" width="50" height="27"/><rect x="300" y="342" width="50" height="27"/><rect x="400" y="342" width="50" height="27"/><rect x="500" y="342" width="50" height="27"/><rect x="600" y="342" width="50" height="27"/><rect x="700" y="342" width="50" height="27"/>
  <rect x="50" y="369" width="50" height="27"/><rect x="150" y="369" width="50" height="27"/><rect x="250" y="369" width="50" height="27"/><rect x="350" y="369" width="50" height="27"/><rect x="450" y="369" width="50" height="27"/><rect x="550" y="369" width="50" height="27"/><rect x="650" y="369" width="50" height="27"/><rect x="750" y="369" width="50" height="27"/>
  <rect x="0" y="396" width="50" height="27"/><rect x="100" y="396" width="50" height="27"/><rect x="200" y="396" width="50" height="27"/><rect x="300" y="396" width="50" height="27"/><rect x="400" y="396" width="50" height="27"/><rect x="500" y="396" width="50" height="27"/><rect x="600" y="396" width="50" height="27"/><rect x="700" y="396" width="50" height="27"/>
  <rect x="50" y="423" width="50" height="27"/><rect x="150" y="423" width="50" height="27"/><rect x="250" y="423" width="50" height="27"/><rect x="350" y="423" width="50" height="27"/><rect x="450" y="423" width="50" height="27"/><rect x="550" y="423" width="50" height="27"/><rect x="650" y="423" width="50" height="27"/><rect x="750" y="423" width="50" height="27"/></g>
  <path d="M-10 340 H810" ${S_}/>
</g>
<g class="arms"></g>
<text class="bang" x="0" y="0" text-anchor="middle">!</text><text class="bang" x="0" y="0" text-anchor="middle">!?</text>
</g>`;

/* ---------- arm rig ---------- */
const L1 = 100, L2 = 88, BASE_H = 40;
function makeArm(parent, o) {
  const W = 32;
  const hop = el('g', { class: 'arm-hop' }, parent);
  const g = el('g', { class: 'arm', transform: `translate(${o.x},${o.y}) scale(${o.flip ? -1 : 1},1)` }, hop);
  const body = o.dark ? '#2c2840' : '#f6f5fb', cap = o.dark ? '#ff8fcf' : '#ffc93c', hi = o.dark ? 'rgba(255,255,255,.22)' : '#fff', fing = o.dark ? '#4a4360' : '#e6e3f0';
  const sk = { stroke: INK, 'stroke-width': 4, 'stroke-linejoin': 'round' };
  el('ellipse', { cx: 6, cy: 10, rx: 62, ry: 9, fill: INK, opacity: .18 }, g);
  el('rect', { x: -46, y: -8, width: 92, height: 20, rx: 10, fill: cap, ...sk }, g);
  el('rect', { x: -26, y: -BASE_H, width: 52, height: BASE_H - 4, rx: 14, fill: body, ...sk }, g);
  const s = el('g', { transform: `translate(0,${-BASE_H})` }, g), j1 = el('g', {}, s);
  el('rect', { x: -W / 2, y: -L1, width: W, height: L1 + 6, rx: W / 2, fill: body, ...sk }, j1);
  el('path', { d: `M${-W / 2 + 8} ${-L1 + 18} V-14`, stroke: hi, 'stroke-width': 5, 'stroke-linecap': 'round' }, j1);
  el('circle', { r: W * .62, fill: cap, ...sk }, j1); el('circle', { r: 5, fill: INK }, j1);
  const e = el('g', { transform: `translate(0,${-L1})` }, j1), j2 = el('g', {}, e);
  el('rect', { x: -W * .42, y: -L2, width: W * .84, height: L2 + 4, rx: W * .42, fill: body, ...sk }, j2);
  el('path', { d: `M${-W * .42 + 7} ${-L2 + 16} V-14`, stroke: hi, 'stroke-width': 4, 'stroke-linecap': 'round' }, j2);
  el('circle', { r: W * .54, fill: cap, ...sk }, j2); el('circle', { r: 4.5, fill: INK }, j2);
  const w = el('g', { transform: `translate(0,${-L2})` }, j2), j3 = el('g', {}, w);
  const prop = el('g', { class: 'prop' }, j3);
  const fl = el('rect', { x: -5, y: -60, width: 9, height: 28, rx: 4, fill: fing, ...sk, 'stroke-width': 3.5 }, j3);
  const fr = el('rect', { x: -4, y: -60, width: 9, height: 28, rx: 4, fill: fing, ...sk, 'stroke-width': 3.5 }, j3);
  const ears = el('g', { class: 'ears', style: 'transform:scale(0)' }, j3);
  el('path', { d: 'M-22 -30 L-20 -56 L-6 -36Z M22 -30 L20 -56 L6 -36Z', fill: '#ff8fcf', ...sk, 'stroke-width': 3.5 }, ears);
  el('rect', { x: -22, y: -36, width: 44, height: 28, rx: 10, fill: body, ...sk }, j3);
  el('circle', { r: W * .44, fill: cap, ...sk }, j3);
  const pupils = [];
  for (const x of [-10, 10]) { el('circle', { cx: x, cy: -22, r: 9, fill: '#fff', ...sk, 'stroke-width': 3 }, j3); pupils.push(el('circle', { cx: x, cy: -20, r: 4.5, fill: INK }, j3)); }
  return { hop, j: [j1, j2, j3], fl, fr, prop, ears, pupils, x: o.x, y: o.y, flip: !!o.flip, cur: [...POSES.rest], vel: [0, 0, 0, 0], tgt: [...POSES.rest], ph: Math.random() * 6, mode: '', frozen: false, propName: null, cat: false };
}
function setCat(a, on) { a.cat = on; a.ears.style.transform = on ? 'scale(1)' : 'scale(0)'; }
function drawProp(a, name, animate) {
  if (a.propName === name) return; a.propName = name;
  const p = a.prop; p.innerHTML = ''; p.classList.remove('is-new');
  const sk = { stroke: INK, 'stroke-width': 4, 'stroke-linejoin': 'round' };
  if (name === 'sword') {
    el('path', { d: 'M-7 -66 V-132 L0 -148 L7 -132 V-66Z', fill: '#e9edf5', ...sk }, p);
    el('rect', { x: -20, y: -72, width: 40, height: 10, rx: 5, fill: '#ffc93c', ...sk }, p);
    el('rect', { x: -5, y: -64, width: 10, height: 30, rx: 3, fill: '#ff5a36', ...sk }, p);
  } else if (name === 'shield') {
    el('path', { d: 'M-36 -124 H36 V-94 Q36 -64 0 -50 Q-36 -64 -36 -94Z', fill: '#4cc3ff', ...sk }, p);
    el('path', { d: 'M0 -114 l7 14 15 2 -11 10 3 15 -14 -8 -14 8 3 -15 -11 -10 15 -2Z', fill: '#ffc93c', ...sk, 'stroke-width': 3 }, p);
  } else if (name === 'flag') {
    el('rect', { x: -4, y: -184, width: 8, height: 152, rx: 4, fill: '#ffc93c', ...sk, 'stroke-width': 3.5 }, p);
    const c = el('g', { class: 'flag-cloth' }, p);
    el('path', { d: 'M4 -182 Q34 -192 62 -180 T116 -180 V-126 Q90 -138 62 -126 T4 -128Z', fill: INK, ...sk, 'stroke-width': 3 }, c);
    el('circle', { cx: 56, cy: -160, r: 11, fill: '#fff' }, c);
    el('path', { d: 'M42 -140 l28 -9 M42 -149 l28 9', stroke: '#fff', 'stroke-width': 4, 'stroke-linecap': 'round' }, c);
    el('circle', { cx: 52, cy: -161, r: 2.5, fill: INK }, c); el('circle', { cx: 60, cy: -161, r: 2.5, fill: INK }, c);
  } else if (name === 'crown') {
    el('path', { d: 'M-28 -58 V-96 L-14 -76 L0 -102 L14 -76 L28 -96 V-58Z', fill: '#ffc93c', ...sk }, p);
    el('circle', { cx: 0, cy: -70, r: 5, fill: '#ff8fcf', ...sk, 'stroke-width': 2.5 }, p);
    el('circle', { cx: -16, cy: -67, r: 3.5, fill: '#4cc3ff', ...sk, 'stroke-width': 2 }, p); el('circle', { cx: 16, cy: -67, r: 3.5, fill: '#3ddc97', ...sk, 'stroke-width': 2 }, p);
  }
  if (animate && !RM) requestAnimationFrame(() => p.classList.add('is-new'));
}
function wristXY(a) {
  const r = Math.PI / 180, t1 = a.cur[0] * r, t2 = (a.cur[0] + a.cur[1]) * r;
  const lx = L1 * Math.sin(t1) + L2 * Math.sin(t2), ly = -L1 * Math.cos(t1) - L2 * Math.cos(t2);
  return [a.x + (a.flip ? -lx : lx), a.y - BASE_H + ly];
}

const ALL = [];
let n = 0;
function mount(root) {
  const id = 'ob' + (n++) + '-';
  const still = root.dataset.beat !== undefined, chatOn = root.dataset.caption !== 'off';
  root.innerHTML = `
    <div class="stage__bar">
      <span class="stage__now" aria-hidden="true"><small>Now playing</small><span class="stage__title"></span></span>
      <span class="chip chip--dot stage__live" aria-hidden="true">Live</span>
      ${still ? '' : '<button class="chip chip--sun stage__pause" type="button" aria-pressed="false">❚❚ Pause</button>'}
    </div>
    <div class="stage__view">
      <svg class="stage__svg" viewBox="0 0 800 450" role="img" aria-label="Two cartoon robot arms with googly eyes, one white and one black, act out an improvised story with props.">${SCENE(id)}</svg>
      <div class="burst stage__burst" aria-hidden="true"><span><b>HEY!</b><span class="stage__shout"></span></span></div>
    </div>
    ${chatOn ? '<div class="chat stage__chat" aria-hidden="true"></div>' : ''}
    <p class="sr-only">Example performance: someone asks for “pirates on Mars”. The AI narrator improvises: Captain Zip plants her flag on Mars, then a furious Martian King pops out of a crater. Someone shouts “make the captain a cat!”, the arms freeze, and the story bends: the captain turns into a cat, purrs, and the king melts.</p>`;
  const q = s => root.querySelector(s);
  const armsG = q('.arms');
  const S = {
    root, still, paused: RM && !still, visible: true, t: 0,
    L: makeArm(armsG, { x: 232, y: 378 }), R: makeArm(armsG, { x: 568, y: 378, flip: true, dark: true }),
    chat: q('.stage__chat'), burst: q('.stage__burst'), shout: q('.stage__shout'), title: q('.stage__title'),
    bangs: [...root.querySelectorAll('.bang')], sets: [...root.querySelectorAll('.set')]
  };
  S.arms = [S.L, S.R];
  ALL.push(S);
  const storyIx = +(root.dataset.story || 0);

  const cue = (a, [pose, prop, mode = ''], instant) => { a.tgt = [...POSES[pose]]; a.mode = mode; if (prop !== undefined) drawProp(a, prop, !instant); if (instant || RM) { a.cur = [...a.tgt]; a.vel = [0, 0, 0, 0]; } };
  const setScene = name => S.sets.forEach(s => s.classList.toggle('is-away', s.dataset.set !== name));
  // keep only the bubbles that fit; the oldest scroll away
  const fit = () => { if (S.chat) while (S.chat.children.length > 1 && S.chat.scrollHeight > S.chat.clientHeight + 1) S.chat.firstChild.remove(); };
  function bubble(kind, who, text) {
    if (!S.chat) return null;
    const b = document.createElement('div');
    b.className = `bubble bubble--${kind}` + (RM || still ? '' : ' is-new');
    b.innerHTML = `<span class="bubble__who"></span><span class="bubble__text"></span>`;
    b.firstChild.textContent = who; b.lastChild.textContent = text;
    S.chat.append(b);
    fit();
    return b.lastChild;
  }
  function interrupt(text, on) {
    if (on) {
      S.shout.textContent = text; S.burst.classList.remove('is-shown'); void S.burst.offsetWidth; S.burst.classList.add('is-shown');
      S.arms.forEach((a, i) => {
        a.frozen = true; a.vel = [0, 0, 0, 0];
        if (!RM && !still) { a.hop.classList.remove('is-hopping'); void a.hop.getBBox(); a.hop.classList.add('is-hopping'); }
        const [x, y] = wristXY(a), bg = S.bangs[i];
        bg.setAttribute('x', x.toFixed(0)); bg.setAttribute('y', Math.max(70, y - 70).toFixed(0)); bg.classList.add('is-shown');
      });
    } else {
      S.burst.classList.remove('is-shown'); S.bangs.forEach(b => b.classList.remove('is-shown'));
      S.arms.forEach(a => { a.frozen = false; a.hop.classList.remove('is-hopping'); });
    }
  }

  if (still) {
    const st = STORIES[storyIx], bi = +root.dataset.beat;
    S.title.textContent = st.title; setScene(st.set);
    let b = null; for (let i = bi; i >= 0 && !b; i--) if (!st.beats[i].shout) b = st.beats[i];
    cue(S.L, b.L, true); cue(S.R, b.R, true); setCat(S.L, !!b.cat);
    render(S, 0);
    bubble('you', 'You', st.ask);
    if (st.beats[bi].shout) { bubble('shout', 'You, mid-story', st.beats[bi].shout); interrupt(st.beats[bi].shout, true); }
    else bubble('bot', 'AI narrator', st.beats[bi].say);
    S.paused = true; return;
  }

  const wait = ms => new Promise(res => { let left = ms, prev = performance.now(); (function tick(now) { if (!S.paused && S.visible) left -= now - prev; prev = now; left <= 0 ? res() : requestAnimationFrame(tick); })(prev); });
  async function type(node, text) {
    if (!node) return wait(text.length * 40);
    if (RM) { node.textContent = text; return; }
    node.textContent = ''; const tn = document.createTextNode(''), caret = document.createElement('span'); caret.className = 'caret'; node.append(tn, caret);
    for (const ch of text) { tn.data += ch; if (ch === ' ') fit(); await wait(/[.,:!?…—]/.test(ch) ? 160 : 32); }
    caret.remove();
  }
  async function run() {
    for (let i = storyIx; ; i = (i + 1) % STORIES.length) {
      const st = STORIES[i];
      if (S.chat) S.chat.innerHTML = '';
      await wait(300);
      await type(bubble('you', 'You', ''), st.ask); await wait(700);
      S.title.textContent = st.title; setScene(st.set); setCat(S.L, false); await wait(900);
      for (const b of st.beats) {
        if (b.shout) {
          bubble('shout', 'You, mid-story', b.shout);
          interrupt(b.shout, true); await wait(2400); interrupt('', false);
          continue;
        }
        cue(S.L, b.L); cue(S.R, b.R); if (b.cat !== undefined) setCat(S.L, b.cat);
        await type(bubble('bot', 'AI narrator', ''), b.say); await wait(1500);
      }
      await wait(1000);
      cue(S.L, ['rest', '']); cue(S.R, ['hide', '']); setCat(S.L, false);
      await wait(800);
    }
  }
  const btn = q('.stage__pause');
  const setPaused = p => { S.paused = p; btn.setAttribute('aria-pressed', p); btn.textContent = p ? '▶ Play' : '❚❚ Pause'; btn.setAttribute('aria-label', p ? 'Play the stage animation' : 'Pause the stage animation'); };
  setPaused(S.paused); btn.addEventListener('click', () => setPaused(!S.paused));
  new IntersectionObserver(es => { S.visible = es[0].isIntersecting; }).observe(q('.stage__view'));
  S.title.textContent = STORIES[storyIx].title; setScene(STORIES[storyIx].set);
  cue(S.L, ['rest', ''], true); cue(S.R, ['hide', ''], true);
  if (RM) { const st = STORIES[storyIx], b = st.beats[0]; cue(S.L, b.L, true); cue(S.R, b.R, true); bubble('you', 'You', st.ask); bubble('bot', 'AI narrator', b.say); }
  render(S, 0);
  if (!RM) run(); else {
    // reduced motion: play only when the visitor presses Play
    const start = () => { btn.removeEventListener('click', start); if (S.chat) S.chat.innerHTML = ''; run(); };
    btn.addEventListener('click', start);
  }
}

function render(S, dt) {
  S.t += dt; const t = S.t;
  for (const a of S.arms) {
    if (!a.frozen && dt) for (let i = 0; i < 4; i++) { a.vel[i] += ((a.tgt[i] - a.cur[i]) * 40 - a.vel[i] * 8.5) * dt; a.cur[i] += a.vel[i] * dt; }
    const o = [0, 0, 0, 0];
    if (!a.frozen && !RM && !S.still) {
      o[0] = Math.sin(t * 1.5 + a.ph) * 1.6; o[1] = Math.sin(t * 1.9 + a.ph) * 2; o[2] = Math.sin(t * 2.3 + a.ph) * 3;
      if (a.mode === 'tremble') { o[2] += Math.sin(t * 47) * 4; o[0] += Math.sin(t * 31) * 1.4; }
      if (a.mode === 'dance') { o[0] += Math.sin(t * 5 + a.ph) * 10; o[1] += Math.sin(t * 5 + 1) * 14; o[2] += Math.sin(t * 10) * 18; o[3] += Math.sin(t * 10) > 0 ? 8 : 0; }
      if (a.mode === 'sway') { o[0] += Math.sin(t * 2.2) * 6; o[2] += Math.sin(t * 2.2 + 1) * 9; }
      if (a.mode === 'tiptoe') { o[0] += Math.abs(Math.sin(t * 4)) * 6; o[3] += Math.sin(t * 8) * 5; }
    }
    const v = a.cur.map((x, i) => x + o[i]);
    a.j.forEach((j, i) => j.setAttribute('transform', `rotate(${v[i].toFixed(2)})`));
    const g = Math.max(2, v[3]);
    a.fl.setAttribute('x', (-5 - g).toFixed(1)); a.fr.setAttribute('x', (-4 + g).toFixed(1));
    const sw = Math.max(-4, Math.min(4, -a.vel[2] * .06)), ab = (v[0] + v[1] + v[2]) * Math.PI / 180;
    // googly pupils: swing with motion, fall toward world-down
    a.pupils.forEach((p, i) => { p.setAttribute('cx', ((i ? 10 : -10) + sw + Math.sin(ab) * 3).toFixed(2)); p.setAttribute('cy', (-22 + Math.cos(ab) * 3).toFixed(2)); });
  }
}
let last = performance.now();
function frame(now) { const dt = Math.min(.05, (now - last) / 1000); last = now; for (const S of ALL) if (!S.still && !S.paused && S.visible) render(S, dt); requestAnimationFrame(frame); }

document.querySelectorAll('[data-stage]').forEach(mount);
requestAnimationFrame(frame);
})();
