#!/usr/bin/env bash
# Build the workload and predictor images and load them into the kind cluster.
# Usage: scripts/load-images.sh [cluster-name]
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER=${1:-autoscale-lab}

docker build -q -t workload:latest app/
docker build -q -t predictor:latest predictor/
kind load docker-image workload:latest predictor:latest --name "$CLUSTER"
echo "images loaded into $CLUSTER"
