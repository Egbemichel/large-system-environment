"""Estimate the capacity of one pod from the calibration run.

The calibration run (scripts/calibrate.sh) holds the Deployment at a fixed number of
replicas and raises the request rate linearly from 1 to 10 requests/s over three minutes.
This script bins the raw k6 data into 10 s windows, pairs each window with the offered rate
at that time, and reports the rate at which latency leaves its low-load baseline (the knee).
The scaling targets (3 requests/s per pod) are set as a fraction of that capacity.

Usage: python analysis/calibrate.py results/calibration_<timestamp> [replicas]
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

folder = Path(sys.argv[1])
replicas = int(sys.argv[2]) if len(sys.argv) > 2 else 1
start = int((folder / "load_start.txt").read_text())
RATE0, RATE1, DURATION = 1.0, 10.0, 180.0   # must match the calib pattern in load/k6-scenarios.js
WIN = 10

dur = defaultdict(list)
with open(folder / "k6-raw.csv", newline="") as f:
    for r in csv.DictReader(f):
        if r["metric_name"] == "http_req_duration":
            t = int(float(r["timestamp"])) - start
            if 0 <= t < DURATION:
                dur[t // WIN].append(float(r["metric_value"]))


def pct(v, q):
    v = sorted(v)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


rows = []
for b in sorted(dur):
    mid = b * WIN + WIN / 2
    offered = (RATE0 + (RATE1 - RATE0) * mid / DURATION) / replicas
    rows.append((offered, pct(dur[b], 0.5), pct(dur[b], 0.95), len(dur[b])))

base = statistics.median([r[2] for r in rows[:4]])
knee = next((r[0] for r in rows if r[2] > 3 * base), None)
print(f"baseline p95 (first 40 s): {base:.0f} ms")
print("offered/pod  p50_ms  p95_ms  n")
for o, p50, p95, n in rows:
    print(f"{o:10.2f}  {p50:6.0f}  {p95:6.0f}  {n}")
print(f"knee (p95 above 3x baseline) at about {knee} requests/s per pod" if knee else "no knee within the tested range")

fig, ax = plt.subplots(figsize=(5.5, 3.2))
ax.plot([r[0] for r in rows], [r[2] for r in rows], marker="o", ms=3, label="p95")
ax.plot([r[0] for r in rows], [r[1] for r in rows], marker="s", ms=3, label="p50")
if knee:
    ax.axvline(knee, color="black", ls="--", lw=0.8)
ax.set_yscale("log")
ax.set_xlabel("offered load per pod (requests/s)")
ax.set_ylabel("latency (ms, log scale)")
ax.set_title("Single-pod capacity calibration")
ax.legend(frameon=False)
fig.tight_layout()
out = folder.parent / "plots"
out.mkdir(exist_ok=True)
fig.savefig(out / "calibration.png", dpi=200)
print(f"wrote {out / 'calibration.png'}")
