#!/usr/bin/env bash
# GitOps demonstration: change the active scaling policy by committing to Git.
# Argo CD notices the commit and applies it to the cluster.
# Usage: scripts/gitops-switch.sh <hpa|keda>
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:?policy: hpa|keda}" in
  hpa)  src=k8s/policies/hpa-cpu.yaml ;;
  keda) src=k8s/policies/keda-prometheus.yaml ;;
  *) echo "unknown policy"; exit 1 ;;
esac
cp "$src" gitops/active/policy.yaml
git add gitops/active/policy.yaml
git commit -m "Switch active scaling policy to $1"
git push
