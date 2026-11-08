#!/usr/bin/env bash
# Deploys the meta-harness into its Google Cloud project. Every step creates what is missing and leaves
# what exists, so any command can run again.
#
#   deploy/gcp.sh infra    network, NAT, firewall, service accounts, secrets, the VM, the edge's access,
#                          and the deploy access GitHub Actions uses (run once, and after changing these)
#   deploy/gcp.sh deploy   vm, then edge: what CI runs after every merge to main (make deploy)
#   deploy/gcp.sh vm       deploy REF to the VM and wait for its result (no SSH; see deploy/vm/deployer.sh)
#   deploy/gcp.sh edge     build the edge image from the checkout and deploy it to Cloud Run behind IAP
#   deploy/gcp.sh status   the VM's last deploy and services, and the edge's URL
#
# Settings: PROJECT (meta-harness-prod), REGION (us-central1), ZONE (us-central1-a), REF (main, the
# meta-harness branch, tag or commit to deploy), OPERATORS (the console's access list, comma-separated).
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
# GitHub Actions deploys as mh-deploy-sa, through workload identity federation, from main only.
GITHUB_REPO=davecthomas/meta-harness
DEPLOY_SA=mh-deploy-sa
# How long a VM deploy may take: packages on a fresh VM, both repos' dependencies, the harness image.
VM_DEPLOY_MINUTES=${VM_DEPLOY_MINUTES:-30}

g() { gcloud --project "$PROJECT" --quiet "$@"; }
log() { echo "[deploy] $*"; }
exists() { "$@" >/dev/null 2>&1; }
# retry COMMAND: a service account created a moment ago takes up to a minute or two to be visible to
# every service's IAM, so a grant to it is tried for three minutes.
retry() {
  local i
  for i in $(seq 1 18); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 10
  done
  "$@" >/dev/null
}
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
  edge_access
  deploy_access
  log "the VM runs deploy/vm/boot.sh at boot; its log is /var/log/mh-boot.log (deploy/gcp.sh status)"
}

# edge_access: the Artifact Registry repository, what Cloud Build's account may do, and who may call the
# edge through IAP. The edge's first deploy needs it; IAP's grants need the service, so a first infra run
# on a new project grants them on the next run, after deploy/gcp.sh edge.
edge_access() {
  local num
  num=$(number)
  log "Artifact Registry $REPO, and Cloud Build's service account"
  exists g artifacts repositories describe "$REPO" --location "$REGION" ||
    g artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
  for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
    g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$num-compute@developer.gserviceaccount.com" \
      --role "$role" --condition=None >/dev/null
  done
  if ! exists g run services describe "$EDGE" --region "$REGION"; then
    log "the edge is not deployed yet: run deploy/gcp.sh edge, then infra again for its IAP grants"
    return
  fi
  log "IAP may call the edge, and the operators may pass IAP"
  exists g beta services identity create --service iap.googleapis.com
  g run services add-iam-policy-binding "$EDGE" --region "$REGION" \
    --member "serviceAccount:service-$num@gcp-sa-iap.iam.gserviceaccount.com" --role roles/run.invoker >/dev/null
  IFS=, read -ra people <<<"$OPERATORS"
  for person in "${people[@]}"; do
    g iap web add-iam-policy-binding --resource-type cloud-run --service "$EDGE" --region "$REGION" \
      --member "user:$person" --role roles/iap.httpsResourceAccessor >/dev/null
  done
}

# deploy_access: GitHub Actions on main deploys as $DEPLOY_SA with no stored key. It may build and deploy
# the edge, and set the VM's metadata, which is how the VM is told to deploy.
deploy_access() {
  local num sa member
  num=$(number)
  sa="$DEPLOY_SA@$PROJECT.iam.gserviceaccount.com"
  log "deploys from GitHub Actions: workload identity pool github, and $DEPLOY_SA"
  exists g iam workload-identity-pools describe github --location global ||
    g iam workload-identity-pools create github --location global --display-name "GitHub Actions"
  exists g iam workload-identity-pools providers describe github --location global --workload-identity-pool github ||
    g iam workload-identity-pools providers create-oidc github --location global --workload-identity-pool github \
      --issuer-uri https://token.actions.githubusercontent.com \
      --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
      --attribute-condition "assertion.repository=='$GITHUB_REPO' && assertion.ref=='refs/heads/main'"
  exists g iam service-accounts describe "$sa" || g iam service-accounts create "$DEPLOY_SA" --display-name "meta-harness deploys"
  member="principalSet://iam.googleapis.com/projects/$num/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO"
  retry g iam service-accounts add-iam-policy-binding "$sa" --member "$member" --role roles/iam.workloadIdentityUser
  for role in roles/run.admin roles/cloudbuild.builds.editor roles/serviceusage.serviceUsageConsumer roles/logging.viewer; do
    retry g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$sa" --role "$role" --condition=None
  done
  retry g storage buckets add-iam-policy-binding "gs://${PROJECT}_cloudbuild" --member "serviceAccount:$sa" \
    --role roles/storage.admin
  # It acts as the accounts the edge, its build and the VM run as.
  for target in "mh-edge-sa@$PROJECT.iam.gserviceaccount.com" "$num-compute@developer.gserviceaccount.com" \
    "mh-vm-sa@$PROJECT.iam.gserviceaccount.com"; do
    retry g iam service-accounts add-iam-policy-binding "$target" --member "serviceAccount:$sa" \
      --role roles/iam.serviceAccountUser
  done
  retry g compute instances add-iam-policy-binding "$VM" --zone "$ZONE" --member "serviceAccount:$sa" \
    --role roles/compute.instanceAdmin.v1
}

