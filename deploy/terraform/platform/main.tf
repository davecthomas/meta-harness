# The platform: what the meta-harness deployment stands on, and what changes rarely. CI applies it on
# every merge to main, before the release stack, which builds on its outputs. make platform applies it by
# hand, for a new project and for a change deploy/platform_guard.py refuses.
#
# Network: one VPC with one subnet; the VM has no public address and reaches out through Cloud NAT. SSH
# comes only through Identity-Aware Proxy, and only the subnet (the edge's Direct VPC egress) reaches
# the services' ports. Identity: a service account each for the VM, the edge, and CI's deploys. CI
# signs in through workload identity federation, limited to this repository's main branch, so GitHub
# holds no key. Secrets: the containers only; an operator adds the values (deploy/README.md), except the
# session key, which is generated here. The console: Firebase Hosting's site meta-harness, so its address
# is https://meta-harness.web.app; the release stack points the site at the edge.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  backend "gcs" {
    bucket = "meta-harness-prod-terraform"
    prefix = "platform"
  }
}

provider "google" {
  project = var.project
  # API calls count against this project, not the credentials' default quota project.
  user_project_override = true
  billing_project       = var.project
  region                = var.region
}

provider "google-beta" {
  project               = var.project
  region                = var.region
  user_project_override = true
  billing_project       = var.project
}

data "google_project" "this" {}

locals {
  project_number = data.google_project.this.number
  vm_sa          = google_service_account.vm.email
  edge_sa        = google_service_account.edge.email
  deploy_sa      = google_service_account.deploy.email
  services = [
    "compute.googleapis.com", "run.googleapis.com", "iap.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "cloudresourcemanager.googleapis.com", "logging.googleapis.com", "monitoring.googleapis.com",
    "firebase.googleapis.com", "firebasehosting.googleapis.com", "bigquery.googleapis.com",
  ]
}

resource "google_project_service" "this" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# --- Network ---------------------------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  name                    = "mh-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "mh-subnet"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = var.subnet_range
  private_ip_google_access = true
}

resource "google_compute_router" "router" {
  name    = "mh-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "mh-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "mh-allow-iap-ssh"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["mh-vm"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "edge" {
  name          = "mh-allow-edge"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = [var.subnet_range]
  target_tags   = ["mh-vm"]
  allow {
    protocol = "tcp"
    ports    = ["8301-8304"]
  }
}

# The VM's address, which the edge is told. It stays while the VM is replaced.
resource "google_compute_address" "vm" {
  name         = "mh-vm-ip"
  region       = var.region
  subnetwork   = google_compute_subnetwork.subnet.id
  address_type = "INTERNAL"
  address      = var.vm_ip
}

# --- The VM's state ---------------------------------------------------------------------------------

# Postgres data, Temporal's history and run workspaces. A deploy replaces the VM and its boot disk; this
# disk is attached to the new one.
resource "google_compute_disk" "data" {
  name = "mh-data"
  zone = var.zone
  type = "pd-balanced"
  size = var.data_disk_gb
  lifecycle {
    prevent_destroy = true
  }
}

# --- Identity --------------------------------------------------------------------------------------

resource "google_service_account" "vm" {
  account_id   = "mh-vm-sa"
  display_name = "meta-harness mh-vm-sa"
}

resource "google_service_account" "edge" {
  account_id   = "mh-edge-sa"
  display_name = "meta-harness mh-edge-sa"
}

resource "google_service_account" "deploy" {
  account_id   = "mh-deploy-sa"
  display_name = "meta-harness deploys"
}

resource "google_project_iam_member" "vm" {
  for_each = toset(["roles/secretmanager.secretAccessor", "roles/logging.logWriter", "roles/monitoring.metricWriter"])
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${local.vm_sa}"
}

# CI's deploys: workload identity federation from GitHub Actions, for this repository's main branch only.
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository=='${var.github_repo}' && assertion.ref=='refs/heads/main'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deploy_from_github" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# What a deploy may do. It applies the release stack: update or replace the VM, deploy the edge, push its
# image. The two after those let it update the running VM over IAP SSH, with sudo, through OS Login.
# It also applies this stack, so a merged change to the network, an identity, a role or a secret's
# container takes effect with no person running Terraform. That needs the roles on the second and third
# lines, and with them the deploy account can grant itself anything in the project: what stands between
# a pull request and those roles is that only main's merged commits sign in as this account, and
# deploy/platform_guard.py, which refuses a plan that destroys what the deployment cannot lose.
resource "google_project_iam_member" "deploy" {
  for_each = toset([
    "roles/compute.instanceAdmin.v1", "roles/run.admin", "roles/firebasehosting.admin",
    "roles/iap.tunnelResourceAccessor", "roles/compute.osAdminLogin",
    "roles/compute.networkAdmin", "roles/compute.securityAdmin", "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin", "roles/resourcemanager.projectIamAdmin", "roles/secretmanager.admin",
    "roles/artifactregistry.admin", "roles/serviceusage.serviceUsageAdmin", "roles/firebase.admin",
    # The billing_export dataset and who may read it.
    "roles/bigquery.dataOwner",
  ])
  project = var.project
  role    = each.value
  member  = "serviceAccount:${local.deploy_sa}"
}

resource "google_service_account_iam_member" "deploy_acts_as" {
  for_each           = { vm = google_service_account.vm.name, edge = google_service_account.edge.name }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${local.deploy_sa}"
}

resource "google_artifact_registry_repository_iam_member" "deploy_pushes" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${local.deploy_sa}"
}

# Both stacks' state, and the bucket's own grants, which this stack holds.
resource "google_storage_bucket_iam_member" "deploy_state" {
  for_each = toset(["roles/storage.objectAdmin", "roles/storage.legacyBucketReader", "roles/storage.admin"])
  bucket   = var.state_bucket
  role     = each.value
  member   = "serviceAccount:${local.deploy_sa}"
}

# --- Images and secrets ------------------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  repository_id = "mh"
  location      = var.region
  format        = "DOCKER"
}

