# Storyteller voice agent

`agent/` is a LiveKit Agents (Python, `livekit-agents~=1.8`, locked at 1.8.11) voice agent. In story mode an unnamed
storyteller narrates ("David - British Storyteller"); Lars, the green plush frog on the table (`HOST_NAME` in `agent/show.md`), only
hosts the show (owner, Nov 7). It asks the listener what story they want, improvises it scene by scene, and cues the two arms as puppet actors through the
stage server ([gestures.md](gestures.md)). Run instructions: [agent/README.md](../agent/README.md).

## Files
| File | What |
|---|---|
| `agent/storyteller.py` | Entrypoint (`uv run storyteller.py console\|dev`). Model choices and timings at the top. |
| `agent/prompt.md` | The system prompt. `{{WHO}}` becomes the narrator (story) or Lars (show), `{{GESTURES}}` the stage's gesture list, `{{SFX}}`/`{{MUSIC}}` the sound library. |
| `agent/gestures.py` | Tag parser, gesture-name mapping, stage HTTP client, the speech-synced gesture hook. |
| `agent/sounds.py`, `agent/sounds/` | Sound effects and mood music: the library (CC0, `sounds/LICENSES.md`) and its player. |
| `agent/voices.py` | Character voices: the voice palette, the voice-run splitter, parallel synthesis, run timing. |
| `agent/director.py` | Freeze when the listener talks, idle when the stage is quiet, auto-continue to the next scene. `StoryClock` (in storyteller.py) times each story. |
| `agent/app.py`, `agent/web/index.html` | Story Theatre: hold-to-talk web page plus the PTT worker it runs. |
| `agent/live.py` | Live chat, the story page's open-mic mode: interruption knobs and the cut-in stage direction. |
| `agent/show.py`, `agent/show.md` | Show mode: the clicker-cued presentation and its script. |
| `agent/fake_stage.py` | Stdlib stand-in for `robot/stage.py`, same API, prints what the arms would do. |
| `agent/telephony/dispatch-rule.json` | SIP dispatch rule for a phone number (not applied yet). |

## Models (LiveKit Inference: one LiveKit API key, billed to the project's credits)
- STT `assemblyai/universal-3-6-pro`, turn detection `inference.TurnDetector()`, preemptive generation on.
- LLM `google/gemma-4-31b-it`: LiveKit's documented latency-optimised default for voice agents (Nov 2026).
- TTS `fishaudio/s2.1-pro`. Narrator by mode (owner, Nov 7): story mode "David - British Storyteller"
  `72324f5951924b2eb27faaf5630153b4` (`STORY_VOICE`), show mode "Deadpool better" `d657aa381ad444e393d7f6ff0f8cc2f0`
  for Lars (`HOST_VOICE`), the show's only voice. Both with
  `expressive=True`: LiveKit injects the TTS's markup guide into the prompt, so the LLM can add emotion and
  laughs inline; LiveKit strips that markup from transcripts.
