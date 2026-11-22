# Deploying the meta-harness

Every merge to main deploys itself: `.github/workflows/deploy.yml` waits for
main's checks, applies the platform Terraform stack when it changed, builds
the edge image, and applies the release Terraform stack.
Two stacks, both under `deploy/terraform`, with state in the settings file's
`state_bucket`:

| Stack | Holds | Applied by |
|---|---|---|
| `platform` | network, NAT, firewall, the VM's address and data disk, service accounts and their roles, the GitHub sign-in, the secrets' containers and the generated session key, Artifact Registry, the Firebase Hosting site | CI on every merge, when its plan shows a change and `deploy/platform_guard.py` allows it; by hand, `make platform`, for a new project or a plan the guard refuses |
| `release` | the VM at a commit, the edge on Cloud Run from that commit's image, and the Hosting site's rewrite to it | CI on every merge; by hand, `make deploy` |

A deploy replaces the VM. Its boot disk is new every time; the data disk
(`mh-data`: Postgres, Temporal's history, run workspaces) moves to the new VM.
`make deploy-status` shows the commit the VM last booted and the console's URL.

## The settings file

`deploy/environment.json` is the deployment's one settings file: `project`,
`project_number`, `region`, `zone`, `state_bucket`, `console_site` (the
Firebase Hosting site, so the console is `https://<console_site>.web.app`),
`google_client_id` and `github_repo`. Both stacks, the deploy workflow, the
Makefile and `deploy/shutdown.sh` read it, and nothing else names a project:
`tests/test_deploy.py` fails when one does. Moving the deployment to another
project is a change to this file, then "Setting up a project once" below.

The repo is public, so the file is not in it: git ignores
`deploy/environment.json`, and `deploy/environment.example.json` shows its
shape. Each machine that deploys keeps its own copy. CI reads it from the
repo secret `MH_ENVIRONMENT` (`gh secret set MH_ENVIRONMENT <
deploy/environment.json`; set it again after changing the file), and masks
the project, its number, the bucket, the site and the client id in the
public Actions logs. The make targets that act on the deployment stop, saying
how to make the file, when it is missing. The
state bucket and the Hosting site are unique across Google Cloud, so a new
project needs new ones.

The platform stack takes the file with `-var-file`, and none of those
variables has a default. Both stacks take the state bucket at `terraform init
-backend-config=bucket=...`, and the plan fails when `project_number` is not
the project's.

## Setting up a project once

1. `deploy/environment.json` names the project. `make platform`, signed in
   to gcloud as a project owner, creates the state bucket when missing, then
   applies the platform stack and asks before it changes anything.
2. Secret values, which Terraform never sees:
   `gcloud secrets versions add <NAME> --data-file=- --project <project>`
   for each secret in `platform/variables.tf`. `MH_REPOS_CONFIG` is the repo
   registry JSON; `MH_GITHUB_APPS` is `owner=app_id` pairs, and each owner's
   private key is `MH_GITHUB_APP_KEY_<owner>`.
3. The console's Google sign-in, once, in the Google Cloud console (Google
   Auth Platform): its branding, its audience (Internal, in an organization,
   lets the organization's accounts sign in; External needs each operator as
   a test user while the app is in testing), and a web client whose
   authorized JavaScript origin is `https://<console_site>.web.app`. Its id
   goes in `deploy/environment.json` as `google_client_id`; the release stack
   refuses to deploy while that is empty.
4. The repo secret `MH_OPERATORS`, the console's Google accounts as a JSON
   list, `MH_ADMINS`, those of them who are admins (unset: all of them), and
   `deploy/.env` for `make platform` (below).
5. Quota for the VM's machine type (`release/variables.tf`) in the region.
6. Merge to main, or `make deploy`.

## Reading the VM

The VM has no public address. `gcloud compute ssh mh-vm --zone <zone>
--project <project> --tunnel-through-iap` reaches it; the boot log is
`/var/log/mh-boot.log`.

## The Jira sign-in

