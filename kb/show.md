# Show mode: rehearsal how-to

The hackathon presentation, "Stage Hands": a founder starts the pitch, the arms cut in, Lars (the frog on the
table) hosts, the arms introduce Alex, Michael and Darius, pitch the project, then a volunteer gets a live story.
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

Space is "next" on `/show`, not push-to-talk. The talk button only works in act 4 (the demo), where the
volunteer holds it. The page shows the act, its state, presenter notes and the next act's first line.

## Acts and timings
Voice measured from the real TTS (narrator plus the arms' character voices, Nov 7), plus the gesture and sound
tails after the last word.

| Act | What | Time |
|---|---|---|
| 0 | Cold open: a founder, nervously (human) | ~10 s |
| 1 | The takeover: scratch, "Stop. Stop, stop, stop.", Lars says hello, "Please welcome... Stage Hands!" | ~52 s |
| 2 | The founders: a point and a joke each, the table gag, applause | ~47 s |
| 3 | The pitch: dragon, knight, space wars, eyes and mouth, LiveKit, the vegan dragon, simulation | ~54 s |
| 4 | The demo: "Who wants a story?", then live LLM stories (as long as you want) | ~12 s + live |
| 5 | The finale: "We were Stage Hands!", tip_hats duet, the_end | ~29 s |

Acts 0–3: about 2:45 before the demo, ~2:55 with the demo's intro, plus laughs. To trim, cut a line from act 3
(the longest). Cue the next act when a laugh dies down; a cue mid-act cuts it
cleanly (arms freeze, voice stops), so you can also skip a line that's running long.

## Editing the script (`agent/show.md`)
- The header of the file explains the format. Each `## Act N: title` is one cue; the text is spoken exactly as if
  the LLM wrote it: plain text is Lars, `[talk:left] "…"` the left arm (deep), `[talk:right] "…"` the right
  (squeaky). Gesture tags go at the end of the sentence they act out, sound tags exactly where they play.
- `<!-- … -->` comments are presenter notes on the page, never spoken.
- Config block: `SHOW_NAME`, `HOST_NAME` (also used by `prompt.md`), `FOUNDER_n = Name @ spot`, and per spot the
  gesture that points at it (`SPOT_back_left = left:point_back_left | left:look_at_other`: the first gesture the
  stage knows wins). When the real point gestures exist, nothing changes in the script.
- `LARS_PICK = on` keeps the line "A little higher, please…" where the right arm lifts Lars from the dock
  (`[pick:lars]`, then `[place:lars]`; a stage without the baked `pick_lars` gets a point instead). The line is
  spaced for the speed-1 bake (pick 4.7 s, place 4.8 s since the face-down bake, no other right-arm tag meanwhile); a slower bake needs
  more words, so try it on the fake stage first. Only with someone at the e-stop, and only once the pick works on
  the real arms.
- Reload: save, then STOP and cue again (each session reads `show.md` afresh; no restart needed). `uv run --group dev pytest -q
  tests/test_show.py` checks every tag exists and that acts 0–4 stay between 2 and 3:20 minutes.

## Gotchas
- STOP during a duet: the stage finishes the duet at half speed before the arms rest (it refuses everything but
  `/freeze` mid-duet).
- The founder points are placeholders (`look_at_other`/`nod`) until `point_back_*` gestures are built.
- Before its ✓ an act still plays, but its voice starts ~1 s after the cue instead of at once.
- `.env` must have the LiveKit keys; the page needs internet (LiveKit Cloud, jsDelivr).