- Noise cancellation: BVC, or BVCTelephony for SIP callers.
- Characters speak in their own voices ([Character voices](#character-voices)); `CHARACTER_VOICES=0` turns that off.
- Every choice can be overridden in `agent/.env` (`LLM_MODEL`, `TTS_MODEL`, `STORY_VOICE`, `HOST_VOICE`, `STT_MODEL`,
  `EXPRESSIVE`, `CHARACTER_VOICES`). The old `TTS_VOICE` is ignored: it set one voice for both modes.

## Tag protocol (LLM output)
- `[left:slash]`, `[right:cower]`, `[both:bow]`: arm is `left|right|both`, gesture is a stage gesture name.
  Written right after the word they act out (or at the end of the sentence for a whole-sentence move);
  `tags_first` (the agent's `transcription_node`) moves each one early enough that its hit lands on that word
  (see "How gestures sync to speech"). One per sentence, every sentence (the prompt; a motionless sentence gets a
  code fill move). `[left:present]`: the arm rises to introduce its character; the code adds it before a casting
  that lacks one.
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
Narration is spoken in the narrator's voice (story mode: David; show mode: Lars, who then reads every line,
quotes included: `Storyteller.host_only`, set by `ShowRunner`); each arm's quoted lines (from its `[talk:...]` tag's opening quote to the closing
quote, the span its mouth flaps for) by that character's own voice. A quote without a talk tag stays narration.
- Palette (`voices.PALETTE`), all public Fish Audio voices on `fishaudio/s2.1-pro` through LiveKit Inference (any
  public library voice id works; an unknown id fails with "provider returned no audio"):

  | Name | Fish voice id | Sound | Measured pitch |
  |---|---|---|---|
  | (story narrator) David | `72324f5951924b2eb27faaf5630153b4` | British storyteller ("David - British Storyteller") | |
  | (show host) Lars | `d657aa381ad444e393d7f6ff0f8cc2f0` | "Deadpool better" | |
  | `deep` | `5becb4c89d67471f96137c91b22e02c6` | very deep, raspy male ("Deep voice") | ~110 Hz |
  | `squeaky` | `396be6334bcc41f68af3070e595aee76` | high, squeaky chipmunk ("Simon the chipmunk") | |
  | `old` | `f7af7c05cc6042d8a8f90be8bb233044` | old, crackly, raspy ("Old Goblin") | ~300 Hz, mostly unvoiced |
  | `child` | `a3d56467a193409984ce245f8f371c9a` | bright, bouncy child ("Noah") | |
  | `witch` | `d056e4cabd094165b66a1acd65e00cc0` | menacing, dramatic British woman ("Witch") | ~190 Hz |
  | `pirate` | `4d78f878f023424cb77975a6dea180a6` | gruff sea dog: pirates, sailors, scallywags | |
- Loudness: each voice can get a fixed playout gain (`voices.VOICE_GAIN_DB`, by Fish voice id). Measured Nov 7
  night (mean of three takes, integrated): storyteller -17.4 LUFS, Lars -23.1, deep -20.7, squeaky -29.3, old -23.2,
  child -16.5, witch -23.0, pirate -23.2 (12.8 LU apart; one voice's takes vary ~3 LU). Gains in use (owner, round 4,
  Nov 8): witch +5.5 dB, child -1 dB, every other voice 0 dB; the proposed boosts for Lars (+5.5), deep (+3.5),
  squeaky (+12), old (+6) and pirate (+6) were declined. `Storyteller.tts_node` wraps each run's frames
  (`with_gain`); a soft knee bends samples past 0.8 FS toward -0.5 dBFS instead of clipping. `VOICE_GAIN=0` turns
  it off. A new voice id gets 0 dB.
- The robot voice is gone (it sounded awful, owner Nov 7): `robot`, `robotic`, `machine`, `alien`, `android`,
  `cyborg`, `computer` map to `deep` (`voices.ALIASES`).
- Story mode's narrator is an unnamed storyteller (`STORY_VOICE`, David); Lars hosts only the show (`HOST_VOICE`).
  Both, and the new squeaky, child and pirate slots, are the owner's picks from the Nov 7 voice review page. A new
  host voice changes the show cache key (it includes the voice id), so every act is synthesised again.
- Casting: exactly two characters per story or comedy set, one per arm, cast once at the start, each in its own
  sentence opening with `[arm:present]` and `[voice:left=NAME]` / `[voice:right=NAME]` (`{{VOICES}}` lists the
  palette). So three voices: the narrator plus one per character. Nobody else gets an arm, a voice or a quoted line;
  others are only mentioned by the narrator, and ensembles never speak (owner, Nov 7). Names are matched loosely (a
  word of a description such as `dragon` -> deep); unknown ones are dropped. Code lock (`voiced_tts(locked=…)`):
  after an arm's first casting, a later voice tag for it is dropped and logged ("dropped recast") until a new story.
  A new story starts (`Storyteller.new_story`, from `StoryClock`) at a story's end (its `[both:bow]` or
  `[ensemble:the_end]`) or on a listener turn asking for a new/another/different story or a comedy. The voices
  carry over to the next story until it casts. Default with no casting: left `deep`, right `squeaky`. The tag parses as a `Tag` (so the chat
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
  and it never waits more than 30 s for a mark. Each run's start is known as soon as the runs before it are
  synthesised (not played): the old version learnt it only after playback, so a long run timed the wait out, the
  rest of the reply went untimed and the comedy set's gestures stalled 72 s then burst (fixed Nov 7; the log says
  "voice run marks late" if it happens again).
