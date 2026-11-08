#!/usr/bin/env bash
# Check the twin against our MuJoCo model:
#   ./check_model.sh                    numeric: mesh-by-mesh FK comparison over 200 random poses (check_model.py)
#   ./check_model.sh compare [specs]    visual: sim stage frozen mid-gesture, MuJoCo vs Viser side by side (compare.py)
# I2RT_SITE = a folder holding i2rt (show.py builds its MuJoCo scene from it); default /tmp/kit/site.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE:${I2RT_SITE:-/tmp/kit/site}"
I2RT_DEPS=(--with tyro --with crcmod --with python-can)
if [ "${1:-}" = compare ]; then  # no dimos needed: the robot env (mujoco 3.17) + Playwright on the installed Chrome
  shift
  exec uv run -q --no-project --with "mujoco<4" --with numpy --with pillow --with pyyaml --with pydantic --with playwright \
    "${I2RT_DEPS[@]}" python "$HERE/compare.py" "$@"
fi
cd "${DIMOS_SRC:-/tmp/dimos-src}"  # FK only (no rendering): dimos's venv, for twin.py's URDF
exec uv run -q --extra planning "${I2RT_DEPS[@]}" python "$HERE/check_model.py"
