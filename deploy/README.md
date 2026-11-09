# Deploying the meta-harness

Every merge to main deploys itself: `.github/workflows/deploy.yml` waits for
main's checks, applies the platform Terraform stack when it changed, builds
the edge image, and applies the release Terraform stack.
Two stacks, both under `deploy/terraform`, with state in the bucket
`meta-harness-prod-terraform`:

| Stack | Holds | Applied by |
|---|---|---|
| `platform` | network, NAT, firewall, the VM's address and data disk, service accounts and their roles, the GitHub sign-in, the secrets' containers and the generated session key, Artifact Registry, Firebase Hosting's `meta-harness` site | CI on every merge, when its plan shows a change and `deploy/platform_guard.py` allows it; by hand, `make platform`, for a new project or a plan the guard refuses |
| `release` | the VM at a commit, the edge on Cloud Run from that commit's image, and the Hosting site's rewrite to it | CI on every merge; by hand, `make deploy` |

A deploy replaces the VM. Its boot disk is new every time; the data disk
(`mh-data`: Postgres, Temporal's history, run workspaces) moves to the new VM.
`make deploy-status` shows the commit the VM last booted and the console's URL.

## Setting up a project once

1. `make platform`. It creates the state bucket when missing, then applies the
   platform stack. The first apply adopts what `deploy/gcp.sh` had created.
2. Secret values, which Terraform never sees:
   `gcloud secrets versions add <NAME> --data-file=- --project meta-harness-prod`
   for each secret in `platform/variables.tf`. `MH_REPOS_CONFIG` is the repo
   registry JSON; `MH_GITHUB_APPS` is `owner=app_id` pairs, and each owner's
   private key is `MH_GITHUB_APP_KEY_<owner>`.
3. The console's Google sign-in, once, in the Google Cloud console (Google
   Auth Platform, Clients): the web client named in `platform/variables.tf`
   as `google_client_id` lists `https://meta-harness.web.app` as an
   authorized JavaScript origin. While the app is in testing, each operator
   is a test user on its Audience page.
4. Merge to main, or `make deploy`.

## Reading the VM

The VM has no public address. `gcloud compute ssh mh-vm --zone us-central1-a
--project meta-harness-prod --tunnel-through-iap` reaches it; the boot log is
`/var/log/mh-boot.log`.
