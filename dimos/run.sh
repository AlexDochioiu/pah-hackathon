#!/usr/bin/env bash
# Launch the dimos digital twin (see README.md). Any flags go to twin.py:
#   ./run.sh                                        mirror the kin stage on :8783 (dev)
#   ./run.sh --stage http://127.0.0.1:8770          mirror the real arms (GET /state only)
#   ./run.sh --mcp                                  plus StageSkills over McpServer (:9990), POSTing to :8783 only
# dimos is not vendored: it runs from DIMOS_SRC (default /tmp/dimos-src), cloned at the pinned commit if missing.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIMOS_REPO=https://github.com/dimensionalOS/dimos.git
DIMOS_COMMIT=ab978ec5a0fef94e37dc2d99b1ff063f20d852ea  # main, Nov 7 2026 ("Pin workflow actions (#4718)")
DIMOS_SRC="${DIMOS_SRC:-/tmp/dimos-src}"

command -v git-lfs >/dev/null || { echo "needs git-lfs: brew install git-lfs" >&2; exit 1; }
if [ ! -d "$DIMOS_SRC/.git" ]; then
  echo "cloning dimos @ ${DIMOS_COMMIT:0:10} into $DIMOS_SRC (first run only)"
  GIT_LFS_SKIP_SMUDGE=1 git clone -q "$DIMOS_REPO" "$DIMOS_SRC"
  git -C "$DIMOS_SRC" checkout -q "$DIMOS_COMMIT"
fi
have="$(git -C "$DIMOS_SRC" rev-parse HEAD)"
[ "$have" = "$DIMOS_COMMIT" ] || echo "note: $DIMOS_SRC is at ${have:0:10}, the twin was tested at ${DIMOS_COMMIT:0:10}" >&2
git -C "$DIMOS_SRC" lfs install --local >/dev/null
# The two YAM model archives; dimos would fetch them lazily too, this just does it before the 3D view starts.
git -C "$DIMOS_SRC" lfs pull --include "data/.lfs/dual_openyam_abc_box_v2.tar.gz,data/.lfs/yam_description.tar.gz" --exclude=

cd "$DIMOS_SRC"
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec uv run --extra planning python -c \
  'import sys, twin; twin.main(sys.argv[1:])' "$@"
