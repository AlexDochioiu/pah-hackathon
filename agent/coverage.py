"""Gesture coverage check: generate scenes with the real LLM and list physical actions left untagged. Read-only on the
stage (GET /gestures, /duets). Run: cd agent && uv run python coverage.py   (COV_STAGE=url to pick the stage)."""
import asyncio, json, os, re, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv; load_dotenv(".env")
from livekit.agents import inference, llm
from livekit.agents.utils import http_context
import storyteller as st
from gestures import GestureBook, DEFAULT_GESTURES

ASKS_OLD = ["a knight and a dragon who both want the same treasure", "two pirates fighting over the last cookie",
        "a snail who wants to win a race against a rabbit", "a chef whose soup comes alive", "two astronauts lost on the moon",
        "a detective cat chasing a sneaky mouse", "a wizard apprentice whose spell goes wrong", "a robot learning to dance",
        "a farmer and a stubborn donkey pulling a cart", "a superhero who is afraid of spiders",
        "two kids building a snowman that runs away", "a firefighter rescuing a kitten from a tree"]

ASKS = ["aliens versus predators", "Pokemon", "Star Wars", "Star Trek", "a zombie apocalypse", "pirates of the Caribbean",
        "Harry Potter at a wizard duel", "Godzilla against King Kong", "two ninjas", "a cowboy showdown", "Jurassic Park",
        "superheroes fighting a giant robot"]

async def ask(model, system, user):
    ctx = llm.ChatContext(); ctx.add_message(role="system", content=system); ctx.add_message(role="user", content=user)
    out = ""
    async with model.chat(chat_ctx=ctx) as s:
        async for ch in s:
            if ch.delta and ch.delta.content: out += ch.delta.content
    return out

async def one(model, instr, a):
    story = await ask(model, instr, f"Tell me a story about {a}.")
    tags = re.findall(r"\[[^\]]+\]", story)
    STATS.append((a, sum(t.startswith("[talk") for t in tags), sum("each_other" in t for t in tags),
                  sum(t.startswith("[duet") for t in tags), story.count('"') // 2))
    miss = await ask(model, "You analyse stories for a robot puppet show. Reply with JSON only.",
        "Below is narration with gesture tags in square brackets. List every PHYSICAL action a character does "
        "(push, jump, eat, throw, run, fall, hug, dig, ...) that has NO tag in the same sentence, or whose tag clearly "
        "doesn't match the action. Return a JSON list of short lowercase verb phrases, e.g. [\"push the cart\"].\n\n" + story)
    m = re.search(r"\[.*\]", miss, re.S)
    try: miss = json.loads(m.group(0)) if m else []
    except Exception: miss = []
    return len(tags), len(re.split(r"[.!?]+\s", story)), miss

STATS = []

async def main():
    async with http_context.open():
        model = inference.LLM(model=st.LLM_MODEL)
        from gestures import Stage
        stage = Stage(os.environ.get("COV_STAGE", "http://127.0.0.1:8770"))  # GET only: read the gesture list
        book = GestureBook(await stage.fetch_gestures() or DEFAULT_GESTURES, await stage.fetch_duets(), stage.about)
        await stage.aclose()
        instr = st.build_instructions(book)
        res = await asyncio.gather(*(one(model, instr, a) for a in ASKS))
    tags = sum(r[0] for r in res); sents = sum(r[1] for r in res)
    print(f"{len(ASKS)} scenes, {sents} sentences, {tags} tags")
    for a, talk, face, duet, quotes in STATS: print(f"  {a[:28]:28} talk={talk} quotes={quotes} face_each_other={face} duets={duet}")
    allm = [p for r in res for p in r[2]]
    print(len(allm), "untagged actions:"); print("; ".join(allm))
asyncio.run(main())