# The containers. Values are added out of band: an operator runs `gcloud secrets versions add`, and the VM
# reads the latest version at boot. Terraform never sees a value.
resource "google_secret_manager_secret" "this" {
  for_each  = toset(var.secrets)
  secret_id = each.value
  replication {
    auto {}
  }
}

# The Google Cloud bill, which the billing account's standard usage cost export writes here daily. Turning the
# export on is the billing account's setting, made once in the console (Billing, Billing export); Google then
# creates the table gcp_billing_export_v1_<billing account> in this dataset. The VM's cost service reads it
# (src/meta_harness/costs/cloud_source.py): it lists the dataset's tables and runs queries.
resource "google_bigquery_dataset" "billing_export" {
  dataset_id  = "billing_export"
  location    = "US"
  description = "Google Cloud billing export, read by the meta-harness cost service"
  depends_on  = [google_project_service.this]
}

resource "google_bigquery_dataset_iam_member" "vm_reads_billing" {
  dataset_id = google_bigquery_dataset.billing_export.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${local.vm_sa}"
}

resource "google_project_iam_member" "vm_runs_queries" {
  project = var.project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${local.vm_sa}"
}

# The edge reads the VM's and its own logs for the console's Logs page (GET /v1/admin/logs).
resource "google_project_iam_member" "edge_reads_logs" {
  project = var.project
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${local.edge_sa}"
}

# The edge reads the service key to call the control plane, and the session key to sign sessions.
resource "google_secret_manager_secret_iam_member" "edge_service_key" {
  secret_id = google_secret_manager_secret.this["MH_SERVICE_KEY"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.edge_sa}"
}

# The key the edge signs console sessions with. Generated here, so it is in this stack's state, which
# only the state bucket's readers can read. A new key (terraform apply -replace=random_password.session_key)
# signs everyone out.
resource "random_password" "session_key" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "session_key" {
  secret_id = "MH_SESSION_KEY"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "session_key" {
  secret      = google_secret_manager_secret.session_key.id
  secret_data = random_password.session_key.result
}

resource "google_secret_manager_secret_iam_member" "edge_session_key" {
  secret_id = google_secret_manager_secret.session_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.edge_sa}"
}

# --- The console's address ---------------------------------------------------------------------------

# Firebase on the project, for Hosting only: the site's address is https://<site_id>.web.app.
resource "google_firebase_project" "this" {
  provider   = google-beta
  project    = var.project
  depends_on = [google_project_service.this]
}

resource "google_firebase_hosting_site" "console" {
  provider   = google-beta
  project    = var.project
  site_id    = var.console_site
  depends_on = [google_firebase_project.this]
}
