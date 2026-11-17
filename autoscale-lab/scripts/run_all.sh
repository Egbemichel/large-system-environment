#!/usr/bin/env bash
# Full experiment matrix: 4 policies x (3 load patterns + 1 chaos run on the spike pattern),
# repeated REPS times. Within each repeat the order of the 16 configurations is shuffled
# with a fixed seed, so slow drift on the machine is spread across policies instead of
# favouring whichever policy ran last. Finished runs are skipped, so the script can be
# stopped and started again.
# Usage: scripts/run_all.sh        (REPS=3 SEED=2026 by default)
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

REPS=${REPS:-3}
SEED=${SEED:-2026}
POLICIES=(fixed hpa keda predictive)
CONFIGS=()
for pol in "${POLICIES[@]}"; do
  for pat in steady ramp spike; do CONFIGS+=("$pol $pat"); done
  CONFIGS+=("$pol spike chaos")
done

shuffle() {   # Fisher-Yates with a seeded generator; prints the shuffled indices
  local n=$1 i j t; local -a a
  for ((i = 0; i < n; i++)); do a[i]=$i; done
  RANDOM=$2
  for ((i = n - 1; i > 0; i--)); do
    j=$((RANDOM % (i + 1))); t=${a[i]}; a[i]=${a[j]}; a[j]=$t
  done
  echo "${a[@]}"
}

for rep in $(seq 1 "$REPS"); do
  order=$(shuffle "${#CONFIGS[@]}" $((SEED + rep)))
  echo "=== repeat $rep, order: $order"
  for idx in $order; do
    cfg=${CONFIGS[$idx]}
    tag=${cfg// /_}; tag=${tag/ /_}
    # skip if a finished run of this configuration and repeat already exists
    if compgen -G "results/${tag}_r${rep}_*/k6-summary.json" >/dev/null; then
      echo "skip $cfg (repeat $rep already done)"; continue
    fi
    # shellcheck disable=SC2086
    REP=$rep scripts/run_experiment.sh $cfg
  done
done

"${PYTHON:-python}" analysis/analyze.py results
