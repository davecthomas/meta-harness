#!/usr/bin/env bash
# Deploys the meta-harness into its Google Cloud project. Every step creates what is missing and leaves
# what exists, so any command can run again.
#
#   deploy/gcp.sh infra    network, NAT, firewall, service accounts, secrets, and the VM
#   deploy/gcp.sh edge     build the edge image and deploy it to Cloud Run behind Identity-Aware Proxy
#   deploy/gcp.sh update   point the VM at REF and rerun its boot script (pull code, restart services)
#   deploy/gcp.sh status   the VM's services and the edge's URL
#
# Settings: PROJECT (meta-harness-prod), REGION (us-central1), ZONE (us-central1-a), REF (main, the
# meta-harness branch or commit the VM runs), OPERATORS (the console's access list, comma-separated).
# Provider keys come from the repo's .env once, into Secret Manager; no secret is printed.
#
# The shape (docs/technical-design.md, Deployment without GKE): one VM with no public address runs the
# control plane, Temporal, the HTTP Gateway, the run worker and its sandboxes; it reaches out through
# Cloud NAT. The edge, on Cloud Run with IAP, serves the console and routes /v1 to the VM's private
# address over Direct VPC egress.

set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${PROJECT:-meta-harness-prod}
REGION=${REGION:-us-central1}
ZONE=${ZONE:-us-central1-a}
REF=${REF:-main}
OPERATORS=${OPERATORS:-davidcthomas@gmail.com}
NETWORK=mh-vpc
SUBNET=mh-subnet
SUBNET_RANGE=10.10.0.0/24
VM=mh-vm
VM_IP=10.10.0.10
# 8 vCPU and 32 GB: the control plane, plus MH_MAX_RUNNING (2) per-run sandboxes at their profiles'
# 2 vCPU and 6 GB each, while each one installs and tests a Python repo.
VM_TYPE=${VM_TYPE:-e2-standard-8}
# Docker images, each run's workspace and virtualenvs, Postgres and Temporal's history.
VM_DISK_GB=${VM_DISK_GB:-100}
EDGE=mh-edge
REPO=mh
# This deployment's data, from the local checkout: the repo registry, and the GitHub App for each repo
# owner (MH_GITHUB_APPS in .env, owner=app_id:key_file pairs). Neither is in the code.
REPOS_CONFIG=${REPOS_CONFIG:-config/repos.local.json}

g() { gcloud --project "$PROJECT" --quiet "$@"; }
log() { echo "[deploy] $*"; }
exists() { "$@" >/dev/null 2>&1; }
number() { gcloud projects describe "$PROJECT" --format='value(projectNumber)'; }

# secret NAME SOURCE: creates the secret from SOURCE's output when it does not exist yet.
secret() {
  local name=$1
  shift
  if exists g secrets describe "$name"; then
    log "secret $name exists"
    return
  fi
  "$@" | g secrets create "$name" --replication-policy=automatic --data-file=- >/dev/null
  log "secret $name created"
}
random_value() { openssl rand -hex 24 | tr -d '\n'; }
from_env_file() { grep -E "^$1=" .env | head -n 1 | cut -d= -f2- | tr -d '\n'; }

# resize_vm: brings an existing VM to VM_TYPE and VM_DISK_GB. A machine type changes only while the VM is
# stopped, so a running VM of another type is left as it is, with the command that changes it. The boot
# disk grows in place; Debian grows its root file system at the next boot.
resize_vm() {
  local current status disk_gb
  current=$(g compute instances describe "$VM" --zone "$ZONE" --format='value(machineType.basename())')
  status=$(g compute instances describe "$VM" --zone "$ZONE" --format='value(status)')
  if [ "$current" != "$VM_TYPE" ]; then
    if [ "$status" = TERMINATED ]; then
      g compute instances set-machine-type "$VM" --zone "$ZONE" --machine-type "$VM_TYPE" >/dev/null
      log "VM $VM changed from $current to $VM_TYPE"
    else
      log "VM $VM is $current and $status; stop it, then run deploy/gcp.sh infra again to make it $VM_TYPE"
    fi
  fi
  disk_gb=$(g compute disks describe "$VM" --zone "$ZONE" --format='value(sizeGb)')
  if [ "$disk_gb" -lt "$VM_DISK_GB" ]; then
    g compute disks resize "$VM" --zone "$ZONE" --size "${VM_DISK_GB}GB" >/dev/null
    log "VM $VM boot disk grown from ${disk_gb} GB to ${VM_DISK_GB} GB"
  fi
}

