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


def test_boot_installs_the_deployer_and_restarts_it_without_waiting():
    """At boot the deployer is ordered after the startup script running boot.sh, so a blocking restart hangs."""
    boot = (ROOT / "deploy" / "vm" / "boot.sh").read_text()
    assert 'install -m 755 "$MH"/deploy/vm/deployer.sh /usr/local/bin/mh-deployer' in boot
    assert "systemctl restart --no-block mh-deployer" in boot
    unit = (UNITS / "mh-deployer.service").read_text()
    assert "ExecStart=/usr/local/bin/mh-deployer watch" in unit
    assert "google-startup-scripts.service" in unit


def test_the_deploy_workflow_waits_for_every_check_main_runs():
    """A renamed CI job would leave the deploy waiting for a check that never comes."""
    jobs = set()
    for workflow in ("control-plane.yml", "e2e.yml"):
        jobs |= set(re.findall(r"^    name: (.+)$", (WORKFLOWS / workflow).read_text(), re.M))
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    waited = set(re.search(r'CHECKS: "(.+)"', deploy).group(1).split("|"))
    assert waited == jobs
    assert f'"$passed" -eq {len(jobs)}' in deploy


def test_the_deploy_workflow_signs_in_with_what_infra_creates():
    gcp = (ROOT / "deploy" / "gcp.sh").read_text()
    deploy = (WORKFLOWS / "deploy.yml").read_text()
    assert "workloadIdentityPools/github/providers/github" in deploy
    assert "workload-identity-pools providers create-oidc github" in gcp
    assert "DEPLOY_SA=mh-deploy-sa" in gcp and "mh-deploy-sa@meta-harness-prod" in deploy
    assert "assertion.ref=='refs/heads/main'" in gcp
