"""What a run gets from its harness profile and routing tier: limits, resources, allowlist, thinking."""

import json
from datetime import timedelta

import pytest

from meta_harness.run_service import routing, sandbox
from meta_harness.run_service.activities import run_body
from meta_harness.run_service.paths import REPO_ROOT
from meta_harness.run_service.shared import Candidate, HarnessRequest
from meta_harness.run_service.workflows import HARNESS_GRACE, harness_timeout

PROFILES = json.loads((REPO_ROOT / "config" / "harness_profiles.json").read_text())["profiles"]


def _candidate(**profile) -> Candidate:
    base = {"name": "pi-lean", "tools": ["read", "bash"], "max_turns": 80, "max_minutes": 45}
    return Candidate(label="x", profile_name="pi-lean", engine="claude", model="m", profile={**base, **profile})


def test_every_shipped_profile_passes_the_startup_check():
    assert {name: routing.profile_errors(name, p) for name, p in PROFILES.items()} == {n: [] for n in PROFILES}


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"tools": []}, "tools must be a non-empty list"),
        ({"max_minutes": 0}, "max_minutes must be a positive number"),
        ({"max_turns": True}, "max_turns must be a positive number"),
        ({"bash_allow": []}, "bash_allow must be a non-empty list"),
        ({"resources": {"cpu": 2}}, "resources.memory_gb must be a positive number"),
    ],
)
def test_a_malformed_profile_is_named_at_startup(change, error):
    profile = {**PROFILES["pi-lean"], **change}
    assert any(error in e for e in routing.profile_errors("pi-lean", profile))


def test_the_run_body_carries_the_allowlist_and_the_tier_thinking_level():
    request = HarnessRequest(
        run_id="run-task-a:run-1", prompt="p", candidate=_candidate(bash_allow=["ls"]), thinking="high"
    )

    body = run_body(request, "rg_key")

    assert body["profile"] == {
        "name": "pi-lean",
        "tools": ["read", "bash"],
        "max_turns": 80,
        "max_minutes": 45,
        "bash_allow": ["ls"],
    }
    assert body["thinking"] == "high"
    assert body["harness_session_id"] == "run-task-a.run-1"
    assert body["grant_key"] == "rg_key"


def test_a_pinned_run_sends_no_thinking_level_and_a_profile_without_an_allowlist_sends_none():
    body = run_body(HarnessRequest(run_id="r", prompt="p", candidate=_candidate()), "rg_key")

    assert "thinking" not in body
    assert "bash_allow" not in body["profile"]


def test_a_run_may_take_its_profile_minutes_plus_the_grace():
    assert harness_timeout(_candidate(max_minutes=120)) == timedelta(minutes=120) + HARNESS_GRACE
    assert harness_timeout(_candidate(max_minutes=45)) == timedelta(minutes=45) + HARNESS_GRACE


def test_sandbox_resources_come_from_the_profile():
    assert sandbox.Resources.from_profile(PROFILES["pi-careful"]) == sandbox.Resources(cpus=2, memory_gb=6)
    assert sandbox.Resources.from_profile({}) == sandbox.Resources()
