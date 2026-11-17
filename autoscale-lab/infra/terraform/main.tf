terraform {
  required_version = ">= 1.6"
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "0.13.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
  }
}

provider "kind" {}

provider "helm" {
  kubernetes = {
    host                   = kind_cluster.lab.endpoint
    cluster_ca_certificate = kind_cluster.lab.cluster_ca_certificate
    client_certificate     = kind_cluster.lab.client_certificate
    client_key             = kind_cluster.lab.client_key
  }
}

# ---------------------------------------------------------------- cluster

resource "kind_cluster" "lab" {
  name            = var.cluster_name
  wait_for_ready  = true
  kubeconfig_path = "${path.module}/kubeconfig.yaml"

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
      extra_port_mappings {
        container_port = 30080
        host_port      = 30080
        protocol       = "TCP"
      }
      extra_port_mappings {
        container_port = 30300
        host_port      = 3000
        protocol       = "TCP"
      }
    }

    dynamic "node" {
      for_each = range(var.worker_nodes)
      content {
        role = "worker"
      }
    }
  }
}

# ---------------------------------------------------------------- add-ons from pinned manifests
# The Helm repositories hosted on github.io are not reachable from the build network, so
# these three components are installed from pinned release manifests instead of charts.

resource "terraform_data" "metrics_server" {
  depends_on       = [kind_cluster.lab]
  triggers_replace = [var.metrics_server_version]
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "bash ${path.module}/../../scripts/install-addon.sh metrics-server ${var.metrics_server_version}"
  }
}

resource "terraform_data" "keda" {
  depends_on       = [kind_cluster.lab]
  triggers_replace = [var.keda_version]
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "bash ${path.module}/../../scripts/install-addon.sh keda ${var.keda_version}"
  }
}

resource "terraform_data" "argocd" {
  count            = var.enable_argocd ? 1 : 0
  depends_on       = [kind_cluster.lab]
  triggers_replace = [var.argocd_version]
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "bash ${path.module}/../../scripts/install-addon.sh argocd ${var.argocd_version}"
  }
}

# ---------------------------------------------------------------- container images

resource "terraform_data" "images" {
  depends_on       = [kind_cluster.lab]
  triggers_replace = [filesha256("${path.module}/../../app/app.py"), filesha256("${path.module}/../../predictor/predictor.py")]
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "bash ${path.module}/../../scripts/load-images.sh ${var.cluster_name}"
  }
}

# ---------------------------------------------------------------- monitoring (Helm, OCI charts)

resource "helm_release" "prometheus" {
  name             = "prometheus"
  namespace        = "monitoring"
  create_namespace = true
  repository       = "oci://ghcr.io/prometheus-community/charts"
  chart            = "prometheus"
  version          = var.prometheus_chart_version
  values           = [file("${path.module}/../../monitoring/prometheus-values.yaml")]
  timeout          = 600
  depends_on       = [kind_cluster.lab]
}

resource "helm_release" "grafana" {
  count            = var.enable_grafana ? 1 : 0
  name             = "grafana"
  namespace        = "monitoring"
  create_namespace = true
  repository       = "oci://ghcr.io/grafana/helm-charts"
  chart            = "grafana"
  version          = var.grafana_chart_version
  timeout          = 600
  values = [
    file("${path.module}/../../monitoring/grafana-values.yaml"),
    yamlencode({
      dashboards = {
        default = {
          autoscale = {
            json = file("${path.module}/../../dashboards/autoscale-lab.json")
          }
        }
      }
    }),
  ]
  depends_on = [helm_release.prometheus]
}
