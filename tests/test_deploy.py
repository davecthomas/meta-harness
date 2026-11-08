"""The VM deployment hands each service the configuration it can read.

The worker's models.json names `harness` tier candidates, which only the
classify-only router accepts. The HTTP Gateway's completions factory refuses
them, so the gateway must read its own config/models.json. The shared
environment file therefore never sets AI_MODELS_CONFIG; the worker unit does.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "deploy" / "vm" / "units"


def _env_lines(unit: str) -> list[str]:
    text = (UNITS / unit).read_text()
    return [line for line in text.splitlines() if line.startswith("Environment=")]


def test_the_shared_environment_file_never_sets_the_models_config():
    boot = (ROOT / "deploy" / "vm" / "boot.sh").read_text()
    written = [line for line in boot.splitlines() if re.match(r'\s*echo "AI_MODELS_CONFIG=', line)]
    assert written == [], "AI_MODELS_CONFIG in the shared env file reaches the HTTP Gateway, which cannot read it"


def test_only_the_worker_unit_gets_the_router_models_config():
    assert _env_lines("mh-worker.service") == ["Environment=AI_MODELS_CONFIG=/opt/meta-harness/config/models.json"]
    for unit in UNITS.glob("*.service"):
        if unit.name == "mh-worker.service":
            continue
        assert not any("AI_MODELS_CONFIG" in line for line in _env_lines(unit.name)), unit.name


def test_every_tier_candidate_is_a_harness_pair():
    """Why the file is the worker's alone: nothing in it is a model the gateway could call directly."""
    config = json.loads((ROOT / "config" / "models.json").read_text())
    candidates = [model for tier in config["router"]["tiers"] for model in tier["models"]]
    assert candidates and all(c.startswith("harness:") for c in candidates)


WORKFLOWS = ROOT / ".github" / "workflows"


def test_the_startup_script_reports_the_commit_the_deploy_waits_for():
    """CI waits for the guest attribute mh/boot to read "ok <commit>"; the startup script must write exactly that."""
    startup = (ROOT / "deploy" / "vm" / "startup.sh").read_text()
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    assert 'report "ok $REF"' in startup and 'report "failed $REF"' in startup
    assert "guest-attributes/mh/boot" in startup
    assert "--query-path mh/boot" in deploy
    assert '"ok ${{ github.sha }}"' in deploy and '"failed ${{ github.sha }}"' in deploy


def test_the_vm_state_lives_on_the_data_disk():
    """A deploy replaces the VM and its boot disk, so everything that must survive is under the mount."""
    startup = (ROOT / "deploy" / "vm" / "startup.sh").read_text()
    release = (ROOT / "deploy" / "terraform" / "release" / "main.tf").read_text()
    assert "/dev/disk/by-id/google-data" in startup and 'device_name = "data"' in release
    assert 'mount -o discard,defaults "$DEV" "$STATE"' in startup
    postgres = (UNITS / "mh-postgres.service").read_text()
    assert "-v /var/lib/meta-harness/postgres:/var/lib/postgresql/data" in postgres
    temporal = (UNITS / "mh-temporal.service").read_text()
    assert "--db-filename /var/lib/meta-harness/temporal.db" in temporal


def test_the_deploy_workflow_waits_for_every_check_main_runs():
    """A renamed CI job would leave the deploy waiting for a check that never comes."""
    jobs = set()
    for workflow in ("control-plane.yml", "e2e.yml"):
        jobs |= set(re.findall(r"^    name: (.+)$", (WORKFLOWS / workflow).read_text(), re.M))
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    waited = set(re.search(r'CHECKS: "(.+)"', deploy).group(1).split("|"))
    assert waited == jobs
    assert f'"$passed" -eq {len(jobs)}' in deploy


def test_the_deploy_workflow_signs_in_with_what_the_platform_stack_creates():
    platform = (ROOT / "deploy" / "terraform" / "platform" / "main.tf").read_text()
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    assert "workloadIdentityPools/github/providers/github" in deploy
    assert 'workload_identity_pool_provider_id = "github"' in platform
    assert 'account_id   = "mh-deploy-sa"' in platform and "mh-deploy-sa@meta-harness-prod" in deploy
    assert "assertion.ref=='refs/heads/main'" in platform


def test_the_release_stack_gets_the_commit_and_the_image_the_workflow_built():
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    release = (ROOT / "deploy" / "terraform" / "release" / "main.tf").read_text()
    assert '-var "commit=${{ github.sha }}" -var "edge_image=$EDGE_IMAGE"' in deploy
    assert "edge:${{ github.sha }}" in deploy
    assert "replace_triggered_by = [terraform_data.commit]" in release
    assert 'startup-script          = file("${path.module}/../../vm/startup.sh")' in release
