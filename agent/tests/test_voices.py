"""The voice splitter: narration vs the characters' quoted lines, streamed in arbitrary chunks."""
import asyncio

from livekit import rtc

from gestures import parse_tag
from livekit.agents.types import NOT_GIVEN

from voices import PALETTE, Cast, RunMarks, VoiceSplitter, resolve_voice, timestamp_runs, voiced_tts


def split(*chunks):
    s = VoiceSplitter()
    items = [i for c in chunks for i in s.feed(c)] + s.flush()
    runs = []  # merge what the chunking split, so tests compare whole runs
    for i in items:
        if runs and isinstance(i, tuple) and isinstance(runs[-1], tuple) and runs[-1][0] == i[0]:
            runs[-1] = (i[0], runs[-1][1] + i[1])
        else:
            runs.append(i)
    return runs


TEXT = 'The dragon leans in. [talk:right] "Is that a cookie?" [right:roar] The knight gasps. [talk:left] “Mine!” [next]'
RUNS = [(None, "The dragon leans in.  "), ("right", '"Is that a cookie?"'), (None, "  The knight gasps.  "),
        ("left", "“Mine!”"), (None, " ")]


def test_narration_and_two_characters():
    assert split(TEXT) == RUNS


def test_any_chunking_gives_the_same_runs():
    for size in (1, 2, 3, 5, 8):
        assert split(*(TEXT[i:i + size] for i in range(0, len(TEXT), size))) == RUNS, size


def test_quote_spanning_chunks():
    assert split('Sir Clank bows. [ta', 'lk:left] "I am', ' the bravest', '!" he says.') == [
        (None, "Sir Clank bows.  "), ("left", '"I am the bravest!"'), (None, " he says.")]


def test_quote_without_talk_tag_is_narration():
    assert split('A sign read "keep out". [left:wave]') == [(None, 'A sign read "keep out". ')]


def test_quotes_inside_expressive_markup_do_not_count():
    text = '[talk:left] "<expr type="emotion" label="angry">Go away!</expr>" Then silence.'
    assert split(text) == [(None, " "), ("left", '"<expr type="emotion" label="angry">Go away!</expr>"'),
                           (None, " Then silence.")]


def test_cast_tags():
    assert split("Gloria the dragon [right:roar] [voice:right=deep]!") == [
        (None, "Gloria the dragon  "), Cast("right", "deep"), (None, "!")]
    assert split("[voice:left = Squeaky Mouse]") == [Cast("left", "squeaky")]
    assert "robot" not in PALETTE and resolve_voice("robotic") == resolve_voice("alien") == "deep" and resolve_voice("dragon") == "deep"
    assert resolve_voice("banana") is None
    assert parse_tag("[voice:left=deep]").arm == "voice"  # a tag, so the transcript keeps it for the LLM


def frame(tag: str) -> rtc.AudioFrame:
    f = rtc.AudioFrame.create(24000, 1, 240)
    f.userdata["voice"] = tag
    return f


async def test_voiced_tts_streams_each_run_in_its_voice_in_order():
    def synth(name, delay):
        async def run(text):
            words = "".join([t async for t in text])
            await asyncio.sleep(delay)  # a slow first byte must not reorder the audio
            for _ in words.split():
                yield frame(name)
        return run

    async def llm():
        for chunk in ('Once a mouse met a dragon. [voice:left=robot] [talk:right] "Hel', 'lo there!" ',
                      '[talk:left] "Beep." The end.'):
            yield chunk
            await asyncio.sleep(0.01)

    cast = {"left": "deep", "right": "squeaky"}
    frames = [f async for f in voiced_tts(llm(), synth("narrator", 0.05), lambda v: synth(v, 0.0), cast)]
    assert [f.userdata["voice"] for f in frames] == ["narrator"] * 6 + ["squeaky"] * 2 + ["deep"] + ["narrator"] * 2
    assert cast["left"] == "deep"  # the robot voice is gone: its nearest


async def test_transcript_is_pinned_to_each_run_start():
    marks = RunMarks()
    marks.starts, marks.times, marks.letters, marks.total = [0, 14], [0.0, 2.5], 20, 4.0
    marks.close()  # everything known: nothing to wait for

    async def text():
        yield 'Once a mouse said, [talk:right] "Squeak!" [next]'

    out = [s async for s in timestamp_runs(text(), lambda: marks)]
    assert "".join(out) == 'Once a mouse said, [talk:right] "Squeak!" [next]'
    assert [(str(s), s.start_time, s.end_time) for s in out] == [
        ("Once a mouse said,", 0.0, NOT_GIVEN),
        (' [talk:right] "', 2.5, 2.5),  # the talk tag fires as the character's audio starts
        ('Squeak!" [next]', NOT_GIVEN, 4.0),
    ]
