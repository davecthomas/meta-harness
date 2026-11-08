# What the release stack builds on (terraform_remote_state).
output "project" { value = var.project }
output "project_number" { value = local.project_number }
output "region" { value = var.region }
output "zone" { value = var.zone }
output "network" { value = google_compute_network.vpc.id }
output "subnet" { value = google_compute_subnetwork.subnet.id }
output "vm_ip" { value = google_compute_address.vm.address }
output "data_disk" { value = google_compute_disk.data.id }
output "vm_service_account" { value = local.vm_sa }
output "edge_service_account" { value = local.edge_sa }
output "deploy_service_account" { value = local.deploy_sa }
output "image_repository" { value = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.images.repository_id}" }
output "operators" { value = var.operators }
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
