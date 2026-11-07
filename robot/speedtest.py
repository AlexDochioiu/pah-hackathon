"""Speed test through a running stage server (stage.py --real): every gesture on both arms. speedtest.py [SPEED] [name,name,...]"""
import json, sys, time, urllib.request
import numpy as np
SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None  # e.g. speedtest.py 0.5 celebrate,roar
U = "http://127.0.0.1:8770"
def req(path, body=None):
    r = urllib.request.Request(U + path, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        return json.loads(urllib.request.urlopen(r, timeout=5).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
JW = {"left": np.zeros(6), "right": np.zeros(6)}
def wait(secs):
    worst, t0 = 0.0, time.time()
    while time.time() - t0 < secs:
        s = req("/state")
        if s.get("fault"): print("FAULT", s["fault"]); raise SystemExit(1)
        for side in ("left", "right"):
            a = s["arms"][side]
            d = np.abs(np.subtract(a["joints"], a["measured"]))
            worst = max(worst, float(d.max())); JW[side] = np.maximum(JW[side], d)
        time.sleep(0.1)
    return worst
for _ in range(60):
    try: req("/state"); break
    except Exception: time.sleep(1)
print("stage", req("/stage", {"arm": "both", "speed": 0.5}).get("ok"), f"{wait(6):.3f}")
for g in req("/gestures"):
    if g["name"] in ("stage", "rest", "idle_breathe"): continue
    if ONLY and g["name"] not in ONLY: continue
    r = req("/play", {"arm": "both", "gesture": g["name"], "speed": SPEED})
    if not r.get("ok", True): print(f"{g['name']:18s} refused: {r.get('error')}"); continue
    e = wait(g["duration_s"] + 0.8)
    if g.get("hold"): req("/stage", {"arm": "both", "speed": 0.6}); e = max(e, wait(4))
    print(f"{g['name']:18s} {g['duration_s']/SPEED:.1f}s  max {e:.3f} rad  per joint L {np.round(JW['left'],2).tolist()} R {np.round(JW['right'],2).tolist()}", flush=True)
    JW["left"][:] = 0; JW["right"][:] = 0
print("freeze test", req("/play", {"arm": "both", "gesture": "wave"}).get("ok"), end=" ")
time.sleep(1.5); print(req("/freeze", {"arm": "both"}).get("ok"), f"{wait(1.5):.3f}")
print("idle", req("/idle", {"arm": "both"}).get("ok"), f"{wait(5):.3f}")
print("rest", req("/rest", {"arm": "both", "speed": 0.5}).get("ok"), f"{wait(8):.3f}")