cmd_infra() {
  log "network $NETWORK, subnet $SUBNET ($SUBNET_RANGE), Cloud NAT"
  exists g compute networks describe "$NETWORK" || g compute networks create "$NETWORK" --subnet-mode=custom
  exists g compute networks subnets describe "$SUBNET" --region "$REGION" ||
    g compute networks subnets create "$SUBNET" --network "$NETWORK" --region "$REGION" --range "$SUBNET_RANGE" \
      --enable-private-ip-google-access
  exists g compute routers describe mh-router --region "$REGION" ||
    g compute routers create mh-router --network "$NETWORK" --region "$REGION"
  exists g compute routers nats describe mh-nat --router mh-router --region "$REGION" ||
    g compute routers nats create mh-nat --router mh-router --region "$REGION" \
      --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges

  log "firewall: SSH only through IAP, and the edge's subnet to the services' ports"
  exists g compute firewall-rules describe mh-allow-iap-ssh ||
    g compute firewall-rules create mh-allow-iap-ssh --network "$NETWORK" --direction INGRESS \
      --source-ranges 35.235.240.0/20 --allow tcp:22 --target-tags mh-vm
  exists g compute firewall-rules describe mh-allow-edge ||
    g compute firewall-rules create mh-allow-edge --network "$NETWORK" --direction INGRESS \
      --source-ranges "$SUBNET_RANGE" --allow tcp:8301-8304 --target-tags mh-vm

  log "service accounts"
  for sa in mh-vm-sa mh-edge-sa; do
    exists g iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" ||
      g iam service-accounts create "$sa" --display-name "meta-harness $sa"
  done
  for role in roles/secretmanager.secretAccessor roles/logging.logWriter roles/monitoring.metricWriter; do
    g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:mh-vm-sa@$PROJECT.iam.gserviceaccount.com" \
      --role "$role" --condition=None >/dev/null
  done

  log "secrets"
  secret MH_POSTGRES_PASSWORD random_value
  secret MH_SERVICE_KEY random_value
  secret MH_GATEWAY_API_KEY random_value
  secret ANTHROPIC_API_KEY from_env_file ANTHROPIC_API_KEY
  secret OPENAI_API_KEY from_env_file OPENAI_API_KEY
  secret GOOGLE_GEMINI_API_KEY from_env_file GOOGLE_GEMINI_API_KEY
  [ -f "$REPOS_CONFIG" ] || { log "no repo registry at $REPOS_CONFIG; copy config/repos.example.json and list the repos"; exit 1; }
  secret MH_REPOS_CONFIG cat "$REPOS_CONFIG"
  local pairs= owner app_id key_file
  for entry in $(from_env_file MH_GITHUB_APPS | tr ',' ' '); do
    owner=${entry%%=*}
    app_id=${entry#*=}
    key_file=${app_id#*:}
    app_id=${app_id%%:*}
    key_file=${key_file/#\~/$HOME}
    secret "MH_GITHUB_APP_KEY_$owner" cat "$key_file"
    pairs="$pairs${pairs:+,}$owner=$app_id"
  done
  [ -n "$pairs" ] || { log "MH_GITHUB_APPS is not set in .env; the VM would have no GitHub App"; exit 1; }
  secret MH_GITHUB_APPS printf '%s' "$pairs"
  g secrets add-iam-policy-binding MH_SERVICE_KEY --member "serviceAccount:mh-edge-sa@$PROJECT.iam.gserviceaccount.com" \
    --role roles/secretmanager.secretAccessor >/dev/null

  log "VM $VM ($VM_TYPE) at $VM_IP, no public address"
  exists g compute addresses describe mh-vm-ip --region "$REGION" ||
    g compute addresses create mh-vm-ip --region "$REGION" --subnet "$SUBNET" --addresses "$VM_IP"
  if ! exists g compute instances describe "$VM" --zone "$ZONE"; then
    g compute instances create "$VM" --zone "$ZONE" --machine-type "$VM_TYPE" \
      --subnet "$SUBNET" --private-network-ip "$VM_IP" --no-address --tags mh-vm \
      --service-account "mh-vm-sa@$PROJECT.iam.gserviceaccount.com" --scopes cloud-platform \
      --image-family debian-12 --image-project debian-cloud --boot-disk-size "${VM_DISK_GB}GB" --boot-disk-type pd-balanced \
      --shielded-secure-boot --metadata "mh-ref=$REF" --metadata-from-file startup-script=deploy/vm/startup.sh
  else
    resize_vm
  fi
  log "the VM runs deploy/vm/boot.sh at boot; its log is /var/log/mh-boot.log (deploy/gcp.sh status)"
}

cmd_edge() {
  local image tag num
  num=$(number)
  tag=$(git rev-parse --short HEAD)
  image="$REGION-docker.pkg.dev/$PROJECT/$REPO/edge:$tag"
  log "Artifact Registry $REPO, and Cloud Build's service account"
  exists g artifacts repositories describe "$REPO" --location "$REGION" ||
    g artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
  for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
    g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$num-compute@developer.gserviceaccount.com" \
      --role "$role" --condition=None >/dev/null
  done
  log "building $image"
  g builds submit --region "$REGION" --config deploy/cloudbuild-edge.yaml --substitutions "_IMAGE=$image" . >/dev/null
  log "deploying $EDGE behind Identity-Aware Proxy"
  g run deploy "$EDGE" --region "$REGION" --image "$image" \
    --service-account "mh-edge-sa@$PROJECT.iam.gserviceaccount.com" \
    --network "$NETWORK" --subnet "$SUBNET" --vpc-egress private-ranges-only \
    --set-env-vars "^;^MH_PLANNER_URL=http://$VM_IP:8303;MH_TASKS_URL=http://$VM_IP:8302;MH_EVENTS_URL=http://$VM_IP:8301;MH_SCHEDULER_URL=http://$VM_IP:8304;MH_EDGE_IAP_AUDIENCE=/projects/$num/locations/$REGION/services/$EDGE;MH_CONSOLE_OPERATORS=$OPERATORS" \
    --set-secrets MH_SERVICE_KEY=MH_SERVICE_KEY:latest \
    --timeout 3600 --memory 1Gi --min-instances 0 --max-instances 2 \
    --no-allow-unauthenticated --iap >/dev/null
  log "IAP may call the edge, and the operators may pass IAP"
  exists g beta services identity create --service iap.googleapis.com
  g run services add-iam-policy-binding "$EDGE" --region "$REGION" \
    --member "serviceAccount:service-$num@gcp-sa-iap.iam.gserviceaccount.com" --role roles/run.invoker >/dev/null
  IFS=, read -ra people <<<"$OPERATORS"
  for person in "${people[@]}"; do
    g iap web add-iam-policy-binding --resource-type cloud-run --service "$EDGE" --region "$REGION" \
      --member "user:$person" --role roles/iap.httpsResourceAccessor >/dev/null
  done
  log "the console: $(g run services describe "$EDGE" --region "$REGION" --format='value(status.url)')"
}

cmd_update() {
  g compute instances add-metadata "$VM" --zone "$ZONE" --metadata "mh-ref=$REF"
  g compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --command "sudo bash /opt/meta-harness/deploy/vm/boot.sh $REF 2>&1 | tail -n 5"
}

cmd_status() {
  g compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --command \
    "systemctl is-active mh-postgres mh-temporal mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker | paste -sd' '; sudo tail -n 3 /var/log/mh-boot.log"
  log "the console: $(g run services describe "$EDGE" --region "$REGION" --format='value(status.url)' 2>/dev/null || echo 'not deployed')"
}

case "${1:-}" in
  infra) cmd_infra ;;
  edge) cmd_edge ;;
  update) cmd_update ;;
  status) cmd_status ;;
  *) echo "usage: deploy/gcp.sh infra|edge|update|status" >&2; exit 2 ;;
esac
