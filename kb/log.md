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
