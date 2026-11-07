# Storyteller voice agent

`agent/` is a LiveKit Agents (Python, `livekit-agents~=1.8`, locked at 1.8.11) voice agent, "Sparky". It asks the
listener what story they want, improvises it scene by scene, and cues the two arms as puppet actors through the
stage server ([gestures.md](gestures.md)). Run instructions: [agent/README.md](../agent/README.md).

## Files
| File | What |
|---|---|
| `agent/storyteller.py` | Entrypoint (`uv run storyteller.py console\|dev`). Model choices and timings at the top. |
| `agent/prompt.md` | The system prompt. `{{GESTURES}}` is replaced at startup with the stage's gesture list. |
| `agent/gestures.py` | Tag parser, gesture-name mapping, stage HTTP client, the speech-synced gesture hook. |
| `agent/director.py` | Freeze when the listener talks, idle when the stage is quiet, auto-continue to the next scene. |
| `agent/fake_stage.py` | Stdlib stand-in for `robot/stage.py`, same API, prints what the arms would do. |
| `agent/telephony/dispatch-rule.json` | SIP dispatch rule for a phone number (not applied yet). |

## Models (LiveKit Inference: one LiveKit API key, billed to the project's credits)
- STT `assemblyai/universal-3-6-pro`, turn detection `inference.TurnDetector()`, preemptive generation on.
- LLM `google/gemma-4-31b-it`: LiveKit's documented latency-optimised default for voice agents (Nov 2026).
- TTS `fishaudio/s2.1-pro`, voice `e3cd384158934cc9a01029cd7d278634` ("Laura", confident narrator), with
  `expressive=True`: LiveKit injects the TTS's markup guide into the prompt, so the LLM can add emotion and
  laughs inline; LiveKit strips that markup from transcripts.
- Noise cancellation: BVC, or BVCTelephony for SIP callers.
- Every choice can be overridden in `agent/.env` (`LLM_MODEL`, `TTS_MODEL`, `TTS_VOICE`, `STT_MODEL`, `EXPRESSIVE`).

## Tag protocol (LLM output)
- `[left:slash]`, `[right:cower]`, `[both:bow]`: arm is `left|right|both`, gesture is a stage gesture name.
  Placed right before the words where the action happens; at most one or two per sentence.
- `[next]` at the end of a scene means "carry on". Once it has been spoken in full, the director waits
  `NEXT_SCENE_AFTER_S` (0.8 s) and asks for the next scene; the listener can jump in during that gap.
  No `[next]` means the agent waits for the listener (a question, or the end of the story).
- Stories end with "The end!" and `[both:bow]`.
- Names are normalised (case, spaces, hyphens), then mapped: exact, then a synonym table (`attack`→`slash`,
  `cheer`→`celebrate`…), then fuzzy match (difflib, cutoff 0.6), then any word of a compound (`sword_slash`).
  Unknown gestures and unknown arms are dropped and logged. Any short `[...]` span is removed from speech.
- The gesture list comes from `GET {STAGE_URL}/gestures` at session start; if the stage is down, the built-in
  default list in `gestures.py` is used (same names as `fake_stage.py`).

## How gestures sync to speech
- `tts_text_transforms=[strip_tags_for_tts, ...]` removes tags from the text sent to the TTS.
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

## Interruptions and idle
- `user_state_changed` → `speaking`: `POST /freeze` at once (VAD start of speech, before STT), and any pending
  next scene is cancelled. The prompt tells the LLM to answer with one playful line and carry on with the change.
- Agent listening and nobody talking: `POST /idle` after `IDLE_AFTER_S` (1.5 s), or later if the last gesture
  (`busy_until`, from the gesture durations) is still playing, so the final bow finishes.
- Session start: `POST /stage`, then the greeting. Session end: `POST /rest`.

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