- Rendering samples: a script must open `livekit.agents.utils.http_context.open()` to use `inference.*` outside a
  worker. Samples of Nov 7 were rendered to `/tmp/pah-voices/` (scene.mp3 plus one file per voice).

## How gestures sync to speech
The chain, LLM to arm: `Storyteller.llm_node` (`voices.Tidy`: no written laughter, no `*laughs*`, character lines
capped) → `tts_node` (voice runs, `RunMarks`) and `transcription_node` (`tags_first`: tags moved early, then
`timestamp_runs`) → LiveKit's `TranscriptSynchronizer` releases words with the audio → `GestureSink` fires each
tag as it is released → `Stage` (HTTP to the stage server) → the arm. The listener hears the word `audio_s`
after its release; the arm's hit comes `rtt/2 + starts_in + hit_at/speed + arm_lag` after the POST.
- Measured causes (Nov 7, from the session-1 logs, the fake stage and the sim):
  - A tag at its sentence's start reached its hit 1.2-3.4 s later: the gesture's first hit `at` (median 1.55 s,
    0.87-2.82) plus the stage's `starts_in` blend (median 0.30 s from the stage pose, 0.32 s from idle, 0.54 s when
    cutting another gesture, p90 1.72 s). That was the main lag.
  - The listener's playout (WebRTC jitter buffer + network + output): ~0.15-0.3 s; it makes a tag fired on time
    look ~0.18 s early, so it is subtracted, not added.
  - Sounds: BackgroundAudioPlayer's own queue (400 ms) plus the mixer block (100 ms) ran ~390 ms ahead with
    ~491 ms queued: sfx were ~0.5 s late. With a 150 ms queue and 20 ms blocks (`SFX_BUFFER_MS`) it is ~0.1 s.
  - Queueing: the session-1 gaps of 6-18 s without motion were the Director idling only while listening; an
    opening duet made the next solos and the face turn fail (409). Now `/idle` breathing is the baseline, solos
    wait for the duet, and a stance change waits for it to end.
  - One-word talk tags opened the mouth for 8 ms; now at least 0.4 s, closed half a word after the last word.
