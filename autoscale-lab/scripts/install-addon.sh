#!/usr/bin/env bash
# Install one cluster add-on from a pinned release manifest.
# Usage: scripts/install-addon.sh <metrics-server|keda|argocd> <version>
# Called by Terraform (infra/terraform/main.tf); can also be run by hand.
set -euo pipefail

ADDON=${1:?addon: metrics-server|keda|argocd}
VERSION=${2:?version}
CLUSTER=${CLUSTER:-autoscale-lab}

kind export kubeconfig --name "$CLUSTER" >/dev/null
CTX="kind-$CLUSTER"
k() { kubectl --context "$CTX" "$@"; }

case "$ADDON" in
  metrics-server)
    k apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${VERSION}/components.yaml"
    # kind kubelets use self-signed certificates
    k patch deployment metrics-server -n kube-system --type=json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
    k rollout status deployment/metrics-server -n kube-system --timeout=300s
    ;;
  keda)
    k apply --server-side -f "https://github.com/kedacore/keda/releases/download/v${VERSION}/keda-${VERSION}.yaml"
    k rollout status deployment/keda-operator -n keda --timeout=300s
    k rollout status deployment/keda-metrics-apiserver -n keda --timeout=300s
    ;;
  argocd)
    k create namespace argocd --dry-run=client -o yaml | k apply -f -
    k apply --server-side -n argocd -f "https://raw.githubusercontent.com/argoproj/argo-cd/${VERSION}/manifests/install.yaml"
    k rollout status deployment/argocd-server -n argocd --timeout=600s
    ;;
  *) echo "unknown addon $ADDON"; exit 1 ;;
esac
echo "$ADDON $VERSION installed"
