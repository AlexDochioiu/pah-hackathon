import asyncio

from gestures import (DEFAULT_GESTURES, GestureBook, GestureSink, Tag, TagParser, hyphenate_without_tags,
                      strip_tags, strip_tags_for_tts)

BOOK = GestureBook(DEFAULT_GESTURES)


def feed_all(chunks):
    parser = TagParser()
    out = []
    for c in chunks:
        out += parser.feed(c)
    return out + parser.flush()


def test_parses_inline_tags():
    out = feed_all(["The knight [left:slash] swings! [both:celebrate][next]"])
    assert out == ["The knight ", Tag("left", "slash", "[left:slash]"), " swings! ",
                   Tag("both", "celebrate", "[both:celebrate]"), Tag(None, "next", "[next]")]


def test_tag_split_across_chunks():
    text = "Boom [ri" + "ght:co" + "wer] ok"
    for size in (1, 2, 3, 5):
        chunks = [text[i:i + size] for i in range(0, len(text), size)]
        pieces = feed_all(chunks)
        assert [p for p in pieces if isinstance(p, Tag)] == [Tag("right", "cower", "[right:cower]")]
        assert "".join(p for p in pieces if isinstance(p, str)) == "Boom  ok"


def test_sloppy_tags_normalised():
    (tag,) = [p for p in feed_all(["[ Left : Die Dramatically ]"]) if isinstance(p, Tag)]
    assert (tag.arm, tag.name) == ("left", "die_dramatically")


def test_unclosed_bracket_is_prose():
    long = "a [ " + "x" * 80 + " and more"
    assert strip_tags(long) == long


def test_strip_tags_for_tts_stream():
    async def run():
        async def src():
            for c in ["Once [le", "ft:wave] upon [both:", "bow] a time.", " [next]"]:
                yield c
        return "".join([c async for c in strip_tags_for_tts(src())])
    out = asyncio.run(run())
    assert "[" not in out and "]" not in out
    assert out.split() == ["Once", "upon", "a", "time."]


def test_resolve_names():
    assert BOOK.resolve("slash") == "slash"
    assert BOOK.resolve("attack") == "slash"
    assert BOOK.resolve("celebrat") == "celebrate"
    assert BOOK.resolve("big roar") == "roar"
    assert BOOK.resolve("sword_slash") == "slash"
    assert BOOK.resolve("juggle") is None


def test_resolve_against_fetched_list():
    book = GestureBook({"wave": 1.0, "spin": 2.0})
    assert book.resolve("attack") is None  # slash isn't on this stage
    assert book.resolve("twirl") is None
    assert book.resolve("spinning") == "spin"


def test_tags_take_no_speaking_time():
    assert hyphenate_without_tags("[left:slash]") == []
    assert hyphenate_without_tags("[left:slash]swings!") == hyphenate_without_tags("swings!")


def test_sink_fires_and_strips():
    played, continued, forwarded = [], [], []

    class Collect:
        async def capture_text(self, t):
            forwarded.append(t)

        def flush(self):
            pass

        def on_attached(self):
            pass

    faced = []
    book = GestureBook(DEFAULT_GESTURES, {"cross_swords": 9.3}, {"storm": 4.9})
    assert book.describe_duets() == "- cross_swords (9.3s)" and book.describe_duets(ensembles=True) == "- storm (4.9s)"
    sink = GestureSink(book, lambda a, g, d: played.append((a, g)), lambda: continued.append(1),
                       next_in_chain=Collect(), on_face=faced.append)

    async def run():
        for c in ["Hi [face:each other] [left:wa", "ve] there [right:juggle] and [knight:bow] [face:sideways] ",
                  "[duet:cross swords] [duet:tango] [ensemble:Storm] [props:left] [props:none] [props:hat] [face:audience] "
                  "[both:cheer] yay [next]"]:
            await sink.capture_text(c)
        sink.flush()
        await asyncio.sleep(0)
    asyncio.run(run())
    assert played == [("left", "wave"), ("duet", "cross_swords"), ("duet", "storm"), ("props", "left"),
                      ("props", "none"), ("both", "celebrate")]
    assert continued == [1]
    assert faced == ["each_other", "audience"]
    assert "[" not in "".join(forwarded)


def test_text_only_mode_still_fires():
    from types import SimpleNamespace

    from gestures import GestureSync
    out = SimpleNamespace(audio=None, transcription=None)
    GestureSync(BOOK, lambda *a: None, lambda: None).install(out)
    assert isinstance(out.transcription, GestureSink)


def test_tags_first_moves_tags_to_sentence_start():
    from gestures import tags_first

    async def run(chunks):
        async def src():
            for c in chunks:
                yield c
        return "".join([t async for t in tags_first(src())])

    text = "Hello there! [both:wave] He was terri" "fied of the dark! [left:shake_head] He hid [left:cower] under the bed. [next]"
    out = asyncio.run(run([text[i:i + 7] for i in range(0, len(text), 7)]))
    assert out == "[both:wave] Hello there! [left:shake_head] He was terrified of the dark! [left:cower] He hid under the bed. [next]", out


