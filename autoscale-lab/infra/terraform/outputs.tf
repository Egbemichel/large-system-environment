output "cluster_name" {
  value = kind_cluster.lab.name
}

output "kubeconfig_path" {
  value = kind_cluster.lab.kubeconfig_path
}
