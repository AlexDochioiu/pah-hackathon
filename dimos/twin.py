"""Dimensional (dimos) digital twin of the two YAM puppet arms.

dimos's Dual OpenYAM stack (ControlCoordinator + ManipulationModule + its Viser 3D view) runs on the laptop, and its
two arms mirror ours: StageConnection polls the stage server's read-only `GET /state` (~20 Hz) and publishes the
measured joints and grippers as the connection's joint state, where a real dimos deployment would read CAN.
The twin never commands the stage: the coordinator's position commands to StageConnection are dropped.

Model fixes against dimos's stock Dual OpenYAM URDF, so the twin matches our MuJoCo model (robot/show.py):
  - base spacing: ours (robot/calib.json, show.RIGHT_OFFSET) instead of dimos's 0.62 m ABC spacing;
  - fingers: dimos fixes them closed; here they slide again, driven by the stage's gripper (0 closed .. 1 open).

Optional (--mcp): StageSkills exposes play / duet / freeze / rest / gestures / duets as dimos skills over McpServer,
so `dimos mcp list-tools` and `dimos mcp call` drive a stage. They POST, so they only ever talk to --skills-stage
(default the kin stage on :8783) and refuse port 8770, the real arms' tunnel.

Run through ./run.sh (see README.md); it puts this folder on PYTHONPATH so dimos's worker processes can import it.
"""
from __future__ import annotations

import argparse
import collections
import http.server
import json
import math
import pathlib
import re
import statistics
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from pydantic import Field
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.control.connection.connection_module import ConnectionConfig, ConnectionModule, Values
from dimos.control.contract.keys import Interface
from dimos.control.coordinator import TaskConfig
from dimos.control.tasks.trajectory_task.trajectory_task import JOINT_TRAJECTORY_TASK_NAME
from dimos.core.coordination.blueprints import Blueprint, autoconnect
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.manipulation.manipulation_module import ManipulationModule, ManipulationModuleConfig
from dimos.manipulation.planning.spec.config import RobotModelConfig
from dimos.msgs.sensor_msgs.JointState import JointState
from dimos.robot.assets.model import RobotModel
from dimos.robot.manipulators._modeling import named_for_robot
from dimos.robot.manipulators.damiao.damiao_blueprint import DamiaoCoordinator
from dimos.robot.manipulators.dual_openyam.config import (
    DUAL_OPENYAM,
    dual_openyam_control_model,
    dual_openyam_model_config,
)
from dimos.robot.manipulators.dual_openyam.model import DUAL_OPENYAM_MODEL_PATH, DUAL_OPENYAM_PACKAGE_PATHS
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

SIDES = ("left", "right")
DEV_STAGE = "http://127.0.0.1:8783"  # robot/stage.py --backend kin --port 8783
REAL_PORT = 8770                     # the real arms' tunnel: the twin only ever GETs /state there
CALIB = pathlib.Path(__file__).resolve().parents[1] / "robot" / "calib.json"


def _right_base() -> tuple[float, float]:
    """The right base in the left arm's frame: -right_offset from robot/calib.json, as robot/show.py places it."""
    try:
        off = json.loads(CALIB.read_text())["right_offset"]
        return -float(off[0]), -float(off[1])
    except (OSError, ValueError, KeyError, IndexError):
        logger.warning(f"twin: cannot read {CALIB}; using the Nov 7 18:45 calibration")
        return 0.00564, -0.6018


# dimos's root sits halfway between the bases, as in its stock model; the bases keep our relative placement.
RIGHT_BASE = _right_base()
BASES = {"left": (-RIGHT_BASE[0] / 2, -RIGHT_BASE[1] / 2), "right": (RIGHT_BASE[0] / 2, RIGHT_BASE[1] / 2)}
FINGER_OPEN = 0.04695  # m, i2rt yam.urdf: each finger joint is 0 closed .. -0.04695 open
FINGERS = tuple(f"{side}_joint{j}" for side in SIDES for j in (7, 8))
VIZ_HZ = 30.0          # ManipulationModule pushes the Viser view at a fixed 10 Hz; the twin re-starts it faster
GHOST_PORT = 8796      # POST /ghost: a joint trajectory replayed, looping, as a translucent pair of arms
GHOST_HOLD_S = 0.5     # the looping ghost holds its last frame this long before starting over
GHOST_MAX_FRAMES = 20000


