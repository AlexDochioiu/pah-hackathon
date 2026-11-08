# Storyteller voice agent

`agent/` is a LiveKit Agents (Python, `livekit-agents~=1.8`, locked at 1.8.11) voice agent; its host is Lars, the green plush frog
on the table (`HOST_NAME` in `agent/show.md`). It asks the
listener what story they want, improvises it scene by scene, and cues the two arms as puppet actors through the
stage server ([gestures.md](gestures.md)). Run instructions: [agent/README.md](../agent/README.md).

## Files
| File | What |
|---|---|
| `agent/storyteller.py` | Entrypoint (`uv run storyteller.py console\|dev`). Model choices and timings at the top. |
| `agent/prompt.md` | The system prompt. `{{GESTURES}}` is replaced at startup with the stage's gesture list, `{{SFX}}`/`{{MUSIC}}` with the sound library, `{{HOST_NAME}}` from `show.md`. |
| `agent/gestures.py` | Tag parser, gesture-name mapping, stage HTTP client, the speech-synced gesture hook. |
| `agent/sounds.py`, `agent/sounds/` | Sound effects and mood music: the library (CC0, `sounds/LICENSES.md`) and its player. |
| `agent/voices.py` | Character voices: the voice palette, the voice-run splitter, parallel synthesis, run timing. |
| `agent/director.py` | Freeze when the listener talks, idle when the stage is quiet, auto-continue to the next scene. |
| `agent/app.py`, `agent/web/index.html` | Story Theatre: hold-to-talk web page plus the PTT worker it runs. |
| `agent/show.py`, `agent/show.md` | Show mode: the clicker-cued presentation and its script. |
| `agent/fake_stage.py` | Stdlib stand-in for `robot/stage.py`, same API, prints what the arms would do. |
| `agent/telephony/dispatch-rule.json` | SIP dispatch rule for a phone number (not applied yet). |

## Models (LiveKit Inference: one LiveKit API key, billed to the project's credits)
- STT `assemblyai/universal-3-6-pro`, turn detection `inference.TurnDetector()`, preemptive generation on.
- LLM `google/gemma-4-31b-it`: LiveKit's documented latency-optimised default for voice agents (Nov 2026).
- TTS `fishaudio/s2.1-pro`, voice `532f7f4adb32420fa4f5c95e9b13be9d` ("frog": Lars the host, warm and enthusiastic; owner pick, was Laura `e3cd3841…`), with
  `expressive=True`: LiveKit injects the TTS's markup guide into the prompt, so the LLM can add emotion and
  laughs inline; LiveKit strips that markup from transcripts.
