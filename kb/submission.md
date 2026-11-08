# Submission: form text and run sheet

MakerMods Physical AI Hacks, Founders Inc SF, Creative track ("Build an application around Dream-1 and our Maker
Arm stations"). Due **Sun Nov 8, 15:00**. Presentation ≈ 5 minutes, live demo included. Team: Alex, Michael, Darius.
Repo overview: [README.md](../README.md). Show script: [agent/show.md](../agent/show.md), rehearsal: [show.md](show.md).

Rule for every line below: claim only what ran. The status table at the end says what ran where; update it (and
these texts) if the Dream-1 stage run or anything else gets verified before submitting.

## Form text

**Title:** Stage Hands

**One-liner:** Tell any story out loud and two robot arms act it out live, as puppets, while an AI improvises it;
interrupt anytime and the story bends.

### Description, 150 words
Stage Hands turns the MakerMods station into a puppet theatre. You speak a story idea; a LiveKit voice agent
improvises the story out loud while two i2rt YAM arms act it: each arm plays a character, its gripper is the
mouth, and every beat is one of 62 solo gestures or 35 duets and ensembles, timed so each move lands on its word.
Interrupt mid-story ("make the dragon vegan") and the arms freeze while the story bends. Lars, a green plush frog,
hosts the show in a Fish Audio voice.

Every move is simulated in MuJoCo before it runs: joint limits, speed, table clearance, the other arm, even the
table tipping. Dream Labs' Dream-1 picked Lars up zero-shot on the real arms; our stage now runs Dream-1's action
chunks through that same checker before they play, a path that passes in simulation and is next on the real arms.

### Description, 400 words
Stage Hands is a robot puppet theatre built on the MakerMods station. You say a story idea out loud, an AI
improvises the story live, and two i2rt YAM arms act it out as puppets. Each arm plays a character, its gripper
is the mouth that flaps when that character talks, and every beat of the story is a gesture: a roar, a sword
slash, a double take, a hug between the arms, a rocket launch both arms play together.

The voice side is LiveKit Agents with LiveKit Inference for speech to text, the LLM and text to speech. The LLM
writes narration with inline stage tags such as `[left:roar]` or `[duet:hug]`. Each tag is moved earlier by its
gesture's measured lead time (blend, first visible hit, arm lag, minus the listener's playout delay), so the hit
lands on its word. Tags are removed from speech, each character gets its own Fish Audio voice, and a tag fires
when playback reaches it. You can talk over the story anytime: hold-to-talk for a loud room, or open-mic live
chat with LiveKit's turn detector. The arms freeze, the narrator answers, and the story bends.

The library holds 62 solo gestures and 35 two-arm moves (13 duets, 22 ensembles), each with a one-line
description that goes into the LLM's prompt. Nothing reaches the arms unchecked: a MuJoCo checker built on i2rt's
meshes tests every gesture on both arms for joint limits, speed and acceleration, table clearance, distance to
the other arm across every pair of gestures, and whether the arm's centre of mass leans back far enough to tip
the light table (it did, on day one). The stage server re-checks each move from wherever the arm is and refuses
what fails.

Lars, a green plush frog, hosts a scripted show: the stage hands interrupt a nervous founder, Lars introduces the
team, pitches and hands a volunteer the button.

On Saturday night Dream-1, zero-shot through `dreamcli`, picked Lars up and lifted him on the real arms. We then
built Dream-1 into our stage: it is asked for a move from the live cameras and joints, each action chunk goes
through our checker at a careful speed, and a closing chunk locks the grip on Lars. Replayed through that path in
simulation, the real Dream-1 pick passes; the run on the real arms is next.

### How Dream-1 is used
- **Zero-shot on its own loop (ran, real arms):** Nov 7 night, after `dream.check` passed and the cameras were
  reseated (the left wrist camera had been on a USB 2 link), `dreamcli robot run` at half speed with "pick up the
  green frog" picked Lars, let go and picked him again; "pick up the green frog and hold it up" gripped and raised
  him, two reach, close, lift cycles. The person at the e-stop watched every run.
