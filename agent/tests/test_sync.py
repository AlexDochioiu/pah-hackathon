"""The story-fix round (Nov 7): gesture sync maths, code-side stage direction (present, fill), the sfx budget,
the tidy filter (laughter, long quotes), early voice-run marks, the two-character casting lock and the story clock."""
import asyncio

import pytest
from livekit import rtc
from livekit.agents import utils

import storyteller
from gestures import GestureBook, Timing, tags_first
from sounds import SoundBook, Soundboard
from storyteller import StoryClock
from test_sounds import FakePlayer
from voices import RunMarks, letter_times, tidy_speech, timestamp_runs, voiced_tts

BOOK = GestureBook({"jump": 2.0, "present": 2.5, "nod": 2.0, "look_at_other": 2.0})
TEXT = "the cat sat on the mat and then it ran [left:jump] far."


def timing(lead=True) -> Timing:
    t = Timing()
    t.lead, t.rtt_s, t.starts_in_s, t.arm_lag_s, t.audio_s, t.hit_default_s, t.rate = lead, 0, 0.5, 0, 0, 0.6, 2.0
    return t


async def place(text, book=BOOK, fill=None, t=None):
    async def src():
        for w in text.split(" "):
            yield w + " "
    return "".join([c async for c in tags_first(src(), book, fill, t or timing())])


def test_gesture_lead_maths():
    t = Timing()
    t.rtt_s, t.starts_in_s, t.arm_lag_s, t.audio_s, t.max_lead_s = 0.04, 0.3, 0.07, 0.25, 3.5
    # rtt/2 + starts_in + hit_at/speed + arm lag - playout
    assert t.gesture_lead(1.0, 2.0) == pytest.approx(0.02 + 0.3 + 0.5 + 0.07 - 0.25)
    t.hit_default_s = 0.6
    assert t.gesture_lead(None) == pytest.approx(0.02 + 0.3 + 0.6 + 0.07 - 0.25)  # no hits: the default
    assert t.gesture_lead(9.0) == 3.5 and t.gesture_lead(0.0, 1.0) >= 0  # capped, never negative
    t.audio_s = 2.0
    assert t.gesture_lead(0.0) == 0.0


def test_tag_moves_earlier_by_lead_times_rate():
    # lead 0.5 + 0.6 = 1.1 s at 2 syllables/s: aimed at "ran" (mid-word, 9.5), fired 2.2 syllables earlier,
    # snapped to the nearest word start ("then", 7); the mark stays where it was aimed, for the sync log
    out = asyncio.run(place(TEXT))
    assert out.split() == "the cat sat on the mat and [left:jump] then it [mark:left=jump]ran far.".split()
    # GESTURE_LEAD=0: the old behaviour, at the sentence's start, no mark
    assert asyncio.run(place(TEXT, t=timing(lead=False))).startswith("[left:jump] the cat")


def test_present_rises_before_a_casting_once():
    out = asyncio.run(place("Meet Gloria the dragon. [voice:left=deep] She roars. [voice:left=deep] Again.",
                            t=timing(lead=False)))
    assert out.count("[left:present]") == 1
    assert out.index("[left:present]") < out.index("[voice:left=deep]")
    # already written by the LLM: nothing added
    out = asyncio.run(place("[left:present] [voice:left=deep] Gloria the dragon rises.", t=timing(lead=False)))
    assert out.count("[left:present]") == 1
    # a stage without the gesture: nothing added
    assert "present" not in asyncio.run(place("[voice:left=deep] Gloria.", book=GestureBook({"nod": 2.0})))


def test_fill_only_for_motionless_sentences():
    out = asyncio.run(place("A long and quiet sentence with nothing moving. The cat nods slowly here. [left:nod] Hi.",
                            fill=lambda: True, t=timing(lead=False)))
    assert out.count("[fill:light]") == 1 and out.index("[fill:light]") < out.index("A long")
    assert "[fill" not in asyncio.run(place("A long and quiet sentence with nothing moving.", fill=lambda: False))


def test_sfx_budget_one_per_gap():
    async def run():
        board = Soundboard(SoundBook.load(), FakePlayer())
        board.min_gap_s = 12
        first = board.sfx("explosion")
        second = board.sfx("roar")  # inside the gap: dropped
        scripted = board.sfx("rimshot", budget=False)  # the show's scripted sounds are exempt
        board._last_sfx -= 13
        later = board.sfx("roar")
        return first, second, scripted, later
    assert asyncio.run(run()) == (True, False, True, True)


