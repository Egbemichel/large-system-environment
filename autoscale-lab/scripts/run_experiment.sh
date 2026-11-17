#!/usr/bin/env bash
# Run one experiment: apply a scaling policy, run a load pattern (optionally with chaos),
# and record replicas over time plus the k6 results into
#   results/<policy>_<pattern>[_chaos]_r<rep>_<timestamp>/
#
# Usage: scripts/run_experiment.sh <policy> <pattern> [chaos]
#   policy : fixed | hpa | keda | predictive
#   pattern: steady | ramp | spike
#   chaos  : add the word "chaos" to kill pods during the run
# Environment: REP (repeat number, default 1), FIXED_REPLICAS (default 4),
#   SETTLE (seconds, default 30), OBSERVE (seconds after load, default 90),
#   OUT_ROOT (default results), TARGET (default http://localhost:30080)
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

POLICY=${1:?policy: fixed|hpa|keda|predictive}
PATTERN=${2:?pattern: steady|ramp|spike}
CHAOS=${3:-}
REP=${REP:-1}
SETTLE=${SETTLE:-30}
OBSERVE=${OBSERVE:-90}
FIXED_REPLICAS=${FIXED_REPLICAS:-4}
OUT_ROOT=${OUT_ROOT:-results}
TARGET=${TARGET:-http://localhost:30080}

cleanup_policies() {
  kc delete hpa workload-hpa --ignore-not-found >/dev/null
  kc delete scaledobject workload-keda --ignore-not-found >/dev/null
  kc delete -f k8s/policies/predictive.yaml --ignore-not-found >/dev/null
}

# --- reset to a clean state: no policy object, exactly 2 ready pods, no traffic
cleanup_policies
kc scale deployment/workload --replicas=2 >/dev/null
for _ in $(seq 1 60); do
  n=$(kc get pods -l app=workload --no-headers 2>/dev/null | wc -l)
  [[ "$n" -eq 2 ]] && break
  sleep 2
done
kc rollout status deployment/workload --timeout=120s >/dev/null

case "$POLICY" in
  fixed)      kc scale deployment/workload --replicas="$FIXED_REPLICAS" >/dev/null ;;
  hpa)        kc apply -f k8s/policies/hpa-cpu.yaml >/dev/null ;;
  keda)       kc apply -f k8s/policies/keda-prometheus.yaml >/dev/null ;;
  predictive) kc apply -f k8s/policies/predictive.yaml >/dev/null ;;
  *) echo "unknown policy $POLICY"; exit 1 ;;
esac
kc rollout status deployment/workload --timeout=120s >/dev/null
echo "[$(date +%H:%M:%S)] $POLICY $PATTERN${CHAOS:+ chaos} rep $REP: settling for ${SETTLE}s"
sleep "$SETTLE"

OUT="$OUT_ROOT/${POLICY}_${PATTERN}${CHAOS:+_chaos}_r${REP}_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
cat > "$OUT/meta.json" <<EOF
{"policy":"$POLICY","pattern":"$PATTERN","chaos":$([[ "$CHAOS" == "chaos" ]] && echo true || echo false),"rep":$REP,
 "settle_s":$SETTLE,"observe_s":$OBSERVE,"fixed_replicas":$FIXED_REPLICAS}
EOF

# --- sample ready/desired replicas every 2 s in the background
(
  echo "ts,ready_replicas,desired_replicas" > "$OUT/replicas.csv"
  while true; do
    row=$(kc get deploy workload -o jsonpath='{.status.readyReplicas},{.spec.replicas}' 2>/dev/null || true)
    ready=${row%%,*}; desired=${row##*,}
    echo "$(date +%s),${ready:-0},${desired:-0}" >> "$OUT/replicas.csv"
    sleep 2
  done
) &
SAMPLER=$!

CHAOS_PID=""
if [[ "$CHAOS" == "chaos" ]]; then
  scripts/chaos.sh 60 1 3 40 > "$OUT/chaos.log" 2>&1 &
  CHAOS_PID=$!
fi

# --- load (record start time so the analysis can align everything)
date +%s > "$OUT/load_start.txt"
k6 run -e PATTERN="$PATTERN" -e TARGET="$TARGET" \
  --summary-export "$OUT/k6-summary.json" \
  --out "csv=$OUT/k6-raw.csv" load/k6-scenarios.js > "$OUT/k6-output.txt" 2>&1 || true
date +%s > "$OUT/load_end.txt"

# keep sampling for the scale-down phase
sleep "$OBSERVE"

kill "$SAMPLER" 2>/dev/null || true
[[ -n "$CHAOS_PID" ]] && kill "$CHAOS_PID" 2>/dev/null || true
[[ "$POLICY" == "predictive" ]] && kc logs deploy/predictor > "$OUT/predictor.log" 2>&1 || true
echo "[$(date +%H:%M:%S)] saved $OUT"
