# To-do

The single list of open work. Update it in the same commit as the work: move items to Done (with the commit) or
change their status. Deadline: **Sun Nov 8, 15:00** submissions. How the pieces connect: [system.md](system.md).

Status: 🔨 being built · 👀 waiting for owner review · ⏸ waiting for someone at the station · 💤 parked

## Show (must work in the demo)
| What | Status | Notes |
|---|---|---|
| Presentation: robots interrupt the founder, introduce the founders, pitch, live demo, finale | 👀 | Show mode built: `/show`, clicker-cued acts in `agent/show.md`, rehearsal how-to in [show.md](show.md). Next: a rehearsal on the real arms, then tune lines and timings |
| Point at where the founders stand | 🔨 next | Gestures `point_back_left/centre/right` (the show script falls back to look/nod until they exist); wrist-camera person finding later |
| Character voices: narrator + a distinct voice per arm, casting palette | 👀 | Built (`agent/voices.py`, `[voice:left=NAME]`); samples of the 6 voices, a scene and 3 Lars host voices in `/tmp/pah-voices/` for the review page; not yet heard in a live session |
| Sound effects + background music, played on the laptop through the web page | 👀 | Built: `[sfx:x]`, `[music:x]`, 76 CC0 sfx in 7 groups + 37 CC0 tracks in 18 moods (they take turns) in `agent/sounds/`; samples for the owner to listen to; not yet heard in a live session |
| Comedy mode: double act, laugh track / rimshot / crickets after punchlines | 🔨 | Sounds + prompt "Comedy show" done; comedy gestures with the gesture work |
| Gesture batch 3b: lower height, limp death + revive, fist bang + drum roll, stirring, Italian hands, high five, fist bump, hat on head, gun recoil, reworks (chomp, explosion, gate, once upon, row boat, shrug, car ride, quick draw), face-acting solos | 👀 | Done in sim (see commit log), previews → round-3b review page |
| Comedy gestures: double take, mic drop, facepalm, knee slap, eye roll, ta-da, slow turn, laugh hard; elbow nudge, straight man, heckle | 👀 | Done in sim with 3b; on the round-3b review page |
| Prop moves: toy car (drive, chase), spaceship (fly, hover/abduct, land, crash) | 🔨 | Solos done in sim (drive, ufo_fly/hover/land/crash, `/props` kinds, previews); still open: car_chase duet, UFO abduct duet over the other head (needs per-arm prop kinds in duets.py). Spaceship: the team is finding one with a grip point (the bath-bomb UFO was dropped) |
| Lars pick from a fixed dock, saved as `pick_lars` / `place_lars` gestures, `[pick:lars]` tag | ✅ sim | Sim-checked at speeds 0.3, 0.5, 0.75 and 1.0 ([props.md](props.md)). Real runs start at speed 0.3, ramp 0.5 → 0.75 → 1.0 only after several clean picks |
| Live prop pick anywhere on the table, 5 s total (detect < 50 ms, plan < 200 ms, move ~4.5 s) | 🔨 after dock | Colour/shape detector on the overhead camera; one grasp template per prop |

## Real-arm tests (need the stage server restarted on new code)
| What | Status | Notes |
|---|---|---|
| Lars pick (unattended allowed, slow) | ⏸ ready | `pick_lars` / `place_lars` baked at speed 0.3; stage server restart needed. Checklist in [props.md](props.md) |
| Deep facing (67°), talk, props on/off, hand ensembles (hug, clap…) | ⏸ | Watch the wrist cable on the big j5 turn; hand gaps sag 1–2 cm |
| Death collapse, fist bang, car drive (touch the table) | ⏸ someone at e-stop | Never unattended |
| Restart the web app (`cd ~/pah-hackathon/agent && uv run app.py`) | ⏸ owner back | Stopped while the station is unattended |
| wrist_l camera fails ("Couldn't resolve requests") | ⏸ owner | Reseat its USB |

## Launch
| What | Status | Notes |
|---|---|---|
| Site design pick (round-1 review) | 👀 | [site.md](site.md) |
| Site deploy, demo video, social images | 💤 after the demo works | |

## Parked
- Dream-1 fine-tune on Lars demos: not needed with the dock/live pick ([dream-1.md](dream-1.md) has the recipe).

## Done (recent)
- Show mode: `/show` page, cued acts with pre-synthesized voices, Lars as host.
- Round 3a verdicts: all 5 hand ensembles kept, facing 67° (beba237).
- Props explicit, deep facing, hand ensembles (e1b95eb). STOP button (af1d40d). Web hold-to-talk app (af8e2ab).
