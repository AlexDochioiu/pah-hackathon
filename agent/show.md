# Show script

The hackathon presentation: the stage hands cut in on a founder, Lars takes over the pitch, introduces the
founders, pitches the project, then hands over to the live story mode. Rehearsal how-to and timings:
[kb/show.md](../kb/show.md). Played by `show.py` (show mode, http://127.0.0.1:8790/show).

How to write it:
- Each `## Act N: title` is one clicker cue. Its text is spoken exactly as if the LLM had written it, all of it
  by the host (Lars). The arms never speak in the show: they react with gestures. No `[talk:…]` tags (the test
  fails on one) and no `[voice:…]` castings; show mode reads every line in the host voice anyway.
- Gesture, `[face:…]`, `[duet:…]`, `[ensemble:…]` and `[props:…]` tags go at the END of the sentence they act
  out; they are moved to its start, like the LLM's. `[sfx:…]` and `[music:…]` play exactly where they are written.
- Tags before an act's first word fire the instant the cue lands.
- `(human)`: a person speaks, nothing plays. `(live)`: after its text the live LLM story mode takes over, with
  "Live context" below added to its prompt.
- `{NAME}` is filled in from the config. A line that starts with `{?KEY}` is kept only when KEY is on;
  `{?!KEY}` only when it is off (both may stack).
- `<!-- comments -->` are never spoken; they show on the page as notes for the presenters.
- Lars is the host and narrator, never a character. No pronouns for Lars, or for the founders: names or "they".
- Budget (owner, Nov 7; about 5 minutes with the demo): acts 0-4 together at most 2:00 (Dream off), act 5 at most
  10 s, act 6 at most 20 s. tests/test_show.py checks the estimates; kb/show.md has the table.

## Config

```ini
SHOW_NAME = Stage Hands             # the project
HOST_NAME = Lars                    # the host: Lars, the green plush frog on the table (prompt.md uses it too)
FOUNDER_1 = Alex                    # named in act 3; no point (owner, Nov 8: the founders wave themselves)
FOUNDER_2 = Michael
FOUNDER_3 = Darius
# A gesture option chain: the first the stage has wins; options after "|" are fallbacks for an older stage.
LARS_POINT = right:point_at_lars | right:double_take        # "Down here.": an arm points at Lars on the table
# The finale's threat: the left arm creeps to the e-stop and hovers ~5 cm above it, then pulls back. Never a press.
PANIC_REACH = left:panic_reach | left:slow_turn
LARS_PICK = off                     # on: in act 2 the right arm lifts Lars from the dock (needs someone at the e-stop)
# The pick line gives ~7 s of talk while Lars goes up and ~6 s while Lars goes down, with no other right-arm tag:
# sized for the speed-1 bake (pick 4.7 s, place 4.8 s). Check it on the fake stage before turning it on.
LARS_PICK_UP = pick:lars | right:point_at_lars | right:double_take    # falls back while the stage lacks pick_lars
LARS_PUT_DOWN = place:lars | right:nod
# Off until it works on the real arms. on: act 2's pick is Dream-1's ([dream:pick_lars], robot/dream_run.py; it wins
# over LARS_PICK). About 90 s: its own lines and the patter fill it, so act 2 runs well over budget.
# A miss: the arm facepalms, a sad trombone, one DREAM_OOPS quip, then our own pick_lars. Needs someone at the e-stop.
DREAM_PICK = off
DREAM_ARGS = --raw --no-fallback --speed 0.5 --exec 16 --pick-s 90 --patter 8   # raw: Dream-1 unchecked, its own timing at half speed (like dreamcli's ~/dream-test.yaml); the arms stay ours
DREAM_OOPS = Ah, frog-nabbit. | Nobody saw that. | That was a practice grab.
# The "Built with" card: the /show page shows it during act BUILT_WITH_ACT. One line per tool,
# "tool: how we use it". Add a sponsor's tool as the next BUILT_n. List only what the code really uses.
BUILT_WITH_ACT = 4
BUILT_1 = LiveKit Agents + Inference: speech to text, the LLM and the voices, in a real-time room with hold-to-talk
BUILT_2 = Fish Audio (through LiveKit Inference): every voice you hear
BUILT_3 = i2rt YAM arms on the MakerMods station: the two stage hands
BUILT_4 = MuJoCo: every move checked in simulation before it runs for real
BUILT_5 = OpenCV: overhead-camera calibration, and the check that Lars is on the dock
BUILT_6 = CC0 sounds and music: Freesound, Kenney and OpenGameArt
BUILT_7 = Claude Code: we built it with it
BUILT_8 = Dimensional dimos: a digital twin of both arms in simulation
# Off until the Dream-1 pick works on the real arms; then drop the "#":
# BUILT_9 = Dream Labs Dream-1: the pick (say how once it runs)
```

## Act 0: Cold open (human)
<!-- A founder starts the pitch, nervously. Nothing plays; the page shows "ready". Cue act 1 while it trails off. -->
Hi everyone! So, we built... it's like... a robot? Two arms? And there's an AI, and it, um... tells stories?
It's kind of hard to expl—

## Act 1: The takeover
<!-- Cut in mid-sentence: the snap to the audience is instant and Lars starts at once. -->
[face:audience]
Stop. Stop, stop, stop. [both:shake_head]
Thirty hours awake, and the pitch is: it's like... a robot? [right:facepalm]
We'll take it from here. [left:nod]
Good evening, humans! I'm {HOST_NAME}. Down here. {LARS_POINT} Yes. The frog.

## Act 2: The stage hands and the wand
<!-- Its own cue, so the clicker can jump straight here. With DREAM_PICK on, Dream-1 fetches the green stick (about 90 s, Lars talks over it), then the show name. -->
[face:audience]
I host the show, I tell the stories, and these two are my stage hands. [both:present]
Stage hands. Because they're hands. That joke cost forty dollars in API credits. [right:tremble_excited] [left:eye_roll] [sfx:rimshot]
{?LARS_PICK}{?!DREAM_PICK} A little higher, please. {LARS_PICK_UP} Higher. Higher! Thank you. Hello, back row! I can see all your laptops from up here. Okay, down again, I get dizzy. {LARS_PUT_DOWN} Gently. Gently! Frogs bruise, you know. A tiny bit to the left. Perfect.
{?DREAM_PICK} Every host needs a magic wand. Mine is that green stick on the table. Tonight, one of my stage hands lets Dream-1 drive: a robot brain from Dream Labs that has never seen this table. Dream-1, fetch me my wand. Live, no rehearsal. [dream:pick_lars]
Please welcome... [sfx:tada] {SHOW_NAME}! [both:ta_da] [sfx:applause] [music:funny]

## Act 3: The founders
<!-- The founders stand behind the arms and wave when their name comes up. -->
[face:audience]
First, {FOUNDER_1}! Hasn't blinked since Friday. We checked: we have cameras. [right:double_take]
{FOUNDER_2}! Keeps telling these two to act natural. They are two arms, bolted to a table, with no legal rights. [left:slow_turn]
And {FOUNDER_3}! Calls them the kids. Kids working thirty hours unpaid: that's not parenting, that's a startup. [both:nod]
On day one, the table tipped over. With them on it. [both:cower] Nobody filed a report. [left:shake_head]
Give it up for {FOUNDER_1}, {FOUNDER_2} and {FOUNDER_3}! [ensemble:clap] [sfx:applause]

## Act 4: The pitch
<!-- About thirty seconds: what it does. The "Built with" card is on the page for this act. -->
[face:audience]
Here's how it works. You say any story out loud, I make it up on the spot, and these two act it out, live.
A dragon? [left:roar] [sfx:roar]
A knight? [right:slash] [sfx:sword_clash]
Space wars? [right:shoot] [sfx:laser] [left:nod]
Anyone can jump in, mid-story, and the story bends. Last night someone made the dragon vegan. [right:double_take]
It ate a salad. Menacingly. Then it raised a seed round. [left:eat]
It's all up on the screen: every move checked in simulation first, and a digital twin of both arms in Dimensional's simulator. [both:present]

## Act 5: The demo (live)
<!-- After this line the live story mode runs: a volunteer holds the button at the laptop and asks for a story. -->
[face:audience] [music:none]
Enough talk. Who wants a story? [left:point_at_audience]
Come up, hold the big yellow button, and tell me what happens. [right:tremble_excited]

## Act 6: The finale
<!-- Works from any state: it stops a story politely. Esc (STOP) afterwards folds the arms and ends the music. -->
[face:audience]
One small note for the judges. [right:nod]
These two worked thirty hours, unpaid. First prize would really help their union negotiations. [left:nod] [sfx:drum_roll]
[music:victory] We were {SHOW_NAME}: {HOST_NAME}, the stage hands, and {FOUNDER_1}, {FOUNDER_2} and {FOUNDER_3}!
Goodnight! [duet:tip_hats] [sfx:applause]

## Live context
You are on stage right now, at a hackathon demo, in front of judges and an audience. You, {HOST_NAME}, just ran
the opening of the show with your two stage hands; what you said is in the chat history above. Now a volunteer
comes to the laptop, holds the button and asks for a story. Don't greet them again and don't offer a comedy show:
go straight into their story. The story lasts about two minutes, counting the volunteer's ask: five or six short
beats with a climax, big gestures, a duet or an ensemble and a sound effect or two, then "The end!" and the bow.
You read every line yourself, the characters' too: the stage hands act, they never speak. If someone asks what
this is, answer in one line and offer a story. The three founders are {FOUNDER_1}, {FOUNDER_2} and {FOUNDER_3};
a quick friendly nod to them is fine. Never make fun of a volunteer.
