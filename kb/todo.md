# To-do

The single list of open work. Update it in the same commit as the work: move items to Done (with the commit) or
change their status. Deadline: **Sun Nov 8, 15:00** submissions. How the pieces connect: [system.md](system.md).

Status: 🔨 being built · 👀 waiting for owner review · ⏸ waiting for someone at the station · 💤 parked

## Show (must work in the demo)
| What | Status | Notes |
|---|---|---|
| Presentation: robots interrupt the founder, introduce the founders, pitch, live demo, finale | 👀 | Draft script and 7 questions on the review page (names, cues, slot length, where you stand, name). Then: a show mode with clicker-cued acts |
| Point at where the founders stand | 🔨 next | Fixed spots first (from the review answer); wrist-camera person finding later |
| Character voices: narrator + a distinct voice per arm, casting palette | 🔨 | [storyteller.md](storyteller.md); samples go on a review page |
| Sound effects + background music, played on the laptop through the web page | 👀 | Built: `[sfx:x]`, `[music:x]`, 32 CC0 sfx + 9 loops in `agent/sounds/`; samples for the owner to listen to; not yet heard in a live session |
| Comedy mode: double act, laugh track / rimshot / crickets after punchlines | 🔨 | Sounds + prompt "Comedy show" done; comedy gestures with the gesture work |
| Gesture batch 3b: lower height, limp death + revive, fist bang + drum roll, stirring, Italian hands, high five, fist bump, hat on head, gun recoil, reworks (chomp, explosion, gate, once upon, row boat, shrug, car ride, quick draw), face-acting solos | 🔨 | Sim previews → round-3b review page |
| Comedy gestures: double take, mic drop, facepalm, knee slap, eye roll, ta-da, slow turn, laugh hard; elbow nudge, straight man, heckle | 🔨 | After 3b |
| Prop moves: toy car (drive, chase), spaceship (fly, hover/abduct, land, crash) | 🔨 later | Spaceship: the team is finding one with a grip point (the bath-bomb UFO was dropped) |
| Lars pick from a fixed dock, saved as `pick_lars` / `place_lars` gestures, `[pick:lars]` tag | 🔨 | Sim GIF first; real runs start at speed 0.3, ramp 0.5 → 0.75 → 1.0 only after several clean picks |
| Live prop pick anywhere on the table, 5 s total (detect < 50 ms, plan < 200 ms, move ~4.5 s) | 🔨 after dock | Colour/shape detector on the overhead camera; one grasp template per prop |

## Real-arm tests (need the stage server restarted on new code)
| What | Status | Notes |
|---|---|---|
| Lars pick (unattended allowed, slow) | ⏸ sim first | Only near-table thing allowed unattended ([AGENTS.md](../AGENTS.md)) |
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
- Round 3a verdicts: all 5 hand ensembles kept, facing 67° (beba237).
- Props explicit, deep facing, hand ensembles (e1b95eb). STOP button (af1d40d). Web hold-to-talk app (af8e2ab).