- Noise cancellation: BVC, or BVCTelephony for SIP callers.
- Characters speak in their own voices ([Character voices](#character-voices)); `CHARACTER_VOICES=0` turns that off.
- Every choice can be overridden in `agent/.env` (`LLM_MODEL`, `TTS_MODEL`, `TTS_VOICE`, `STT_MODEL`, `EXPRESSIVE`,
  `CHARACTER_VOICES`).

## Tag protocol (LLM output)
- `[left:slash]`, `[right:cower]`, `[both:bow]`: arm is `left|right|both`, gesture is a stage gesture name.
  Written at the end of the sentence they act out (the LLM's habit anyway); `tags_first` (the agent's
  `transcription_node`) moves them to the start of that sentence, so the arm moves as the words begin. At most one or
  two per sentence. Before this (Nov 7 real run) gestures visibly lagged the speech by about a sentence.
- `[face:each_other]` / `[face:audience]`: turn both actors toward each other or back to the audience
  (`POST /stance`); the prompt asks for each_other when the two arms play characters who meet or fight, and
  audience before the final bow. Stage requests go out in order, so `[face:audience] [both:bow]` turns, then bows.
- `[duet:name]`: both arms play a two-arm duet (`POST /duet`), names from `GET /duets` (`{{DUETS}}` in the
  prompt). The client holds the request until the gestures it already fired should be done (`busy_until` +
  0.3 s), since the stage refuses a duet unless both arms are free; `/freeze` cancels a duet still waiting.
- `[ensemble:name]`: the same, for the stage's ensemble-kind duets (both arms as ONE thing: storm, wings, jaws),
  listed separately as `{{ENSEMBLES}}` (`Stage.fetch_duets("ensemble")`). Either tag plays any duet or ensemble.
  The prompt has the LLM drop into one at any big beat, even mid-scene, then go back to the two characters; the
  ending may use `[ensemble:the_end]` instead of `[both:bow]`.
- `[props:left|right|both]` / `[props:none]`: `POST /props` (routed through `Stage.play("props", who)`). Empty hands
  are the default; the prompt asks for a prop only when a scene needs one, and gives a holding arm no talk tags.
  `[props:left car]` / `[props:left ufo]` send `"kind"`: that arm then plays only the toy's moves (drive, ufo_*).
  Toy verbs (drive, race, park, fly, abduct, crash...) map there in SYNONYMS; the stage refuses them (409, logged)
  if the arm doesn't hold that toy.
- `[talk:left]` / `[talk:right]` (also `[left:talk]`) right before a character's quoted line: `POST /talk` on
  for that arm; the sink turns it off at the closing quote mark (`"` or `”`), at the next talk tag, or when the
  reply ends or is interrupted (flush). Talk tags are not moved by `tags_first`: they stay before their quote.
- `[sfx:explosion]`: one sound effect from `agent/sounds/sfx/` (`{{SFX}}` in the prompt). `[music:spooky]`: start
  or crossfade the looping background music from `agent/sounds/music/` (`{{MUSIC}}`); `[music:none]` stops it.
  Sound tags are not moved by `tags_first`: they play where they were written (a laugh track after its punchline,
  a boom before its "KA-BOOM!"), except one in the same sentence as a gesture with hits (below). Names map like gestures (exact, synonym table, difflib 0.7, any word); unknown
  ones are dropped and logged. See "Sound effects and music" below.
- `[next]` at the end of a scene means "carry on". Once it has been spoken in full, the director waits
  `NEXT_SCENE_AFTER_S` (0.8 s) and asks for the next scene; the listener can jump in during that gap.
  No `[next]` means the agent waits for the listener (a question, or the end of the story).
- Stories end with "The end!" and `[both:bow]`.
- Names are normalised (case, spaces, hyphens), then mapped: exact, then a synonym table (`attack`→`slash`,
  `cheer`→`celebrate`…), then fuzzy match (difflib, cutoff 0.6), then any word of a compound (`sword_slash`).
  Unknown gestures and unknown arms are dropped and logged. Any short `[...]` span is removed from speech.
  Duets map first through `DUET_SYNONYMS` (catch/ball → `invisible_ball`, copy → `mirror`, through_the_wall →
  `hand_through_wall`); the mime solos have their own verbs (`wall`, `box`, `ladder`, `wind`, `flower`, `sour`...).
- The gesture list comes from `GET {STAGE_URL}/gestures` at session start; if the stage is down, the built-in
  default list in `gestures.py` is used (same names as `fake_stage.py`).

## Character voices
Narration is spoken in the host voice (Lars, "frog"); each arm's quoted lines (from its `[talk:...]` tag's opening quote to the closing
quote, the span its mouth flaps for) by that character's own voice. A quote without a talk tag stays narration.
- Palette (`voices.PALETTE`), all public Fish Audio voices on `fishaudio/s2.1-pro` through LiveKit Inference (any
  public library voice id works; an unknown id fails with "provider returned no audio"):

  | Name | Fish voice id | Sound | Measured pitch |
  |---|---|---|---|
  | (narrator) Laura | `e3cd384158934cc9a01029cd7d278634` | confident female narrator | ~160 Hz |
  | `deep` | `5becb4c89d67471f96137c91b22e02c6` | very deep, raspy male ("Deep voice") | ~110 Hz |
  | `squeaky` | `d7cdeff76ac94ee6bd23d39e019ad72a` | high, squeaky cartoon ("High Pitched Squeaks") | ~470 Hz |
  | `old` | `f7af7c05cc6042d8a8f90be8bb233044` | old, crackly, raspy ("Old Goblin") | ~300 Hz, mostly unvoiced |
  | `child` | `4fcb3a423c61415fb35604eba567d95f` | bright, bouncy child ("Energetic Child") | ~320 Hz |
  | `witch` | `d056e4cabd094165b66a1acd65e00cc0` | menacing, dramatic British woman ("Witch") | ~190 Hz |
  | `robot` | `175d7d5358b644d2ad2e8b7e4e13ece3` | flat, distorted robot ("Robot") | ~130 Hz, dull |
- The narrator is the show's host, Lars the frog. Its voice is the one setting `TTS_VOICE` (default Laura). Host
  candidates rendered Nov 7 for the owner to pick (`/tmp/pah-voices/host_<name>.mp3`, same welcome line):
  `frog` `532f7f4adb32420fa4f5c95e9b13be9d` (warm, enthusiastic, friendly male, fairly high), `showman`
  `58b3344db8b44d6ba27cc67dd908ccb2` (dramatic, playful, confident male), `ringmaster`
  `75d309f669524e7fbc0324b39e6b94ad` (energetic, theatrical, cheerful female), and `laura` for comparison.
- Casting: `[voice:left=NAME]` / `[voice:right=NAME]` at the casting (the prompt asks for one per arm, once per
  story, fitted to the character; `{{VOICES}}` lists the palette). Names are matched loosely (`robotic` -> robot,
  a word of a description such as `dragon` -> deep); unknown ones are dropped. The cast lasts until recast, also
  across stories. Default with no casting: left `deep`, right `squeaky`. The tag parses as a `Tag` (so the chat
  history keeps it); `GestureSink` ignores it.
- How: the session's `tts_text_transforms` no longer strip tags; `Storyteller.tts_node` gets the LLM text with them
  and `voices.VoiceSplitter` cuts it into runs of one voice, dropping every tag (quotes inside `<...>` expressive
  markup don't count). Each run gets its own TTS stream, started with the run's first words, so every run
  synthesises in parallel and the audio is played in order; narration runs go through the default `tts_node`
  (session TTS), character runs through one prewarmed `inference.TTS` per palette voice. A failed run is skipped
  (logged), the rest plays on.
- Switch gaps (Nov 7, 34 s two-character scene with all six voices, real Inference TTS through the real
  AgentSession pipeline): 0 s stall at all 9 switches, with the LLM streaming the scene in 2.5 s or in 8 s. The
  pause heard at a switch is the voices' own trailing + leading silence, 0.17-0.37 s (mean 0.22 s). Without the
  parallel start each switch would wait a stream's first byte: 0.8-1.5 s on a warm connection, ~3 s cold.
- Mouth and gesture timing: LiveKit's transcript synchronizer paces words at a fixed syllable rate until a reply's
  audio is complete; with voices of very different speeds the mouths drifted up to 3.5 s. So the tts node records
  where each run starts (`voices.RunMarks`: spoken letters, seconds of audio) and the transcription node
  (`timestamp_runs`, after `tags_first`) sends each run's text as a `TimedString` with that start time, the tags
  and quote leading into a run as a zero-length span at its start, and the reply's total at the end. The
  synchronizer then releases each run's text across that run's audio. Measured: talk on within -0.26..+0.23 s of
  the character's first sound, off 0.1-0.45 s before the line's last sound. The two nodes are paired through the
  reply's `SpeechHandle` (`_SpeechHandleContextVar`); with no tts node (text-only) the transcript passes through,
  and it never waits more than 8 s for a mark.
- Rendering samples: a script must open `livekit.agents.utils.http_context.open()` to use `inference.*` outside a
  worker. Samples of Nov 7 were rendered to `/tmp/pah-voices/` (scene.mp3 plus one file per voice).

## How gestures sync to speech
- `Storyteller.tts_node` (via `voices.VoiceSplitter`) removes tags from the text sent to the TTS; the session's
  `tts_text_transforms` are only `filter_markdown` and `filter_emoji`, which leave the tags alone.
- The LLM text, tags included, still goes through `transcription_node` to the transcript. `GestureSync.install`
  wraps the session's audio and transcription outputs (after `session.start()`) in a LiveKit
  `TranscriptSynchronizer`, which releases the reply's words in step with audio playout. `GestureSink`, right after
  it, fires each tag as it is released and passes the text on without tags. Tags count as zero syllables for the
  pacing (`hyphenate_without_tags`).
- `use_tts_aligned_transcript=False` is required: with TTS-aligned transcripts the transcript would be the TTS's
  words, which contain no tags, and no gesture would ever fire.
- On an interruption LiveKit stops releasing words, so tags after the cut never fire. The chat history keeps
  only what was heard (tags included), so the LLM continues from the cut.
- Measured in the fake-session test: a tag on the second word fires ~0.5 s into playout; one near the end of 4 s
  of audio fires at ~4.0 s. Pacing is LiveKit's speaking-rate estimate, so expect a few hundred ms of slop.
- Stage calls are fire-and-forget (2 s timeout, errors logged); a dead stage never stalls the voice.

## Sound effects and music
- Played by `sounds.Soundboard` on LiveKit's `BackgroundAudioPlayer`: its own `background_audio` track in the
  room, which the web page plays like the voice (it attaches every audio track). `GestureSink` hands sound tags
  to `Soundboard.cue` at the same moment it would fire a gesture there, so sounds are synced to playout the same
  way and an interruption cancels the ones after the cut. The player starts after `ctx.connect()`; if it fails the
  show runs without sound.
- Library (Nov 7): 76 sfx in 7 groups (`sfx/<group>/<name>.ogg`: weapons, impacts, movement, creatures, magic,
  world, crowd) and 37 music tracks in 18 moods (`music/<mood>/*.ogg`, two or more per mood), 26 MB, all
  open-licensed (sfx CC0 from Kenney packs and Freesound; music CC0 from OpenGameArt and Freesound; per-file sources, authors and
  licences in `agent/sounds/LICENSES.md`). Mono 48 kHz Opus in Ogg; sfx normalised so their loudest 400 ms sit at
  -16 LUFS, music at -20 LUFS integrated, all limited at -2 dBFS. The sfx file name is the tag name and the group
  folder only sorts the prompt list; the mood folder is the music tag, and its tracks take turns (a random one
  first, then the next each time that mood starts). A new file is picked up automatically (describe non-obvious
  names in `ABOUT`/`MUSIC_ABOUT` in `sounds.py`, add common other words to `SFX_SYNONYMS`/`MUSIC_SYNONYMS`).
  Tag names (sfx files, mood folders) must be letters and underscores (no digits: the tag parser rejects them).
- Volumes (env overrides): `SFX_VOLUME` 0.8; music `MUSIC_VOLUME` 0.35 when nobody talks, ducked to
  `MUSIC_DUCKED` 0.15 while Lars (`agent_state_changed` speaking) or the listener (`user_state_changed`) speaks.
  The music source is our own looping frame generator that glides its gain (down in 0.25 s, up in 1.2 s); a new
  `[music:x]` fades the old track out and the new one in over 1.5 s. Offline mix check: music about -34 dBFS RMS
  between lines, -41 dBFS ducked, an explosion about -17 dBFS.
- STOP: the page mutes every audio element at once, then `/stop` deletes the room; the session's shutdown callback
  closes the player (cancels every sound and unpublishes the track).
- Sound on the hit (round 3b): a gesture or duet whose stage entry has `sound` (`{"name", "at"}`, see gestures.md)
  plays it on its visible hits, not at its start: `Hit` in `gestures.py` schedules each at `t0 + at / speed +
  SFX_SYNC_MS`, where `t0` is the reply's arrival + its `starts_in` and `speed` the stage's (clamped) speed. An
  `[sfx:x]` in the same sentence (or right after it, before the next word) replaces the default on the first
  upcoming hit; one in a later sentence plays when spoken, as before. Refused gesture: a pending `[sfx]` plays at
  once. A hit already past plays now unless that sound already played. `SFX_SYNC_MS` (agent/.env, default 0)
  shifts every scheduled sound: the sim measures ~12 ms from POST to the first commanded sample, so tune it by ear
  on the real arms. Known gap: an interruption does not cancel hit sounds already scheduled (ponytail: they are
  at most a few seconds out).
- Comedy mode (prompt "Comedy show"): the opening offers a story or a comedy show; the arms do stand-up (two
  comedians, comedian and heckler, or a double act), `[music:lounge]`, and `[sfx:rimshot]` / `[sfx:laugh_track]`
  after small punchlines, `[sfx:laugh_big]` only after the best joke or two, with crickets or sad_trombone for a
  deliberate flop.

## Interruptions and idle
- `user_state_changed` → `speaking`: `POST /freeze` at once (VAD start of speech, before STT), and any pending
  next scene is cancelled. The prompt tells the LLM to answer with one playful line and carry on with the change.
- Agent listening and nobody talking: `POST /idle` after `IDLE_AFTER_S` (1.5 s), or later if the last gesture
  (`busy_until`, from the gesture durations) is still playing, so the final bow finishes.
- Session start: `POST /stage`, then the greeting. Session end: `POST /rest`.

## Story Theatre: hold-to-talk page (for the loud venue)
`cd agent && uv run app.py`, then open http://127.0.0.1:8790 (Chrome; `127.0.0.1` counts as secure, so the mic
works). The talk button (or the space bar) is the only way in: the mic is ignored unless it is held.
- `app.py` (aiohttp) runs `storyteller.py dev --no-reload` with `PTT=1 AGENT_NAME=storyteller-ptt` in its own
  process group, serves `web/index.html` (read from disk per request, so a browser refresh picks up page edits),
  `GET /token` and `GET /status`. The worker uses its own agent name so a plain `storyteller.py dev` can't take
  these rooms (override with `PTT_AGENT_NAME`).
- `/token` mints a LiveKit token locally (`livekit-api`) for a fresh room, `story-<boot>-<n>`, with the worker
  dispatched through the token's room config, and deletes the previous room: one story drives the arms at a time.
  A second window takes the story over; the first says so and waits for a click.
- `/status` reports the boot id, the git short hash (shown in the page footer, `+dirty` for uncommitted changes),
  the page hash, the worker and the stage (`GET {STAGE_URL}/state`, 1 s timeout). The page polls it every 1.5 s:
  server gone means "Server restarting…"; a new boot id means it rejoins a fresh room by itself (and reloads itself
  if `index.html` changed). No agent 20 s after joining: it starts over in a fresh room.
- Restarting: run it again. A new launch stops the one already on that port, worker included (pidfile
  `$TMPDIR/story-theatre-<port>.json`; a worker left over by a `kill -9`'d app is killed too). SIGINT/SIGTERM/SIGHUP
  close the room (the session ends, so the arms go to `/rest`), then stop the worker's process group. A worker that
  dies on its own is restarted after 3 s.
- It only talks to `STAGE_URL` (default `127.0.0.1:8770`, the tunnel to the station); it starts nothing on the
  station. Stage down shows "stage: offline".
- Agent side (`PTT=1`): `turn_detection="manual"`, input audio off after `session.start()`, and three RPC methods on
  the agent's participant (`director.PushToTalk`), which the page calls:
  - `start_turn`: freeze the arms (same as listener speech, and no idling while held, `Director.hold`), interrupt,
    clear the user turn, audio on.
  - `end_turn`: audio off, `commit_user_turn(transcript_timeout=2.5)`. The arms stay frozen until the commit
    resolves, then the usual idle/next-scene logic resumes. A press shorter than 250 ms is sent as `cancel_turn`.
  - `cancel_turn`: audio off, clear the user turn (also sent when the window loses focus mid-hold).
- `[next]` auto-continue works as before; pressing the button cancels a pending next scene, like speech does.
- Gotcha: if the STT finalises while the button is still held, LiveKit's `commit_user_turn` waits for a second final
  transcript until `transcript_timeout`, then uses what it has. At 10 s that made replies start 13 s after release;
  at 2.5 s they start ~3.5 s after.
- The page loads `livekit-client` 2.23.0 from jsDelivr (the venue needs internet for LiveKit anyway).

## Show mode (the hackathon presentation)
Clicker-cued acts: the arms cut in on a founder, introduce Lars, the founders and the project, then hand over to
the live story mode. Rehearsal how-to: [show.md](show.md). Script: `agent/show.md` (format in its header).
- `http://127.0.0.1:8790/show` (same `app.py`): `/token?mode=show` dispatches the worker with metadata
  `{"mode":"show"}`; `storyteller.py` then skips the greeting, appends the script's "Live context" to the prompt
  and starts `show.ShowRunner`. The normal page at `/` is unchanged.
- Cue flow: the page POSTs `/show/cue {cmd: next|prev|jump}`; `app.py` keeps the act cursor and forwards the act to
  the worker on `127.0.0.1:8792/play` (`SHOW_CUE_PORT`; 409 for another room). If that fails the page calls the
  agent's `show_play` RPC instead (about 250 ms slower, through the SFU). The worker publishes `show.act`,
  `show.state` (playing, done, live, ready), `show.primed` and `show.latency_ms` as participant attributes.
- Playback uses the LLM's own pipeline: `session.say(text, add_to_chat_ctx=True)` with the speech-synced gesture
  hook, sounds, talk tags (voices, mouths). A cue first cuts the current act (`director.cut()`: freeze, drop the
  next scene; `session.interrupt(force=True)`). Tags before an act's first word fire at once; with a sound among
  them the voice waits `LEAD_PAUSE_S` (0.3 s).
- LLM guard: until the `(live)` act any reply the LLM starts (`speech_created` with source `generate_reply`) is
  interrupted, so a stray turn can't talk over the script. The scripted lines land in the chat history, so the
  live stories know what was said.
- Pre-synthesis: at session start every act's speech goes through `tts_node` (the per-character voices included)
  and is cached as WAV in `agent/.cache/show/` (key: text, TTS model and voice, the `tts_node` source, text
  transforms, voices); act 1 first (~10 s), all acts ~40 s cold, instant warm. A cached act replays its frames through
  `say(audio=…)`; before the cache is ready it streams from the TTS (~1 s first audio).
- Measured (Nov 7, LiveKit Cloud, fake stage): cue → voice in the worker 310–320 ms on act 1 (the 0.3 s scratch
  lead included), 12–60 ms on acts without a lead sound. Heard in a client: scratch 0.46–0.78 s and voice
  0.75–1.0 s after the key press on the local path; 0.7–1.0 s / 1.1–1.3 s over the RPC fallback.
- Per-character voices: `Storyteller.tts_node` records where each voice run starts (`voices.RunMarks`, keyed by the
  speech handle) and `transcription_node` pins gestures and mouths to them. A replayed act skips `tts_node`, so
  `show.py` synthesizes under a stand-in speech handle, caches the marks next to the WAV (`<key>.json`) and hands
  them to the replayed speech. The cache key includes the palette's voice ids and the cast.
- Founder points use `SPOT_*` chains in the config: the first gesture the stage knows wins, so the placeholders
  (`point_back_left` and co., which don't exist yet) fall back to `look_at_other`/`nod` until they do.

## Gotchas
- Install the hook after `session.start()`. The console's text/audio toggle resets the session outputs and drops
  the hook: choose `console` or `console --text` at launch.
- `console --text` has no audio clock, so gestures fire in bursts as the text streams (seen Nov 7 against
  `fake_stage.py`). Use it to check wiring only; judge timing in voice mode.
- In `console --text` there is no audio, so gestures fire as the text arrives.
- The agent registers as `storyteller` with explicit dispatch: in `dev` mode it only joins rooms it is dispatched
  to (`lk dispatch create --new-room --agent-name storyteller`, or the SIP dispatch rule).
- A LiveKit Cloud-hosted agent can't reach `127.0.0.1:8770` on the station; run the agent on the station or on a
  laptop with `ssh -N -L 8770:127.0.0.1:8770 nuck9`.
- Tests (`uv run --group dev pytest -q`) need no credentials; they use LiveKit's own test fakes, vendored from the
  `livekit-agents@1.8.11` tag into `agent/tests/lkfakes/`.

- **STOP** (page button or Esc): `POST /stop` on app.py freezes the arms, closes the story's room and then sends
  `/rest` (retrying while a frozen duet finishes). Same effect as Ctrl-C in the console.
