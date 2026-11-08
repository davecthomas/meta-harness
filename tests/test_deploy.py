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
    # A deploy keeps the VM: no -replace, so running tasks survive a merge.
    assert "-replace=" not in deploy
    assert re.search(r'startup-script\s+= file\("\$\{path.module\}/../../vm/startup.sh"\)', release)


def test_the_edge_reads_every_sign_in_setting_the_release_stack_gives_it():
    """A renamed variable on either side would start the edge without sign-in, or refuse to start it."""
    release = (ROOT / "deploy" / "terraform" / "release" / "main.tf").read_text()
    edge = (ROOT / "src" / "meta_harness" / "edge" / "app.py").read_text()
    for name in (
        "MH_GOOGLE_CLIENT_ID",
        "MH_CONSOLE_ORIGIN",
        "MH_STREAM_ORIGIN",
        "MH_CONSOLE_OPERATORS",
        "MH_SERVICE_KEY",
        "MH_SESSION_KEY",
    ):
        assert name in release, name
        assert f'os.environ.get("{name}"' in edge or f'"{name}"' in edge, name
    assert "MH_EDGE_IAP_AUDIENCE" not in release


def test_the_console_address_forwards_everything_to_the_edge_and_nothing_else_signs_in():
    release = (ROOT / "deploy" / "terraform" / "release" / "main.tf").read_text()
    platform = (ROOT / "deploy" / "terraform" / "platform" / "main.tf").read_text()
    variables = (ROOT / "deploy" / "terraform" / "platform" / "variables.tf").read_text()
    assert 'default     = "meta-harness"' in variables
    assert 'resource "google_firebase_hosting_site" "console"' in platform
    assert 'glob = "**"' in release and "service_id = google_cloud_run_v2_service.edge.name" in release
    # Firebase Hosting calls without credentials: Cloud Run is open, IAP is off, and the edge's session decides.
    assert "iap_enabled         = false" in release
    assert 'member   = "allUsers"' in release
    assert "iap_web_cloud_run_service_iam_member" not in platform


def test_a_deploy_updates_the_running_vm_in_place_unless_terraform_replaced_it():
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    release = (ROOT / "deploy" / "terraform" / "release" / "main.tf").read_text()
    platform = (ROOT / "deploy" / "terraform" / "platform" / "main.tf").read_text()
    assert "if: steps.apply.outputs.replaced == 'false'" in deploy
    assert "--tunnel-through-iap" in deploy and "systemd-run --wait --collect" in deploy
    assert "--setenv=MH_DEPLOY_MODE=update" in deploy
    assert re.search(r'enable-oslogin\s+= "TRUE"', release)
    assert '"roles/iap.tunnelResourceAccessor"' in platform and '"roles/compute.osAdminLogin"' in platform


def test_an_update_keeps_the_databases_running_and_restarts_services_one_at_a_time():
    boot = (ROOT / "deploy" / "vm" / "boot.sh").read_text()
    update = boot[boot.index('if [ "$MODE" = boot ]; then') :]
    update = update[update.index("else") : update.index("\nfi\nlog")]
    assert "systemctl restart mh-postgres" not in update and "systemctl restart mh-temporal" not in update
    assert "systemctl start mh-postgres mh-temporal" in update
    assert 'systemctl restart "$unit"' in update and 'healthy "$unit"' in update
    assert "for pair in mh-events:8301 mh-tasks:8302 mh-planner:8303 mh-scheduler:8304 mh-worker:" in update
    # A gateway or egress restart drops a running sandbox's model calls, so each restarts only when it changed.
    assert "if changed samples/temporal-run-task/egress-proxy" in update
    assert 'GW_PREVIOUS" ] || changed deploy/vm/units/mh-gateway.service' in update
    startup = (ROOT / "deploy" / "vm" / "startup.sh").read_text()
    assert startup.index("MH_PREVIOUS_COMMIT=$(git") < startup.index("checkout -q -f FETCH_HEAD")