async def _tidy(*chunks):
    async def src():
        for c in chunks:
            yield c
    return "".join([c async for c in tidy_speech(src())])


def test_no_written_laughter():
    out = asyncio.run(_tidy('He said, [talk:right] "', "HA" * 100, '!" *laughs* Then (laughs) silence.'))
    assert "ha ha" in out.lower() and "HAHAHA" not in out.upper() and "laughs" not in out
    assert out.startswith("He said,")
    assert len(out) < 80, out


def test_character_lines_are_capped():
    words = " ".join(["word"] * 10)
    out = asyncio.run(_tidy(f'[talk:left] "{words}. {words}. {words}. {words}." The end.'))
    quote = out.split('"')[1]
    assert len(quote.split()) == 30 and quote.endswith(".")  # cut at the first sentence end past 25 words
    assert out.rstrip().endswith("The end.")
    out = asyncio.run(_tidy('[talk:left] "' + " ".join(["on"] * 50) + '" Done.'))
    assert len(out.split('"')[1].split()) <= 41  # no sentence end: the hard cap


def _frame(ms=10):
    return rtc.AudioFrame.create(24000, 1, 24000 * ms // 1000)


async def test_run_times_are_known_before_playback():
    """The comedy bug: times[k] used to be known only once run k-1 had played, so long runs timed out and the
    gestures burst at the end. Now a run's start is known as soon as the runs before it are synthesised."""
    def synth(n):
        async def run(text):
            "".join([t async for t in text])
            for _ in range(n):
                yield _frame()
        return run

    async def llm():
        yield 'Long narration here. [talk:right] "Squeak!" More narration. [talk:left] "Boo."'

    marks = RunMarks()
    stream = voiced_tts(llm(), synth(300), lambda v: synth(50), {"left": "deep", "right": "squeaky"}, marks)
    first = await stream.__anext__()  # playback has only just started
    assert first is not None
    await asyncio.sleep(0.2)
    assert marks.times[:4] == pytest.approx([0.0, 3.0, 3.5, 6.5]) and marks.total == pytest.approx(7.0)
    await stream.aclose()


async def test_two_characters_per_story_no_recast():
    async def llm():
        yield '[voice:left=deep] [voice:right=squeaky] A tale. [voice:left=old] [talk:left] "Hi."'

    def synth(text_ch):
        async def run():
            async for _ in text_ch:
                pass
            yield _frame()
        return run()

    cast, locked = {}, set()
    _ = [f async for f in voiced_tts(llm(), synth, lambda v: synth, cast, locked=locked)]
    assert cast == {"left": "deep", "right": "squeaky"} and locked == {"left", "right"}
    agent = storyteller.Storyteller(instructions="x")
    agent.cast_locked.update(locked)
    agent.new_story()  # a new story may cast again
    assert agent.cast_locked == set()


class _Session:
    def __init__(self):
        self.handlers = {}

    def on(self, ev, fn):
        self.handlers[ev] = fn


class _Agent:
    instructions, scripted = "BASE", False

    def __init__(self):
        self.updates, self.new_stories = [], 0

    def update_instructions(self, text):
        self.updates.append(text)

    def new_story(self):
        self.new_stories += 1


class _Director:
    continue_instructions = ""


class _Tag:
    def __init__(self, arm, name):
        self.arm, self.name = arm, name


class _Turn:
    def __init__(self, text):
        self.transcript, self.is_final = text, True


async def test_story_clock_logs_nudges_and_resets(monkeypatch, caplog):
    monkeypatch.setattr(storyteller, "STORY_WRAP_S", 0.05)
    monkeypatch.setattr(storyteller, "STORY_END_S", 0.1)
    session, agent, director = _Session(), _Agent(), _Director()
    clock = StoryClock(session, agent, director)
    clock.on_tag(_Tag("left", "nod"))
    assert clock.started is None  # a story starts with its casting (or music, or a present)
    clock.on_tag(_Tag("voice", "left=deep"))
    assert clock.started is not None
    await asyncio.sleep(0.07)
    assert "# Time" in agent.updates[-1] and "two minutes" in agent.updates[-1] and "climax" in director.continue_instructions
    await asyncio.sleep(0.06)
    assert "end it in this reply" in agent.updates[-1]
    with caplog.at_level("INFO", logger="storyteller"):
        clock.on_tag(_Tag("both", "bow"))
    assert "story length: 0:00" in caplog.text and clock.started is None and len(clock.lengths) == 1
    assert agent.updates[-1] == "BASE" and agent.new_stories == 1  # the note is gone; the next story may recast
    session.handlers["user_input_transcribed"](_Turn("can we have another story about a cat?"))
    assert agent.new_stories == 2
    # the show's live demo: the clock starts at the volunteer's ask; a show cue ends it
    clock.arm()
    session.handlers["user_input_transcribed"](_Turn("a pirate who is scared of water"))
    assert clock.started is not None
    agent.scripted = True
    clock.on_tag(_Tag("face", "audience"))
    assert clock.started is None and len(clock.lengths) == 2


def test_a_gestures_own_sound_plays_even_when_not_offered():
    async def run():
        board = Soundboard(SoundBook.load(), FakePlayer())
        offered = board.sfx("kiss")  # the LLM's [sfx:kiss]: not offered
        board.cue("hit", "kiss")  # the kiss ensemble's own hit sound: curated, plays
        return offered, [w for _, k, w in board.player.log if k == "play"]
    offered, plays = asyncio.run(run())
    assert offered is False and plays == ["sfx:kiss"]


def _speech(*bursts, rate=24000):
    """Frames of a tone in each (start, end) second span, silence elsewhere."""
    import numpy as np
    n = int(max(e for _, e in bursts) * rate) + rate // 2
    x = np.zeros(n, dtype=np.int16)
    for s, e in bursts:
        t = np.arange(int(s * rate), int(e * rate))
        x[t] = (8000 * np.sin(2 * np.pi * 220 * t / rate)).astype(np.int16)
    step = rate // 20
    return [rtc.AudioFrame(x[i:i + step].tobytes(), rate, 1, len(x[i:i + step])) for i in range(0, n, step)]


def test_letter_times_snap_clauses_to_pauses():
    # "Hello there." for 1 s, a 0.7 s pause, "General Kenobi." from 1.7 s: letter-linear would start "General" at 1.0 s
    times = letter_times("Hello there. [left:wave] General Kenobi.", _speech((0.1, 1.1), (1.8, 3.0)))
    assert times[0] == pytest.approx(0.1, abs=0.02)
    assert times[len("Hellothere")] == pytest.approx(1.8, abs=0.02)  # the tag's letters don't count
    assert times[-1] == pytest.approx(3.0, abs=0.02)


async def test_timed_words_put_tags_on_the_next_word():
    """A show act's cached audio times every word; a tag is a zero-length span at the next letter's time, so it
    takes no speaking time (the synchronizer paced by characters, tags included: up to 3.8 s off, Nov 8)."""
    marks = RunMarks()
    marks.starts, marks.times, marks.letters, marks.total, marks.closed = [0], [0.0], 12, 4.0, True
    marks.letter_times = [0.1 * i for i in range(12)] + [3.5]

    async def src():
        yield "Hi there. [left:wave] [mark:left=wave]"
        yield "Bye now."
    pieces = [p async for p in timestamp_runs(src(), lambda: marks)]
    assert "".join(pieces) == "Hi there. [left:wave] [mark:left=wave]Bye now."
    spans = [(str(p), p.start_time, p.end_time if utils.is_given(p.end_time) else None) for p in pieces]
    assert ("there. ", pytest.approx(0.2), None) in spans
    assert ("[left:wave] ", pytest.approx(0.7), pytest.approx(0.7)) in spans  # "Bye" starts at letter 7
    assert spans[-1] == ("now.", pytest.approx(1.0), 4.0)


def test_tag_leads_into_the_sentence_before():
    # a short sentence: the whole lead (1.1 s = 2.2 syllables) can't fit in it, so the tag moves into the one before
    out = asyncio.run(place("We walk and walk and walk. A dragon? [left:jump] Yes."))
    assert out.split() == "We walk and walk and [left:jump] walk. A [mark:left=jump]dragon? Yes.".split()
