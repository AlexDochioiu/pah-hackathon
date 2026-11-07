/* ArmChair Theatre · stage.js
   The animated robot-arm centrepiece: two 2D forward-kinematics arms (white + black),
   props, painted sets, spotlight and a narrator/heckle story loop.
   Usage: <div class="stage" data-stage></div>
     data-story="1"   start with story #1
     data-beat="2"    freeze frame on that beat (no autoplay; for cards/stills)
     data-caption="off" hide the narrator caption
   Respects prefers-reduced-motion (starts paused, no tweening) and pauses off-screen. */
(() => {
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };

const POSES = {
  rest: [6, 70, 40, 3], raise: [-6, 40, -26, 2], hold: [12, 44, 8, 2], thrust: [40, 44, 6, 2],
  cower: [-30, 100, 14, 2], loom: [8, 22, 100, 16], hide: [-14, 128, 30, 4], bow: [52, 64, 48, 2],
  nuzzle: [40, 58, 36, 7], plant: [34, 58, 22, 2], cheer: [0, 8, -8, 2]
};
const STORIES = [
  { title: 'A Knight Who’s Afraid of the Dark', set: 'castle', ask: 'Tell us about a knight who’s afraid of the dark!', beats: [
    { say: 'Once upon a time, Sir Clank guarded a castle at the very edge of the night.', L: ['raise', 'sword'], R: ['hide', 'eyes'], spot: 'L' },
    { say: 'But whenever the torches went out, his sword began to tremble…', L: ['hold', 'sword', 'tremble'], R: ['hide', 'eyes'], spot: 'L', dark: .45 },
    { say: 'Then, from the cave, rose a dragon: enormous, hungry, and—', L: ['cower', 'sword', 'tremble'], R: ['loom', 'eyes', 'snap'], spot: 'R', dark: .2 },
    { heckle: 'Make the dragon friendly!' },
    { say: '—and very, very friendly. “I’m scared of the dark too,” it whispered.', L: ['hold', 'sword'], R: ['nuzzle', 'eyes'], spot: 'C', dark: 0 },
    { say: 'So they kept each other’s lanterns lit, happily ever after. The end.', L: ['bow', 'sword', 'sway'], R: ['bow', 'eyes', 'sway'], spot: 'C' }
  ]},
  { title: 'Pirates on Mars', set: 'mars', ask: 'Pirates! On Mars!', beats: [
    { say: 'Captain Rust steered her rocket-ship across the red dunes of Mars.', L: ['raise', 'flag', 'sway'], R: ['hide', 'crown'], spot: 'L' },
    { say: 'She planted her flag: “This planet belongs to pirates now!”', L: ['plant', 'flag'], R: ['hide', 'crown'], spot: 'L' },
    { say: 'But out of his crater rose the Martian King, crown gleaming, furious—', L: ['cower', 'flag', 'tremble'], R: ['cheer', 'crown'], spot: 'R' },
    { heckle: 'Settle it with a dance-off!' },
    { say: 'So they settled it the Martian way: a dance-off at dawn.', L: ['hold', 'flag', 'dance'], R: ['hold', 'crown', 'dance'], spot: 'C' },
    { say: 'Nobody won. Everybody danced. The end.', L: ['bow', 'flag', 'sway'], R: ['bow', 'crown', 'sway'], spot: 'C' }
  ]}
];

const SCENE = id => `
<defs>
  <radialGradient id="${id}wall" cx="50%" cy="40%" r="75%"><stop offset="0" stop-color="#4b1713"/><stop offset=".6" stop-color="#260b09"/><stop offset="1" stop-color="#120504"/></radialGradient>
  <linearGradient id="${id}floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6b3a1c"/><stop offset="1" stop-color="#2c1408"/></linearGradient>
  <radialGradient id="${id}pool"><stop offset="0" stop-color="#ffe9b8" stop-opacity=".75"/><stop offset=".6" stop-color="#ffcf80" stop-opacity=".25"/><stop offset="1" stop-color="#ffcf80" stop-opacity="0"/></radialGradient>
  <linearGradient id="${id}cone" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff2cf" stop-opacity="0"/><stop offset=".25" stop-color="#fff2cf" stop-opacity=".22"/><stop offset="1" stop-color="#ffe0a0" stop-opacity=".1"/></linearGradient>
  <linearGradient id="${id}w" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#d9dde2"/><stop offset=".45" stop-color="#fff"/><stop offset="1" stop-color="#b8bec6"/></linearGradient>
  <linearGradient id="${id}b" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#151619"/><stop offset=".45" stop-color="#474b52"/><stop offset="1" stop-color="#0d0e10"/></linearGradient>
  <filter id="${id}blur"><feGaussianBlur stdDeviation="8"/></filter>
  <filter id="${id}glow"><feGaussianBlur stdDeviation="3"/></filter>
</defs>
<rect width="800" height="450" fill="url(#${id}wall)"/>
<g class="set" data-set="castle">
  <g fill="#f8e6b0"><circle cx="120" cy="70" r="1.6"/><circle cx="210" cy="40" r="1.2"/><circle cx="330" cy="88" r="1.4"/><circle cx="470" cy="34" r="1.6"/><circle cx="560" cy="80" r="1.1"/><circle cx="700" cy="52" r="1.5"/><circle cx="760" cy="120" r="1.1"/><circle cx="60" cy="150" r="1.2"/></g>
  <circle cx="640" cy="96" r="38" fill="#f3dfa6"/><circle cx="626" cy="86" r="38" fill="#2d0e0b"/>
  <path d="M40 330 V210 h22 v-18 h12 v18 h14 v-18 h12 v18 h22 V330 Z M140 330 V240 h70 V330 Z M210 330 V180 h18 v-16 h12 v16 h12 v-16 h12 v16 h18 V330 Z" fill="#3a1410"/>
  <rect x="244" y="208" width="10" height="16" rx="5" fill="#e9b85a" opacity=".85"/><rect x="66" y="232" width="9" height="14" rx="4" fill="#e9b85a" opacity=".6"/>
  <path d="M600 330 Q640 250 700 262 Q760 270 800 230 V330 Z" fill="#2c0f0c"/>
</g>
<g class="set is-away" data-set="mars">
  <g fill="#ffd9c4"><circle cx="90" cy="60" r="1.4"/><circle cx="260" cy="30" r="1.2"/><circle cx="380" cy="70" r="1.6"/><circle cx="520" cy="44" r="1.1"/><circle cx="730" cy="160" r="1.4"/><circle cx="40" cy="190" r="1.2"/></g>
  <circle cx="150" cy="110" r="62" fill="#c4512b"/><ellipse cx="150" cy="110" rx="92" ry="16" fill="none" stroke="#e8a467" stroke-width="5" transform="rotate(-14 150 110)"/>
  <circle cx="130" cy="95" r="10" fill="#a03d1d"/><circle cx="170" cy="130" r="7" fill="#a03d1d"/>
  <g transform="translate(650 120) rotate(28)"><path d="M0 -50 Q16 -30 16 10 H-16 Q-16 -30 0 -50Z" fill="#e7d6b8"/><circle cy="-14" r="7" fill="#5aa6c4"/><path d="M-16 10 l-12 16 h12Z M16 10 l12 16 h-12Z" fill="#b84a2a"/><path d="M-8 12 Q0 40 8 12Z" fill="#ffb347"/></g>
  <path d="M0 330 Q120 280 240 300 T480 290 T800 300 V330Z" fill="#7a2a14"/>
</g>
<path d="M0 330 H800 V450 H0Z" fill="url(#${id}floor)"/>
<g stroke="#2a1206" stroke-opacity=".55" stroke-width="1.5"><path d="M0 352 H800 M0 378 H800 M0 408 H800"/><path d="M120 330 L100 352 M330 352 L322 378 M560 378 L566 408 M210 408 L200 450 M690 330 L700 352 M450 408 L452 450"/></g>
<g class="lights"><g class="spot" style="transform:translateX(-150px)">
  <path d="M370 -10 H430 L520 420 H280Z" fill="url(#${id}cone)" filter="url(#${id}blur)"/>
  <ellipse cx="400" cy="396" rx="150" ry="34" fill="url(#${id}pool)"/>
</g></g>
<g class="arms"></g>
<rect class="dim" width="800" height="450" fill="#080202" opacity="0"/>
<rect x="0" y="432" width="800" height="18" fill="#1a0a05"/>
<g fill="#ffd27a" filter="url(#${id}glow)" opacity=".9"><circle cx="60" cy="434" r="5"/><circle cx="160" cy="434" r="5"/><circle cx="260" cy="434" r="5"/><circle cx="360" cy="434" r="5"/><circle cx="440" cy="434" r="5"/><circle cx="540" cy="434" r="5"/><circle cx="640" cy="434" r="5"/><circle cx="740" cy="434" r="5"/></g>
<g fill="#0b0302">
  <g><circle cx="40" cy="430" r="22"/><rect x="2" y="440" width="76" height="30" rx="26"/></g>
  <g class="heckler"><circle cx="128" cy="424" r="21"/><rect x="92" y="436" width="72" height="30" rx="24"/><path class="hand" d="M150 440 L170 392 l6 -14 l6 3 l-4 14 l-14 40Z" opacity="0"/></g>
  <g><circle cx="680" cy="432" r="23"/><rect x="640" y="444" width="80" height="30" rx="26"/></g>
  <g><circle cx="770" cy="426" r="20"/><rect x="735" y="438" width="70" height="30" rx="24"/></g>
</g>`;

/* ---------- arm rig ---------- */
function makeArm(parent, o, id) {
  const L1 = 104, L2 = 94, W = 22;
  const g = el('g', { class: 'arm', transform: `translate(${o.x},${o.y}) scale(${o.flip ? -1 : 1},1)` }, parent);
  const fill = `url(#${id}${o.dark ? 'b' : 'w'})`, cap = o.dark ? '#2b2e33' : '#eef0f3', edge = o.dark ? '#000' : '#9aa1aa', accent = '#e2b04a', fing = o.dark ? '#3a3e44' : '#d0d5dc';
  el('ellipse', { cx: 0, cy: 14, rx: 58, ry: 8, fill: '#000', opacity: .35 }, g);
  el('rect', { x: -40, y: -2, width: 80, height: 16, rx: 4, fill: o.dark ? '#1a1b1e' : '#c9ced5', stroke: edge }, g);
  el('rect', { x: -22, y: -36, width: 44, height: 36, rx: 9, fill, stroke: edge }, g);
  const s = el('g', { transform: 'translate(0,-36)' }, g), j1 = el('g', {}, s);
  el('rect', { x: -W / 2, y: -L1, width: W, height: L1 + 6, rx: W / 2, fill, stroke: edge }, j1);
  el('circle', { r: W * .7, fill: cap, stroke: edge }, j1); el('circle', { r: 4, fill: accent }, j1);
  const e = el('g', { transform: `translate(0,${-L1})` }, j1), j2 = el('g', {}, e);
  el('rect', { x: -W * .4, y: -L2, width: W * .8, height: L2 + 4, rx: W * .4, fill, stroke: edge }, j2);
  el('circle', { r: W * .56, fill: cap, stroke: edge }, j2); el('circle', { r: 3.5, fill: accent }, j2);
  const w = el('g', { transform: `translate(0,${-L2})` }, j2), j3 = el('g', {}, w);
  el('circle', { r: W * .42, fill: cap, stroke: edge }, j3);
  el('rect', { x: -15, y: -30, width: 30, height: 22, rx: 5, fill, stroke: edge }, j3);
  const prop = el('g', { class: 'prop' }, j3);
  const fl = el('rect', { x: -4, y: -56, width: 5, height: 28, rx: 2, fill: fing, stroke: edge }, j3);
  const fr = el('rect', { x: -1, y: -56, width: 5, height: 28, rx: 2, fill: fing, stroke: edge }, j3);
  const eyes = el('g', { opacity: 0 }, j3), pupils = [];
  for (const x of [-8, 8]) { el('circle', { cx: x, cy: -20, r: 6.5, fill: '#fff', stroke: '#000' }, eyes); pupils.push(el('circle', { cx: x, cy: -20, r: 3, fill: '#000' }, eyes)); }
  return { j: [j1, j2, j3], fl, fr, prop, eyes, pupils, cur: [...POSES.rest], vel: [0, 0, 0, 0], tgt: [...POSES.rest], ph: Math.random() * 6, mode: '', frozen: false, propName: null };
}
function drawProp(a, name, animate) {
  if (a.propName === name) return; a.propName = name;
  const p = a.prop; p.innerHTML = ''; p.classList.remove('is-new');
  if (name === 'sword') {
    el('rect', { x: -14, y: -62, width: 28, height: 6, rx: 3, fill: '#c98a2b' }, p);
    el('rect', { x: -3, y: -58, width: 6, height: 30, fill: '#6b3a1c' }, p);
    el('path', { d: 'M-5 -62 V-128 L0 -140 L5 -128 V-62Z', fill: '#e8eef5', stroke: '#8e99a6', 'stroke-width': 1.5 }, p);
  } else if (name === 'shield') {
    el('rect', { x: -3, y: -60, width: 6, height: 14, fill: '#6b3a1c' }, p);
    el('path', { d: 'M-34 -118 H34 V-88 Q34 -60 0 -46 Q-34 -60 -34 -88Z', fill: '#2f5fa8', stroke: '#e2b04a', 'stroke-width': 4 }, p);
    el('path', { d: 'M0 -112 V-56 M-26 -92 H26', stroke: '#e2b04a', 'stroke-width': 5 }, p);
  } else if (name === 'flag') {
    el('rect', { x: -2.5, y: -176, width: 5, height: 146, rx: 2, fill: '#c98a2b' }, p);
    const c = el('g', { class: 'flag-cloth' }, p);
    el('path', { d: 'M2 -174 Q30 -182 58 -172 T110 -172 V-122 Q84 -132 58 -122 T2 -124Z', fill: '#111' }, c);
    el('circle', { cx: 52, cy: -150, r: 10, fill: '#f4e8cf' }, c);
    el('path', { d: 'M40 -132 l24 -8 M40 -140 l24 8', stroke: '#f4e8cf', 'stroke-width': 3 }, c);
  } else if (name === 'crown') {
    el('path', { d: 'M-24 -56 V-88 L-12 -72 L0 -94 L12 -72 L24 -88 V-56Z', fill: '#f2c14e', stroke: '#9c6f1f', 'stroke-width': 2 }, p);
    el('circle', { cx: 0, cy: -66, r: 4, fill: '#c0263b' }, p); el('circle', { cx: -14, cy: -64, r: 3, fill: '#2f5fa8' }, p); el('circle', { cx: 14, cy: -64, r: 3, fill: '#2f5fa8' }, p);
  }
  a.eyes.setAttribute('opacity', name === 'eyes' ? 1 : 0);
  if (animate && !RM) requestAnimationFrame(() => p.classList.add('is-new'));
}

const ALL = [];
let n = 0;
function mount(root) {
  const id = 'st' + (n++) + '-';
  const still = root.dataset.beat !== undefined;
  root.innerHTML = `
    <div class="board stage__board" aria-hidden="true">Tonight <b class="stage__title"></b></div>
    <div class="stage__frame">
      ${still ? '' : '<button class="chip chip--gilt stage__pause" type="button" aria-pressed="false">❚❚ Pause</button>'}
      <div class="heckle stage__heckle" aria-hidden="true"></div>
      <svg class="stage__svg" viewBox="0 0 800 450" role="img" aria-label="Two robot arms, one white and one black, perform an improvised story on a tiny theatre stage under a spotlight.">${SCENE(id)}</svg>
    </div>
    ${root.dataset.caption === 'off' ? '' : '<div class="snippet stage__caption" aria-hidden="true"><span class="snippet__who">The Narrator</span><p class="snippet__text"></p></div>'}
    <p class="sr-only">Example performance: someone asks for “a knight who’s afraid of the dark”. The narrator improvises: Sir Clank guards a castle but his sword trembles in the dark, then a dragon rises from a cave. An audience member shouts “make the dragon friendly!”, the arms freeze, and the story bends: the dragon admits it is scared of the dark too.</p>`;
  const q = s => root.querySelector(s);
  const svg = q('svg'), armsG = q('.arms');
  const S = {
    root, still, paused: RM && !still, visible: true, t: 0,
    L: makeArm(armsG, { x: 262, y: 392 }, id), R: makeArm(armsG, { x: 538, y: 392, flip: true, dark: true }, id),
    line: q('.snippet__text'), who: q('.snippet__who'), spot: q('.spot'), dim: q('.dim'), lights: q('.lights'),
    heckle: q('.stage__heckle'), heckler: q('.heckler'), hand: q('.hand'), title: q('.stage__title'), sets: [...root.querySelectorAll('.set')]
  };
  S.arms = [S.L, S.R];
  ALL.push(S);
  const storyIx = +(root.dataset.story || 0);

  const cue = (a, [pose, prop, mode = ''], instant) => { a.tgt = [...POSES[pose]]; a.mode = mode; if (prop !== undefined) drawProp(a, prop, !instant); if (instant || RM) { a.cur = [...a.tgt]; a.vel = [0, 0, 0, 0]; } };
  const spotTo = w => { S.spot.style.transform = `translateX(${w === 'L' ? -150 : w === 'R' ? 150 : 0}px)`; };
  const setScene = name => S.sets.forEach(s => s.classList.toggle('is-away', s.dataset.set !== name));
  const say = (who, text, audience) => { if (!S.line) return; S.who.textContent = who; S.who.classList.toggle('is-audience', !!audience); S.line.textContent = text; };
  const showHeckle = (text, on) => { S.heckle.textContent = on ? '“' + text + '”' : S.heckle.textContent; S.heckle.classList.toggle('is-shown', on); S.heckler.classList.toggle('is-up', on); S.hand.setAttribute('opacity', on ? 1 : 0); };

  if (still) {
    const st = STORIES[storyIx], bi = +root.dataset.beat;
    S.title.textContent = st.title; setScene(st.set);
    // apply the last acting beat at or before bi
    let b = null; for (let i = bi; i >= 0 && !b; i--) if (!st.beats[i].heckle) b = st.beats[i];
    cue(S.L, b.L, true); cue(S.R, b.R, true); spotTo(b.spot); S.dim.setAttribute('opacity', b.dark || 0);
    if (st.beats[bi].heckle) { S.arms.forEach(a => a.frozen = true); showHeckle(st.beats[bi].heckle, true); say('Heckle!', 'The arms freeze… the narrator thinks…', true); }
    else say('The Narrator', st.beats[bi].say);
    S.paused = true; render(S, 0); return;
  }

  const wait = ms => new Promise(res => { let left = ms, prev = performance.now(); (function tick(now) { if (!S.paused && S.visible) left -= now - prev; prev = now; left <= 0 ? res() : requestAnimationFrame(tick); })(prev); });
  async function type(text) {
    if (!S.line) return wait(text.length * 40);
    if (RM) { S.line.textContent = text; return; }
    S.line.textContent = ''; const node = document.createTextNode(''), caret = document.createElement('span'); caret.className = 'caret'; S.line.append(node, caret);
    for (const ch of text) { node.data += ch; await wait(/[.,:!?…—]/.test(ch) ? 170 : 34); }
  }
  async function run() {
    for (let i = storyIx; ; i = (i + 1) % STORIES.length) {
      const st = STORIES[i];
      say('A voice from the stalls', '', true); await type('“' + st.ask + '”'); await wait(900);
      S.title.style.opacity = 0; await wait(350); S.title.textContent = st.title; S.title.style.opacity = 1;
      setScene(st.set); await wait(1200);
      for (const b of st.beats) {
        if (b.heckle) {
          S.arms.forEach(a => { a.frozen = true; a.vel = [0, 0, 0, 0]; });
          S.lights.classList.remove('flicker'); void S.lights.getBBox(); S.lights.classList.add('flicker');
          showHeckle(b.heckle, true); say('Heckle!', 'The arms freeze… the narrator thinks…', true);
          await wait(2300);
          showHeckle('', false); S.arms.forEach(a => a.frozen = false);
          continue;
        }
        say('The Narrator', '');
        cue(S.L, b.L); cue(S.R, b.R); spotTo(b.spot); S.dim.setAttribute('opacity', b.dark || 0);
        await type(b.say); await wait(1500);
      }
      await wait(1200); S.dim.setAttribute('opacity', .85); await wait(1100);
      cue(S.L, ['rest', '']); cue(S.R, ['hide', '']); spotTo('C'); S.dim.setAttribute('opacity', 0);
    }
  }
  const btn = q('.stage__pause');
  const setPaused = p => { S.paused = p; btn.setAttribute('aria-pressed', p); btn.textContent = p ? '▶ Play' : '❚❚ Pause'; btn.setAttribute('aria-label', p ? 'Play the stage animation' : 'Pause the stage animation'); };
  setPaused(S.paused); btn.addEventListener('click', () => setPaused(!S.paused));
  new IntersectionObserver(es => { S.visible = es[0].isIntersecting; }).observe(svg);
  S.title.textContent = STORIES[storyIx].title;
  cue(S.L, ['rest', ''], true); cue(S.R, ['hide', ''], true);
  setScene(STORIES[storyIx].set);
  run();
  // reduced motion: start paused on a composed still of the first beat (Play continues the story)
  if (RM) { const b = STORIES[storyIx].beats[0]; cue(S.L, b.L, true); cue(S.R, b.R, true); spotTo(b.spot); say('The Narrator', b.say); }
  render(S, 0, true);
}

function render(S, dt, force) {
  if (!force && !S.still && (S.paused || !S.visible)) return;
  S.t += dt; const t = S.t;
  for (const a of S.arms) {
    if (!a.frozen && dt) for (let i = 0; i < 4; i++) { a.vel[i] += ((a.tgt[i] - a.cur[i]) * 38 - a.vel[i] * 9.5) * dt; a.cur[i] += a.vel[i] * dt; }
    const o = [0, 0, 0, 0];
    if (!a.frozen && !RM && !S.still) {
      o[0] = Math.sin(t * 1.3 + a.ph) * 1.4; o[1] = Math.sin(t * 1.7 + a.ph) * 1.8; o[2] = Math.sin(t * 2.1 + a.ph) * 2.5;
      if (a.mode === 'tremble') { o[2] += Math.sin(t * 47) * 3.5; o[0] += Math.sin(t * 31) * 1.2; }
      if (a.mode === 'snap') o[3] = Math.sin(t * 7) > 0 ? 12 : -2;
      if (a.mode === 'dance') { o[0] += Math.sin(t * 4.2 + a.ph) * 9; o[1] += Math.sin(t * 4.2 + 1) * 12; o[2] += Math.sin(t * 8.4) * 16; }
      if (a.mode === 'sway') { o[0] += Math.sin(t * 2) * 5; o[2] += Math.sin(t * 2 + 1) * 8; }
    }
    const v = a.cur.map((x, i) => x + o[i]);
    a.j.forEach((j, i) => j.setAttribute('transform', `rotate(${v[i].toFixed(2)})`));
    const g = Math.max(1, v[3]);
    a.fl.setAttribute('x', (-4 - g).toFixed(1)); a.fr.setAttribute('x', (-1 + g).toFixed(1));
    const pv = Math.max(-3, Math.min(3, -a.vel[2] * .05));
    a.pupils.forEach((p, i) => { p.setAttribute('cx', (i ? 8 : -8) + pv); p.setAttribute('cy', -20 + Math.abs(pv) * .4); });
  }
}
let last = performance.now();
function frame(now) { const dt = Math.min(.05, (now - last) / 1000); last = now; for (const S of ALL) if (!S.still) render(S, dt); requestAnimationFrame(frame); }

document.querySelectorAll('[data-stage]').forEach(mount);
requestAnimationFrame(frame);
})();
