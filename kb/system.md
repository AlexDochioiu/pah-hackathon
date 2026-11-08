# How the pieces fit

Update this page when a piece is added or a connection changes. Details live on each piece's own page.

```
 laptop                                              station (robot PC)
 ┌──────────────────────────────┐                    ┌──────────────────────────────┐
 │ web page (hold/live, STOP)   │ ◄── room audio ──┐ │ stage.py --real  :8770       │
 │   agent/web/index.html       │                  │ │  gestures/duets/ensembles    │
 │ app.py :8790 ── runs ──► storyteller worker ────┼─┼─► /play /duet /stance /talk  │
 │                              │  LiveKit room    │ │   /props /freeze /rest       │
 └──────────────────────────────┘                  │ │  show.Checker on every move  │
          ▲ STOP: freeze → end room → /rest        │ │  two YAM arms + 3 cameras    │
                                                   │ └──────────────────────────────┘
   LiveKit Cloud: STT → LLM → TTS (Inference) ─────┘      ▲ ssh tunnel 8770
```

## The chain, one sentence at a time
1. The user holds the button and talks. The web page sends the audio to the LiveKit room. (Or, with the story
   page's `mode: live chat` toggle, just talks: open mic, LiveKit's VAD + turn detector take the turns and a cut-in
   interrupts the storyteller; `agent/live.py`, a fresh room per mode.)
2. The storyteller (`agent/storyteller.py`) runs STT → LLM. The LLM writes narration with inline tags
   (`[left:roar]`, `[duet:hug]`, `[talk:left] "…"`, `[face:each_other]`, `[props:right]`, `[sfx:x]`,
   `[music:x]`, and soon `[pick:lars]`). The prompt is `agent/prompt.md`; the gesture list in it comes from the stage server.
3. `Storyteller.llm_node` tidies the text (`voices.Tidy`: no written laughter, character lines capped).
   `tags_first` moves each gesture tag early enough that its hit lands on its word (the stage's blend, the
   gesture's first hit and the arm's lag, minus the listener's playout delay, which the page measures and publishes
   as the `playout_ms` attribute), and adds a `present` lift before a casting and a light move for a motionless
   sentence; `Storyteller.tts_node` (`agent/voices.py`)
   removes all tags before speech and gives each character's quoted lines its own TTS voice; `GestureSync` fires
   each tag when playback reaches it (the transcript is time-stamped at every voice switch). A gesture's default
   sound (its stage `sound`, or an `[sfx]` from its sentence) is scheduled on its visible hit from the stage's
   `starts_in` reply.
4. Fired tags become HTTP calls to the stage server (`agent/gestures.py` `Stage`), through the ssh tunnel.
   `[dream:pick_lars]` instead runs `robot/dream_run.py`: it asks Dream-1 on the station for a move (cameras and
   `GET /state` only), plays it through `POST /trajectory` if our checker passes it, else our `pick_lars`, and its
   lines are spoken in the host's voice ([dream-1.md](dream-1.md)).
5. The stage server (`robot/stage.py`) plays pre-built, pre-checked trajectories from `robot/gestures/` and
   `robot/duets/` on the arms. Anything that fails the checker is refused, not played.
6. TTS audio goes back through the room to the web page's speaker, and so do the sound effects and music
   (`agent/sounds.py`, a second `background_audio` track in the same room).

## Where things are built
| Piece | Built by | Checked by | Page |
|---|---|---|---|
| Solo gestures | `robot/make_gestures.py` → `robot/gestures/*.json` | `show.Checker` (sim) | [gestures.md](gestures.md) |
| Duets and ensembles | `robot/make_duets.py` → `robot/duets/*.json` | `duets.DuetChecker` | [gestures.md](gestures.md) |
| Previews (GIFs) | `robot/preview*.py` → `robot/previews/` | owner review pages | |
| Lars pick | `robot/lars.py` (`--bake` from the fixed table dock `DOCK_RIGHT`, in the right arm's frame; live `--dry`/`--go` through `calib.json`) | its own sim checks + checker; the stage's `--dock-check` maps the dock into the camera through `calib.json` | [props.md](props.md) |
| Dream-1 pick (`[dream:pick_lars]`) | `robot/dream_preview.py` (station `~/dream-ghost/`: cameras + `GET /state` → Dream-1, no motion) → `robot/dream_run.py` (laptop) → `POST /trajectory` | the stage: `show.Checker`, ≤ 0.3 rad/s; else our `pick_lars` | [dream-1.md](dream-1.md) |
| dimos digital twin (parked: the owner saw no value; reference only) | `dimos/twin.py` (runs dimos from `/tmp/dimos-src`) | `dimos/check_model.sh` (FK vs `show.py`, side-by-side shots) | [dimos.md](dimos.md) |
| Narration, tags, voices, sounds | `agent/` | `agent/tests` (`uv run --group dev pytest -q`), `fake_stage.py` | [storyteller.md](storyteller.md) |

## Rules that cross pieces
- A new gesture needs: the trajectory, an `about` line (it goes into the prompt), SYNONYMS verbs in
  `agent/gestures.py`, and a preview. Without the `about` the LLM won't use it well. A sound on it is optional:
  `sound` with `Seq.hit` times from the trajectory, names from `agent/sounds/sfx/` (check_gestures verifies both).
- Real arms only through the stage server or `lars.py`, one program at a time (station lock). Safety rules:
  [AGENTS.md](../AGENTS.md), [arm-control.md](arm-control.md).
- The dimos twin (parked) only reads the real stage (`GET /state`); its MCP skills POST only to a sim stage (`:8783`), never 8770.
- New code on the station needs `./sync.sh` and a stage server restart (it checks every gesture at start, ~20 s).
