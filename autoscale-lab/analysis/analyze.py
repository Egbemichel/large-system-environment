"""Turn the raw experiment folders in results/ into tables and figures.

Usage:  python analysis/analyze.py [results_dir]
Needs:  pip install matplotlib   (pandas and numpy are not required)

Outputs in <results_dir>:
  summary_runs.csv     one row per run
  summary.csv          one row per configuration: mean, standard deviation and 95% confidence
                       interval of every metric across repeats
  summary_table.md     the same table in Markdown, ready to paste into the report
  plots/*.png          comparison charts and timelines

Metric definitions (see the SRS, Table 14, and the report, Chapter 3):
  p50/p95/p99      request duration percentiles over the whole run (k6)
  error_rate       share of requests that failed or were not HTTP 200 (k6)
  dropped          iterations k6 could not start because it ran out of virtual users
  slo_ok           share of 10 s windows during the load whose p95 is at or below SLO_MS
  slo1s_ok         the same for the stricter 1000 ms objective (sensitivity check)
  p95_tail_ms      median of the 10 s window p95 values over the last 60 s of the load
                   (latency after the policy has had time to settle)
  replica_seconds  integral of ready replicas over the load period (cost indicator)
  scale_up_s       seconds from load start until desired replicas first exceeds its start value
  time_to_peak_s   seconds from load start until ready replicas first reach their maximum
  scale_down_s     seconds from load end until desired replicas is back at its start value
  recovery_s       mean seconds from a pod kill until ready replicas equal desired again
"""
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path(sys.argv[1] if len(sys.argv) > 1 else "results")
PLOTS = RESULTS / "plots"
PLOTS.mkdir(parents=True, exist_ok=True)
(PLOTS / "runs").mkdir(exist_ok=True)

SLO_MS = 2000.0         # latency objective used for the SLO metric (see report, Section 3.8)
SLO_STRICT_MS = 1000.0  # stricter objective, reported as a sensitivity check
WINDOW_S = 10           # width of the latency windows
POLICIES = ["fixed", "hpa", "keda", "predictive"]
LABEL = {"fixed": "Fixed (4)", "hpa": "HPA (CPU)", "keda": "KEDA (req/s)", "predictive": "Predictive"}
COLOR = {"fixed": "#6b6b6b", "hpa": "#1f77b4", "keda": "#d95f02", "predictive": "#1b9e77"}
PATTERNS = ["steady", "ramp", "spike"]
# two-sided 95% t quantiles by degrees of freedom
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}

plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.25, "figure.dpi": 100,
})


# ------------------------------------------------------------------ reading one run