cmd_edge() {
  local image tag num
  num=$(number)
  tag=$(git rev-parse --short HEAD)
  image="$REGION-docker.pkg.dev/$PROJECT/$REPO/edge:$tag"
  log "building $image"
  g builds submit --region "$REGION" --config deploy/cloudbuild-edge.yaml --substitutions "_IMAGE=$image" \
    --suppress-logs . >/dev/null
  log "deploying $EDGE behind Identity-Aware Proxy"
  g run deploy "$EDGE" --region "$REGION" --image "$image" \
    --service-account "mh-edge-sa@$PROJECT.iam.gserviceaccount.com" \
    --network "$NETWORK" --subnet "$SUBNET" --vpc-egress private-ranges-only \
    --set-env-vars "^;^MH_PLANNER_URL=http://$VM_IP:8303;MH_TASKS_URL=http://$VM_IP:8302;MH_EVENTS_URL=http://$VM_IP:8301;MH_SCHEDULER_URL=http://$VM_IP:8304;MH_EDGE_IAP_AUDIENCE=/projects/$num/locations/$REGION/services/$EDGE;MH_CONSOLE_OPERATORS=$OPERATORS" \
    --set-secrets MH_SERVICE_KEY=MH_SERVICE_KEY:latest \
    --timeout 3600 --memory 1Gi --min-instances 0 --max-instances 2 \
    --no-allow-unauthenticated --iap >/dev/null
  log "the console: $(g run services describe "$EDGE" --region "$REGION" --format='value(status.url)')"
}

# commit_of REF: the full commit a branch, tag or commit names on GitHub.
commit_of() {
  if [[ $1 =~ ^[0-9a-f]{40}$ ]]; then
    echo "$1"
    return
  fi
  git ls-remote "https://github.com/$GITHUB_REPO.git" "refs/heads/$1" "refs/tags/$1" | head -n 1 | cut -f1
}

deploy_result() {
  g compute instances get-guest-attributes "$VM" --zone "$ZONE" --query-path "mh/$1" --format='value(value)' 2>/dev/null
}

cmd_vm() {
  local commit id result waited=0
  commit=$(commit_of "$REF")
  [ -n "$commit" ] || { log "$REF is not a branch, tag or commit on GitHub"; exit 1; }
  id="$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:7}"
  log "VM deploy $id: $REF ($commit)"
  # The startup script lives in the VM's metadata, so a change to deploy/vm/startup.sh reaches the VM here.
  g compute instances add-metadata "$VM" --zone "$ZONE" \
    --metadata "mh-ref=$commit,mh-deploy-id=$id,enable-guest-attributes=TRUE" \
    --metadata-from-file startup-script=deploy/vm/startup.sh >/dev/null
  if [ "$(deploy_result deployer)" != ready ]; then
    # A VM booted before the deployer existed. Its startup script installs the deployer as it deploys.
    log "the VM has no deployer yet: restarting it, so its startup script deploys $id and installs one"
    g compute instances reset "$VM" --zone "$ZONE" >/dev/null
  fi
  while [ "$waited" -lt $((VM_DEPLOY_MINUTES * 60)) ]; do
    result=$(deploy_result deploy || true)
    case "$result" in
      "$id ok"*) log "VM deploy ${result#"$id "}"; return 0 ;;
      "$id failed"*) log "VM deploy ${result#"$id "}"; exit 1 ;;
    esac
    sleep 15
    waited=$((waited + 15))
  done
  log "VM deploy $id has no result after $VM_DEPLOY_MINUTES minutes (last: ${result:-none}); see deploy/gcp.sh status"
  exit 1
}

cmd_deploy() {
  local commit
  commit=$(commit_of "$REF")
  # The edge is built from this checkout, so it must hold the commit the VM gets.
  if [ "$(git rev-parse HEAD)" != "$commit" ]; then
    log "this checkout is at $(git rev-parse --short HEAD), not $REF (${commit:0:7}): check it out and pull first"
    exit 1
  fi
  REF=$commit cmd_vm
  cmd_edge
}

cmd_status() {
  log "VM deployer: $(deploy_result deployer || echo absent); last deploy: $(deploy_result deploy || echo none)"
  log "the console: $(g run services describe "$EDGE" --region "$REGION" --format='value(status.url)' 2>/dev/null || echo 'not deployed')"
  log "the boot log, which needs SSH: gcloud compute ssh $VM --zone $ZONE --project $PROJECT --tunnel-through-iap --command 'sudo tail -n 40 /var/log/mh-boot.log'"
}

case "${1:-}" in
  infra) cmd_infra ;;
  edge) cmd_edge ;;
  deploy) cmd_deploy ;;
  vm) cmd_vm ;;
  status) cmd_status ;;
  *) echo "usage: deploy/gcp.sh infra|deploy|vm|edge|status" >&2; exit 2 ;;
esac
