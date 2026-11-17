# Autoscale Lab: evaluating autoscaling policies on Kubernetes

Course project, Large Systems Environment, Fall 2026 (track 4: Container Orchestration and Auto-Scaling System Evaluation).

**Research question.** Which scaling policy gives the best latency, recovery time and resource cost under load and under failure: a fixed replica count, a CPU-based HPA, a request-rate policy (KEDA with Prometheus), or a predictive policy that scales from a forecast of the request rate?

## Layout

```
app/                  CPU-burning API (/work, /health, /metrics) and its Dockerfile
predictor/            predictive autoscaler (forecasts the request rate, sets replicas)
k8s/base/             Deployment and Service of the workload
k8s/policies/         hpa-cpu.yaml, keda-prometheus.yaml, predictive.yaml
infra/terraform/      kind cluster, metrics-server, KEDA, Prometheus (and Grafana, Argo CD)
monitoring/           Prometheus and Grafana values
dashboards/           Grafana dashboard
gitops/               Argo CD applications and the active policy folder
load/                 k6 load patterns: steady, ramp, spike, calib
scripts/              setup, calibration, chaos, single experiment, full matrix
analysis/             analyze.py (tables and figures), calibrate.py
results/              created by the experiments
```

## Prerequisites

Docker Desktop (about 8 GB available to it), `kind`, `kubectl`, `terraform`, `k6`, Python 3 with `matplotlib`, and a bash shell (Git Bash on Windows). Versions used: kind 0.33.0, Terraform 1.17.1, k6 2.4.0.

## Quick start

```bash
scripts/setup.sh                      # cluster, metrics-server, KEDA, Prometheus, images, workload
curl http://localhost:30080/work?ms=100

scripts/calibrate.sh 1                # capacity of one pod
python analysis/calibrate.py results/calibration_<timestamp> 1

scripts/run_experiment.sh hpa spike           # one experiment: policy, pattern
scripts/run_experiment.sh hpa spike chaos     # same, killing a pod three times

scripts/run_all.sh                    # whole matrix (4 policies x 4 runs x 3 repeats), then analysis
python analysis/analyze.py results
```

For the demonstration phase add Grafana (http://localhost:3000, user `admin`, password `autoscale`) and Argo CD:

```bash
scripts/setup.sh -var enable_grafana=true -var enable_argocd=true
```

## Experiment design

| Policy | Signal | Replicas | Target |
|---|---|---|---|
| fixed | none | 4 | none |
| hpa | average CPU utilization | 2 to 12 | 60% of 500m (about 3 requests/s per pod) |
| keda | `sum(rate(app_requests_total[15s]))` | 2 to 12 | 3 requests/s per pod |
| predictive | forecast of the same rate, 20 s ahead | 2 to 12 | 3 requests/s per pod |

All policies share the same actuation limits: scale up by the larger of 100% or 4 pods per 15 s; scale down after a 60 s window by at most 2 pods per 30 s.

| Pattern | Profile |
|---|---|
| steady | 12 requests/s for 3 minutes |
| ramp | 2 to 30 requests/s over 2.5 minutes, hold 30 s, back to 2 over 2 minutes |
| spike | 5 requests/s for 30 s, jump to 36 in 10 s, hold 2 minutes, drop to 5, 1.5 minutes of recovery |

A chaos run is the spike pattern with one pod killed 60 s in, 3 times, 40 s apart. Each run: reset, apply policy, settle 30 s, run load, observe 90 s. Three repeats of every configuration, run in a seeded random order.

## Measured

p50, p95 and p99 latency, error rate, dropped iterations, share of 10 s windows meeting a 2 s p95 objective, scale-up delay, time to peak, scale-down time, replica-seconds (cost), and recovery time after a pod kill. Means with 95% confidence intervals over the repeats are written to `results/summary.csv` and `results/summary_table.md`; figures go to `results/plots/`.

## Troubleshooting

- `kubectl top pods` shows nothing: wait a minute for metrics-server.
- HPA shows `<unknown>`: the Deployment needs CPU requests (it has them) and metrics-server must be ready.
- KEDA shows no metric: check `kubectl get scaledobject` and that Prometheus is running in the `monitoring` namespace.
- k6 and the cluster share the same machine; keep the setup identical for every run and treat absolute latencies with that in mind.