def read_replicas(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                rows.append((int(r["ts"]), int(r["ready_replicas"] or 0), int(r["desired_replicas"] or 0)))
            except ValueError:
                continue
    return rows


def step_at(series, t, col):
    """Value of a step series [(ts, ready, desired)] at time t (column 1 or 2)."""
    val = None
    for row in series:
        if row[0] <= t:
            val = row[col]
        else:
            break
    return val


def metric(summary, name, key):
    return summary.get("metrics", {}).get(name, {}).get(key)


def percentile(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def latency_windows(folder, start, end):
    """[(window_start_s_relative, p95_ms, count)] from the raw k6 CSV."""
    path = folder / "k6-raw.csv"
    if not path.exists():
        return []
    buckets = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["metric_name"] != "http_req_duration":
                continue
            try:
                ts = int(float(r["timestamp"]))
                val = float(r["metric_value"])
            except ValueError:
                continue
            if start <= ts <= end:
                buckets[(ts - start) // WINDOW_S].append(val)
    return [(b * WINDOW_S, percentile(v, 0.95), len(v)) for b, v in sorted(buckets.items())]


def analyze(folder: Path):
    meta = {}
    if (folder / "meta.json").exists():
        meta = json.loads((folder / "meta.json").read_text())
    parts = folder.name.split("_")
    policy = meta.get("policy", parts[0])
    pattern = meta.get("pattern", parts[1])
    chaos = meta.get("chaos", "chaos" in parts)
    rep = meta.get("rep", 1)

    reps = read_replicas(folder / "replicas.csv")
    start = int((folder / "load_start.txt").read_text())
    end = int((folder / "load_end.txt").read_text())
    summary = json.loads((folder / "k6-summary.json").read_text())

    base = step_at(reps, start, 2) or (reps[0][2] if reps else 0)
    during = [r for r in reps if start <= r[0] <= end]
    after = [r for r in reps if r[0] > end]
    peak_ready = max((r[1] for r in during), default=0)

    # replica-seconds: sum of ready replicas times the sampling gap, over the load period
    rsec = 0.0
    for a, b in zip(during, during[1:]):
        rsec += a[1] * (b[0] - a[0])

    up = next((r[0] - start for r in during if r[2] > base), None)
    to_peak = next((r[0] - start for r in during if r[1] >= peak_ready and peak_ready > base), None)
    down = next((r[0] - end for r in after if r[2] <= base), None) if peak_ready > base or any(r[2] > base for r in during) else None

    # recovery after each kill: first dip below desired, then first return to desired
    recoveries = []
    log = folder / "chaos.log"
    if chaos and log.exists():
        kills = [int(l.split()[0]) for l in log.read_text().splitlines() if "CHAOS" in l]
        for k in kills:
            dip = next((r[0] for r in reps if r[0] >= k and r[1] < r[2]), None)
            if dip is None:
                continue
            back = next((r[0] for r in reps if r[0] > dip and r[1] >= r[2] and r[2] > 0), None)
            if back is not None:
                recoveries.append(back - k)

    win = latency_windows(folder, start, end)
    scored = [w for w in win if w[1] is not None and w[2] >= 5]
    slo_ok = (sum(1 for w in scored if w[1] <= SLO_MS) / len(scored)) if scored else None
    slo1s_ok = (sum(1 for w in scored if w[1] <= SLO_STRICT_MS) / len(scored)) if scored else None
    tail = [w[1] for w in scored if w[0] >= (end - start) - 60]
    p95_tail = statistics.median(tail) if tail else None

    failed = metric(summary, "http_req_failed", "value")
    if failed is None:
        failed = metric(summary, "http_req_failed", "rate")

    row = {
        "experiment": folder.name, "policy": policy, "pattern": pattern, "chaos": chaos, "rep": rep,
        "p50_ms": metric(summary, "http_req_duration", "med"),
        "p95_ms": metric(summary, "http_req_duration", "p(95)"),
        "p99_ms": metric(summary, "http_req_duration", "p(99)"),
        "error_rate": failed,
        "dropped": metric(summary, "dropped_iterations", "count") or 0,
        "slo_ok": slo_ok, "slo1s_ok": slo1s_ok, "p95_tail_ms": p95_tail,
        "peak_replicas": peak_ready,
        "replica_seconds": round(rsec, 1),
        "scale_up_s": up, "time_to_peak_s": to_peak, "scale_down_s": down,
        "recovery_s": round(statistics.mean(recoveries), 1) if recoveries else None,
    }
    series = {"reps": reps, "start": start, "end": end, "win": win}
    return row, series


# ------------------------------------------------------------------ aggregation

KEYS = ["p50_ms", "p95_ms", "p99_ms", "p95_tail_ms", "error_rate", "dropped", "slo_ok", "slo1s_ok", "peak_replicas",
        "replica_seconds", "scale_up_s", "time_to_peak_s", "scale_down_s", "recovery_s"]


def stats(values):
    v = [x for x in values if x is not None]
    if not v:
        return None, None, None, 0
    m = statistics.mean(v)
    if len(v) < 2:
        return m, None, None, len(v)
    sd = statistics.stdev(v)
    ci = T95.get(len(v) - 1, 1.96) * sd / math.sqrt(len(v))
    return m, sd, ci, len(v)


def aggregate(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["policy"], r["pattern"], r["chaos"])].append(r)
    out = []
    for (pol, pat, ch), rs in sorted(groups.items(), key=lambda kv: (POLICIES.index(kv[0][0]) if kv[0][0] in POLICIES else 9, kv[0][1], kv[0][2])):
        row = {"policy": pol, "pattern": pat, "chaos": ch, "n": len(rs)}
        for k in KEYS:
            m, sd, ci, _ = stats([r[k] for r in rs])
            row[f"{k}_mean"] = None if m is None else round(m, 4)
            row[f"{k}_sd"] = None if sd is None else round(sd, 4)
            row[f"{k}_ci95"] = None if ci is None else round(ci, 4)
        out.append(row)
    return out


def fmt(m, ci, digits=0, pct=False):
    if m is None:
        return "n/a"
    if pct:
        m, ci = m * 100, (ci * 100 if ci is not None else None)
    s = f"{m:.{digits}f}"
    return s if ci is None else f"{s} ± {ci:.{digits}f}"


def write_markdown(agg, path):
    head = ["Policy", "Pattern", "n", "p95 (ms)", "p99 (ms)", "Errors (%)", "SLO windows met (%)",
            "Replica-s", "Scale-up (s)", "Scale-down (s)", "Recovery (s)"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for a in agg:
        name = a["pattern"] + (" + chaos" if a["chaos"] else "")
        lines.append("| " + " | ".join([
            LABEL.get(a["policy"], a["policy"]), name, str(a["n"]),
            fmt(a["p95_ms_mean"], a["p95_ms_ci95"]), fmt(a["p99_ms_mean"], a["p99_ms_ci95"]),
            fmt(a["error_rate_mean"], a["error_rate_ci95"], 1, True),
            fmt(a["slo_ok_mean"], a["slo_ok_ci95"], 0, True),
            fmt(a["replica_seconds_mean"], a["replica_seconds_ci95"]),
            fmt(a["scale_up_s_mean"], a["scale_up_s_ci95"]),
            fmt(a["scale_down_s_mean"], a["scale_down_s_ci95"]),
            fmt(a["recovery_s_mean"], a["recovery_s_ci95"]),
        ]) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ figures

def bars(agg, key, ylabel, title, fname, pct=False, chaos=False, patterns=PATTERNS):
    sel = [a for a in agg if a["chaos"] == chaos]
    if not sel:
        return
    pols = [p for p in POLICIES if any(a["policy"] == p for a in sel)]
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    width = 0.8 / max(1, len(pols))
    for i, pol in enumerate(pols):
        xs, ys, es = [], [], []
        for j, pat in enumerate(patterns):
            m = next((a for a in sel if a["policy"] == pol and a["pattern"] == pat), None)
            if m is None or m[f"{key}_mean"] is None:
                continue
            f = 100 if pct else 1
            xs.append(j + i * width)
            ys.append(m[f"{key}_mean"] * f)
            es.append((m[f"{key}_ci95"] or 0) * f)
        ax.bar(xs, ys, width, yerr=es, capsize=2, label=LABEL[pol], color=COLOR[pol], error_kw={"lw": 0.8})
    ax.set_xticks([j + width * (len(pols) - 1) / 2 for j in range(len(patterns))])
    ax.set_xticklabels(patterns)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(PLOTS / fname, dpi=200)
    plt.close(fig)


def timeline(runs, pattern, fname):
    """Mean ready replicas and 10 s p95 against time since load start, per policy."""
    sel = [(r, s) for r, s in runs if r["pattern"] == pattern and not r["chaos"]]
    if not sel:
        return
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.8, 5), sharex=True)
    for pol in POLICIES:
        rs = [(r, s) for r, s in sel if r["policy"] == pol]
        if not rs:
            continue
        horizon = max(s["end"] - s["start"] for _, s in rs) + 90
        grid = list(range(-20, horizon, 2))
        mean_ready = []
        for t in grid:
            vals = [step_at(s["reps"], s["start"] + t, 1) for _, s in rs]
            vals = [v for v in vals if v is not None]
            mean_ready.append(statistics.mean(vals) if vals else None)
        a1.step([g for g, v in zip(grid, mean_ready) if v is not None], [v for v in mean_ready if v is not None],
                where="post", label=LABEL[pol], color=COLOR[pol], lw=1.4)
        wins = defaultdict(list)
        for _, s in rs:
            for w, p95, n in s["win"]:
                if p95 is not None and n >= 5:
                    wins[w].append(p95)
        ws = sorted(wins)
        a2.plot(ws, [statistics.mean(wins[w]) for w in ws], color=COLOR[pol], lw=1.4, label=LABEL[pol])
    a1.set_ylabel("ready replicas (mean of repeats)")
    a1.legend(frameon=False, fontsize=8, ncol=2)
    a2.axhline(SLO_MS, color="black", lw=0.8, ls="--")
    a2.text(2, SLO_MS * 1.08, f"SLO {int(SLO_MS)} ms", fontsize=8)
    a2.set_yscale("log")
    a2.set_ylabel("p95 latency per 10 s (ms, log)")
    a2.set_xlabel("seconds since load start")
    a1.set_title(f"{pattern} pattern")
    fig.tight_layout()
    fig.savefig(PLOTS / fname, dpi=200)
    plt.close(fig)


def run_plot(row, s):
    reps, start, end = s["reps"], s["start"], s["end"]
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.step([r[0] - start for r in reps], [r[1] for r in reps], where="post", label="ready")
    ax.step([r[0] - start for r in reps], [r[2] for r in reps], where="post", ls="--", label="desired")
    ax.axvline(0, color="grey", lw=0.8)
    ax.axvline(end - start, color="grey", lw=0.8)
    ax.set_xlabel("seconds since load start")
    ax.set_ylabel("replicas")
    ax.set_title(row["experiment"])
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(PLOTS / "runs" / f"{row['experiment']}.png", dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------ main

def main():
    folders = sorted(p for p in RESULTS.iterdir()
                     if p.is_dir() and (p / "k6-summary.json").exists() and (p / "replicas.csv").exists()
                     and p.name.split("_")[0] in POLICIES)
    if not folders:
        sys.exit(f"No finished experiments found in {RESULTS}/")
    runs = [analyze(f) for f in folders]
    rows = [r for r, _ in runs]

    with open(RESULTS / "summary_runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    agg = aggregate(rows)
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(agg[0].keys()))
        w.writeheader()
        w.writerows(agg)
    write_markdown(agg, RESULTS / "summary_table.md")

    bars(agg, "p95_ms", "p95 latency (ms)", "p95 latency by policy and pattern (mean, 95% CI)", "p95_latency.png")
    bars(agg, "replica_seconds", "replica-seconds during load", "Resource cost by policy and pattern", "cost.png")
    bars(agg, "slo_ok", "10 s windows within SLO (%)", f"Windows with p95 at or below {int(SLO_MS)} ms", "slo.png", pct=True)
    bars(agg, "error_rate", "failed requests (%)", "Error rate by policy and pattern", "errors.png", pct=True)
    bars(agg, "recovery_s", "recovery time (s)", "Recovery after a pod kill (spike pattern)", "recovery.png",
         chaos=True, patterns=["spike"])
    bars(agg, "p95_ms", "p95 latency (ms)", "p95 latency with pod kills (spike pattern)", "p95_chaos.png",
         chaos=True, patterns=["spike"])
    for pat in PATTERNS:
        timeline(runs, pat, f"timeline_{pat}.png")
    for row, s in runs:
        run_plot(row, s)

    print(f"{len(rows)} runs, {len(agg)} configurations. Wrote summary.csv, summary_runs.csv, "
          f"summary_table.md and plots in {PLOTS}/")


if __name__ == "__main__":
    main()
