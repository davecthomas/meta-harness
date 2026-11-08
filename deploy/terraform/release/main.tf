# A release: the commit the meta-harness runs. CI applies this stack after every merge to main
# (.github/workflows/deploy.yml) with the merged commit and the edge image it built from it.
#
# The VM is replaced, never changed in place: every apply passes -replace=google_compute_instance.vm, so
# the commit gets a new VM, whose startup script checks it out and runs deploy/vm/boot.sh. The data disk, from the platform stack, carries Postgres,
# Temporal's history and run workspaces across. The edge gets a new Cloud Run revision from the image.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
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
  name                = local.edge
  location            = local.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = true
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
          MH_PLANNER_URL       = "http://${local.vm_ip}:8303"
          MH_TASKS_URL         = "http://${local.vm_ip}:8302"
          MH_EVENTS_URL        = "http://${local.vm_ip}:8301"
          MH_SCHEDULER_URL     = "http://${local.vm_ip}:8304"
          MH_EDGE_IAP_AUDIENCE = "/projects/${local.platform.project_number}/locations/${local.region}/services/${local.edge}"
          MH_CONSOLE_OPERATORS = join(",", local.platform.operators)
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "MH_SERVICE_KEY"
        value_source {
          secret_key_ref {
            secret  = "MH_SERVICE_KEY"
            version = "latest"
          }
        }
      }
    }
  }
}

# Identity-Aware Proxy calls the edge on the operators' behalf.
resource "google_cloud_run_v2_service_iam_member" "iap_invokes" {
  name     = google_cloud_run_v2_service.edge.name
  location = local.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${local.platform.project_number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# Adopted from deploy/gcp.sh on the first apply; these blocks can be deleted after it.
import {
  to = google_compute_instance.vm
  id = "projects/${local.project}/zones/${local.platform.zone}/instances/mh-vm"
}

import {
  to = google_cloud_run_v2_service.edge
  id = "projects/${local.project}/locations/${local.region}/services/mh-edge"
}

import {
  to = google_cloud_run_v2_service_iam_member.iap_invokes
  id = "projects/${local.project}/locations/${local.region}/services/mh-edge roles/run.invoker serviceAccount:service-${local.platform.project_number}@gcp-sa-iap.iam.gserviceaccount.com"
}
