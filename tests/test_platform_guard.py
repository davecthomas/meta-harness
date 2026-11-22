"""The guard on the platform plan a deploy applies with no person reading it."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("platform_guard", ROOT / "deploy" / "platform_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

DEPLOY = "serviceAccount:mh-deploy-sa@example-project.iam.gserviceaccount.com"


def change(address: str, actions: list[str], before: dict | None = None) -> dict:
    return {"address": address, "type": address.split(".")[0], "change": {"actions": actions, "before": before}}


def test_new_resources_and_changes_in_place_apply():
    plan = {
        "resource_changes": [
            change('google_project_iam_member.edge["roles/logging.viewer"]', ["create"]),
            change("google_compute_firewall.edge", ["update"], {"name": "mh-allow-edge"}),
            change("google_compute_disk.data", ["no-op"], {"name": "mh-data"}),
        ]
    }
    assert guard.refusals(plan) == []


def test_a_plan_with_no_changes_applies():
    assert guard.refusals({}) == []


def test_deleting_or_replacing_what_the_deployment_stands_on_is_refused():
    plan = {
        "resource_changes": [
            change("google_compute_disk.data", ["delete", "create"], {"name": "mh-data"}),
            change("google_service_account.vm", ["delete"], {"account_id": "mh-vm-sa"}),
            change('google_secret_manager_secret.this["MH_SERVICE_KEY"]', ["create", "delete"], {}),
        ]
    }
    assert guard.refusals(plan) == [
        "the plan replaces google_compute_disk.data",
        "the plan deletes google_service_account.vm",
        'the plan replaces google_secret_manager_secret.this["MH_SERVICE_KEY"]',
    ]


def test_removing_the_data_disks_snapshot_schedule_or_its_attachment_is_refused():
    """The disk is the only copy of the deployment's state: a deploy never leaves it with no backup."""
    plan = {
        "resource_changes": [
            change("google_compute_resource_policy.data_snapshots", ["delete", "create"], {"name": "mh-data-daily"}),
            change("google_compute_disk_resource_policy_attachment.data_snapshots", ["delete"], {}),
        ]
    }
    assert guard.refusals(plan) == [
        "the plan replaces google_compute_resource_policy.data_snapshots",
        "the plan deletes google_compute_disk_resource_policy_attachment.data_snapshots",
    ]
    created = {"resource_changes": [change("google_compute_resource_policy.data_snapshots", ["create"], {})]}
    assert guard.refusals(created) == []


def test_the_platform_stack_snapshots_the_data_disk_every_day():
    from pathlib import Path

    stack = (Path(__file__).resolve().parents[1] / "deploy" / "terraform" / "platform" / "main.tf").read_text()
    assert 'resource "google_compute_resource_policy" "data_snapshots"' in stack
    assert "days_in_cycle = 1" in stack and "max_retention_days    = 14" in stack
    assert 'on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"' in stack
    assert "disk = google_compute_disk.data.name" in stack


def test_a_removed_grant_or_firewall_rule_applies():
    plan = {
        "resource_changes": [
            change(
                'google_project_iam_member.deploy["roles/run.admin"]',
                ["delete"],
                {"role": "roles/run.admin", "member": DEPLOY},
            ),
            change("google_compute_firewall.edge", ["delete"], {"name": "mh-allow-edge"}),
        ]
    }
    assert guard.refusals(plan) == []


def test_removing_the_deploy_accounts_own_way_in_is_refused():
    plan = {
        "resource_changes": [
            change(
                "google_service_account_iam_member.deploy_from_github",
                ["delete"],
                {"role": "roles/iam.workloadIdentityUser", "member": "principalSet://iam.googleapis.com/x"},
            ),
            change(
                'google_storage_bucket_iam_member.deploy_state["roles/storage.objectAdmin"]',
                ["delete"],
                {"role": "roles/storage.objectAdmin", "member": DEPLOY},
            ),
        ]
    }
    assert len(guard.refusals(plan)) == 2
    assert all("the deploy account needs to deploy" in reason for reason in guard.refusals(plan))
