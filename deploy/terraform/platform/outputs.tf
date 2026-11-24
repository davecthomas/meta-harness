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
output "operators" {
  value     = var.operators
  sensitive = true
}
output "admins" {
  value     = var.admins
  sensitive = true
}
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
output "console_site" { value = google_firebase_hosting_site.console.site_id }
output "console_origin" { value = "https://${google_firebase_hosting_site.console.site_id}.web.app" }
output "console_home_site" { value = google_firebase_hosting_site.home.site_id }
output "console_redirect_sites" { value = [for s in google_firebase_hosting_site.redirect : s.site_id] }
# Every address the console is served at, the one people are given first. The edge takes a change from any.
# Only the home address since console_site became a redirect to it.
output "console_next_site" { value = google_firebase_hosting_site.next.site_id }
# Every address the console is served at, its home first: Muxxy's since 2026-11-24. The Octofusion address
# stays for a while after it began to redirect, so a page opened there before the change can still save.
output "console_origins" {
  value = [
    "https://${google_firebase_hosting_site.next.site_id}.web.app",
    "https://${google_firebase_hosting_site.home.site_id}.web.app",
  ]
}
output "google_client_id" { value = var.google_client_id }
output "session_key_secret" { value = google_secret_manager_secret.session_key.secret_id }