- What the code does (`gestures.Timing`, `_Placer`): a tag right after a word is aimed at that word, one after the
  sentence's last word `SENTENCE_HIT_S` into the sentence. It fires `gesture_lead = rtt/2 + starts_in + hit_at/speed
  + ARM_LAG_MS - audio_s` (capped at 3.5 s) earlier, converted to syllables at the voice's rate and snapped to a word
  boundary, never before a face/props/pick tag or an earlier sentence's tags. `starts_in` and the rtt are EMAs of
  the stage's replies; the rate is measured per reply (sink); `audio_s` is the page's measured playout (index.html
  publishes `playout_ms`/`playout_bg_ms` every 2 s from `getStats`: jitter-buffer delay per track + RTT/2 + output
  latency). An `[sfx]` in a gesture's sentence rides on its hit; elsewhere it moves earlier by the sound track's
  backlog + its playout minus the voice's + the file's own lead.
- Sync log: every gesture leaves a `[mark:arm=name]` where it was aimed; when it is released the log says
  `sync left:slash: sent -1.42 s, word heard +0.25 s, hit +0.31 s (+60 ms)`, i.e. the hit's error against the
  word. Tune from those lines: hits consistently late → raise `ARM_LAG_MS` (or `HIT_DEFAULT_S` for gestures without
  hit marks); early → lower it. `GESTURE_LEAD=0` restores the old sentence-start behaviour.
- Always motion: `/idle` (idle_breathe between gestures) from the session start; a sentence of 4+ words with
  nothing moving gets `[fill:light]` (a look or nod on a free arm, rotating; `FILL_GESTURES=0` turns it off; never in
  scripted show acts). One arm's next gesture waits for its previous hit + `HIT_GAP_MS` (at most 1.5 s), so two
  close tags don't cut each other's hit.
- `use_tts_aligned_transcript=False` is required: with TTS-aligned transcripts the transcript would be the TTS's
  words, which contain no tags, and no gesture would ever fire. The code's own `[mark:…]`/`[fill:…]` tags are
  taken out of the history the LLM reads.
- On an interruption LiveKit stops releasing words, so tags after the cut never fire. The chat history keeps
  only what was heard (tags included), so the LLM continues from the cut.
- Stage calls are fire-and-forget (2 s timeout, errors logged); a dead stage never stalls the voice.
- Knobs (agent/.env; defaults in `.env.example`): `GESTURE_LEAD` 1, `AUDIO_DELAY_MS` 250 (until the page measures),
  `ARM_LAG_MS` 70, `HIT_DEFAULT_S` 0.6, `SENTENCE_HIT_S` 0.5, `SPEECH_RATE` 2.8 (until measured; the host ~2.5),
  `HIT_GAP_MS` 200, `FILL_GESTURES` 1, `SFX_SYNC_MS` 0, `SFX_BUFFER_MS` 150.
- Not verified without the real arms: the real `arm_lag` (sim commands only; 70 ms is from the 0.1-0.19 rad
  tracking error at speed), whether the hits now land on their words by eye, and the playout numbers from a real
  browser on the venue's network.

## Story length
`StoryClock` (storyteller.py): a story or comedy set should run about 2 minutes in 5-6 beats (prompt). The clock
starts at its first casting, music or present tag (in the show's live demo at the volunteer's ask), logs
`story length: m:ss` at its bow or `the_end`, and past `STORY_WRAP_S` (105 s) adds a wrap-up note to the
instructions and the next-scene prompt ("head for the climax, end within one or two replies"), past `STORY_END_S`
(150 s) "end it in this reply". The note goes at the end; a show cue ends the clock.

## Spoken text tidy (`voices.Tidy`, in `llm_node`)
Runs of written laughter (three syllables or more: "HAHAHA…", "hehehe", "mwahaha") become "ha ha"; `*laughs*`,
`(chuckles)` and other stage directions in `*…*` or laughter parentheses go; a quoted character line is cut at the
first sentence end after 25 words (hard cap 40, "…"). A comedy set's minute of "HAHAHA" (Nov 7) stalled the TTS.
The prompt says the same: no written laughter, short lines.

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
- Volumes (env overrides): `SFX_VOLUME` 0.4 (owner, round 4, Nov 8; it was 0.2 from Nov 7 evening, 0.8 before; the
  files were re-levelled to -18 LUFS short-term, Nov 7); music `MUSIC_VOLUME` 0.35 when nobody talks, ducked to
  `MUSIC_DUCKED` 0.15 while the narrator (`agent_state_changed` speaking) or the listener (`user_state_changed`) speaks.
  Measured Nov 7 night: ducked music sits ~19 LU under the storyteller; a hit at 0.2 peaks (400 ms) ~-34 LUFS,
  ~15-19 LU under the narration, so 0.4 puts it ~10-13 LU under.
  The music source is our own looping frame generator that glides its gain (down in 0.25 s, up in 1.2 s); a new
  `[music:x]` fades the old track out and the new one in over 1.5 s. Offline mix check: music about -34 dBFS RMS
  between lines, -41 dBFS ducked, an explosion about -17 dBFS.
- STOP: the page mutes every audio element at once, then `/stop` deletes the room; the session's shutdown callback
  closes the player (cancels every sound and unpublishes the track).
- Sound on the hit (round 3b): a gesture or duet whose stage entry has `sound` (`{"name", "at"}`, see gestures.md)
  plays it on its visible hits, not at its start: `Hit` in `gestures.py` schedules each at `t0 + at / speed +
  SFX_SYNC_MS`, where `t0` is the reply's arrival + its `starts_in` and `speed` the stage's (clamped) speed. Each
  sound starts its own lead early (`sfx_lead`: from the file's start to its first 25 ms within 6 dB of its loudest;
  sword_swing 0.4 s, body_fall 0.35, bonk and roar 0.2, most others ≤ 0.05), so its peak lands on the hit. An
  `[sfx:x]` in the same sentence (or right after it, before the next word) replaces the default on the first
  upcoming hit; one in a later sentence plays when spoken, as before. Refused gesture: a pending `[sfx]` plays at
  once. A hit already past plays now unless that sound already played. `SFX_SYNC_MS` (agent/.env, default 0)
  shifts every scheduled sound: the sim measures ~12 ms from POST to the first commanded sample, so tune it by ear
  on the real arms. A listener cut-in (a push-to-talk press, or a live-chat interruption) stops the sound effects
  already playing (`Soundboard.stop_sfx`; the music only ducks) and cancels hit sounds still scheduled
  (`GestureSync.cancel_sounds` → `Hit.cancel`): the frozen arms never reach those hits. A show cue doesn't.
- Budget (stories and comedy): at most one sfx per `SFX_MIN_GAP_S` (12 s), about one per beat; a gesture's own
  hit sound counts. Over budget it is dropped and logged ("dropped [sfx:x]: budget"). Scripted show acts are exempt.
- Not offered (files kept, `sounds.OFFERED_NOT`; the prompt doesn't list them and a tag maps to a near sound or is
  dropped): evil_laugh, stomp (→ footsteps), spaceship (→ flyby), magic (→ spell), kiss, laugh_track and laugh_big
  (→ laugh). Rifle, gallop and debris are offered again, and all 23 borderline sfx stay (owner, round 4, Nov 8).
  double_take has no hit sound.
- Comedy mode (prompt "Comedy show"): the opening offers a story or a comedy show; the two arms are a double act
  (the set's two characters, cast once), `[music:lounge]`, `[sfx:rimshot]` or `[sfx:laugh]` after a punchline (`laugh`:
  `sfx/crowd/laugh.ogg`, a 2 s small-crowd burst, the owner's round-4 pick of four CC0 candidates), crickets or
  sad_trombone for a deliberate flop; about 2 minutes like a story.

## Interruptions and idle
- `user_state_changed` → `speaking`: `POST /freeze` at once (VAD start of speech, before STT), and any pending
  next scene is cancelled. The prompt tells the LLM to answer with one playful line and carry on with the change.
  This is the console/`dev` behaviour; push-to-talk freezes on the press, live chat on a real cut-in (below).
- From the session start the arms breathe (`Stage.idle()`: `/idle`, so the stage plays idle_breathe after every
  gesture too); a `Stage.play` re-sends it if needed.
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
  - `start_turn`: freeze the arms (same as listener speech, and no idling while held, `Director.hold`), stop the
    sound effects (`hush`), interrupt, clear the user turn, audio on.
  - `end_turn`: audio off, `commit_user_turn(transcript_timeout=2.5)`. The arms stay frozen until the commit
    resolves, then the usual idle/next-scene logic resumes. A press shorter than 250 ms is sent as `cancel_turn`.
  - `cancel_turn`: audio off, clear the user turn (also sent when the window loses focus mid-hold).
- `[next]` auto-continue works as before; pressing the button cancels a pending next scene, like speech does.
- Gotcha: if the STT finalises while the button is still held, LiveKit's `commit_user_turn` waits for a second final
  transcript until `transcript_timeout`, then uses what it has. At 10 s that made replies start 13 s after release;
  at 2.5 s they start ~3.5 s after.
- The page loads `livekit-client` 2.23.0 from jsDelivr (the venue needs internet for LiveKit anyway).

## Live chat: the story page's open-mic mode
The `mode: hold to talk ⇄` chip in the story page's header (not on `/show`) flips to `mode: live chat`: the
audience just talks, and can cut the storyteller off mid-sentence. Hold to talk stays the default; the choice is
kept in the browser (`localStorage.storyMode`).
- Switching ends the current story and starts a fresh room in the new mode (the greeting comes again). LiveKit
  1.8.11 can change `turn_detection` (`session.update_options`) and the audio input at runtime, but not the
  interruption options, so a fresh room was the simpler, cleaner path.
- Page: `/token?mode=live` → dispatch metadata `{"mode":"live"}` (`app.dispatch_metadata`). The talk button turns
  into a mint "LIVE CHAT" panel with a mic level meter (`createAudioAnalyser` on the local mic track) and the
  agent's state ("listening: just talk", "talk any time to cut in"); press and space do nothing. The mic is
  captured with `echoCancellation`, `noiseSuppression` and `autoGainControl` on (checked in Chrome's track
  settings): the voice plays from the same laptop.
- Agent (`agent/live.py`, `live.is_live`): audio input on, `inference.TurnDetector()`, silero VAD (LiveKit's
  default `inference.VAD`, built into livekit-agents 1.8: no plugin needed), noise cancellation as before, and the
  interruption knobs below. `live.LiveTurns` replaces the director's freeze-on-any-VAD-speech
  (`Director.freeze_on_speech = False`). The arms freeze, the pending next scene drops and the sound effects stop
  (`hush`) only once the interruption is real: the speech handle the listener was hearing ends `interrupted`
  (`speech_created` → done callback), the same as a push-to-talk press. While LiveKit only pauses the voice (agent
  state speaking → listening while the user speaks), nothing freezes: the current gesture plays on, and the stage
  holds (`Director.holding`: no idle, no next scene) until the voice resumes or is cut. On a resume the tags carry
  on with the voice. A final transcript between scenes drops the pending next scene without freezing the idling
  arms (`Director.drop_next_scene`).
- Knobs (agent/.env, read per session):

  | Env | Default | LiveKit's | What |
  |---|---|---|---|
  | `LIVE_INTERRUPT_MODE` | `adaptive` | auto | `adaptive`: LiveKit's model tells a cut-in from "uh-huh" (on in `dev`); `vad`: any speech |
  | `LIVE_MIN_INTERRUPT_S` | 0.6 | 0.5 | speech this long before it counts as an interruption |
  | `LIVE_MIN_INTERRUPT_WORDS` | 2 | 0 | transcribed words needed too; a one-word "stop" doesn't cut in (0 = off) |
  | `LIVE_RESUME` | 1 | 1 | a cut-in that turns out to be noise: the voice resumes where it paused |
  | `LIVE_FALSE_INTERRUPT_S` | 2.0 | 2.0 | silence after a cut-in with no words before it counts as noise |
- Tested end to end on LiveKit's fakes (`tests/test_live.py`): a wordless noise mid-story pauses the voice
  (3.8 s), it resumes (6.7 s) with no freeze and no idle in between, and the next tag fires after the resume; real
  words pause it, then freeze the arms once when the turn commits and the speech is cut (~0.6 s later).
  With `LIVE_MIN_INTERRUPT_WORDS` ≥ 1 a wordless noise doesn't even pause the voice.
- Checked Nov 7 (fake stage `--backend kin`, headless Chrome with a looping recorded sentence as the mic): the
  toggle, the meter, the turn detector (sentence heard, reply), and an interruption every loop mid-reply ("live:
  the listener cut in" in the worker log). Not checkable headless: echo from a real speaker, a noisy room, how the
  guards feel.
- Headless test gotcha: Chrome's `--use-file-for-fake-audio-capture` needs `--disable-features=AudioServiceSandbox`
  on macOS, or the mic is silent ("Failed to read … as input to the fake device").

## Show mode (the hackathon presentation)
Clicker-cued acts: the stage hands cut in on a founder, Lars hosts, introduces the founders and the project, then
hands over to the live story mode. One voice only, Lars's (owner, Nov 7): the arms never speak, they react with
gestures; `ShowRunner` sets `agent.host_only`, so even a live-demo character line is read by Lars. Budget: acts 0-3
at most 2:00, act 4 at most 10 s, act 5 at most 20 s, the live story about 2 minutes (test_show checks the
estimates; the table is in [show.md](show.md)). Rehearsal how-to: [show.md](show.md). Script: `agent/show.md` (format in its header).
- `http://127.0.0.1:8790/show` (same `app.py`): `/token?mode=show` dispatches the worker with metadata
  `{"mode":"show"}`; `storyteller.py` then skips the greeting, appends the script's "Live context" to the prompt
  and starts `show.ShowRunner`. The normal page at `/` is unchanged.
