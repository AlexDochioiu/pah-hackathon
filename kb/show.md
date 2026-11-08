# Show mode: rehearsal how-to

The hackathon presentation, "Stage Hands": a founder starts the pitch, the stage hands cut in, Lars (the frog on the
table) hosts, introduces Alex, Michael and Darius, pitches the project, then a volunteer gets a live story. About 5
minutes in all, demo included. One voice, Lars's: the arms never speak, they react with gestures (owner, Nov 7).
How it works inside: [storyteller.md](storyteller.md#show-mode-the-hackathon-presentation).

## Run a rehearsal
1. Stage: the tunnel to the station (`ssh -N -L 8770:127.0.0.1:8770 nuck9`), or without the arms
   `cd agent && python3 fake_stage.py --port 8781` and run step 2 with `STAGE_URL=http://127.0.0.1:8781`. Real
   arms only with someone at the e-stop.
2. `cd agent && uv run app.py`, open http://127.0.0.1:8790/show in Chrome and click once (the browser needs a click
   before it plays audio).
3. Wait for a ✓ after each act in the act list (primed): the first run synthesizes every line (~10 s for act 1,
   ~40 s for all); later runs read `agent/.cache/show/` and are ready at once. Edit a line and only that act is synthesized again.
4. Act 0 shows "ready". Start talking, then cue act 1 mid-sentence.

## Keys (a presenter clicker sends PageDown/PageUp)
| Key | Does |
|---|---|
| PageDown, →, ↓, Space | next act (cuts the one playing) |
| PageUp, ←, ↑ | previous act; act 0 is "ready" (nothing plays) |
| 0–9 | jump to that act |
| Esc or the STOP button | STOP: arms freeze then rest, sound off, room closed. The next cue starts a fresh room |

Auto-advance (default on, the "auto-advance" box under the cue board): once act 1 is cued, acts 2 to 5 follow
on their own, each `SHOW_AUTO_GAP_S` (1.5 s) after the previous act's last gesture ends. Any cue (next, prev, a
number) still cuts the act playing and takes over, and the chain goes on from there. Act 5 hands over to the live
story and doesn't advance; the finale (act 6) is always a manual cue. Untick the box for one act per cue.
To skip the takeover, press 2 (or click act 2): the pickup starts at once and the chain goes on from there.

Space is "next" on `/show`, not push-to-talk. The talk button only works in act 5 (the demo), where the
volunteer holds it. The page shows the act, its state, presenter notes and the next act's first line.

## Acts and timings
Estimates from the host voice's measured pace (8.9 letters/s on the cached acts, Nov 7) plus the gesture and sound
tails after the last word; on the old script they came within 1-2 s of the real audio (act 1, with its old lead sound
and character voices, ran 7 s over). Budget (owner, Nov 7): acts 0-4 at most 2:00 (Dream off), act 5 at most 10 s,
act 6 at most 20 s, plus ~30 s of laughs and the volunteer walking up, and a 2-minute live story (the volunteer's ask
included). `tests/test_show.py` fails over budget.

| Act | What | Time |
|---|---|---|
| 0 | Cold open: a founder, nervously (human) | ~10 s |
| 1 | The takeover: instant face to the audience, "Stop. Stop, stop, stop.", "Down here. Yes. The frog." | ~13 s |
| 2 | The pickup: the Lars pick (only with `LARS_PICK` or `DREAM_PICK` on), then the stage hands rise (present), "Stage hands. Because they're hands.", "Please welcome... Stage Hands!" | ~19 s |
| 3 | The founders: a line each, the one table gag, applause | ~39 s |
| 4 | The pitch: dragon, knight, space wars, the vegan dragon, one line on the "Built with" card, the simulation and the Dimensional twin | ~36 s |
| 5 | The demo: "Who wants a story?", then the live story (~2 min) | ~8 s + live |
| 6 | The finale: "We were Stage Hands", tip_hats duet | ~20 s |

Acts 0-4: about 1:57. Cue the next act when a laugh dies down; a cue mid-act cuts it cleanly (arms freeze, voice
stops), so you can also skip a line that's running long.

The "Built with" card shows on the page during act 4 (`BUILT_WITH_ACT`): LiveKit Agents + Inference, Fish Audio,
the i2rt YAM arms on the MakerMods station, MuJoCo, OpenCV, the CC0 sounds and music, Claude Code, and Dimensional dimos (the
digital twin of both arms, `dimos/`; the move checker itself is MuJoCo). Act 4's last line says it: "a digital twin
of both arms in Dimensional's simulator". Only what the code uses; add a sponsor's tool as the next `BUILT_n` line
in show.md's config. Dream Labs Dream-1 is there as a commented-out `BUILT_9`: drop the `#` once the Dream-1 pick
works on the real arms.

## Editing the script (`agent/show.md`)
- The header of the file explains the format. Each `## Act N: title` is one cue; the text is spoken exactly as if
  the LLM wrote it, all by Lars. No `[talk:…]` or `[voice:…]` tags (the test fails on one): the arms react with
  gestures. Gesture tags go at the end of the sentence they act out, sound tags exactly where they play.
- `<!-- … -->` comments are presenter notes on the page, never spoken.
- Config block: `SHOW_NAME`, `HOST_NAME` (also used by `prompt.md`), `BUILT_WITH_ACT` and `BUILT_n = tool: how`
  (the card), `FOUNDER_n = Name` (named in act 3, never pointed at: owner, Nov 8, the founders wave themselves),
  and gesture chains (`LARS_POINT = right:point_at_lars | right:double_take`: the first gesture the stage knows wins).
- `LARS_PICK = on` keeps the line "A little higher, please…" where the right arm lifts Lars from the dock
  (`[pick:lars]`, then `[place:lars]`; a stage without the baked `pick_lars` gets a point instead). The line is
  spaced for the speed-1 bake (pick 4.7 s, place 4.8 s since the face-down bake, no other right-arm tag meanwhile); a slower bake needs
  more words, so try it on the fake stage first. Only with someone at the e-stop, and only once the pick works on
  the real arms.
- `DREAM_PICK = on` (off by default; it wins over `LARS_PICK` through `{?LARS_PICK}{?!DREAM_PICK}`) makes act 2's
  pick Dream-1's: `[dream:pick_lars]`, then "Hello, back row!…" and `{LARS_PUT_DOWN}`. The runner says the part up
  to the tag, waits for the Dream-1 run and its lines (`DREAM_ARGS` gives it `--hold --patter 6`, so no dead air),
  then the rest. A miss: facepalm, sad trombone and one `DREAM_OOPS` quip, then our own pick. About 90 s: act 2 runs
  far over the 2:00 budget with it on. [dream-1.md](dream-1.md) has the details.
- `{?!KEY}` keeps a line only when KEY is off; conditionals stack.
- Reload: save, then STOP and cue again (each session reads `show.md` afresh; no restart needed). `uv run --group dev pytest -q
  tests/test_show.py` checks every tag exists, no arm speaks, one table gag, the card, and the budget.

## Gotchas
- STOP during a duet: the stage finishes the duet at half speed before the arms rest (it refuses everything but
  `/freeze` mid-duet).
- Before its ✓ an act still plays, but its voice starts ~1 s after the cue instead of at once.
- `.env` must have the LiveKit keys; the page needs internet (LiveKit Cloud, jsDelivr).
