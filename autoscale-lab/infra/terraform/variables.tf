variable "cluster_name" {
  type    = string
  default = "autoscale-lab"
}

variable "worker_nodes" {
  description = "Number of worker nodes. Two is the design value; use one on a small machine."
  type        = number
  default     = 2
}

variable "enable_grafana" {
  description = "Install Grafana (demonstration only, left off during measured runs)."
  type        = bool
  default     = false
}

variable "enable_argocd" {
  description = "Install Argo CD for the GitOps demonstration (left off during measured runs)."
  type        = bool
  default     = false
}

variable "metrics_server_version" {
  type    = string
  default = "v0.9.1"
}

variable "keda_version" {
  type    = string
  default = "2.21.1"
}

variable "argocd_version" {
  type    = string
  default = "v3.6.1"
}

variable "prometheus_chart_version" {
  type    = string
  default = "29.47.2"
}

variable "grafana_chart_version" {
  type    = string
  default = "10.5.15"
}
