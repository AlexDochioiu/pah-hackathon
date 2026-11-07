"""Record a new gesture by moving a floating arm by hand.

  record.py NAME [--arm left|right] [--secs 8] [--about TEXT] [--hold] [--both]
  record.py NAME --from recordings/NAME.npz ...      re-process a saved recording, no arms

Takes the station lock and opens both arms in gravity compensation (energize=False: they float and are never
commanded). Move the chosen arm through the gesture, starting near the stage pose. Joints are logged at 50 Hz, the
still start and end are trimmed, hand jitter is smoothed, the motion is slowed down if it breaks the speed or
acceleration limits, and a smooth return to the stage pose is appended (unless --hold). It is saved relative to the
stage pose (mirrored to the left-arm convention if recorded on the right arm) and must pass the safety checker:
passing gestures go to gestures/NAME.json, failing ones to gestures/rejected/NAME.json with the reason.
The raw joints are kept in recordings/NAME.npz. When done you must fold both arms back to rest: they go limp when
the script ends.
Run on the station: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/record.py NAME --arm left
"""
import argparse, datetime, json, pathlib, sys, time
import numpy as np
import show

STILL = 0.08   # rad/s: slower than this counts as not moving (trimmed at both ends)
REST_TOL = 0.25


def capture(arm, secs):
    from dream_hackathon import arms, config, station_lock
    station = config.load("configs/station.yaml")
    lock = station_lock.acquire("dial-record")
    a = arms.open_arms(station, energize=False)  # gravity compensation: floats, never commanded
    rows = []
    try:
        input(f"Arms are floating. Hold the {arm} arm near the stage pose, press Enter, then perform the gesture "
              f"({secs:.0f} s, Ctrl-C stops early)... ")
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < secs:
                rows.append(a.read()[0][arm].copy())
                time.sleep(1 / show.HZ)
        except KeyboardInterrupt:
            pass
        print(f"recorded {len(rows)} samples")
        while True:
            input("Fold BOTH arms back to rest (they go limp when the script ends), then press Enter... ")
            q, _ = a.read()
            far = {s: round(float(np.max(np.abs(q[s] - show.HOME[s]))), 2) for s in show.SIDES}
            if max(far.values()) <= REST_TOL:
                break
            if input(f"not at rest yet {far}; type 'limp' to switch off anyway: ").strip() == "limp":
                break
    finally:
        a.close()
        lock.close()
    return np.array(rows)


def process(raw, arm, name, about, hold, both):
    """Raw joints (N,6) at 50 Hz -> gesture dict, or exits if nothing moved."""
    k = np.ones(7) / 7
    Q = np.column_stack([np.convolve(np.pad(raw[:, j], 3, mode="edge"), k, mode="valid") for j in range(6)])
    v = np.abs(np.diff(Q, axis=0)).max(1) * show.HZ
    moving = np.nonzero(v > STILL)[0]
    if not len(moving):
        sys.exit("nothing moved: no gesture saved")
    a, b = max(moving[0] - 10, 0), min(moving[-1] + 10, len(Q) - 1)
    dq = show.mirror(Q[a:b + 1] - show.stage_pose(arm), arm)  # left-arm convention, relative to stage
    t = np.arange(len(dq)) / show.HZ
    if not hold:
        R, _ = show.blend(dq[-1], np.zeros(6), 1.0)
        dq = np.vstack([dq, R]); t = np.arange(len(dq)) / show.HZ
    idx = list(range(0, len(dq), 2)) + ([len(dq) - 1] if (len(dq) - 1) % 2 else [])
    total = 1.0
    for _ in range(6):  # slow down until the played-back (interpolated) motion is within the limits
        keys = [{"t": round(float(t[i] * total), 3), "q": [round(float(x), 4) for x in dq[i]]} for i in idx]
        g = {"name": name, "about": about, "arms": "both" if both else "one", "hold": hold, "loop": False,
             "recorded": f"{arm} {datetime.datetime.now():%Y-%m-%d %H:%M}", "keys": keys}
        vmax, amax = show.peak_rates(show.Gesture(g).samples("left")[0])
        f = max(float(np.max(vmax / show.VMAX)), float(np.sqrt(np.max(amax / show.AMAX))))
        if f <= 1.0:
            break
        total *= 1.05 * f
    if total > 1.0:
        print(f"too fast for the limits: slowed down x{total:.2f}")
    return g


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--arm", choices=show.SIDES, default="left")
    ap.add_argument("--secs", type=float, default=8.0)
    ap.add_argument("--about", default="Recorded by hand.")
    ap.add_argument("--hold", action="store_true", help="end where the motion ends (no return to stage)")
    ap.add_argument("--both", action="store_true", help="meant to be played on both arms at once")
    ap.add_argument("--from", dest="src", help="re-process a saved recordings/*.npz instead of recording")
    args = ap.parse_args()
    if not args.name.replace("_", "").isalnum():
        sys.exit("name: letters, digits and _ only")

    rec_dir = show.DIR / "recordings"
    if args.src:
        z = np.load(args.src); raw, arm = z["joints"], str(z["arm"])
    else:
        raw, arm = capture(args.arm, args.secs), args.arm
        rec_dir.mkdir(exist_ok=True)
        np.savez(rec_dir / f"{args.name}.npz", joints=raw, arm=arm)
    g = process(raw, arm, args.name, args.about, args.hold, args.both)

    path = show.GESTURE_DIR / f"{args.name}.json"
    if path.exists() and not json.loads(path.read_text()).get("recorded"):
        sys.exit(f"{path.name} is a built-in gesture: pick another name")
    path.write_text(json.dumps(g, separators=(",", ":")) + "\n")
    ok, bad, unsafe = show.load_library(verbose=False)
    if args.name in ok:
        pairs = sorted(p for p in unsafe if args.name in p)
        print(f"OK: {path} ({ok[args.name].duration:.1f} s, max speed x{ok[args.name].max_speed:.2f})"
              + (f"; refused together with {pairs}" if pairs else ""))
        return
    rej = show.GESTURE_DIR / "rejected"; rej.mkdir(exist_ok=True)
    path.rename(rej / path.name)
    sys.exit(f"REJECTED {args.name}: {bad.get(args.name)}\nsaved to {rej / path.name} (not playable)")


if __name__ == "__main__":
    main()
