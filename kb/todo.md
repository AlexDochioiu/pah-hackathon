# To-do

The single list of open work. Update it in the same commit as the work: move items to Done (with the commit) or
change their status. Deadline: **Sun Nov 8, 15:00** submissions. How the pieces connect: [system.md](system.md).

Status: 🔨 being built · 👀 waiting for owner review · ⏸ waiting for someone at the station · 💤 parked

## Show (must work in the demo)
| What | Status | Notes |
|---|---|---|
| Presentation: robots interrupt the founder, introduce the founders, pitch, live demo, finale | 👀 | Show mode built: `/show`, clicker-cued acts in `agent/show.md`, rehearsal how-to in [show.md](show.md). Next: a rehearsal on the real arms, then tune lines and timings |
| Finale panic-button threat: `panic_reach` (left arm creeps to the e-stop, hovers ~5 cm above, pulls back; never a press) | 🔨 needs the e-stop's spot on the table | Script line in act 5 done; falls back to `slow_turn` until the gesture exists |
| Point at where the founders stand | ✅ sim · ⏸ real | Gestures `point_back_left/centre/right` (Nov 7 night): turn round, aim the head at the spot, jab, back (6.8 s), lean rule kept by turning, not leaning ([gestures.md](gestures.md)). Real arms: check the turn reads from the audience, retune the spots where the founders really stand; wrist-camera person finding later |
| Character voices: narrator + a distinct voice per arm, casting palette | 👀 | Built (`agent/voices.py`, `[voice:left=NAME]`); samples of the 6 voices, a scene and 3 Lars host voices in `/tmp/pah-voices/` for the review page; not yet heard in a live session |
| Sound effects + background music, played on the laptop through the web page | 👀 | Built: `[sfx:x]`, `[music:x]`, 76 CC0 sfx in 7 groups + 37 CC0 tracks in 18 moods (they take turns) in `agent/sounds/`; samples for the owner to listen to; not yet heard in a live session |
| Round-4 owner review: voice gains, sfx level (0.2 measures ~15-19 LU under the narration), laugh pick, borderline/dropped sfx, gesture previews, show roles (act 2 points vs e-stop/laptop spots), Dream-1 miss reaction | 👀 | `/Users/alexandrudochioiu/.review-pages/pah-round-4/index.html`; Q13 has placeholders for the miss-reaction preview and quips |
| Live chat toggle on the story page: open mic, turn detector, interruptions (for the LiveKit sponsor) | 👀 needs a live listen | Built (`agent/live.py`, page chip `mode: hold to talk ⇄`); checked headless with a recorded voice. Owner: try it on the laptop speaker in the room, tune `LIVE_MIN_INTERRUPT_*` ([storyteller.md](storyteller.md#live-chat-the-story-pages-open-mic-mode)) |
| Story fix (Nov 7 real run): gestures aimed at their words with a sync log, sfx budget and -12 dB, David narrates stories (Lars, "Deadpool better" voice, only the show), new squeaky/child voices and a pirate, two characters per story, present lifts, ~2 min stories, no written laughter, comedy marks-late stall fixed | 👀 | Built and tested on the fake stage ([storyteller.md](storyteller.md#how-gestures-sync-to-speech)); real-arm check below. Laugh candidates waiting for the owner in `/tmp/pah-sfx-review/` |
| Show rework: acts 0-3 under 2:00, Lars the only voice (arms react), one table gag, "Built with" card in act 3 | 👀 | `agent/show.md`, [show.md](show.md). Founder points built (Nov 7 night) |
| Comedy mode: double act, rimshot / crickets after punchlines (laugh_track, laugh_big out of the offer) | 🔨 | Sounds + prompt "Comedy show" done; comedy gestures with the gesture work |
| Gesture batch 3b: lower height, limp death + revive, fist bang + drum roll, stirring, Italian hands, high five, fist bump, hat on head, gun recoil, reworks (chomp, explosion, gate, once upon, row boat, shrug, car ride, quick draw), face-acting solos | 👀 | Done in sim (see commit log), previews → round-3b review page |
| Comedy gestures: double take, facepalm, knee slap, eye roll, ta-da, slow turn, laugh hard; elbow nudge, straight man, heckle | 👀 | Done in sim with 3b; round-3b answers applied (mic drop cut; die, italian, shoot, chef kiss, flick reworked) → round-4 review page (Q10) |
| Mime set: invisible wall, trapped in a box, ladder, wind, lean on nothing, juggle, flower, sour, knock, tightrope, elevator; duets mirror, invisible ball, hand through wall | 👀 | Done in sim, previews on the round-4 review page (Q8, Q9) |
| Sound on the visible hit: gesture/duet `sound` `{name, at}`, `[sfx]` moves onto the hit, `SFX_SYNC_MS` | 👀 | Done in sim + agent test; mp4s `robot/previews/sound/` on the round-4 page (Q11); tune `SFX_SYNC_MS` by ear on the real arms |
| Prop moves: toy car (drive, chase), spaceship (fly, hover/abduct, land, crash) | 👀 | Done in sim: solos, `/props` kinds, car_chase and ufo_abduct duets, previews. Spaceship: the team is finding one with a grip point (the bath-bomb UFO was dropped) |
| Lars pick from a fixed dock, saved as `pick_lars` / `place_lars` gestures, `[pick:lars]` tag | ✅ sim (the face-up bake picked for real) | Face-down dock (Lars held facing the audience) since Nov 7 evening, sim-checked at speeds 0.3, 0.5, 0.75 and 1.0 ([props.md](props.md)). Real runs start at speed 0.3, ramp 0.5 → 0.75 → 1.0 only after several clean picks |
| Dream-1 picks Lars from anywhere, our checker decides, the stage then holds him (`[dream:pick_lars]`, `POST /trajectory` with `carry`) | ✅ sim · ⏸ real | dreamcli picked and raised him for real (Nov 7 ~21:45); that run replayed through our checker passes (fingertips may brush to −3 mm). Real run through the show path: [dream-1.md](dream-1.md#real-arm-steps-for-the-show-path-preview--checker--play) |
| Live prop pick anywhere on the table, 5 s total (detect < 50 ms, plan < 200 ms, move ~4.5 s) | 🔨 after dock | Colour/shape detector on the overhead camera; one grasp template per prop |

## dimos twin: parked
Parked Nov 7 ~20:20: the owner saw no value in it. The code stays in `dimos/` for reference ([dimos.md](dimos.md)); don't run it at the demo. `stage.py` `/state` now has `grip_measured` per arm (station sync + restart to use it).

## Real-arm tests (need the stage server restarted on new code)
| What | Status | Notes |
|---|---|---|
| Gesture sync on the real arms: run a story, read the `sync arm:name … (±ms)` log lines, tune `ARM_LAG_MS` / `HIT_DEFAULT_S`; check `/idle` breathing and the `present` lift | ⏸ someone at e-stop | New stage gesture `present` needs the stage server restarted |
| Lars pick (unattended allowed, slow) | ⏸ ready | Face-up bake picked and placed a face-down Lars on the real arms (Nov 7 evening). Now rebaked face-down (held facing the audience), sim-checked at 0.3/0.5/0.75/1.0, committed at 0.3: sync + stage server restart, then the checklist in [props.md](props.md). `--face-up` rebake for the old profile hold |
| Deep facing (67°), talk, props on/off, hand ensembles (hug, clap…) | ⏸ | Watch the wrist cable on the big j5 turn; hand gaps sag 1–2 cm |
| Death collapse, fist bang, car drive (touch the table) | ⏸ someone at e-stop | Never unattended. Re-solved for the Round 3 table (~6 mm higher in the arm frame, the same depth into the table as before): watch the first knock and the car's wheels |
| Restart the web app (`cd ~/pah-hackathon/agent && uv run app.py`) | ⏸ owner back | Stopped while the station is unattended |
| Dream-1 real test (dream.check, dry run, real run with dreamcli) | ⏸ owner + stage stopped | Steps in [dream-1.md](dream-1.md#dream-1-real-test-dreamcli-with-the-owner-at-the-e-stop) |
| Dream-1 through the show path on the real arms | ⏸ someone at e-stop | Needs sync + stage restart for `/trajectory`; steps in [dream-1.md](dream-1.md#real-arm-steps-for-the-show-path-preview--checker--play) |

## Launch
| What | Status | Notes |
|---|---|---|
| Site design pick (round-1 review) | 👀 | [site.md](site.md) |
| Site deploy, demo video, social images | 💤 after the demo works | |

## Parked
- Dream-1 fine-tune on Lars demos: the base model stays at rest on our scene; a fine-tune is the way to make it
  reach (~$200, 50+ demos, 7-8 h; [dream-1.md](dream-1.md) has the recipe).

## Done (recent)
- Voice loudness: per-voice playout gain, every voice within ~1.2 LU of the storyteller (Nov 7 night).
- wrist_l camera back on USB 3.2 after a reseat (it had linked at USB 2.1, 480 Mbit/s, so 1280x720@30 failed) (Nov 7 ~21:50).
- fist_bump renamed `kiss` (the owner saw a kiss), sound on the contact; rub_hands and ufo_abduct under 8 s (Nov 7 ~20:30).
- dimos twin: live mirror, model match (arm ≤ 0.04 mm), ghost endpoint, MCP skills (Nov 7 ~20:10).
- Checker on the Round 3 calibration: right offset and per-arm table from `calib.json`; table moves re-solved;
  a finished stance move no longer blocks pick_lars (Nov 7 ~19:40).
- Show mode: `/show` page, cued acts with pre-synthesized voices, Lars as host.
- Round 3a verdicts: all 5 hand ensembles kept, facing 67° (beba237).
- Props explicit, deep facing, hand ensembles (e1b95eb). STOP button (af1d40d). Web hold-to-talk app (af8e2ab).
