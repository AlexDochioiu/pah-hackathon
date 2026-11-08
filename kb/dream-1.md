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

## Fine-tuning on our own scripted demos (Lars pick)
Teleop isn't required; only the dataset format matters. Sources: `~/physical-ai-hacks/docs/{data_collection,
fine_tuning,running_your_model}.md`, `tools/finetune.py`, `src/dream_hackathon/{dataset,collect,layouts}.py`.
- **Format:** LeRobot v2.1 in `~/hackathon_data/<name>/`, 30 fps. `observation.state` and `action` are float32[14]
  (measured and commanded joints): `right_joint_0..5, right_gripper, left_joint_0..5, left_gripper`, absolute
  radians, gripper 0 closed to 1 open. All three cameras are required (640x360 H.264). Episodes are at least 1.6 s;
  aim for 50+ of 10–30 s. Don't mix in `~/hackathon_data/myteam-cubes` (organiser test set).
- **Recording from our own loop:** `dataset.DatasetWriter(path, 30, "yam", names, video_keys, 640, 360)`, then
  `ep = w.start_episode(task)`, `ep.add_frame(layouts.to_vector("yam", q, g), layouts.to_vector("yam", cmd_q, cmd_g),
  {video_key: cams[role].latest()})` every 1/30 s in real time, then `ep.save()` or `ep.discard()`. Start each
  episode from dreamcli's home pose (`dreamcli robot run` homes first), keep the idle arm at home, and don't record
  the reset. The stage server holds `~/.dreamlabs/arms.lock`; stop it first.
- **Train:** `~/dream-env/bin/python tools/finetune.py ~/hackathon_data/lars --arm yam --check` (local, sends
  nothing), then without `--check` plus `--name lars --suffix lars --lr-schedule cosine` in tmux. It prints
  `dream-1-ft:<uid8>-lars`; resume with `--dataset-id`/`--job-id`. Knobs not exposed by the script
  (`dream.DreamClient().finetune(...)`): `steps` 500–2000 (default 1000; fewer is cheaper and faster),
  `base_share` (0.5 default), `learning_rate`. ~$200 per job from prepaid credits; there's no balance API, so ask
  the organisers. A queued job can't be cancelled; a failed one costs nothing.
- **Run:** `dreamcli robot run --checkpoint dream-1-ft:<uid8>-lars --task "<same task text>" --action-space joint
  --dry-run`, then without `--dry-run`. The first call can take up to 1 h to start the model.
