# Dream-1 (Dream Labs) — tried, then bypassed

Dream-1 is a language-conditioned robot policy. On the station: `source ~/dream-env/bin/activate`, then
`dreamcli robot run --task "<instruction>" [--dry-run] [--config file] [--view]`. It homes the arms first, runs
until Ctrl-C (`timeout -s INT <secs> dreamcli ...` works), then homes and switches off. `dreamcli robot report`
summarises the last run. It needs a team `DREAM_API_KEY` in `~/.dreamlabs/credentials` (from platform.dreamlabs.ai;
never commit it).

## What happened (Nov 7)
- 4 zero-shot runs of "pick up the yellow block": no successful pick. The gripper closed on air, grabbed the wrong
  (red) block, and once drove into the table, so the e-stop was pressed.
- A long, detailed "what + how" prompt made it worse than a short one.
- Helpful if you try again: short prompts, a clean table, half speed (`max_ee_speed_m_s: 0.3` in a copy of the
  config, `~/dial/station_slow.yaml`).
- Fine-tuning needs 50+ teleoperated demos, 7–8 h of training plus queue time, and costs ~$200.

**Decision:** drive the arms directly ([decisions.md](decisions.md), [arm-control.md](arm-control.md)).
