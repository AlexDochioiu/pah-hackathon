# dimos digital twin

Dimensional's (dimos) Dual OpenYAM stack runs on the laptop, and its two arms in the Viser 3D view mirror ours
live. A dimos `ConnectionModule` (`StageConnection`) polls the stage server's read-only `GET /state` at 20 Hz.
More in [kb/dimos.md](../kb/dimos.md).

## Run
Needs `uv`, `git-lfs` (`brew install git-lfs`) and Google Chrome (only for the check screenshots).
dimos is not vendored: `run.sh` uses `DIMOS_SRC` (default `/tmp/dimos-src`), and on the first run clones it at the
pinned commit `ab978ec5a0`.

```bash
cd dimos
./run.sh                                   # mirror the kin stage on :8783 (dev)
./run.sh --stage http://127.0.0.1:8770     # mirror the REAL arms through the ssh tunnel (GET /state only)
./run.sh --mcp                             # plus dimos skills over MCP (:9990); they POST only to :8783
```
Open http://127.0.0.1:8095. It opens on our MuJoCo "audience" camera. Stop the twin with Ctrl-C (SIGINT). A plain
`kill` (SIGTERM) leaves dimos's worker processes running.

Dev stage (sim), in another terminal (i2rt is on `PYTHONPATH`, from the kit copy at `/tmp/kit/site`):
```bash
cd robot && PYTHONPATH=/tmp/kit/site uv run --no-project --with "mujoco<4" --with numpy --with tyro --with pydantic \
  --with crcmod --with python-can --with pyyaml python stage.py --backend kin --port 8783
```

## Skills over MCP (`--mcp`)
```bash
cd /tmp/dimos-src
uv run --extra planning dimos mcp list-tools        # gestures, duets, play, duet, freeze, rest (+ dimos's own)
uv run --extra planning dimos mcp call play --arg arm=both --arg gesture=wave
uv run --extra planning dimos mcp call duet --arg name=hug
uv run --extra planning dimos mcp call freeze
uv run --extra planning dimos mcp call rest
```
The skills POST to `--skills-stage` (default `http://127.0.0.1:8783`) and refuse port 8770.

## Ghost (POST :8796/ghost)
A joint trajectory replayed as dimos's translucent preview robot over the live arms, looping until cleared. Used to
show a Dream-1 action chunk before the real arms run it. The JSON is in [kb/dimos.md](../kb/dimos.md#ghost).
```bash
curl -s -X POST localhost:8796/ghost -d '{"side":"left","t":[0,1,2],"q":{"left":[[0,0.8,0.7,0.2,0,0,1],[0.4,0.8,0.7,0.2,0,0,0],[0,0.8,0.7,0.2,0,0,1]]}}'
curl -s -X POST localhost:8796/ghost/clear
```

## Checks
```bash
./check_model.sh            # FK, mesh by mesh, twin URDF vs our MuJoCo scene, 200 random poses
./check_model.sh compare    # kin stage frozen mid-gesture: MuJoCo render vs Viser screenshot -> shots/
```

## Files
| File | What |
|---|---|
| `twin.py` | `StageConnection`, `TwinManipulationModule` (30 Hz view, fingers, latency probe, ghost), `StageSkills`, the blueprint, `main` |
| `run.sh` | Launcher: dimos at the pinned commit, its LFS models, this folder on `PYTHONPATH` (for dimos's worker processes) |
| `check_model.py`, `compare.py`, `check_model.sh` | The numeric and visual checks against `robot/show.py` |
| `shots/` | MuJoCo vs Viser comparisons (`rest`, `wave`, `celebrate`, `bow`, `bite`, `duet_hug`), `ghost.png`, `real_arms.png` |
