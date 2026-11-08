# Progress log

## 2026-11-07 (Sat)
- Idea brainstorm: Dial-a-Robot (public phone line → LiveKit voice agent → robot, asks callers for help when stuck); Snack Thief as idle gag.
- Station explored; arms + cameras verified via direct i2rt control (`wiggle.py`, `lift.py`).
- Dream-1 base model (`dreamcli robot run`, zero-shot) on "pick up the yellow block": 4 runs, no successful pick.
  - Gripper closed on air / grabbed wrong (red) block; long "what + how" prompt made it worse; one run hit the table → e-stop.
  - Takeaways: short prompts only, clean table, half speed (`max_ee_speed_m_s: 0.3`); fine-tune needs 50+ demos and 7–8 h.
- **Decision: bypass Dream-1, control the arms directly** (overhead camera → pixel↔arm calibration → IK pick/place → block castle).
- Next: calibration with `teach.py` (5 same-size blocks, touch top centres with each arm), then `pick.py`.
- Calibration (2 rounds of hand-guided touches): overhead pixel → arm frame homography, ~5 mm mean / 10 mm max error.
  Arm bases are 0.602 m apart, parallel (right = left + (0.006, 0.602)). Block top z: left 0.013, right 0.020.
- `pick.py` written: colour blob → centre + rotation → top-down IK plan (raise → above → down → grip → lift → place),
  every waypoint solved in sim, path checked against the table floor, refuses unless arm at rest. Dry run OK.
- After a script ends the motors switch off and blink red (normal idle).
- **Pivot to robot story theatre** (see [decisions.md](decisions.md)). Started in parallel: gesture library + stage
  server (sim only, arms busy with another team), LiveKit storyteller agent, 3 website versions.
- Knowledge base set up in `kb/` (this folder) plus `AGENTS.md` for anyone's AI agents.
- Storyteller agent in `agent/` ([storyteller.md](storyteller.md)): LiveKit Agents 1.8 on LiveKit Inference
  (gemma-4-31b + Fish Audio s2.1-pro expressive). Gesture tags are synced to playout through a
  TranscriptSynchronizer; listener speech freezes the arms; `[next]` chains scenes. `fake_stage.py` for dev.
  Verified without credentials: 14 tests incl. a full AgentSession with LiveKit's fakes. Needs a LiveKit Cloud
  project + keys in `agent/.env` to talk to it.
- Gesture library + stage server built, sim only ([gestures.md](gestures.md)): 19 procedural gestures, all pass the
  checker (limits, 1.5 rad/s, ≥ 4 cm table clearance with a 15 cm prop, ≥ 15 cm between arms; no unsafe pairs).
  `stage.py` HTTP API on :8770 matches `agent/fake_stage.py` (`GET /gestures` = list with `duration_s`).
  Bring-up passes in i2rt's MuJoCo sim on the station (41 steps, ~0.01 rad tracking). Not yet run on real arms:
  next is `bash ~/dial/bringup.sh --real` once the arms are free. GIF previews in `robot/previews/`.
- Launch site: 3 candidate designs in `site/v1-3/` (ArmChair Theatre / Once Upon a Bot / Sword & Servo), each with
  its own `design.css` design language, `stage.js` animated arms, `style.html` design sheet and `og-image.png`;
  screenshots in `site/shots/` for the team to pick ([site.md](site.md)).