- Cue flow: the page POSTs `/show/cue {cmd: next|prev|jump}`; `app.py` keeps the act cursor and forwards the act to
  the worker on `127.0.0.1:8792/play` (`SHOW_CUE_PORT`; 409 for another room). If that fails the page calls the
  agent's `show_play` RPC instead (about 250 ms slower, through the SFU). The worker publishes `show.act`,
  `show.state` (playing, done, live, ready), `show.primed` and `show.latency_ms` as participant attributes.
- Playback uses the LLM's own pipeline: `session.say(text, add_to_chat_ctx=True)` with the speech-synced gesture
  hook and sounds. A cue first cuts the current act (`director.cut()`: freeze, drop the next scene;
  `session.interrupt(force=True)`) and sets `agent.scripted` (no fill moves, no sfx budget, the story clock stops).
  Tags before an act's first word fire at once (act 1: the snap to the audience, then Lars's first word).
- LLM guard: until the `(live)` act any reply the LLM starts (`speech_created` with source `generate_reply`) is
  interrupted, so a stray turn can't talk over the script. The scripted lines land in the chat history, so the
  live stories know what was said.
- Pre-synthesis: at session start every act's speech goes through `tts_node` (the per-character voices included)
  and is cached as WAV in `agent/.cache/show/` (key: text, TTS model and voice, the `tts_node` source, text
  transforms, voices); act 1 first (~10 s), all acts ~40 s cold, instant warm. A cached act replays its frames through
  `say(audio=…)`; before the cache is ready it streams from the TTS (~1 s first audio).