# ---------------------------------------------------------------- model

def twin_urdf() -> str:
    """dimos's Dual OpenYAM URDF with our base placement and sliding fingers (as in i2rt's single-arm yam.urdf)."""
    xml = pathlib.Path(str(DUAL_OPENYAM_MODEL_PATH)).read_text()
    for side, stock in (("left", "0 0.31 0"), ("right", "0 -0.31 0")):
        x, y = BASES[side]
        xml, n = re.subn(
            rf'(<joint name="{side}_arm_fixed_joint" type="fixed">\s*<parent[^>]*/>\s*<child[^>]*/>\s*)'
            rf'<origin xyz="{stock}" rpy="0 0 0" />',
            rf'\g<1><origin xyz="{x:.4f} {y:.4f} 0" rpy="0 0 0" />', xml)
        assert n == 1, f"{side} base joint not found: dimos's dual URDF changed"
        for j, axis in ((7, "0 1 0"), (8, "0 -1 0")):
            xml, n = re.subn(
                rf'<joint name="{side}_joint{j}" type="fixed">(\s*<origin[^>]*/>\s*<parent[^>]*/>\s*<child[^>]*/>)',
                rf'<joint name="{side}_joint{j}" type="prismatic">\g<1><axis xyz="{axis}" />'
                rf'<limit effort="1" velocity="1" lower="-{FINGER_OPEN}" upper="0" />', xml)
            assert n == 1, f"{side}_joint{j} not found: dimos's dual URDF changed"
    return xml


def twin_model_config() -> RobotModelConfig:
    """dimos's Dual OpenYAM planning config on the twin URDF, joints named under the "arm" robot."""
    stock = dual_openyam_model_config()
    model = RobotModel.from_urdf_string(twin_urdf(), package_paths=DUAL_OPENYAM_PACKAGE_PATHS)
    for side in SIDES:
        model = model.with_mimic_joint(f"{side}_joint8", f"{side}_joint7")
    return named_for_robot(
        stock.model_copy(update={"model": model.with_default_joint_acceleration_limit(1.0)}), "arm", fingers=FINGERS)


def finger_values(grips: dict[str, float]) -> dict[str, float]:
    """Stage gripper (0 closed .. 1 open) -> the twin's finger joints (m)."""
    return {f"arm/{side}_joint{j}": -min(max(g, 0.0), 1.0) * FINGER_OPEN for side, g in grips.items() for j in (7, 8)}


# ---------------------------------------------------------------- the stage as a dimos connection

