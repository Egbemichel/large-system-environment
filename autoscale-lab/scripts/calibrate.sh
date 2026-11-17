#!/usr/bin/env bash
# Capacity calibration: hold the Deployment at N replicas (default 1), raise the load from
# 1 to 10 requests/s over three minutes, and save the raw data to results/calibration_<time>/.
# Then run: python analysis/calibrate.py results/calibration_<time> <N>
# Usage: scripts/calibrate.sh [replicas]
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
N=${1:-1}

kc delete hpa workload-hpa --ignore-not-found >/dev/null
kc delete scaledobject workload-keda --ignore-not-found >/dev/null
kc delete -f k8s/policies/predictive.yaml --ignore-not-found >/dev/null
kc scale deployment/workload --replicas="$N" >/dev/null
kc rollout status deployment/workload --timeout=120s >/dev/null
for _ in $(seq 1 60); do
  [[ $(kc get pods -l app=workload --no-headers | wc -l) -eq "$N" ]] && break
  sleep 2
done
sleep 20

OUT="results/calibration_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
date +%s > "$OUT/load_start.txt"
k6 run -e PATTERN=calib --summary-export "$OUT/k6-summary.json" \
  --out "csv=$OUT/k6-raw.csv" load/k6-scenarios.js > "$OUT/k6-output.txt" 2>&1 || true
date +%s > "$OUT/load_end.txt"
echo "saved $OUT"