The Jira tracker signs in as one Jira account, from two secrets the platform
stack creates empty: `MH_JIRA_EMAIL`, the account's email, and
`MH_JIRA_TOKEN`, an API token of that account (made at
id.atlassian.com, Security, API tokens). Give each a value, then deploy, and
the VM's next boot or update puts them in the planner's environment:

    printf '%s' 'bot@example.com' | gcloud secrets versions add MH_JIRA_EMAIL --data-file=- --project <project>
    pbpaste | tr -d '\n' | gcloud secrets versions add MH_JIRA_TOKEN --data-file=- --project <project>

The second reads the token from the clipboard, so it is never typed or
shown. With no value in either, the deployment has no Jira sign-in, and
Configuration, Issue trackers says which is missing.

## What stays out of the repo

The repo is public, so nothing that identifies a person or an account is in
it. Who may sign in to the console (`operators` in the platform stack) has no
default: CI reads it from the repo secret `MH_OPERATORS`, a JSON list
(`gh secret set MH_OPERATORS --body '["you@example.com"]'`), and who of
them is an admin (`admins`) from `MH_ADMINS` the same way; `make
platform` and `deploy/shutdown.sh` read `deploy/.env`, which git ignores
(copy `deploy/.env.example`). Terraform marks the list sensitive, so plans and
logs show `(sensitive value)`. The billing account is read from Google Cloud
when a command needs it, never written down. Database backups (`*.sql.gz`)
are ignored by git too.

## Pausing or leaving the project

One command each, from the repo root on a machine signed in to gcloud (a
project owner) and gh (a repo admin):

| Command | Does | Undo |
|---|---|---|
| `make gcp-teardown` | backs up the database, stops the meta-harness, deletes the project | `gcloud projects undelete <project>` within 30 days |
| `make gcp-disable` | backs up the database, stops the meta-harness once running tasks finish, turns billing off | relink billing (it prints how), then `make gcp-resume` |
| `make gcp-pause` | turns the deploy workflow off, drains, waits up to an hour for running tasks, stops the VM | `make gcp-resume` |
| `make gcp-resume` | starts the VM, ends the drain, turns the deploy workflow on | |
| `make gcp-backup` | dumps the database to `~/meta-harness-backups` | |
| `make gcp-status` | the VM, the deploy workflow, billing, running tasks | |

`gcp-teardown` and `gcp-disable` ask once, before changing anything, for the
project id typed out. Every step is safe to run again.
`deploy/shutdown.sh` holds them.

## The HTTP Gateway's commit

The VM runs the HTTP Gateway at the one commit named in
`deploy/vm/gateway.ref`, never at a branch. The gateway starts with the
provider keys, and none of this repo's checks covers its code. To take a newer
gateway, put its full commit SHA in that file
(`git ls-remote https://github.com/davecthomas/ai-api-unified-http.git main`
shows the newest) in a pull request. The file is under `deploy/`, so an
operator merges it. The boot script stops, and an update rolls back, when the
file does not hold 40 hex characters.

## Backups of the data disk

The `mh-data` disk holds Postgres, Temporal's history and the run workspaces.
The platform stack snapshots it every day at 09:00 UTC and keeps each snapshot
14 days (`mh-data-daily`). The snapshots outlive the disk. A deploy never
deletes or replaces the schedule or its attachment (`deploy/platform_guard.py`),
so a change to the schedule itself, which Google Cloud makes by replacing it,
is applied by hand with `make platform`.

To restore, with `PROJECT` and `ZONE` from `deploy/environment.json`:

1. List the snapshots and pick one:
   `gcloud compute snapshots list --project "$PROJECT" --filter "sourceDisk~mh-data" --sort-by ~creationTimestamp`
2. Stop the VM, so nothing writes to the disk:
   `gcloud compute instances stop mh-vm --zone "$ZONE" --project "$PROJECT"`
3. Make a disk from the snapshot under a new name, and look at it before
   anything is replaced:
   `gcloud compute disks create mh-data-restored --source-snapshot <snapshot> --type pd-balanced --zone "$ZONE" --project "$PROJECT"`
4. Swap the disks on the VM: detach `mh-data`, attach `mh-data-restored` under
   the device name `data`, and start the VM. Keep the old disk until the
   restored one has run a task end to end.
5. Terraform still names the disk `mh-data`. Once the old disk is no longer
   needed, rename by hand (delete the old disk, create `mh-data` from the same
   snapshot, attach it), so the platform stack's state matches again.

`make gcp-backup` still dumps the database to a laptop, for a copy outside the
project.
