"""Predictive autoscaler for the workload Deployment.

Reads the cluster-wide request rate from Prometheus every few seconds, fits a straight
line through the last minute of samples, projects the rate a short time ahead (the time
a new pod needs to become ready) and sets the replica count for the projected load.

Design choices
  * Scale up uses the projected rate, so capacity can be added before CPU rises.
  * Scale down uses the current rate and the same limits as the Kubernetes HPA
    (60 s stabilization window, at most 2 pods per 30 s), so the comparison with the
    other policies differs in the signal and the forecast, not in how fast it may shrink.
  * Only the standard library is used. Talks to the API server with the pod's service
    account token.

Every decision is printed as one line, so the experiment scripts can store it as a log.
"""
import collections
import json
import math
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

PROM_URL = os.environ.get("PROM_URL", "http://prometheus-server.monitoring.svc.cluster.local:80")
QUERY = os.environ.get("QUERY", "sum(rate(app_requests_total[15s]))")
NAMESPACE = os.environ.get("NAMESPACE", "default")
DEPLOYMENT = os.environ.get("DEPLOYMENT", "workload")
TARGET_PER_POD = float(os.environ.get("TARGET_PER_POD", "3"))
MIN_REPLICAS = int(os.environ.get("MIN_REPLICAS", "2"))
MAX_REPLICAS = int(os.environ.get("MAX_REPLICAS", "12"))
INTERVAL = float(os.environ.get("INTERVAL", "5"))
WINDOW = float(os.environ.get("WINDOW", "45"))          # seconds of history used by the fit
HORIZON = float(os.environ.get("HORIZON", "20"))        # how far ahead to project
DOWN_STAB = float(os.environ.get("DOWN_STAB", "60"))    # scale-down stabilization window
DOWN_STEP = int(os.environ.get("DOWN_STEP", "2"))       # max pods removed per DOWN_PERIOD
DOWN_PERIOD = float(os.environ.get("DOWN_PERIOD", "30"))

API = "https://kubernetes.default.svc"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"


def api(method, path, body=None, ctype="application/json"):
    token = open(f"{SA}/token").read().strip()
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.loads(r.read())


def current_replicas():
    d = api("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}")
    return int(d["spec"]["replicas"])


def set_replicas(n):
    api("PATCH", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}/scale",
        {"spec": {"replicas": n}}, "application/merge-patch+json")


def request_rate():
    url = PROM_URL + "/api/v1/query?" + urllib.parse.urlencode({"query": QUERY})
    with urllib.request.urlopen(url, timeout=5) as r:
        res = json.loads(r.read())["data"]["result"]
    if not res:
        return None
    v = float(res[0]["value"][1])
    return None if math.isnan(v) else v


def slope(points):
    """Least-squares slope (requests/s per second) of (t, rate) points."""
    n = len(points)
    if n < 4:
        return 0.0
    mt = sum(t for t, _ in points) / n
    mr = sum(r for _, r in points) / n
    den = sum((t - mt) ** 2 for t, _ in points)
    if den == 0:
        return 0.0
    return sum((t - mt) * (r - mr) for t, r in points) / den


def main():
    history = collections.deque()          # (t, rate)
    wanted = collections.deque()           # (t, replicas wanted from the current rate), for stabilization
    last_down = 0.0
    print("predictor started", flush=True)
    while True:
        t = time.time()
        try:
            rate = request_rate()
            cur = current_replicas()
            if rate is not None:
                history.append((t, rate))
            while history and history[0][0] < t - WINDOW:
                history.popleft()
            if rate is None:
                time.sleep(INTERVAL)
                continue

            m = slope(list(history))
            forecast = max(0.0, rate + m * HORIZON)
            up_need = math.ceil(forecast / TARGET_PER_POD)
            now_need = math.ceil(rate / TARGET_PER_POD)
            wanted.append((t, now_need))
            while wanted and wanted[0][0] < t - DOWN_STAB:
                wanted.popleft()
            down_floor = max(n for _, n in wanted)      # highest need seen in the window

            desired = cur
            if up_need > cur:
                desired = up_need
            elif down_floor < cur and t - last_down >= DOWN_PERIOD:
                desired = max(down_floor, cur - DOWN_STEP)
            desired = max(MIN_REPLICAS, min(MAX_REPLICAS, desired))

            if desired != cur:
                set_replicas(desired)
                if desired < cur:
                    last_down = t
            print(f"{int(t)} PREDICT rate={rate:.2f} slope={m:.3f} forecast={forecast:.2f} "
                  f"current={cur} desired={desired}", flush=True)
        except Exception as exc:  # keep running through transient API or Prometheus errors
            print(f"{int(t)} ERROR {exc}", file=sys.stderr, flush=True)
        time.sleep(max(0.0, INTERVAL - (time.time() - t)))


if __name__ == "__main__":
    main()
