#!/usr/bin/env bash
# Shared environment for the lab scripts (sourced, not executed).
# Adds the locations where winget installs kind, terraform and k6 on Windows, and stops
# Git Bash from rewriting arguments that look like Unix paths.
export MSYS_NO_PATHCONV=1
if command -v cygpath >/dev/null 2>&1 && [[ -n "${LOCALAPPDATA:-}" ]]; then
  export PATH="$PATH:$(cygpath "$LOCALAPPDATA")/Microsoft/WinGet/Links:/c/Program Files/k6"
fi
CLUSTER=${CLUSTER:-autoscale-lab}
export CLUSTER
export KUBECTL_CONTEXT="kind-$CLUSTER"
kc() { kubectl --context "$KUBECTL_CONTEXT" "$@"; }
