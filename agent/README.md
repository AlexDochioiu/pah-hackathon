# Storyteller agent

A LiveKit voice agent that improvises a story from what the listener asks for, narrates it, and has the two arms
act it out through the stage server. How it works: [../kb/storyteller.md](../kb/storyteller.md).

Needs `uv` and the LiveKit CLI (`brew install livekit-cli`). Run everything from `agent/`.

## 1. LiveKit Cloud project (once)
1. Sign in at https://cloud.livekit.io, create a project, and redeem the hackathon code `physical-ai-hackathon`
   on the project's billing page (the credits cover LiveKit Inference: STT, LLM and TTS).
2. `lk cloud auth` (links the CLI to the project; pick it as the default).
3. `lk app env -w -d .env` fills `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` into `agent/.env`
   from `.env.example`. (Or copy `.env.example` to `.env` and paste the keys from Settings > API keys.)
   `.env` is gitignored; never commit it.

## 2. Talk to it in the terminal, with the fake stage
```sh
python3 fake_stage.py                 # terminal 1: prints PLAY / FREEZE / IDLE as the arms would move
uv run storyteller.py console         # terminal 2: mic + speaker in this terminal
```
`uv run storyteller.py console --text` types instead of talking (gestures then fire as text arrives, not synced to
speech). Pick the mode at launch: switching modes inside the console drops the gesture hook.

## 2b. Hold-to-talk page (Story Theatre), for loud rooms
```sh
uv run app.py                         # then open http://127.0.0.1:8790 in Chrome
```
Runs the agent worker in push-to-talk mode and serves a page with a big HOLD TO TALK button (or hold the space bar):
the agent only hears the mic while it is held. It shows the transcript (gesture tags stripped), the agent's state,
whether the stage at `STAGE_URL` answers, and the git hash in the footer. To restart after a code change, run
`uv run app.py` again: it replaces the running one (worker included) and open pages rejoin a fresh room on their
own. With the fake stage on another port: `python3 fake_stage.py --port 8771` and
`STAGE_URL=http://127.0.0.1:8771 uv run app.py` (the environment wins over `.env`). `APP_PORT` changes the port.
Details: [../kb/storyteller.md](../kb/storyteller.md#story-theatre-hold-to-talk-page-for-the-loud-venue).

## 3. Against the real stage
The stage server (`robot/stage.py`, see [../kb/gestures.md](../kb/gestures.md)) listens on the station's
`127.0.0.1:8770`. Either:
- tunnel it to this laptop: `ssh -N -L 8770:127.0.0.1:8770 nuck9`, then run step 2 without the fake stage; or
- run the agent on the station: copy `agent/` there (with `.env`), `uv sync`, `uv run storyteller.py console`
  (the station needs a mic and speaker for console mode; otherwise use `dev` mode below).

A human must be at the e-stop whenever the real arms can move.

## 4. From a browser or a phone (dev mode)
`uv run storyteller.py dev` registers the agent with the project as `storyteller` (explicit dispatch: it only
joins rooms it is sent to).
- Browser: create a room with the agent in it, `lk dispatch create --new-room --agent-name storyteller`, then
  join that room from any LiveKit client (e.g. https://agents-playground.livekit.io, if it asks for an agent name
  use `storyteller`).
- Phone (not set up yet; every LiveKit Cloud plan includes one free US number, the Build plan 50 free inbound
  minutes):
  ```sh
  lk number search --country-code US --area-code 415
  lk number purchase --numbers +1415XXXXXXX
  lk sip dispatch create telephony/dispatch-rule.json     # each caller gets a room with the storyteller in it
  lk number list                                          # the number's ID
  lk number update --id <PHONE_NUMBER_ID> --sip-dispatch-rule-id <DISPATCH_RULE_ID>
  ```
  Callers get telephony noise cancellation automatically. The agent process must be running (`dev`, or
  `lk agent create` to deploy it to LiveKit Cloud; a cloud deployment can't reach the stage on the station
  unless the stage is exposed, so for the demo run the agent on or next to the station).

## Tests (no credentials needed)
```sh
uv run --group dev pytest -q
```
Covers the tag parser, gesture name mapping, the stage client against `fake_stage.py`, and a full
`AgentSession` with LiveKit's scripted fakes: gestures fire when playout reaches them, the voice never gets a
tag, an interruption freezes the arms and cancels the gestures after the cut, and `[next]` starts the next scene.
Plus the push-to-talk RPCs (`tests/test_ptt.py`): hold freezes and listens, release commits, no idling while held.

## Tweaking
- The prompt is [prompt.md](prompt.md) (`{{GESTURES}}` is replaced with the stage's gesture list at startup).
- Models, voice and timings: top of [storyteller.py](storyteller.py), each overridable in `.env`.