- Real arms, first gesture tests: bring-up at 50 % passed (worst 0.12 rad); full speed on both arms tripped the
  0.35 rad auto-freeze in `cower`; 75 % ran clean until stopped (wrist pitch j4 lags most). Details in
  [gestures.md](gestures.md#real-arm-results-nov-7-1300).
- Table tipped when the arms leaned back. Measured each arm's centre of mass in sim: stage +1.3 cm, rest −2.5 cm,
  worst celebrate −10.3 cm (both arms), roar −7.8, slash −6.6, wave −6.5. New checker rule: COM ≥ stage − 2 cm
  (`show.COM_BACK_MARGIN`, switch `LEAN_BACK_CHECK`), enforced by the stage server and `arm.py`. Reworked 10
  gestures to raise the head with j2+/j3+ instead of leaning back ([gestures.md](gestures.md)). Added the teammate's
  `robot/arm.py` wrapper. **The station copy `~/dial` is NOT synced yet: run `./sync.sh` before the next real run.**
- Reworked 10 gestures run on the real arms at 50 %, both arms: all passed, worst 0.134 rad (`block`).
- Storyteller smoke test with the real LiveKit Inference models: LLM first token ~2.0 s, Fish TTS first audio
  ~3.2 s; tags well placed. Scripts that call `inference.*` outside the agent worker need
  `async with livekit.agents.utils.http_context.open():`.
- First real story run with the arms (user at the console): works well, "pretty awesome". Feedback: gestures lag the
  words a bit (fixed: tags move to their sentence start), and the arms both face the audience even when they play
  two characters: they should face and interact with each other (passed to the duets work). Props being printed:
  hat, sword, steering wheel, small gun.
- Prompt: the story must be told through physical action words that match the gestures (user request), so the
  arms are busy and the show feels dynamic.
- Face-each-other stance: `POST /stance {"stance":"each_other"}` yaws both arms ~26° toward each other (j1 ∓0.20,
  j5 ±0.25) and solo gestures play relative to it; 16/17 pass the checker there (not `look_at_other`). Agent tag
  `[face:each_other]` / `[face:audience]`, prompt asks for it when two characters meet. Sim-only so far
  ([gestures.md](gestures.md#stances-facing-each-other)).
- Duets, first two: `cross_swords` and `cheers` (prop to prop, 1.5–1.8 cm closest, final approach ≤ 4.3 cm/s),
  `POST /duet`, `GET /duets`, agent tag `[duet:name]`. Hand-to-hand duets (high five, handshake) are infeasible with
  15 cm props held. Sim-only so far ([gestures.md](gestures.md#duets-robotduetspy)).
- Duets 3–7: `poke` (prop to the right's hand), and no-touch `quick_draw`, `tip_hats`, `copycat`, `car_ride`
  (5.6–7.7 s). `cross_swords`/`cheers` trimmed to 8.7/9.0 s. Approach moves now ease their turn in and out.
- Story Theatre (`agent/app.py`, `uv run app.py` → http://127.0.0.1:8790): a hold-to-talk web page for the loud
  venue. The worker runs with `PTT=1` (manual turns, mic ignored unless the button or space bar is held). Checked
  end to end in headless Chrome with a fake mic looping a spoken request against `fake_stage.py`: 15 s of looping
  speech without the button gets no reaction; a held turn gets a story with gestures, the reply starting ~3.5 s after
  release. Gotcha: if the STT finalises while the button is still held, `commit_user_turn` waits for a second final
  until `transcript_timeout`, so it is 2.5 s (it was 13 s with 10 s).
- New direction from the user: each arm is a puppet head (camera lenses = eyes, gripper = mouth). Step A, the
  talking mouth: `POST /talk` overlay (3–5.5 Hz syllables, grip rate ≤ 3.5/s, small head bob), agent tag
  `[talk:left] "..."` on until the closing quote. Sim-only; the gripper's real speed is untested.
- Ensembles (step between A and B): 12 two-arm moves where both arms are ONE thing, action or mood (chomp jaws,
  wings, this_big, shrug, rocket, waves, storm, gate_opening, explosion, row_boat, once_upon_a_time, the_end), as
  `"kind": "ensemble"` duets; agent tag `[ensemble:name]`, no casting lock, blend in from either stance and back.
- Gesture coverage (`agent/coverage.py`): 12 generated scenes, an LLM lists physical actions left untagged. The LLM
  saw only gesture names; now it gets each gesture's look (`about` from the stage) and the story verbs it plays
  (inverted `SYNONYMS`), plus prompt rules: build the plot from the known moves, use the mouths a lot, face each
  other by default. Franchise asks (Star Wars, Pokemon, aliens vs predators…): every scene faces each other, every
  quote has a talk tag, untagged actions 45 → 33. Still missing: look around/spot, jump back/leap out, duck/dodge,
  land/stomp, snap jaws, beat chest, snore, sigh, hop.
- Owner rule (Nov 7 evening): while the station is unattended, don't run anything on the real arms that comes close
  to the table (death collapse, fist bang, the Lars pick's grasp). Those wait for someone at the e-stop.
- Step C (props explicit, deep facing): empty hands are the default; `each_other` turns the heads ~67° (mostly j5),
  `/props` switches to the old shallow turn and checks with props; 5 hand-contact ensembles (clap, rub_hands, hug,
  pull_rope, heart). Next batch: lower working height, limp death + revive, table bang, stirring, Italian hands,
  reworks of chomp/explosion/gate/once_upon/row_boat/shrug/car_ride/quick_draw, hat on the head, gun recoil.
- Owner OK (Nov 7 evening): the Lars pick and its demo recording may run unattended (`lars.py --unattended`);
  nothing else near the table does. Dream-1 fine-tune recipe from our own scripted demos added to dream-1.md.
- Round 3a review (Nov 7): clap, rub_hands, hug, pull_rope, heart all kept; deep facing stays ~67°. Lars fine-tune:
  1000 steps, queued only after the owner confirms Dream credits with the organisers.
- kb/todo.md (all open work) and kb/system.md (how the pieces connect) added; AGENTS.md asks every agent to keep them current.
- Storyteller sound (Nov 7 late): `[sfx:name]` one-shots and `[music:mood]` loops through LiveKit's
  BackgroundAudioPlayer (`agent/sounds.py`), music ducked under speech, 32 CC0 sfx + 9 CC0 music tracks in
  `agent/sounds/`, plus a stand-up comedy mode with rimshots and laugh tracks. See [storyteller.md](storyteller.md).
- Round 3b (sim only): working height lowered (stage j2 0.8, j3 0.7); table contact as a scoped `"table"` flag
  (hand only, depth, impact speed; empty hands); die_dramatically is now a limp slump onto the table, with revive;
  bang_table (fist); stir_lazy/stir_frantic; italian, italian_exasperated, chef_kiss, flick; shoot with recoil; 23
  face-acting verbs (bite, eat, laugh, duck, dodge...); comedy solos (mic_drop, facepalm, knee_slap, eye_roll, ta_da,
  slow_turn, laugh_hard, double_take with a tilt). Duets: high_five, fist_bump, cauldron, drum_roll, place_hat,
  elbow_nudge, straight_man, heckle; chomp/explosion/gate/once_upon/row_boat/shrug/quick_draw reworked, car_ride
  is now a steering-wheel ensemble. Toys: `/props` kinds `car` (drive) and `ufo` (ufo_fly/hover/land/crash), modelled
  in `show.PROP_SHAPES`. 62 gestures, 32 duets/ensembles, all pass. Table moves and toys are never run on the real
  arms unattended.
- Names (owner, Nov 7): project **Stage Hands**; the AI host/narrator is **Lars** (the green frog doll is the host's body and a prop); the arms stay unnamed because they play the story's characters.
- Character voices (Nov 7): narrator stays Laura; each arm's quoted lines get their own Fish Audio voice (palette
  deep/squeaky/old/child/witch/robot, cast with `[voice:left=NAME]`, default left deep, right squeaky). Runs synthesise
  in parallel: 0 s stall at 9 switches; mouths pinned to the audio (talk on within ±0.25 s). Host (Lars) voice candidates rendered for the owner.
  See storyteller.md.
- Round 3b review (Nov 7): all kept except die_dramatically (rework: power-cut collapse), italian (rework: pump forward/back, more aggressive), chef_kiss and flick (unclear: one rework, else cut), shoot (rework, pair with sound), mic_drop (cut). Sounds: both laughs, rimshot/crickets/explosion kept; music level right; lounge loop to be a real (non-8-bit) track. Sound must land on the gesture's impact moment (owner: very important).
- Voices review (Nov 7): Lars's host/narrator voice is "frog" (532f7f4a…); scene works; all six character voices kept.
- Lars dock pick (Nov 7): `lars.py --bake` writes `pick_lars` (5.0 s at speed 1) and `place_lars` (5.2 s), played
  through the stage server on the right arm, facing the audience; `[pick:lars]` / `[place:lars]` agent tags.
  Sim-checked on the stage server at speeds 0.3, 0.5, 0.75 and 1.0; baked at 0.3 for the first real runs
  ([props.md](props.md)).