def get_json(url: str, timeout: float = 1.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


class StageConnectionConfig(ConnectionConfig):
    source: str = "arm"
    model: RobotModel | None = Field(default_factory=lambda: DUAL_OPENYAM.model)
    joint_commands: tuple[Interface, ...] = (Interface.POSITION,)
    stage_url: str = DEV_STAGE
    poll_hz: float = Field(default=20.0, gt=0, le=100)


class StageConnection(ConnectionModule):
    """Dual OpenYAM connection whose joint state is the stage server's measured pose. Read-only: GET /state only."""

    config: StageConnectionConfig
    position_command: In[JointState]
    joint_state: Out[JointState]
    stage_raw: Out[JointState]  # the same reading, untouched, for the twin's latency probe and fingers

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stop_poll = threading.Event()
        self._poller: threading.Thread | None = None
        self._rtt: collections.deque[float] = collections.deque(maxlen=200)

    def connect(self) -> None:
        get_json(self.config.stage_url + "/state", timeout=3.0)  # fail early with a clear error if the stage is down
        logger.info(f"StageConnection: mirroring {self.config.stage_url}/state at {self.config.poll_hz:g} Hz (read-only)")

    @rpc
    def start(self) -> None:
        super().start()
        # Commands are accepted (the coordinator always sends some) and dropped in write(): the twin never moves the arms.
        self.register_disposable(Disposable(self.position_command.subscribe(lambda _m: None)))
        self._poller = threading.Thread(target=self._poll, name="StagePoll", daemon=True)
        self._poller.start()

    @rpc
    def stop(self) -> None:
        self._stop_poll.set()
        if self._poller is not None and self._poller.is_alive():
            self._poller.join(timeout=2.0)
        super().stop()

    def write(self, values: Values) -> None:
        """Never forwarded: the stage is read-only from the twin."""

    def halt(self) -> None:
        """Nothing to halt: the twin drives nothing."""

    def _poll(self) -> None:
        period, nxt, last_log, fails = 1.0 / self.config.poll_hz, time.monotonic(), time.monotonic(), 0
        names = [f"arm/{s}_joint{i}" for s in SIDES for i in range(1, 7)] + [f"arm/{s}_gripper" for s in SIDES]
        while not self._stop_poll.is_set():
            t0 = time.time()
            try:
                arms = get_json(self.config.stage_url + "/state")["arms"]
                t1 = time.time()
                self._rtt.append(t1 - t0)
                pos = [float(v) for s in SIDES for v in arms[s]["measured"]]
                pos += [min(max(float(arms[s].get("grip", 1.0)), 0.0), 1.0) for s in SIDES]
                n = len(pos)
                msg = dict(frame_id="dual_openyam_base", name=names, position=pos, velocity=[0.0] * n, effort=[0.0] * n)
                self.joint_state.publish(JointState(ts=t1, **msg))
                self.stage_raw.publish(JointState(ts=t1, **msg))
                fails = 0
            except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
                fails += 1
                if fails in (1, 20) or fails % 200 == 0:
                    logger.warning(f"StageConnection: GET {self.config.stage_url}/state failed ({fails}x): {e}")
            if time.monotonic() - last_log > 10 and self._rtt:
                last_log = time.monotonic()
                ms = sorted(1000 * r for r in self._rtt)
                logger.info(f"StageConnection: GET /state round trip p50 {ms[len(ms) // 2]:.1f} ms, "
                            f"p95 {ms[int(len(ms) * 0.95)]:.1f} ms")
            nxt += period
            self._stop_poll.wait(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 0.5:
                nxt = time.monotonic()


# ---------------------------------------------------------------- ghost: a trajectory replayed over the live mirror

class GhostError(ValueError):
    pass


def parse_ghost(body: Any) -> dict[str, Any]:
    """Validate a POST /ghost body (kb/dimos.md) -> {"side", "t" (N,), "q": {side: (N, 6 or 7)}, "loop", "label"}."""
    if not isinstance(body, dict):
        raise GhostError("body must be a JSON object")
    side = body.get("side", "both")
    if side not in ("left", "right", "both"):
        raise GhostError('side must be "left", "right" or "both"')
    sides = SIDES if side == "both" else (side,)
    try:
        t = np.asarray(body["t"], dtype=float).reshape(-1)
        q = {s: np.asarray(body["q"][s], dtype=float) for s in sides}
    except KeyError as e:
        raise GhostError(f"missing {e}") from None
    except (TypeError, ValueError) as e:
        raise GhostError(f"t and q must be numbers: {e}") from None
    if not 1 <= len(t) <= GHOST_MAX_FRAMES or not np.all(np.isfinite(t)) or np.any(np.diff(t) < 0):
        raise GhostError(f"t must be 1..{GHOST_MAX_FRAMES} finite, non-decreasing seconds")
    for s, rows in q.items():
        if rows.ndim != 2 or rows.shape[0] != len(t) or rows.shape[1] not in (6, 7) or not np.all(np.isfinite(rows)):
            raise GhostError(f"q.{s} must be {len(t)} rows (one per t) of 6 joints (rad) + optional gripper (0..1)")
    return {"side": side, "t": t - t[0], "q": q, "loop": bool(body.get("loop", True)), "label": str(body.get("label", ""))}


class _GhostHandler(http.server.BaseHTTPRequestHandler):
    twin: TwinManipulationModule

    def _send(self, code: int, obj: Any) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send(200, self.twin.ghost_status()) if self.path == "/ghost" else self._send(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path == "/ghost/clear":
            self.twin.set_ghost(None)
            return self._send(200, {"ok": True})
        if self.path != "/ghost":
            return self._send(404, {"ok": False, "error": "POST /ghost or /ghost/clear"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
            self.twin.set_ghost(parse_ghost(body))
            self._send(200, {"ok": True, **self.twin.ghost_status()})
        except (GhostError, ValueError) as e:
            self._send(400, {"ok": False, "error": str(e)})

    def log_message(self, *_a: Any) -> None:
        pass


# ---------------------------------------------------------------- the planner + Viser view, tuned for mirroring

class TwinManipulationModuleConfig(ManipulationModuleConfig):
    ghost_port: int = Field(default=GHOST_PORT, ge=0, le=65535)  # 0 = no ghost endpoint


class TwinManipulationModule(ManipulationModule):
    """ManipulationModule with the Viser view pushed at VIZ_HZ, the fingers drawn from the stage gripper, a
    latency probe (per pushed frame, how old the stage reading it shows is: poll -> Viser), and the ghost: a joint
    trajectory POSTed to 127.0.0.1:<ghost_port>/ghost, looped on dimos's translucent preview robot over the live arms."""

    config: TwinManipulationModuleConfig
    stage_raw: In[JointState]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fingers: dict[str, float] = finger_values({s: 1.0 for s in SIDES})
        self._live: list[float] = [0.0] * 12 + [1.0, 1.0]
        self._raw: collections.deque[tuple[float, tuple[float, ...]]] = collections.deque(maxlen=100)
        self._ages: collections.deque[float] = collections.deque(maxlen=600)
        self._last_log = time.monotonic()
        self._raw_lock = threading.Lock()
        self._ghost: dict[str, Any] | None = None
        self._ghost_stop = threading.Event()
        self._ghost_http: http.server.ThreadingHTTPServer | None = None
        self._viz: Any = None

    def set_ghost(self, ghost: dict[str, Any] | None) -> None:
        if ghost is not None:
            ghost["start"] = time.monotonic()
        self._ghost = ghost
        logger.info("Twin ghost cleared" if ghost is None else
                    f"Twin ghost: {ghost['side']}, {len(ghost['t'])} frames, {ghost['t'][-1]:.2f} s {ghost['label']}")

    def ghost_status(self) -> dict[str, Any]:
        g = self._ghost
        if g is None:
            return {"active": False}
        return {"active": True, "side": g["side"], "frames": len(g["t"]), "duration_s": round(float(g["t"][-1]), 3),
                "loop": g["loop"], "label": g["label"]}

    def _ghost_values(self, g: dict[str, Any]) -> tuple[list[str], list[float]]:
        """The ghost's joints now: its trajectory, linearly interpolated; an arm it doesn't carry follows the live one."""
        t = g["t"]
        el = time.monotonic() - g["start"]
        tau = el % (t[-1] + GHOST_HOLD_S) if g["loop"] else el
        tau = min(tau, t[-1])
        live = self._live
        names: list[str] = []
        values: list[float] = []
        for i, s in enumerate(SIDES):
            rows = g["q"].get(s)
            if rows is None:
                q, grip = live[6 * i:6 * i + 6], live[12 + i]
            else:
                q = [float(np.interp(tau, t, rows[:, j])) for j in range(6)]
                grip = float(np.interp(tau, t, rows[:, 6])) if rows.shape[1] == 7 else live[12 + i]
            names += [f"arm/{s}_joint{j}" for j in range(1, 7)]
            values += q
            fingers = finger_values({s: grip})
            names += list(fingers)
            values += list(fingers.values())
        return names, values

    def _ghost_loop(self) -> None:
        shown, asserted = False, 0.0
        while not self._ghost_stop.wait(1.0 / VIZ_HZ):
            scene = getattr(self._viz, "_scene", None)
            g = self._ghost
            if scene is None:
                continue
            try:
                with scene._scene_lock:
                    if g is None:
                        if shown:
                            scene._set_preview_visibility(False)
                            shown = False
                        continue
                    scene._set_preview_ghost_joints(*self._ghost_values(g))
                    if not shown or time.monotonic() - asserted > 1.0:  # dimos's own Preview hides it when done
                        scene._set_preview_visibility(True)
                        shown, asserted = True, time.monotonic()
            except Exception as e:  # never let a bad frame kill the loop
                logger.warning(f"Twin ghost frame failed: {e}")

    @rpc
    def stop(self) -> None:
        self._ghost_stop.set()
        if self._ghost_http is not None:
            self._ghost_http.shutdown()
            self._ghost_http.server_close()
        super().stop()

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.stage_raw.subscribe(self._on_raw)))
        wm = self._world_monitor
        viz = wm.visualization if wm is not None else None
        if viz is None:
            return
        self._viz = viz
        if self.config.ghost_port:
            handler = type("GhostHandler", (_GhostHandler,), {"twin": self})
            self._ghost_http = http.server.ThreadingHTTPServer(("127.0.0.1", self.config.ghost_port), handler)
            threading.Thread(target=self._ghost_http.serve_forever, name="TwinGhostHTTP", daemon=True).start()
            threading.Thread(target=self._ghost_loop, name="TwinGhost", daemon=True).start()
            logger.info(f"Twin ghost: POST http://127.0.0.1:{self.config.ghost_port}/ghost, /ghost/clear")
        update = viz.update_state

        def update_state(frame: Any) -> None:
            scene = getattr(viz, "_scene", None)
            if scene is not None and not getattr(scene, "_twin_fingers", False):
                cfg = scene.viser_joint_configuration

                def with_fingers(urdf: Any, names: Any, joints: Any) -> list[float]:
                    # live fingers first, so a caller's own finger values (the ghost's) win
                    return cfg(urdf, [*self._fingers, *names], [*self._fingers.values(), *joints])

                scene.viser_joint_configuration = with_fingers
                scene._twin_fingers = True
                server = getattr(viz, "_server", None)
                if server is not None:  # open on robot/show.py's "audience" camera, shifted into dimos's root frame
                    cam = server.initial_camera
                    cam.position = (1.05 + BASES["left"][0], 0.35 + BASES["left"][1], 0.72)
                    cam.look_at = (0.12 + BASES["left"][0], -0.301 + BASES["left"][1], 0.3)
                    cam.up = (0.0, 0.0, 1.0)
                    cam.fov = math.radians(50)
            update(frame)
            self._probe(frame.joint_state)

        viz.update_state = update_state
        wm.stop_visualization_thread()
        wm.start_visualization_thread(rate_hz=VIZ_HZ)

    def _on_raw(self, msg: JointState) -> None:
        pos = list(msg.position)
        with self._raw_lock:
            self._raw.append((float(msg.ts), tuple(round(v, 4) for v in pos[:12])))
        self._live = pos
        self._fingers = finger_values({"left": pos[12], "right": pos[13]})

    def _probe(self, state: JointState | None) -> None:
        if state is None or len(state.position) < 12:
            return
        shown, now = tuple(round(float(v), 4) for v in list(state.position)[:12]), time.time()
        with self._raw_lock:
            raw = list(self._raw)
        # Only while the arms move: then exactly one reading has these values, and its stamp is when it arrived.
        match = [ts for ts, p in raw if p == shown]
        if len(match) == 1:
            self._ages.append(now - match[0])
        if time.monotonic() - self._last_log > 10 and len(self._ages) > 10:
            self._last_log = time.monotonic()
            ms = sorted(1000 * a for a in self._ages)
            logger.info(f"Twin latency (stage reading -> Viser push), moving: p50 {statistics.median(ms):.0f} ms, "
                        f"p95 {ms[int(len(ms) * 0.95)]:.0f} ms, n={len(ms)}")
            self._ages.clear()


# ---------------------------------------------------------------- optional: stage skills over MCP

class StageSkillsConfig(ModuleConfig):
    stage_url: str = DEV_STAGE


class StageSkills(Module):
    """Drive a stage server's gestures as dimos skills. Never the real arms: port 8770 is refused."""

    config: StageSkillsConfig

    def _call(self, path: str, body: dict[str, Any] | None = None) -> str:
        url = self.config.stage_url
        if urllib.parse.urlparse(url).port == REAL_PORT:
            return f"refused: {url} is the real arms' stage; the twin's skills only drive a sim stage"
        req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            return f"stage refused ({e.code}): {e.read().decode()}"
        except OSError as e:
            return f"stage unreachable at {url}: {e}"

    @skill
    def gestures(self) -> str:
        """List the solo gestures the stage can play, with one line about each."""
        try:
            return "\n".join(f"{g['name']}: {g.get('about') or ''}" for g in json.loads(self._call("/gestures")))
        except (ValueError, TypeError):
            return self._call("/gestures")

    @skill
    def duets(self) -> str:
        """List the two-arm duets and ensembles the stage can play."""
        try:
            return "\n".join(f"{d['name']} ({d['kind']}): {d.get('about') or ''}" for d in json.loads(self._call("/duets")))
        except (ValueError, TypeError):
            return self._call("/duets")

    @skill
    def play(self, arm: str, gesture: str, speed: float = 1.0) -> str:
        """Play a solo gesture on an arm.

        Args:
            arm: "left", "right" or "both".
            gesture: A gesture name from the gestures skill, e.g. "wave" or "nod".
            speed: Playback speed, 1.0 = as authored.
        """
        return self._call("/play", {"arm": arm, "gesture": gesture, "speed": speed})

    @skill
    def duet(self, name: str, speed: float = 1.0) -> str:
        """Play a two-arm duet or ensemble (both arms must be free).

        Args:
            name: A duet name from the duets skill, e.g. "hug".
            speed: Playback speed, 1.0 = as authored.
        """
        return self._call("/duet", {"name": name, "speed": speed})

    @skill
    def freeze(self) -> str:
        """Stop both arms where they are, at once, and drop their queues."""
        return self._call("/freeze", {"arm": "both"})

    @skill
    def rest(self) -> str:
        """Fold both arms back to rest."""
        return self._call("/rest", {"arm": "both"})


# ---------------------------------------------------------------- blueprint + launcher

def _gripper_tasks() -> list[TaskConfig]:
    return [TaskConfig(name=f"{s}_arm_gripper", type="gripper", joint_names=[f"arm/{s}_gripper"], priority=20)
            for s in SIDES]


def twin_blueprint(stage_url: str = DEV_STAGE, poll_hz: float = 20.0, viser_port: int = 8095,
                   mcp: bool = False, skills_stage: str = DEV_STAGE, ghost_port: int = GHOST_PORT) -> Blueprint:
    """dimos's dual-openyam-planner-coordinator, with StageConnection in place of the CAN (or mock) connection."""
    model = twin_model_config()
    control = autoconnect(
        DamiaoCoordinator.blueprint(
            instance_name="ControlCoordinator",
            model=dual_openyam_control_model(robot="arm"),
            tasks=[TaskConfig(name=JOINT_TRAJECTORY_TASK_NAME, type="trajectory", joint_names=list(model.joint_names),
                              priority=20, params={"start_position_tolerance": 0.05}), *_gripper_tasks()],
            zero_stiffness_joints=[],
        ),
        StageConnection.blueprint(stage_url=stage_url, poll_hz=poll_hz),
    ).remappings([(StageConnection, "position_command", "arm_position_command"),
                  (DamiaoCoordinator, "arm_joint_state", "joint_state")])
    parts: list[Blueprint] = [
        TwinManipulationModule.blueprint(
            model=model, planning_timeout=10.0, visualization={"backend": "viser", "port": viser_port},
            ghost_port=ghost_port,
            gripper_tasks={"left_manipulator": "left_arm_gripper", "right_manipulator": "right_arm_gripper"}),
        control,
    ]
    if mcp:
        from dimos.agents.mcp.mcp_server import McpServer
        parts += [StageSkills.blueprint(stage_url=skills_stage), McpServer.blueprint()]
    return autoconnect(*parts)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default=DEV_STAGE, help=f"stage server to mirror, GET /state only (default {DEV_STAGE}; "
                    f"the real arms: http://127.0.0.1:{REAL_PORT})")
    ap.add_argument("--hz", type=float, default=20.0, help="GET /state rate (default 20)")
    ap.add_argument("--viser-port", type=int, default=8095)
    ap.add_argument("--mcp", action="store_true", help="also serve StageSkills over dimos McpServer (:9990)")
    ap.add_argument("--skills-stage", default=DEV_STAGE, help=f"stage the MCP skills POST to (default {DEV_STAGE}; "
                    f"port {REAL_PORT} is refused)")
    ap.add_argument("--ghost-port", type=int, default=GHOST_PORT, help=f"ghost endpoint, 0 = off (default {GHOST_PORT})")
    a = ap.parse_args(argv)
    from dimos.core.coordination.module_coordinator import ModuleCoordinator
    print(f"twin: mirroring {a.stage} -> Viser http://127.0.0.1:{a.viser_port}", flush=True)
    ModuleCoordinator.build(twin_blueprint(a.stage, a.hz, a.viser_port, a.mcp, a.skills_stage, a.ghost_port)).loop()


if __name__ == "__main__":
    main()
