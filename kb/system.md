# How the pieces fit

Update this page when a piece is added or a connection changes. Details live on each piece's own page.

```
 laptop                                              station (robot PC)
 ┌──────────────────────────────┐                    ┌──────────────────────────────┐
 │ web page (hold to talk, STOP)│ ◄── room audio ──┐ │ stage.py --real  :8770       │
 │   agent/web/index.html       │                  │ │  gestures/duets/ensembles    │
 │ app.py :8790 ── runs ──► storyteller worker ────┼─┼─► /play /duet /stance /talk  │
 │                              │  LiveKit room    │ │   /props /freeze /rest       │
 └──────────────────────────────┘                  │ │  show.Checker on every move  │
          ▲ STOP: freeze → end room → /rest        │ │  two YAM arms + 3 cameras    │
                                                   │ └──────────────────────────────┘
   LiveKit Cloud: STT → LLM → TTS (Inference) ─────┘      ▲ ssh tunnel 8770
```

## The chain, one sentence at a time
1. The user holds the button and talks. The web page sends the audio to the LiveKit room.
2. The storyteller (`agent/storyteller.py`) runs STT → LLM. The LLM writes narration with inline tags
   (`[left:roar]`, `[duet:hug]`, `[talk:left] "…"`, `[face:each_other]`, `[props:right]`, and soon `[sfx:x]`,
   `[music:x]`, `[pick:lars]`). The prompt is `agent/prompt.md`; the gesture list in it comes from the stage server.
3. `tags_first` moves gesture tags to the start of their sentence; `strip_tags_for_tts` removes all tags before
   speech; `GestureSync` fires each tag when playback reaches it.
4. Fired tags become HTTP calls to the stage server (`agent/gestures.py` `Stage`), through the ssh tunnel.
5. The stage server (`robot/stage.py`) plays pre-built, pre-checked trajectories from `robot/gestures/` and
   `robot/duets/` on the arms. Anything that fails the checker is refused, not played.
6. TTS audio (and soon sound effects and music) goes back through the room to the web page's speaker.

## Where things are built
| Piece | Built by | Checked by | Page |
|---|---|---|---|
| Solo gestures | `robot/make_gestures.py` → `robot/gestures/*.json` | `show.Checker` (sim) | [gestures.md](gestures.md) |
| Duets and ensembles | `robot/make_duets.py` → `robot/duets/*.json` | `duets.DuetChecker` | [gestures.md](gestures.md) |
| Previews (GIFs) | `robot/preview*.py` → `robot/previews/` | owner review pages | |
| Lars pick | `robot/lars.py` (plan, `--bake`, `--go`) | its own sim checks + checker | [arm-control.md](arm-control.md) |
| Narration, tags, voices, sounds | `agent/` | `agent/tests` (`uv run --group dev pytest -q`), `fake_stage.py` | [storyteller.md](storyteller.md) |

## Rules that cross pieces
- A new gesture needs: the trajectory, an `about` line (it goes into the prompt), SYNONYMS verbs in
  `agent/gestures.py`, and a preview. Without the `about` the LLM won't use it well.
- Real arms only through the stage server or `lars.py`, one program at a time (station lock). Safety rules:
  [AGENTS.md](../AGENTS.md), [arm-control.md](arm-control.md).
- New code on the station needs `./sync.sh` and a stage server restart (it checks every gesture at start, ~20 s).
