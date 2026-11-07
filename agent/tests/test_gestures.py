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
    book = GestureBook(DEFAULT_GESTURES, {"cross_swords": 9.3})
    sink = GestureSink(book, lambda a, g, d: played.append((a, g)), lambda: continued.append(1),
                       next_in_chain=Collect(), on_face=faced.append)

    async def run():
        for c in ["Hi [face:each other] [left:wa", "ve] there [right:juggle] and [knight:bow] [face:sideways] ",
                  "[duet:cross swords] [duet:tango] [face:audience] [both:cheer] yay [next]"]:
            await sink.capture_text(c)
        sink.flush()
        await asyncio.sleep(0)
    asyncio.run(run())
    assert played == [("left", "wave"), ("duet", "cross_swords"), ("both", "celebrate")]
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
