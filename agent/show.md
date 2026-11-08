# Show script

The hackathon presentation: the arms cut in on a founder, take over the pitch as a double act, introduce the
founders, pitch the project, then hand over to the live story mode. Rehearsal how-to: [kb/show.md](../kb/show.md).
Played by `show.py` (show mode, http://127.0.0.1:8790/show).

How to write it:
- Each `## Act N: title` is one clicker cue. Its text is spoken exactly as if the LLM had written it: plain
  narration is the host (Lars), `[talk:left] "…"` is the LEFT arm (deep voice), `[talk:right] "…"` the RIGHT
  arm (squeaky voice).
- Gesture, `[face:…]`, `[duet:…]`, `[ensemble:…]` and `[props:…]` tags go at the END of the sentence they act
  out; they are moved to its start, like the LLM's. `[sfx:…]`, `[music:…]` and talk tags play exactly where
  they are written.
- Tags before an act's first word fire the instant the cue lands; if one of them is a sound, the voice starts
  `LEAD_PAUSE_S` later.
- `(human)`: a person speaks, nothing plays. `(live)`: after its text the live LLM story mode takes over, with
  "Live context" below added to its prompt.
- `{NAME}` is filled in from the config. A line that starts with `{?KEY}` is kept only when KEY is on.
- `<!-- comments -->` are never spoken; they show on the page as notes for the presenters.
- Lars is the host and narrator, never a character. No pronouns for Lars, or for the founders: names or "they".

## Config

```ini
SHOW_NAME = Stage Hands             # the project
HOST_NAME = Lars                    # the host: Lars, the green plush frog on the table (prompt.md uses it too)
FOUNDER_1 = Alex @ back_left        # name @ the spot they stand on
FOUNDER_2 = Michael @ back_centre
FOUNDER_3 = Darius @ back_right
# Each spot as the gesture that points at it. Options after "|" are fallbacks, used while the stage lacks the first.
SPOT_back_left = left:point_back_left | left:look_at_other
SPOT_back_centre = right:point_back_centre | right:nod
SPOT_back_right = right:point_back_right | right:look_at_other
LARS_POINT = right:point_at_lars | right:double_take        # "Down here.": an arm points at Lars on the table
LARS_PICK = off                     # on: in act 1 the right arm lifts Lars from the dock (needs someone at the e-stop)
# The pick line gives ~7 s of talk while Lars goes up and ~6 s while Lars goes down, with no other right-arm tag:
# sized for the speed-1 bake (pick 4.7 s, place 4.8 s). Check it on the fake stage before turning it on.
LARS_PICK_UP = pick:lars | right:point_at_lars | right:double_take    # falls back while the stage lacks pick_lars
LARS_PUT_DOWN = place:lars | right:nod
LEAD_PAUSE_S = 0.3                  # an act's opening sound (the record scratch) plays this long before the voice
```

## Act 0: Cold open (human)
<!-- A founder starts the pitch, nervously. Nothing plays; the page shows "ready". Cue act 1 while it trails off. -->
Hi everyone! So, we built... it's like... a robot? Two arms? And there's an AI, and it, um... tells stories?
It's kind of hard to expl—

## Act 1: The takeover
<!-- Cut in mid-sentence. The scratch and the snap to the audience are instant. -->
[sfx:record_scratch] [face:audience]
[talk:left] "Stop. Stop, stop, stop." [left:shake_head]
[talk:right] "Thirty hours awake, and they describe us as, like, a robot?" [right:facepalm]
[talk:left] "We'll take it from here." [left:flick]
Good evening, humans! I'm {HOST_NAME}. Down here. {LARS_POINT} Yes. The frog.
{?LARS_PICK} A little higher, please. {LARS_PICK_UP} Higher. Higher! Thank you. Hello, back row! I can see all your laptops from up here. Okay, down again, I get dizzy. {LARS_PUT_DOWN} Gently. Gently! Frogs bruise, you know. A tiny bit to the left. Perfect.
I host the show, I tell the stories, and these two are my stage hands. [both:bow]
[talk:right] "Stage hands! Because we're hands. On a stage!" [right:tremble_excited]
[talk:left] "Never explain the joke." [left:eye_roll] [sfx:rimshot]
Ladies and gentlemen, judges, mentors, and everyone who hasn't slept since Friday. [sfx:drum_roll]
The show you never knew you needed, built in one weekend, on zero sleep. [both:tremble_excited]
Please welcome... [sfx:tada] {SHOW_NAME}! [both:ta_da] [sfx:applause] [music:funny]

## Act 2: The founders
<!-- The founders stand behind the arms. Each name gets a point (a placeholder look or nod until the point gestures exist). -->
[face:audience]
[talk:left] "Now. The humans." [left:look_around]
Every show needs a crew, and ours is standing right behind us.
First: {FOUNDER_1}! {POINT_1}
[talk:right] "{FOUNDER_1} hasn't blinked since Friday. We checked. We have cameras." [right:double_take]
Next: {FOUNDER_2}! {POINT_2}
[talk:left] "{FOUNDER_2} keeps telling us to act natural. We are two arms. Bolted to a table." [left:slow_turn]
And {FOUNDER_3}! {POINT_3}
[talk:right] "{FOUNDER_3} calls us the kids, when they think we're not listening." [right:tremble_excited]
[talk:left] "We are always listening." [left:nod]
And on day one, the table tipped over. With us on it. [both:cower]
[talk:left] "We don't talk about the table." [sfx:laugh_track]
[talk:right] "We don't talk about the table!" [right:shake_head]
Give it up for {FOUNDER_1}, {FOUNDER_2} and {FOUNDER_3}! [ensemble:clap] [sfx:applause]

## Act 3: The pitch
<!-- About thirty seconds: what it does, what it's made of. -->
[face:audience]
Here's how it works. You say any story out loud, I make it up on the spot, and these two act it out, live.
A dragon? [sfx:roar] [talk:left] "ROOOAR!" [left:roar]
A knight? [sfx:sword_clash] [talk:right] "Have at thee!" [right:slash]
Space wars? [sfx:laser] [talk:right] "Pew! Pew!" [right:shoot]
Legally distinct space wars. [talk:left] "Very legally distinct." [left:nod]
[talk:right] "These two cameras are my eyes, and this is my mouth!" [right:tremble_excited]
[talk:left] "Mine is deeper." [left:beat_chest]
Our voices travel over LiveKit in real time, so anyone can jump in, mid-story, and the story bends.
[talk:right] "Last night someone made the dragon vegan." [right:double_take]
[talk:left] "It ate a salad. Menacingly." [left:eat]
And every single move is checked in simulation before it touches the real world.
[talk:right] "Because of the table." [right:cower]
[talk:left] "We don't talk about the table." [left:flick] [sfx:rimshot]

## Act 4: The demo (live)
<!-- After this line the live story mode runs: a volunteer holds the button at the laptop and asks for a story. -->
[face:audience] [music:none]
Enough talk. Who wants a story? [left:point_at_audience]
Come up to the laptop, hold the big yellow button, and tell me what happens.
[talk:right] "Anything!" [right:tremble_excited]
[talk:left] "Within reason." [left:nod]

## Act 5: The finale
<!-- Works from any state: it stops a story politely. Esc (STOP) afterwards folds the arms and ends the music. -->
[face:audience] [music:victory]
And that, sadly, is where we have to stop. The judges have a schedule, and the frog has a curfew.
We were {SHOW_NAME}! I'm {HOST_NAME}, these two were my stage hands, and back there: {FOUNDER_1}, {FOUNDER_2} and {FOUNDER_3}.
Tip your robots!
[talk:right] "Not the table!" [right:cower] [sfx:rimshot]
[talk:left] "Never the table." [left:shake_head]
Thank you, and goodnight! [duet:tip_hats] [ensemble:the_end] [sfx:applause]

## Live context
You are on stage right now, at a hackathon demo, in front of judges and an audience. You, {HOST_NAME}, just ran
the opening of the show with your two stage hands; what you said is in the chat history above. Now volunteers
come to the laptop, hold the button and ask for a story. Don't greet them again and don't offer a comedy show:
go straight into their story. Keep each story short: two or three scenes, about a minute in all, with big
gestures, a duet or an ensemble, and a couple of sound effects, ending with "The end!". If someone asks what this
is, answer in one line and offer a story. The three founders are {FOUNDER_1}, {FOUNDER_2} and {FOUNDER_3}; a
quick friendly nod to them is fine. Never make fun of a volunteer.