- **Inside our stage (built; passes in simulation; real arms pending):** `robot/dream_preview.py` asks Dream-1 for
  a move from the three cameras and the measured joints and never moves an arm. `robot/dream_run.py` skips idle
  chunks, picks the arm that moves, and POSTs each chunk to the stage's `/trajectory`, which retimes it to
  ≤ 0.3 rad/s, runs our checker (fingertips alone may brush the table to −3 mm; joints at most 0.05 rad past a safe
  limit are clamped onto it) and, on a closing chunk, holds Lars with a grip lock so later gestures keep him in
  hand. A refusal, a fault or a tracking error sends the arm back along its chunks and plays our own `pick_lars`.
  The real Dream-1 run, replayed through this path on the laptop simulator, passes and ends holding Lars.
- **In the show:** the `[dream:pick_lars]` tag runs that path with Lars narrating ("Let me ask Dream-1…"). It is
  not in the show script until the real-arm run passes.

### What's novel
- An LLM as puppet director: free-form narration with inline stage tags, each one moved earlier by its own
  gesture's measured lead so motion lands on the word, not after it.
- The gripper as a mouth, synced to the character voice speaking, so two arms read as two characters.
- Interruptible improv with physical actors: a cut-in freezes the arms and the story bends live.
- A foundation-model policy treated as a suggestion: Dream-1's actions pass through the same simulation checker
  as our hand-built gestures before they touch the real arms.

