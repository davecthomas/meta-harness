"""Refuses a platform stack plan that would destroy what the deployment cannot lose.

The deploy workflow applies the platform stack on every merge to main, with
no person reading the plan. This reads the plan (`terraform show -json`) on
stdin and exits 1, naming each resource, when the plan deletes or replaces:

- anything that holds state or that everything else stands on: the data
  disk, the network, the service accounts, the sign-in from GitHub Actions,
  a secret, the image repository, the console's site, an enabled API;
- a grant the deploy account needs to deploy at all: its sign-in from GitHub
  Actions, and its access to the Terraform state. Without them the next
  deploy could not run, so it could not put the grant back.

Any other change applies: new resources, updates in place, and removed
grants and firewall rules. A refused change needs `make platform`, by hand.
"""

import json
import sys

# Resource types a deploy never deletes or replaces.
PROTECTED_TYPES = {
    "google_compute_disk",
    "google_compute_network",
    "google_compute_subnetwork",
    "google_compute_router",
    "google_compute_router_nat",
    "google_compute_address",
    "google_service_account",
    "google_iam_workload_identity_pool",
    "google_iam_workload_identity_pool_provider",
    "google_secret_manager_secret",
    "google_artifact_registry_repository",
    "google_firebase_project",
    "google_firebase_hosting_site",
    "google_project_service",
    "random_password",
}
# Grants to the deploy account without which no later deploy could sign in or read the state.
DEPLOY_ACCOUNT = "serviceAccount:mh-deploy-sa@"
LOCKOUT_ROLES = {"roles/iam.workloadIdentityUser", "roles/storage.objectAdmin", "roles/storage.legacyBucketReader"}


def refusals(plan: dict) -> list[str]:
    """Why the plan may not be applied by a deploy: one line per resource, empty when it may."""
    reasons = []
    for change in plan.get("resource_changes", []):
        if "delete" not in change["change"]["actions"]:
            continue
        verb = "replaces" if "create" in change["change"]["actions"] else "deletes"
        before = change["change"].get("before") or {}
        if change["type"] in PROTECTED_TYPES:
            reasons.append(f"the plan {verb} {change['address']}")
        elif (
            change["type"].endswith("_iam_member")
            and before.get("role") in LOCKOUT_ROLES
            and (DEPLOY_ACCOUNT in str(before.get("member")) or before.get("role") == "roles/iam.workloadIdentityUser")
        ):
            reasons.append(f"the plan {verb} {change['address']}, which the deploy account needs to deploy")
    return reasons


def main() -> int:
    reasons = refusals(json.load(sys.stdin))
    for reason in reasons:
        print(f"refused: {reason}")
    if reasons:
        print(
            "A deploy does not apply this plan. Change the stack so it keeps these, or apply it by hand: make platform."
        )
        return 1
    print("the platform plan destroys nothing a deploy must keep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
