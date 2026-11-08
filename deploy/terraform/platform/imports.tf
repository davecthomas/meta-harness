# What deploy/gcp.sh created before Terraform, adopted on the first apply so nothing is recreated.
# These blocks can be deleted after that apply.

locals {
  p = var.project
  r = var.region
}

import {
  to = google_compute_network.vpc
  id = "projects/${local.p}/global/networks/mh-vpc"
}

import {
  to = google_compute_subnetwork.subnet
  id = "projects/${local.p}/regions/${local.r}/subnetworks/mh-subnet"
}

import {
  to = google_compute_router.router
  id = "projects/${local.p}/regions/${local.r}/routers/mh-router"
}

import {
  to = google_compute_router_nat.nat
  id = "${local.p}/${local.r}/mh-router/mh-nat"
}

import {
  to = google_compute_firewall.iap_ssh
  id = "projects/${local.p}/global/firewalls/mh-allow-iap-ssh"
}

import {
  to = google_compute_firewall.edge
  id = "projects/${local.p}/global/firewalls/mh-allow-edge"
}

import {
  to = google_compute_address.vm
  id = "projects/${local.p}/regions/${local.r}/addresses/mh-vm-ip"
}

import {
  to = google_service_account.vm
  id = "projects/${local.p}/serviceAccounts/mh-vm-sa@${local.p}.iam.gserviceaccount.com"
}

import {
  to = google_service_account.edge
  id = "projects/${local.p}/serviceAccounts/mh-edge-sa@${local.p}.iam.gserviceaccount.com"
}

import {
  to = google_service_account.deploy
  id = "projects/${local.p}/serviceAccounts/mh-deploy-sa@${local.p}.iam.gserviceaccount.com"
}

import {
  for_each = toset(["roles/secretmanager.secretAccessor", "roles/logging.logWriter", "roles/monitoring.metricWriter"])
  to       = google_project_iam_member.vm[each.value]
  id       = "${local.p} ${each.value} serviceAccount:mh-vm-sa@${local.p}.iam.gserviceaccount.com"
}

import {
  to = google_iam_workload_identity_pool.github
  id = "projects/${local.p}/locations/global/workloadIdentityPools/github"
}

import {
  to = google_iam_workload_identity_pool_provider.github
  id = "projects/${local.p}/locations/global/workloadIdentityPools/github/providers/github"
}

import {
  to = google_project_iam_member.deploy["roles/run.admin"]
  id = "${local.p} roles/run.admin serviceAccount:mh-deploy-sa@${local.p}.iam.gserviceaccount.com"
}

import {
  to = google_artifact_registry_repository.images
  id = "projects/${local.p}/locations/${local.r}/repositories/mh"
}

import {
  for_each = toset(var.secrets)
  to       = google_secret_manager_secret.this[each.value]
  id       = "projects/${local.p}/secrets/${each.value}"
}

import {
  for_each = toset(var.operators)
  to       = google_iap_web_cloud_run_service_iam_member.operators[each.value]
  id       = "projects/${local.p}/iap_web/cloud_run-${local.r}/services/mh-edge roles/iap.httpsResourceAccessor user:${each.value}"
}