- Measured (Nov 7, LiveKit Cloud, fake stage): cue → voice in the worker 12–60 ms on acts without a lead sound
  (act 1 no longer has one). Heard in a client: voice ~0.45-0.7 s after the key press on the local path, ~0.4 s
  more over the RPC fallback.
- Auto-advance (`ShowRunner.auto`, page toggle → RPC `show_auto`): a finished scripted act starts the next
  `SHOW_AUTO_GAP_S` after `stage.busy_until`, up to the live act (never into the finale). The worker publishes
  `show.advanced` (`act.seq`) and the page moves app.py's cursor there (`/show/cue {cmd: "at"}`, plays nothing).
- A gesture's own stage sound is cued as kind `hit` and may be a not-offered file (the kiss ensemble's kiss).
- "Built with" card: `BUILT_n = tool: how` lines in show.md's config, shown on `/show` while the cursor is on act
  `BUILT_WITH_ACT` (3); `Script.built_with` → `/show/script` JSON. Add a sponsor's tool as the next `BUILT_n`.
- Per-character voices: `Storyteller.tts_node` records where each voice run starts (`voices.RunMarks`, keyed by the
  speech handle) and `transcription_node` pins gestures and mouths to them. A replayed act skips `tts_node`, so
  `show.py` synthesizes under a stand-in speech handle, caches the marks next to the WAV (`<key>.json`) and hands
  them to the replayed speech. The cache key includes the palette's voice ids and the cast.
- Founder points use `SPOT_*` chains in the config: the first gesture the stage knows wins (`point_back_left` and
  co., built Nov 7 night; a stage without them falls back to `look_at_other`/`nod`).

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
