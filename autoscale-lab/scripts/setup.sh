#!/usr/bin/env bash
# One-command environment: kind cluster, metrics-server, KEDA, Prometheus, images, workload.
# Usage: scripts/setup.sh [extra terraform args]
#   e.g. scripts/setup.sh -var enable_grafana=true -var enable_argocd=true
# Prerequisites: docker, kind, kubectl, terraform, k6 (see README).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

terraform -chdir=infra/terraform init -input=false
terraform -chdir=infra/terraform apply -auto-approve -input=false "$@"

kind export kubeconfig --name "$CLUSTER"
kc apply -f k8s/base/deployment.yaml
kc rollout status deployment/workload --timeout=180s
echo "Ready. Test with: curl http://localhost:30080/health"
