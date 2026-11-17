#!/usr/bin/env bash
# Chaos: delete random workload pods at intervals while load is running.
# Usage: scripts/chaos.sh <delay_seconds_before_first_kill> <pods_per_kill> <number_of_kills> <interval_seconds>
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
DELAY=${1:-60}
COUNT=${2:-1}
ROUNDS=${3:-3}
INTERVAL=${4:-40}

sleep "$DELAY"
for i in $(seq 1 "$ROUNDS"); do
  victims=$(kc get pods -l app=workload --field-selector=status.phase=Running \
    -o name | shuf -n "$COUNT")
  echo "$(date +%s) CHAOS round $i killing: $victims"
  # shellcheck disable=SC2086
  kc delete $victims --wait=false
  sleep "$INTERVAL"
done
