"""High-level control of the NUCK-9 station's two i2rt YAM arms.

One object owns both arms, the station lock and the IK, and turns a move into a
single call instead of a 100 Hz command loop:

    from arm import Arms

    with Arms.open() as arms:
        arms.stage()                                  # both arms to the stage pose
        arms.move_to("left", [0.30, -0.10, 0.20])     # Cartesian, keeps orientation
        arms.grasp("left")                            # close, report whether it caught
        arms.move_linear("left", [0.30, 0.10, 0.20])  # straight line, not a joint arc
        arms.play("slash", side="right")              # a gesture from robot/gestures/
        arms.rest()                                   # fold back before releasing

Every method that moves an arm returns `self`, so calls chain:

    arms.stage().move_to("left", [0.3, 0, 0.2]).grasp("left").rest()

Units are the recordings': joints in radians (six per arm, base to wrist), the
gripper 0 (closed) to 1 (open), positions in metres in that arm's base frame.

Safety, all enforced here:
- Joint targets are checked against the arm's model limits before anything moves.
- Moves are smoothstep-interpolated at a capped joint speed, then held until the
  arm settles; a move that cannot settle raises instead of silently lagging.
- The arm that is not being moved is always sent its current pose, never left
  uncommanded.
- `close()` folds both arms back to the pose they were opened in before releasing
  the motors: i2rt zeroes torque on close, so ending anywhere else drops them.
- The station lock is taken on open, so a `dreamcli` run or another script cannot
  drive the same arms at the same time.

Run on the station:
    cd ~/physical-ai-hacks && .venv/bin/python ~/dial/arm.py --demo
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np

SIDES = ("left", "right")
N_JOINTS = 6
CONTROL_HZ = 100.0
GESTURE_HZ = 50.0

DEFAULT_SPEED = 0.4  # rad/s; the repo homes at 0.3 and picks at 0.6
DEFAULT_TOLERANCE = 0.05  # rad, "arrived": above the ~1.5 deg gravity sag on joints 4-6
FAIL_TOLERANCE = 0.15  # rad; a move that ends further off than this was blocked, not sagging
DEFAULT_TIMEOUT = 12.0  # s, give up waiting for a move to settle
GRASP_CLOSED = 0.05  # gripper below this closed on air, not on a prop
GRIP_TOLERANCE = 0.05  # gripper counts as arrived within this of its target
LIMIT_SLACK = 0.01  # rad of slack on the joint limits; the rest pose sits on the j2/j3 stops

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


class ArmError(RuntimeError):
    """A move was refused or did not complete; the message says why."""


@dataclasses.dataclass(frozen=True)
class ArmState:
    """One reading of one arm."""

    joints: np.ndarray  # (6,) rad
    gripper: float  # 0 closed .. 1 open
    velocity: np.ndarray  # (6,) rad/s
    torque: np.ndarray  # (6,) N m
    pose: np.ndarray  # (4, 4) grasp site in the arm's base frame

    def __str__(self) -> str:
        deg = " ".join(f"{np.degrees(v):+7.2f}" for v in self.joints)
        return f"joints [{deg}] deg  gripper {self.gripper:.3f}"


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _default_home() -> dict[str, np.ndarray]:
    """The station's recorded home pose, the same one `robot/home.py` drives to.

    Not the pose at open time: if a previous run released the motors mid-move the
    arms are lying wherever gravity left them, and that is not a safe place to
    fold back to.
    """
    import show

    return {s: show.HOME[s] for s in SIDES}


def _joint_limits() -> np.ndarray:
    """The arm's joint limits, `(6, 2)` radians.

    The arm spec intersected with i2rt's model, the same values `show.py` checks
    gestures against: the model allows j1 to 3.14 and j4 to -1.69, the arm does not.
    """
    from i2rt.robots.utils import ArmType

    found = {}
    for joint in ET.parse(ArmType.YAM.get_xml_path()).getroot().iter("joint"):
        name, span = joint.get("name", ""), joint.get("range")
        if name.startswith("joint") and span:
            found[name] = [float(v) for v in span.split()]
    model = np.array([found[f"joint{i}"] for i in range(1, N_JOINTS + 1)])
    spec = np.array(
        [
            [-2.62, 3.05],
            [0.0, 3.65],
            [0.0, 3.14],
            [-1.57, 1.57],
            [-1.57, 1.57],
            [-2.09, 2.09],
        ]
    )
    return np.column_stack([np.maximum(model[:, 0], spec[:, 0]), np.minimum(model[:, 1], spec[:, 1])])


class Arms:
    """Both YAM arms of a station, with high-level moves.

    Open with `Arms.open()` (or `with Arms.open() as arms:`); it takes the station
    lock and energizes the arms. `close()` returns them to the pose they were
    opened in and releases the motors.
    """

    def __init__(self, station, kit_arms, lock, home=None) -> None:
        self._station = station
        self._arms = kit_arms
        self._lock = lock
        self._robots = kit_arms._robots
        self._energized = bool(kit_arms.energized)
        self._closed = False
        self._limits = _joint_limits()
        self._ik = {}
        self._home = {s: np.asarray(v, float).copy() for s, v in (home or _default_home()).items()}
        self._home_grips = {s: 1.0 for s in SIDES}
        self._ck = None  # show.Checker, built on first use (~0.2 s)
        self._lib, self._rejected = None, {}

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def open(
        cls,
        *,
        energize: bool = True,
        lock: bool = True,
        config_path: str = "configs/station.yaml",
        home=None,
    ) -> "Arms":
        """Opens both arms and takes the station lock.

        @param energize Hold and drive the arms; False floats them in gravity
            compensation, which is only useful for reading or hand-guiding.
        @param lock Take the station lock so nothing else drives the arms.
        @param config_path The station config, relative to the working directory.
        @param home The pose `rest()` returns to; None for the pose at open time.
        """
        from dream_hackathon import arms as kit_arms
        from dream_hackathon import config, station_lock

        station = config.load(config_path)
        handle = station_lock.acquire("arm-class") if lock else None
        try:
            opened = kit_arms.open_arms(station, energize=energize)
        except BaseException:
            if handle is not None:
                handle.close()
            raise
        return cls(station, opened, handle, home)

    def close(self, *, return_home: bool = True) -> None:
        """Folds the arms back to their opening pose, then releases the motors."""
        if self._closed:
            return
        try:
            if return_home and self._energized:
                try:
                    self.rest()
                except Exception as error:  # noqa: BLE001 - never skip the release
                    print(f"could not return home: {error}", file=sys.stderr)
        finally:
            for robot in self._robots.values():
                robot.close()
            if self._lock is not None:
                self._lock.close()
            self._closed = True

    def __enter__(self) -> "Arms":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------------- state

    def read(self) -> dict[str, ArmState]:
        """The current state of both arms."""
        out = {}
        for side in SIDES:
            obs = self._robots[side].get_observations()
            joints = np.asarray(obs["joint_pos"], float)[:N_JOINTS]
            out[side] = ArmState(
                joints=joints,
                gripper=float(np.asarray(obs["gripper_pos"]).reshape(-1)[0]),
                velocity=np.asarray(obs.get("joint_vel", np.zeros(N_JOINTS)), float)[:N_JOINTS],
                torque=np.asarray(obs.get("joint_eff", np.zeros(N_JOINTS)), float)[:N_JOINTS],
                pose=self._fk(side, joints),
            )
        return out

    def joints(self, side: str) -> np.ndarray:
        """One arm's six joints, radians."""
        return self.read()[side].joints

    def gripper(self, side: str) -> float:
        """One arm's gripper, 0 closed to 1 open."""
        return self.read()[side].gripper

    def pose(self, side: str) -> np.ndarray:
        """One arm's grasp-site pose, `(4, 4)` in its base frame."""
        return self.read()[side].pose

    def at_rest(self, side: str = "both", tolerance: float = 0.25) -> bool:
        """Whether the arm is within `tolerance` rad of the pose it was opened in."""
        state = self.read()
        return all(
            float(np.max(np.abs(state[s].joints - self._home[s]))) <= tolerance
            for s in self._sides(side)
        )

    def limits(self) -> np.ndarray:
        """The arm's joint limits, `(6, 2)` radians."""
        return self._limits.copy()

    # ---------------------------------------------------------------- joint space

    def move_joints(
        self,
        targets,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
        tolerance: float = DEFAULT_TOLERANCE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "Arms":
        """Moves to joint targets.

        @param targets `{side: 6 joints}`, or one array for `side` when given with
            `side=`. Sides left out keep their current pose.
        @param speed No joint moves faster than this, rad/s.
        @param wait Hold the target until the arms settle, and raise if they do not.
        """
        return self._move_joints(self._targets(targets), speed, wait, tolerance, timeout)

    def move_delta(
        self,
        deltas,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
        tolerance: float = DEFAULT_TOLERANCE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "Arms":
        """Moves by joint deltas from the current pose, e.g. `{"left": [0, 0.2, 0, 0, 0, 0]}`."""
        current = self.read()
        targets = {}
        for side, delta in self._targets(deltas).items():
            targets[side] = current[side].joints + np.asarray(delta, float)
        return self._move_joints(targets, speed, wait, tolerance, timeout)

    def move_joint(
        self,
        side: str,
        index: int,
        value: float,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
    ) -> "Arms":
        """Moves one joint (0-based) of one arm, leaving the rest where they are."""
        if not 0 <= index < N_JOINTS:
            raise ArmError(f"joint index {index} is outside 0..{N_JOINTS - 1}")
        target = self.joints(side).copy()
        target[index] = value
        return self._move_joints({side: target}, speed, wait)

    # ------------------------------------------------------------------ Cartesian

    def move_to(
        self,
        targets,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
        tolerance: float = DEFAULT_TOLERANCE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "Arms":
        """Moves the grasp site to a position, keeping the current orientation.

        @param targets `{side: [x, y, z]}` or `{side: 4x4 pose}`, in that arm's
            base frame, metres. Sides left out keep their current pose.
        """
        return self._move_cartesian(targets, speed, wait, tolerance, timeout, linear=False)

    def move_linear(
        self,
        targets,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
        tolerance: float = DEFAULT_TOLERANCE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "Arms":
        """Moves the grasp site in a straight line, not the arc joint interpolation gives.

        The path is solved in simulation first; a target the arm cannot reach
        raises before any motor moves.
        """
        return self._move_cartesian(targets, speed, wait, tolerance, timeout, linear=True)

    def move_xyz(
        self,
        side: str,
        x: float,
        y: float,
        z: float,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
    ) -> "Arms":
        """`move_to` for one arm, with the position as three arguments."""
        return self.move_to({side: [x, y, z]}, speed=speed, wait=wait)

    def approach(
        self,
        side: str,
        xyz,
        *,
        height: float = 0.10,
        speed: float = DEFAULT_SPEED,
    ) -> "Arms":
        """Hovers `height` metres above a point, ready to descend onto it."""
        x, y, z = (float(v) for v in xyz)
        return self.move_to({side: [x, y, z + height]}, speed=speed)

    # -------------------------------------------------------------------- gripper

    def set_gripper(
        self,
        values,
        *,
        speed: float = DEFAULT_SPEED,
        wait: bool = True,
    ) -> "Arms":
        """Sets a gripper, 0 closed to 1 open, holding the arm where it is."""
        targets = self._targets(values)
        current = self.read()
        grips = {s: current[s].gripper for s in SIDES}
        grips.update({s: float(np.clip(v, 0.0, 1.0)) for s, v in targets.items()})
        joints = {s: current[s].joints for s in SIDES}
        return self._execute(joints, grips, speed, wait, DEFAULT_TOLERANCE, DEFAULT_TIMEOUT)

    def open_gripper(self, side: str = "both", *, speed: float = DEFAULT_SPEED) -> "Arms":
        """Opens the gripper fully."""
        return self.set_gripper({s: 1.0 for s in self._sides(side)}, speed=speed)

    def close_gripper(self, side: str = "both", *, speed: float = DEFAULT_SPEED) -> "Arms":
        """Closes the gripper fully; the force limiter stops it on contact."""
        return self.set_gripper({s: 0.0 for s in self._sides(side)}, speed=speed)

    def grasp(self, side: str = "both", *, speed: float = DEFAULT_SPEED) -> dict[str, bool]:
        """Closes the gripper and reports, per side, whether it caught something.

        A gripper that closes all the way shut on air reads near 0; one that stops
        on a prop reads higher.
        """
        sides = self._sides(side)
        self.close_gripper(side, speed=speed)
        state = self.read()
        return {s: state[s].gripper > GRASP_CLOSED for s in sides}

    # ---------------------------------------------------------------------- poses

    def rest(self, *, speed: float = 0.3) -> "Arms":
        """Folds both arms back to the pose they were opened in."""
        return self._move_joints({s: self._home[s] for s in SIDES}, speed, True, DEFAULT_TOLERANCE, DEFAULT_TIMEOUT)

    def stage(self, *, speed: float = DEFAULT_SPEED) -> "Arms":
        """Raises both arms to the stage pose the gesture library is written against."""
        return self._move_joints(self._stage_pose(), speed, True, DEFAULT_TOLERANCE, DEFAULT_TIMEOUT)

    def raise_arms(self, *, speed: float = DEFAULT_SPEED) -> "Arms":
        """Lifts both arms from rest by the repo's raised pose, `[0, 0.8, 1.2, 0, 0, 0]`."""
        lift = np.array([0.0, 0.8, 1.2, 0.0, 0.0, 0.0])
        return self._move_joints({s: self._home[s] + lift for s in SIDES}, speed, True)

    # ------------------------------------------------------------------- gestures

    def gestures(self) -> list[str]:
        """The names of the gestures in `robot/gestures/`."""
        return sorted(self._library())

    def play(self, name: str, *, side: str = "both", speed: float = 1.0) -> "Arms":
        """Plays a gesture from `robot/gestures/` on one or both arms.

        The gesture is written relative to the stage pose, so the arms are moved
        there first. `speed` scales the gesture's timing (1.0 is as authored),
        capped at the gesture's checked `max_speed` like the stage server does.
        """
        library = self._library()
        if name in self._rejected:
            raise ArmError(f"gesture {name!r} fails the safety checker: {self._rejected[name]}")
        if name not in library:
            raise ArmError(f"no gesture {name!r}; try one of {', '.join(sorted(library))}")
        gesture = library[name]
        if gesture.side:  # pick/place at the table: absolute, one arm, prop bookkeeping: the stage server only
            raise ArmError(f"gesture {name!r} goes down to the table: play it through the stage server")
        speed = min(speed, gesture.max_speed)
        sides = self._sides(side)
        if gesture.arms == "one" and len(sides) == 2:
            raise ArmError(f"gesture {name!r} is written for one arm; pass side='left' or 'right'")

        self._move_joints({s: self._stage_pose()[s] for s in sides}, DEFAULT_SPEED, True)

        Q, G = {}, {}
        for s in sides:
            Q[s], G[s] = gesture.samples(s, speed)
        return self._stream(Q, G, tolerance=0.35)

    # ------------------------------------------------------------------ utilities

    def hold(self, seconds: float) -> "Arms":
        """Keeps commanding the current pose for `seconds`, so the arms stay put."""
        current = self.read()
        joints = {s: current[s].joints for s in SIDES}
        grips = {s: current[s].gripper for s in SIDES}
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._send(joints, grips)
            time.sleep(1.0 / CONTROL_HZ)
        return self

    def stop(self) -> "Arms":
        """Freezes both arms where they are now."""
        current = self.read()
        joints = {s: current[s].joints for s in SIDES}
        grips = {s: current[s].gripper for s in SIDES}
        for _ in range(5):
            self._send(joints, grips)
            time.sleep(1.0 / CONTROL_HZ)
        return self

    def show(self) -> "Arms":
        """Prints both arms' state."""
        for side, state in self.read().items():
            print(f"{side:5s} {state}", flush=True)
        return self

    # ------------------------------------------------------------------- internals

    def _sides(self, side: str) -> tuple[str, ...]:
        if side == "both":
            return SIDES
        if side not in SIDES:
            raise ArmError(f"side must be 'left', 'right' or 'both', not {side!r}")
        return (side,)

    def _targets(self, value) -> dict:
        """Normalizes a per-side mapping, or a bare value for both arms."""
        if isinstance(value, dict):
            unknown = set(value) - set(SIDES)
            if unknown:
                raise ArmError(f"unknown side(s) {sorted(unknown)}; expected 'left' and/or 'right'")
            return dict(value)
        return {s: value for s in SIDES}

    def _fk(self, side: str, joints: np.ndarray) -> np.ndarray:
        return self._solver(side).fk(joints)

    def _solver(self, side: str):
        if side not in self._ik:
            from dream_hackathon.arms import yam

            self._ik[side] = yam.teleop_ik(self._station)[side]
        return self._ik[side]

    def _stage_pose(self) -> dict[str, np.ndarray]:
        import show

        return {s: show.stage_pose(s) for s in SIDES}

    def _checker(self):
        if self._ck is None:
            import show

            self._ck = show.Checker()
        return self._ck

    def _library(self) -> dict:
        """The gestures that pass show's checker (limits, speed, table, lean-back); rejected ones in `_rejected`."""
        import show

        if self._lib is None:
            self._lib, self._rejected, _ = show.load_library(self._checker(), _HERE / "gestures", verbose=False)
        return self._lib

    def _check_limits(self, side: str, joints: np.ndarray) -> None:
        """Refuses a target outside the arm's limits, allowing a hair of slack.

        The rest pose sits exactly on the j2/j3 zero stops, and a measured pose
        reads a few 1e-4 rad below them, so a strict check would refuse `rest()`.
        """
        low, high = self._limits[:, 0], self._limits[:, 1]
        bad = np.nonzero((joints < low - LIMIT_SLACK) | (joints > high + LIMIT_SLACK))[0]
        if len(bad):
            j = int(bad[0])
            raise ArmError(
                f"{side} joint{j + 1} target {joints[j]:+.3f} rad is outside "
                f"[{low[j]:+.3f}, {high[j]:+.3f}]"
            )

    def _send(self, joints: dict, grips: dict) -> None:
        for side in SIDES:
            self._robots[side].command_joint_pos(
                np.append(np.asarray(joints[side], float), float(np.clip(grips[side], 0.0, 1.0)))
            )

    def _move_joints(
        self,
        targets,
        speed=DEFAULT_SPEED,
        wait=True,
        tolerance=DEFAULT_TOLERANCE,
        timeout=DEFAULT_TIMEOUT,
    ) -> "Arms":
        current = self.read()
        joints = {s: current[s].joints for s in SIDES}
        grips = {s: current[s].gripper for s in SIDES}
        for side, target in targets.items():
            target = np.asarray(target, float)
            if target.shape != (N_JOINTS,):
                raise ArmError(f"{side} target must be {N_JOINTS} joints, got shape {target.shape}")
            self._check_limits(side, target)
            joints[side] = target
        return self._execute(joints, grips, speed, wait, tolerance, timeout)

    def _move_cartesian(self, targets, speed, wait, tolerance, timeout, *, linear: bool) -> "Arms":
        current = self.read()
        joints = {s: current[s].joints for s in SIDES}
        grips = {s: current[s].gripper for s in SIDES}
        for side, target in self._targets(targets).items():
            target = np.asarray(target, float)
            if target.shape == (3,):
                pose = current[side].pose.copy()
                pose[:3, 3] = target
            elif target.shape == (4, 4):
                pose = target
            else:
                raise ArmError(f"{side} target must be [x, y, z] or a 4x4 pose, got shape {target.shape}")
            joints[side] = self._solve(side, pose, joints[side], linear=linear)
        return self._execute(joints, grips, speed, wait, tolerance, timeout)

    def _solve(self, side: str, target: np.ndarray, seed: np.ndarray, *, linear: bool) -> np.ndarray:
        """Joints reaching `target` from `seed`, solved in simulation before any motor moves."""
        solver = self._solver(side)
        if not linear:
            return self._solve_pose(solver, target, seed)

        # A straight line: solve the pose at each waypoint, seeded from the last,
        # so the joint path follows the Cartesian path instead of cutting across it.
        start = solver.fk(seed)
        distance = float(np.linalg.norm(target[:3, 3] - start[:3, 3]))
        steps = max(int(distance / 0.01), 2)
        q = np.asarray(seed, float)
        for i in range(1, steps + 1):
            t = _smoothstep(i / steps)
            waypoint = np.eye(4)
            waypoint[:3, :3] = _slerp(start[:3, :3], target[:3, :3], t)
            waypoint[:3, 3] = start[:3, 3] + t * (target[:3, 3] - start[:3, 3])
            q = self._solve_pose(solver, waypoint, q)
        return q

    @staticmethod
    def _solve_pose(solver, target: np.ndarray, seed: np.ndarray, iterations: int = 400) -> np.ndarray:
        q = np.asarray(seed, float)
        for _ in range(iterations):
            q = solver.step(target, q, 0.02)
        reached = solver.fk(q)
        position_error = float(np.linalg.norm(reached[:3, 3] - target[:3, 3]))
        rotation_error = float(
            np.arccos(np.clip((np.trace(reached[:3, :3].T @ target[:3, :3]) - 1) / 2, -1, 1))
        )
        if position_error > 0.01 or rotation_error > 0.10:
            raise ArmError(
                f"cannot reach {np.round(target[:3, 3], 3).tolist()}: "
                f"{position_error * 1000:.0f} mm and {rotation_error:.2f} rad off; nothing moved"
            )
        return q

    def _stream(self, Q: dict, G: dict, tolerance: float = 0.35) -> "Arms":
        """Plays sampled trajectories `Q[side]` (N,6) and `G[side]` (N,) at GESTURE_HZ.

        Sides missing from `Q` are held at their current pose. The arms are then
        held on the final sample until they settle, so the gesture ends where it
        was authored rather than wherever the last command left them.
        """
        current = self.read()
        n = max(len(q) for q in Q.values())
        for s in SIDES:
            if s not in Q:
                Q[s] = np.repeat(current[s].joints[None, :], n, axis=0)
                G[s] = np.full(n, current[s].gripper)
            elif G.get(s) is None:
                G[s] = np.full(n, current[s].gripper)
            if len(Q[s]) != n:
                raise ArmError(f"{s} trajectory has {len(Q[s])} samples, expected {n}")

        for i in range(n):
            self._send({s: Q[s][i] for s in SIDES}, {s: G[s][i] for s in SIDES})
            time.sleep(1.0 / GESTURE_HZ)

        final = {s: Q[s][-1] for s in SIDES}
        final_g = {s: G[s][-1] for s in SIDES}
        deadline = time.monotonic() + DEFAULT_TIMEOUT
        while time.monotonic() < deadline:
            self._send(final, final_g)
            time.sleep(1.0 / CONTROL_HZ)
            measured = self.read()
            if max(float(np.max(np.abs(measured[s].joints - final[s]))) for s in SIDES) < tolerance:
                return self
        return self

    def _execute(self, joints, grips, speed, wait, tolerance, timeout, hz: float = CONTROL_HZ) -> "Arms":
        """Smoothstep from the current pose to `joints`, then hold until settled.

        `joints` and `grips` may name only the arms being moved; the others are
        sent their current pose so they hold still.
        """
        start = self.read()
        joints = {s: np.asarray(joints[s], float) if s in joints else start[s].joints for s in SIDES}
        grips = {s: float(grips[s]) if grips.get(s) is not None else start[s].gripper for s in SIDES}
        # Don't lean back (show.COM_BACK_MARGIN): the arms' mass behind the bases tips the table. A move may lean
        # back as far as rest or where it starts, no further; checked on 26 samples of the path before moving.
        for s in SIDES:
            path = [start[s].joints + _smoothstep(u) * (joints[s] - start[s].joints) for u in np.linspace(0, 1, 26)]
            problems = self._checker().lean_problems(s, path, fold=True)
            if problems:
                raise ArmError(f"{s}: {problems[0]}; nothing moved")
        span = max(float(np.max(np.abs(joints[s] - start[s].joints))) for s in SIDES)
        duration = max(span / max(speed, 1e-3), 0.2)
        steps = max(int(duration * hz), 1)

        for i in range(1, steps + 1):
            t = _smoothstep(i / steps)
            self._send(
                {s: start[s].joints + t * (joints[s] - start[s].joints) for s in SIDES},
                {s: start[s].gripper + t * (grips[s] - start[s].gripper) for s in SIDES},
            )
            time.sleep(1.0 / hz)

        if not wait:
            return self

        deadline = time.monotonic() + timeout
        residual = float("inf")
        while time.monotonic() < deadline:
            self._send(joints, grips)
            time.sleep(1.0 / hz)
            measured = self.read()
            residual = max(float(np.max(np.abs(measured[s].joints - joints[s]))) for s in SIDES)
            # The gripper travels slower than the joints, so a gripper-only move
            # would otherwise return before the fingers had moved at all.
            grip_error = max(abs(measured[s].gripper - grips[s]) for s in SIDES)
            if residual < tolerance and grip_error < GRIP_TOLERANCE:
                return self
        # Joints 4-6 run at kp=10 and sag ~1.5 deg under gravity, so a small
        # residual is normal. A large one means something blocked the arm.
        if residual > FAIL_TOLERANCE:
            raise ArmError(
                f"blocked: {np.degrees(residual):.1f} deg from target after {timeout:.0f}s "
                f"(over {np.degrees(FAIL_TOLERANCE):.0f} deg means the arm was obstructed)"
            )
        return self


def _slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Interpolates two rotation matrices along the shortest arc."""
    relative = a.T @ b
    angle = np.arccos(np.clip((np.trace(relative) - 1) / 2, -1.0, 1.0))
    if angle < 1e-6:
        return b.copy()
    axis = np.array(
        [relative[2, 1] - relative[1, 2], relative[0, 2] - relative[2, 0], relative[1, 0] - relative[0, 1]]
    ) / (2 * np.sin(angle))
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    step = np.eye(3) + np.sin(t * angle) * k + (1 - np.cos(t * angle)) * (k @ k)
    return a @ step


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--demo", action="store_true", help="read, stage, nod, rest")
    parser.add_argument("--gestures", action="store_true", help="list the gesture library")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    args = parser.parse_args()

    if args.gestures:
        with Arms.open(energize=False) as arms:
            print("\n".join(arms.gestures()))
        return 0

    with Arms.open() as arms:
        arms.show()
        if args.demo:
            print("stage", flush=True)
            arms.stage(speed=args.speed)
            print("nod on both arms", flush=True)
            arms.play("nod", side="left", speed=0.6)
            arms.play("nod", side="right", speed=0.6)
            print("rest", flush=True)
            arms.rest()
        arms.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
