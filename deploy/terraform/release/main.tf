# A release: the commit the meta-harness runs. CI applies this stack after every merge to main
# (.github/workflows/deploy.yml) with the merged commit and the edge image it built from it.
#
# The VM is replaced, never changed in place: every apply passes -replace=google_compute_instance.vm, so
# the commit gets a new VM, whose startup script checks it out and runs deploy/vm/boot.sh. The data
# disk, from the platform stack, carries Postgres, Temporal's history and run workspaces across. The
# edge gets a new Cloud Run revision from the image.
#
# The console's address is the platform stack's Firebase Hosting site, https://meta-harness.web.app,
# which forwards every request to the edge. The edge signs operators in with Google itself, so the
# service is open to the internet at the Cloud Run level, and every /v1 request needs its session.

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
  }
  backend "gcs" {
    bucket = "meta-harness-prod-terraform"
    prefix = "release"
  }
}

data "terraform_remote_state" "platform" {
  backend = "gcs"
  config = {
    bucket = "meta-harness-prod-terraform"
    prefix = "platform"
  }
}

locals {
  platform = data.terraform_remote_state.platform.outputs
  project  = local.platform.project
  region   = local.platform.region
  vm_ip    = local.platform.vm_ip
  edge     = "mh-edge"
}

provider "google" {
  project = local.project
  # API calls count against this project, not the credentials' default quota project.
  user_project_override = true
  billing_project       = local.project
  region                = local.region
}

provider "google-beta" {
  project               = local.project
  region                = local.region
  user_project_override = true
  billing_project       = local.project
}

# --- The VM ----------------------------------------------------------------------------------------

resource "google_compute_instance" "vm" {
  name         = "mh-vm"
  zone         = local.platform.zone
  machine_type = var.machine_type
  tags         = ["mh-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 50
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = local.platform.data_disk
    device_name = "data"
  }

  network_interface {
    subnetwork = local.platform.subnet
    network_ip = local.vm_ip
  }

  service_account {
    email  = local.platform.vm_service_account
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot = true
  }

  metadata = {
    mh-ref                  = var.commit
    enable-guest-attributes = "TRUE"
    startup-script          = file("${path.module}/../../vm/startup.sh")
  }
}

# --- The edge --------------------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "edge" {
  name     = local.edge
  location = local.region
  ingress  = "INGRESS_TRAFFIC_ALL"
  # Firebase Hosting forwards requests without credentials, so neither IAP nor Cloud Run's own sign-in
  # can sit in front; the edge's Google sign-in does.
  iap_enabled         = false
  deletion_protection = false

  template {
    service_account = local.platform.edge_service_account
    timeout         = "3600s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        # Cloud Run keeps the names, not the full paths; full paths would differ at every plan.
        network    = basename(local.platform.network)
        subnetwork = basename(local.platform.subnet)
      }
    }
    containers {
      image = var.edge_image
      # As Cloud Run stores them, so a plan shows no difference: CPU in millicores, idle between requests.
      resources {
        limits   = { memory = "1Gi", cpu = "1000m" }
        cpu_idle = true
      }
      dynamic "env" {
        for_each = {
          MH_PLANNER_URL      = "http://${local.vm_ip}:8303"
          MH_TASKS_URL        = "http://${local.vm_ip}:8302"
          MH_EVENTS_URL       = "http://${local.vm_ip}:8301"
          MH_SCHEDULER_URL    = "http://${local.vm_ip}:8304"
          MH_GOOGLE_CLIENT_ID = local.platform.google_client_id
          MH_CONSOLE_ORIGIN   = local.platform.console_origin
          # The edge's own address, which the live event stream uses (Firebase Hosting ends requests at 60s).
          MH_STREAM_ORIGIN     = "https://${local.edge}-${local.platform.project_number}.${local.region}.run.app"
          MH_CONSOLE_OPERATORS = join(",", local.platform.operators)
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = { MH_SERVICE_KEY = "MH_SERVICE_KEY", MH_SESSION_KEY = local.platform.session_key_secret }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }
}

# Firebase Hosting calls the edge without credentials.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.edge.name
  location = local.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# The console's address: every path on the Hosting site goes to the edge.
resource "google_firebase_hosting_version" "console" {
  provider = google-beta
  site_id  = local.platform.console_site
  config {
    rewrites {
      glob = "**"
      run {
        service_id = google_cloud_run_v2_service.edge.name
        region     = local.region
      }
    }
  }
}

resource "google_firebase_hosting_release" "console" {
  provider     = google-beta
  site_id      = local.platform.console_site
  version_name = google_firebase_hosting_version.console.name
  message      = "every path to the Cloud Run service ${local.edge}"
}
