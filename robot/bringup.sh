#!/usr/bin/env bash
# One-command bring-up of the gesture library, for when the arms are free and someone stands at the e-stop.
#   bash ~/dial/bringup.sh --real     real arms: home.py, then stage at 30 %, every gesture once at 50 % on one arm
#                                     at a time (the other holds the stage pose), then rest; stops on any error
#   bash ~/dial/bringup.sh            the same in i2rt's simulation (no hardware)
# Remove props from the grippers first (gripper_clap opens them). Log: ~/dial/bringup-<time>.log
set -euo pipefail
cd ~/physical-ai-hacks
log=~/dial/bringup-$(date +%H%M%S).log
if [[ "${1:-}" == "--real" ]]; then
  .venv/bin/python ~/dial/home.py 2>&1 | tee -a "$log"   # fold to rest from wherever, switch off
fi
.venv/bin/python ~/dial/stage.py --bringup "$@" 2>&1 | tee -a "$log"
