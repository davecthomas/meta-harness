output "commit" { value = var.commit }
output "console_url" { value = local.platform.console_origin }
output "edge_url" { value = google_cloud_run_v2_service.edge.uri }
output "vm" { value = google_compute_instance.vm.name }
output "zone" { value = google_compute_instance.vm.zone }