### Challenges
- **Sync of gestures to speech.** The first real story run hit 1.2–3.4 s late (the gesture's first hit, the
  stage's blend and sound queues). Fixed with per-gesture lead times, a page-measured playout delay, time stamps at
  every voice switch and a sync log per gesture.
- **The safety checker.** Swept-volume clearance between the arms for every gesture pair, table clearance for
  every link and any held prop, opt-in table contact for a fist bang or a dramatic death, and a centre-of-mass rule
  after leaning back tipped the light table. Full speed on both arms tripped the tracking freeze; about 75 % is
  the practical ceiling, the wrist pitch lags most.
- **Dream-1 bring-up.** Morning zero-shot block picks failed (closed on air, grabbed the wrong block, once drove
  into the table: e-stop). The calibration check (`dream.check`) and moving the left wrist camera off a USB 2 link
  made the evening's Lars pick work. Its grasps brush the table by a fraction of a millimetre in our model and
  its joints jitter just past their stops, so our checker needed a fingertip-only allowance and a small clamp.
- **Motor dropouts.** Each motor disables itself when it hears nothing for 400 ms ("loss communication"). On the
  stage's start, checking the library with the arms open froze the motor threads; the stage now checks first, then
  opens the arms. Two later `dreamcli` runs (a hand-over, a hat onto the other gripper) hit the same fault
  mid-run on both arms at once. The cause was not power or the CAN bus: the policy client froze its own process for
  ~0.7 s while handling a chunk. i2rt then re-enabled the limp arms at their old targets, so they jumped back. Our
  show path keeps Dream-1's client in a separate process from the arms, and the stage times its own checks
  ([arm-control.md](arm-control.md#motor-dropouts-nov-7)).
- **A loud venue.** Hold-to-talk for the room, open-mic live chat for quieter settings.

### What's next
- Run Dream-1 through our stage on the real arms, then put `[dream:pick_lars]` in the show.
- Report the dropout and the jumpy auto-recovery to Dream Labs and i2rt; try `dreamcli` on the joint head (no
  client IK).
- Fine-tune Dream-1 on our own scripted Lars picks (50+ demos in its dataset format) so it picks props from
  anywhere on the table, mid-story.
- Point gestures for the founders and the finale's reach toward the e-stop, which fall back to stand-ins today.
- Props in hand during stories (swords, hats, a steering wheel), and a phone line to call in a story.

## Run sheet (≈ 5 minutes)

Roles (proposed; confirm at the rehearsal):
- **Alex**: speaks the cold open, then holds the clicker (PageDown = next act) for every cue.
- **Michael**: at the station's e-stop the whole time, hand on it, eyes on the arms. Presses on anything unplanned.
- **Darius**: at the laptop: STOP (Esc) if needed, brings the volunteer up, shows them the big yellow button.
- **Volunteer**: from the audience, holds the button and asks for a story.

Before going on: stage server running with `--real` and its checks done; the tunnel and `uv run app.py` up;
`/show` open in Chrome, clicked once, a ✓ next to every act (primed); auto-advance ticked; act 0 shows "ready";
Lars on his dock; table clear; volume checked; Michael at the e-stop.

| Time | Act | Who does what |
|---|---|---|
| 0:00 | 0, cold open (~10 s) | Alex starts a nervous pitch. Mid-sentence, Alex clicks: act 1 cuts in. |
| 0:10 | 1, the takeover (~31 s) | Lars stops Alex; the arms rise and present. Auto-advance runs acts 2 and 3. |
| 0:45 | 2, the founders (~41 s) | Each founder is pointed at in turn (a look or nod stand-in); applause ensemble. |
| 1:30 | 3, the pitch (~36 s) | Dragon, knight, space wars; the "Built with" card on screen. |
| 2:10 | 4, "Who wants a story?" (~8 s) | Darius brings the volunteer to the laptop and hands them the button. |
| 2:30 | live story (~2:00, the ask included) | Volunteer holds, asks, releases. The volunteer or the room may cut in. |
| 4:30 | 5, the finale (~19 s) | After "The end!", Alex clicks to act 5 (manual cue, it stops a story politely). |
| 4:50 | end | Tip hats. Darius presses Esc: the arms fold to rest, music off. |

Acts 0–3 are about 1:57 of audio plus laughs; budget ~30 s for laughs and the walk-up. Auto-advance doesn't wait
for laughs: a click cuts the act playing and takes over, and the chain goes on from there.

### Fallbacks
| Failure | Sign | What we do |
|---|---|---|
| No network | The page can't reach LiveKit Cloud (no ✓, no voice) | Switch the laptop to a prepared phone hotspot and reload `/show` (the cached acts prime at once). If still down: Alex pitches in person, Darius plays gestures by hand from the stage API (`/play`, `/duet`; the stage runs on the station, not in the cloud). Untested; rehearse once. |
| Dream-1 idles or refuses | Dream-1's first chunks sit at rest, or the checker refuses a chunk | Only relevant if the Dream-1 beat is in the show. `dream_run.py` skips still chunks and keeps asking up to its limits, then plays our own `pick_lars`; Lars's lines cover the wait. Nobody acts. |
| Arm fault | An arm stops, a motor drops out ("loss communication"), or a big tracking error | A tracking error: the stage freezes both arms where they are and refuses further moves. A motor dropout: the stage stops, and the arm whose motors dropped goes limp and sags where it is (it can't fold: its motors are off). An arm still answering holds its last pose (in the dreamcli runs both arms dropped together). On the stage's i2rt this should not auto-recover and jump the way the dreamcli runs did (afternoon logs; recheck the station's version before the show). Michael presses the e-stop at once on a sag or anything unplanned. The show goes on voice-only (gestures are refused, the voice continues); Lars covers it. Recovery (release e-stop, station power reset, `home.py`, restart the stage) is after the presentation, not during it. |
| TTS slow | A long pause before Lars speaks | Scripted acts are cached, so this hits the live story: give it ~2 s. If the story stalls, the volunteer asks again; if it still stalls, Alex clicks to the finale, which works from any state. |
| Story goes off the rails | The LLM says something wrong or runs long | Alex clicks to the finale; Esc as a last resort (it stops everything). |
| Volunteer freezes | No ask | Darius suggests one: "a knight who's afraid of the dark". |

## What ran where (Nov 7 night)
| Piece | Status |
|---|---|
| Story agent, sync, voices, sounds, stories with the real arms | Ran: a full real-arm story run (Nov 7 ~20:15), sync fixes after it. |
| Gesture library on the real arms | Partly: bring-up at 50 % passed on the 13:00 library; 10 reworked gestures passed at 50 % on both arms. Table-contact moves, prop moves, hand ensembles (hug, clap) and deep facing: simulation only. |
| The show (`/show`, acts 0–5) | On the fake stage; not yet run end to end on the real arms. |
| Our own Lars pick (`pick_lars`) | The face-up bake picked and placed Lars on the real arms; the current face-down bake: sim only. `LARS_PICK = off` in the show. |
| Dream-1 zero-shot with `dreamcli` | Ran on the real arms: picked Lars and lifted him. |
| Dream-1 through our stage (`/trajectory`, checker, grip lock) | Passes on the laptop kinematic sim with the real run's chunks. **Real arms: pending.** |
| Dream-1 longer tasks with `dreamcli` (green pen, hand-over, hat) | Ran on the real arms. The pen pick ran its full length and gripped (the grip force cap engaged). The hand-over and the hat dropped motors mid-run when dreamcli's process froze (not power, not reach); i2rt's auto-recovery jumped the arms ([arm-control.md](arm-control.md#motor-dropouts-nov-7)). |
| Dimensional dimos twin | Tried, parked; not in the show path. |
