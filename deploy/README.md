# Deploying the meta-harness

Every merge to main deploys itself: `.github/workflows/deploy.yml` waits for
main's checks, builds the edge image, and applies the release Terraform stack.
Two stacks, both under `deploy/terraform`, with state in the bucket
`meta-harness-prod-terraform`:

| Stack | Holds | Applied by |
|---|---|---|
| `platform` | network, NAT, firewall, the VM's address and data disk, service accounts and their roles, the GitHub sign-in, the secrets' containers, Artifact Registry, the console's access list | an operator: `make platform` |
| `release` | the VM at a commit, and the edge on Cloud Run from that commit's image | CI on every merge; by hand, `make deploy` |

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
3. Identity-Aware Proxy's OAuth client, once, in the Google Cloud console
   (APIs and services, Credentials): a web client with the redirect URI
   `https://iap.googleapis.com/v1/oauth/clientIds/<client id>:handleRedirect`,
   then `gcloud iap settings set` with the client id and secret on the
   Cloud Run service `mh-edge`.
4. Merge to main, or `make deploy`.

## Reading the VM

The VM has no public address. `gcloud compute ssh mh-vm --zone us-central1-a
--project meta-harness-prod --tunnel-through-iap` reaches it; the boot log is
`/var/log/mh-boot.log`.