def test_talk_tags_stay_put_and_mouth_follows_the_quote():
    from gestures import tags_first

    async def first(text):
        async def src():
            for i in range(0, len(text), 5):
                yield text[i:i + 5]
        return "".join([t async for t in tags_first(src())])

    text = 'The dragon sighs. [talk:right] "I am so hungry!" [right:roar] The end.'
    assert asyncio.run(first(text)) == 'The dragon sighs. [right:roar] [talk:right] "I am so hungry!" The end.'

    talks, forwarded = [], []

    class Collect:
        async def capture_text(self, t):
            forwarded.append(t)

        def flush(self):
            pass

    sink = GestureSink(BOOK, lambda *a: None, lambda: None, next_in_chain=Collect(),
                       on_talk=lambda arm, on: talks.append((arm, on)))

    async def run():
        for c in ['Then [talk:left] “Who', ' goes there?” she asks. [left:talk] "Me', 'ow', '!" [talk:right] "Hi']:
            await sink.capture_text(c)
        sink.flush()  # the reply ends (or is interrupted) mid-quote: the mouth shuts
        await asyncio.sleep(0)
    asyncio.run(run())
    assert talks == [("left", True), ("left", False), ("left", True), ("left", False), ("right", True), ("right", False)]
    assert "[" not in "".join(forwarded)


def test_guest_pick_and_place_tags():
    played = []

    class Collect:
        async def capture_text(self, t):
            pass

        def flush(self):
            pass

        def on_attached(self):
            pass

    book = GestureBook({**DEFAULT_GESTURES, "pick_lars": 5.0, "place_lars": 5.2}, sides={"pick_lars": "right"})
    assert "pick_lars" not in book.describe() and book.resolve("pick_lars") != "pick_lars"
    sink = GestureSink(book, lambda a, g, d: played.append((a, g, d)), lambda: None, next_in_chain=Collect())

    async def run():
        await sink.capture_text("Here comes Lars [pick:Lars] hello [place:lars] [pick:bob] [right:wave]")
        sink.flush()
        await asyncio.sleep(0)
    asyncio.run(run())
    assert played == [("right", "pick_lars", 5.0), ("right", "place_lars", 5.2), ("right", "wave", 2.0)]


def test_toy_moves_fall_back_without_the_toy():
    from gestures import TOY_FALLBACK, without_toy

    played = []
    book = GestureBook({**DEFAULT_GESTURES, "drive": 5.3, "ufo_fly": 5.9, "hop": 4.7, "die_dramatically": 5.8}, {})
    assert set(TOY_FALLBACK.values()) <= set(DEFAULT_GESTURES) | {"hop", "grab", "stomp", "die_dramatically"}
    assert without_toy("drive", ["car"]) == "drive" and without_toy("drive", [None]) == "gallop"
    assert without_toy("ufo_fly", ["ufo", None]) == "hop" and without_toy("wave", [None]) == "wave"

    class Collect:
        async def capture_text(self, text):
            pass

        def flush(self):
            pass

        def on_attached(self):
            pass

    sink = GestureSink(book, lambda a, g, d: played.append((a, g)), lambda: None, next_in_chain=Collect())

    async def run():
        await sink.capture_text("[left:race] [props:left car] [left:race] [right:drive] [props:none] [left:fly] ")
        sink.flush()
    asyncio.run(run())
    assert played == [("left", "gallop"), ("props", "left_car"), ("left", "drive"), ("right", "gallop"),
                      ("props", "none"), ("left", "hop")], played


def test_sounds_land_on_the_gesture_hit():
    """A gesture's sound plays at its start + at / speed + SFX_SYNC_MS; an [sfx] in its sentence moves onto that
    hit instead of the default; one in a later sentence plays when spoken."""
    import time

    from gestures import Hit, sfx_lead
    assert sfx_lead("gunshot") < 0.05 < 0.3 < sfx_lead("sword_swing") < 0.5  # a swish peaks late: it starts early

    async def run():
        played = []
        h = Hit([1.6, 2.0], "stomp", played.append, sync_s=0.05)
        t0 = time.monotonic() + 0.3
        h.started(t0, speed=0.5)
        assert h.scheduled == [("stomp", t0 + 3.2 + 0.05), ("stomp", t0 + 4.0 + 0.05)]
        h.sfx("punch")  # explicit, same sentence: replaces the default, on the first hit
        assert h.scheduled == [("punch", t0 + 3.25)]
        quick = Hit([0.01], "gunshot", played.append, sync_s=0.0)
        quick.started(time.monotonic(), 1.0)
        await asyncio.sleep(0.05)
        assert played == ["gunshot"]
        late = Hit([0.0], None, played.append, sync_s=0.0)
        late.sfx("crash")  # before the stage answered: waits for the start
        assert played == ["gunshot"]
        late.started(None)  # refused: plays now rather than never
        assert played == ["gunshot", "crash"]

    asyncio.run(run())

    starts, sounds = [], []

    class Collect:
        async def capture_text(self, text):
            pass

        def flush(self):
            pass

    def play(arm, name, secs, on_start=None):
        starts.append((name, on_start))

    book = GestureBook({**DEFAULT_GESTURES, "shoot": 3.4}, sounds={"shoot": ("gunshot", [1.6])})
    sink = GestureSink(book, play, lambda: None, next_in_chain=Collect(), on_sound=lambda k, n: sounds.append(n))

    async def run_sink():
        await sink.capture_text("[left:shoot] Bang! ")
        await sink.capture_text("[sfx:laser] ")  # right after its sentence: still this gesture's
        await sink.capture_text("[left:wave] Bye. [sfx:applause] ")  # a later sentence: plays when spoken
        ((_, on_start), (_, none)) = starts
        assert none is None  # wave has no hits
        on_start(time.monotonic() - 10, 1.0)  # its hit is long gone: nothing more plays
        await asyncio.sleep(0)

    asyncio.run(run_sink())
    assert sounds == ["applause", "laser"], sounds
