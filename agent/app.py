"""Story Theatre: a hold-to-talk web page for the storyteller, for rooms too loud for an always-on mic.

    uv run app.py        # then open http://127.0.0.1:8790 (story mode) or http://127.0.0.1:8790/show (show mode)

Runs the storyteller worker in push-to-talk mode (PTT=1) as a subprocess, serves web/index.html, mints LiveKit
tokens that dispatch that worker into a fresh room, and reports whether the stage server answers. Ctrl-C (or
SIGTERM) stops the worker too. The story page's mode toggle can start a room in live chat instead (open mic,
?mode=live, see live.py). Starting it again while one is running replaces the old one, worker included.
It only ever talks to STAGE_URL; it starts nothing on the robot station.

Env (agent/.env, the environment wins): LIVEKIT_URL/API_KEY/API_SECRET, STAGE_URL, APP_PORT (8790),
PTT_AGENT_NAME (storyteller-ptt: its own name, so a plain `storyteller.py dev` worker can't take these rooms).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from livekit import api

from show import CUE_PORT, Cursor, Script

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")  # override=False: anything already in the environment wins

PORT = int(os.getenv("APP_PORT", "8790"))
STAGE_URL = os.getenv("STAGE_URL", "http://127.0.0.1:8770").rstrip("/")
AGENT_NAME = os.getenv("PTT_AGENT_NAME", "storyteller-ptt")
PAGE = HERE / "web" / "index.html"
PIDFILE = Path(tempfile.gettempdir()) / f"story-theatre-{PORT}.json"
BOOT = uuid.uuid4().hex[:8]  # the page reconnects when this changes
WORKER_CMD = [sys.executable, "storyteller.py", "dev", "--no-reload", "--log-level", "INFO"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s app %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("app")


def git_version() -> str:
    def git(*args: str) -> str:
        out = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    short = git("rev-parse", "--short", "HEAD") or "unknown"
    return short + ("+dirty" if git("status", "--porcelain") else "")


def page_hash() -> str:
    return hashlib.sha1(PAGE.read_bytes()).hexdigest()[:10]


# --- one app per port: a new launch replaces the old one --------------------------------------------------------

def _command(pid: int) -> str:
    out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return out.stdout.strip()


def _group_alive(pgid: int) -> bool:
    out = subprocess.run(["ps", "-A", "-o", "pgid=,command="], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        group, _, command = line.strip().partition(" ")
        if group == str(pgid) and ("storyteller.py" in command or "multiprocessing" in command):
            return True
    return False


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _wait_gone(check, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not check():
            return True
        time.sleep(0.2)
    return not check()


def replace_previous() -> None:
    if not PIDFILE.exists():
        return
    try:
        old = json.loads(PIDFILE.read_text())
    except (OSError, ValueError):
        return
    app_pid, worker_pgid = old.get("app"), old.get("worker")
    if isinstance(app_pid, int) and app_pid != os.getpid() and "app.py" in _command(app_pid):
        log.info("another Story Theatre is running on port %d (pid %d): stopping it", PORT, app_pid)
        os.kill(app_pid, signal.SIGTERM)
        if not _wait_gone(lambda: "app.py" in _command(app_pid), 15):
            sys.exit(f"the old app (pid {app_pid}) did not stop; stop it by hand")
    if isinstance(worker_pgid, int) and _group_alive(worker_pgid):  # left behind by an app that was killed hard
        log.info("stopping a leftover worker (process group %d)", worker_pgid)
        _killpg(worker_pgid, signal.SIGKILL)
        _wait_gone(lambda: _group_alive(worker_pgid), 5)


def write_pidfile(worker_pgid: int | None) -> None:
    PIDFILE.write_text(json.dumps({"app": os.getpid(), "worker": worker_pgid, "port": PORT}))


def remove_pidfile() -> None:
    try:
        if json.loads(PIDFILE.read_text()).get("app") == os.getpid():
            PIDFILE.unlink()
    except (OSError, ValueError):
        pass


# --- the worker -------------------------------------------------------------------------------------------------

class Worker:
    """The storyteller in push-to-talk mode, in its own process group so stopping it takes its job processes too."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.stopping = False
        self.restarts = 0

    def start(self) -> None:
        env = {**os.environ, "PTT": "1", "AGENT_NAME": AGENT_NAME, "STAGE_URL": STAGE_URL}
        self.proc = subprocess.Popen(WORKER_CMD, cwd=HERE, env=env, start_new_session=True)
        write_pidfile(self.proc.pid)
        log.info("worker started (pid %d, agent %r, stage %s)", self.proc.pid, AGENT_NAME, STAGE_URL)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def watch(self) -> None:
        """Restart the worker if it dies on its own."""
        while not self.stopping:
            await asyncio.sleep(1)
            if self.proc is not None and not self.running and not self.stopping:
                self.restarts += 1
                log.warning("worker exited with %s; restarting in 3 s", self.proc.returncode)
                await asyncio.sleep(3)
                if not self.stopping:
                    self.start()

    def stop(self) -> None:
        self.stopping = True
        if self.proc is None:
            return
        pgid = self.proc.pid
        if self.running:
            log.info("stopping the worker")
            _killpg(pgid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.info("worker still draining after 5 s: killing it")
        if _group_alive(pgid):  # job processes, or a worker that didn't drain in time
            _killpg(pgid, signal.SIGKILL)
        if self.proc.poll() is None:
            self.proc.wait(timeout=5)


# --- web ----------------------------------------------------------------------------------------------------------

def dispatch_metadata(mode: str | None) -> str:
    """The job metadata for a page's mode: show (show.is_show), live chat (live.is_live), or "" for push-to-talk."""
    return json.dumps({"mode": mode}) if mode in ("show", "live") else ""


class App:
    def __init__(self, worker: Worker) -> None:
        self.worker = worker
        self.version = git_version()
        self.rooms = 0
        self.room: str | None = None
        self.http: aiohttp.ClientSession | None = None
        self.stage_cache: tuple[float, dict] = (0.0, {})
        self.cursor = Cursor()  # show mode: the act the show is on; outlives rooms, STOP and page reloads

    async def index(self, _req: web.Request) -> web.Response:
        return web.Response(text=PAGE.read_text(), content_type="text/html", headers={"Cache-Control": "no-store"})

    async def token(self, req: web.Request) -> web.Response:
        """A fresh room per page join, with the PTT worker dispatched into it. The previous room is closed, so
        only one story drives the arms at a time. `?mode=show` (the /show page) starts the worker in show mode,
        `?mode=live` (the story page's toggle) in live chat (live.py); anything else is push-to-talk."""
        metadata = dispatch_metadata(req.query.get("mode"))
        self.rooms += 1
        old, self.room = self.room, f"story-{BOOT}-{self.rooms}"
        if old:
            asyncio.create_task(self.delete_room(old))
        token = (
            api.AccessToken()
            .with_identity(f"listener-{uuid.uuid4().hex[:6]}")
            .with_name("Listener")
            .with_grants(api.VideoGrants(room_join=True, room=self.room))
            .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=metadata)]))
            .to_jwt()
        )
        log.info("page joined room %s%s", self.room, f" ({req.query['mode']})" if metadata else "")
        return web.json_response({"url": os.environ["LIVEKIT_URL"], "token": token, "room": self.room},
                                 headers={"Cache-Control": "no-store"})

    async def show_script(self, _req: web.Request) -> web.Response:
        """show.md, parsed per request (an edit shows on the next poll), plus the act the show is on."""
        try:
            script = Script.load()
        except Exception as e:  # noqa: BLE001 - a broken script shows on the page
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response({**script.to_json(), "current": self.cursor.act},
                                 headers={"Cache-Control": "no-store"})

    async def show_cue(self, req: web.Request) -> web.Response:
        """A clicker cue from the /show page: {"cmd": "next"|"prev"|"jump", "act": n} -> the act to play now
        (null: past the last act, nothing to do). The page then asks the worker to play it (RPC show_play)."""
        body = await req.json()
        last = Script.load().last
        cmd = body.get("cmd")
        if cmd == "next":
            act = self.cursor.next(last)
        elif cmd == "prev":
            act = self.cursor.prev()
        elif cmd == "jump":
            act = self.cursor.jump(int(body.get("act", 0)), last)
        else:
            return web.json_response({"error": f"unknown cmd {cmd!r}"}, status=400)
        sent = act is not None and await self.cue_worker(act)
        log.info("show cue %s -> act %s%s", cmd, act, " (sent)" if sent else "")
        return web.json_response({"act": act, "current": self.cursor.act, "sent": sent})

    async def cue_worker(self, act: int) -> bool:
        """Hand the cue to the show session on localhost (show.ShowRunner.serve_cues). False: the page sends it
        as an RPC instead (no session yet, an old one on the port, or the page is rejoining after STOP)."""
        if not self.room:
            return False
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.0))
        try:
            async with self.http.post(f"http://127.0.0.1:{CUE_PORT}/play", json={"act": act, "room": self.room},
                                      timeout=aiohttp.ClientTimeout(total=0.4)) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - not listening (yet)
            return False

    async def delete_room(self, name: str) -> None:
        try:
            async with api.LiveKitAPI() as lk:
                await asyncio.wait_for(lk.room.delete_room(api.DeleteRoomRequest(room=name)), 5)
            log.info("closed room %s", name)
        except api.ServerError as e:
            if e.code != "not_found":  # not_found: everyone had left already, so LiveKit closed it
                log.warning("could not close room %s: %s", name, e)
        except Exception as e:  # noqa: BLE001 - best effort: an empty room times out on its own
            log.warning("could not close room %s: %s", name, e)

    async def stage_state(self) -> dict:
        at, cached = self.stage_cache
        if time.monotonic() - at < 1.0:
            return cached
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.0))
        try:
            async with self.http.get(f"{STAGE_URL}/state") as resp:
                state = {"ok": resp.status == 200, "state": await resp.json() if resp.status == 200 else None}
        except Exception as e:  # noqa: BLE001 - any failure means offline
            state = {"ok": False, "error": type(e).__name__}
        self.stage_cache = (time.monotonic(), state)
        return state

    async def status(self, _req: web.Request) -> web.Response:
        return web.json_response({
            "boot": BOOT, "version": self.version, "page": page_hash(), "stage_url": STAGE_URL,
            "worker": "running" if self.worker.running else "stopped", "stage": await self.stage_state(),
        }, headers={"Cache-Control": "no-store"})

    async def stop(self, _req: web.Request) -> web.Response:
        """The page's STOP button, like Ctrl-C in the console: freeze the arms now, end the story (its room), then
        fold the arms to rest. A frozen duet answers 409 while it first finishes on its own path, so retry."""
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.0))
        frozen = await self.stage_post("/freeze")
        if self.room:
            old, self.room = self.room, None
            await self.delete_room(old)
        asyncio.create_task(self.rest())
        log.info("STOP from the page (freeze %s)", "ok" if frozen else "failed")
        return web.json_response({"frozen": frozen})

    async def stage_post(self, path: str) -> bool:
        if self.http is None:
            return False
        try:
            async with self.http.post(f"{STAGE_URL}{path}", json={}) as resp:
                return resp.status == 200
        except Exception as e:  # noqa: BLE001 - stage offline
            log.warning("stage %s failed: %s", path, e)
            return False

    async def rest(self) -> None:
        for _ in range(15):  # ponytail: retry while a frozen duet finishes (up to ~30 s)
            await asyncio.sleep(2.0)
            if await self.stage_post("/rest"):
                return
        log.warning("arms did not take /rest after STOP")

    async def close(self) -> None:
        if self.http is not None:
            await self.http.close()
        if self.room:
            await self.delete_room(self.room)


async def main() -> None:
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.getenv(k)]
    if missing:
        sys.exit(f"missing {', '.join(missing)} (agent/.env, see README.md)")
    if not PAGE.exists():
        sys.exit(f"page not found: {PAGE}")
    replace_previous()

    worker = Worker()
    app = App(worker)
    web_app = web.Application()
    web_app.add_routes([web.get("/", app.index), web.get("/show", app.index), web.get("/token", app.token),
                        web.get("/status", app.status), web.post("/stop", app.stop),
                        web.get("/show/script", app.show_script), web.post("/show/cue", app.show_cue)])
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
    except OSError as e:
        await runner.cleanup()
        sys.exit(f"port {PORT} is taken by something else ({e}); set APP_PORT to use another")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)

    worker.start()
    watcher = asyncio.create_task(worker.watch())
    log.info("Story Theatre %s on http://127.0.0.1:%d (stage %s)", app.version, PORT, STAGE_URL)
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        watcher.cancel()
        await app.close()  # closing the room ends the session, which sends the arms to /rest
        await asyncio.sleep(1)
        worker.stop()
        await runner.cleanup()
        remove_pidfile()


if __name__ == "__main__":
    asyncio.run(main())
