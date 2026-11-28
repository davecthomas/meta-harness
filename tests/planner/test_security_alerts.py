"""A repo's open security alerts become issues, once each (planner/security_alerts.py)."""

import asyncio
import uuid

import pytest

from meta_harness.planner import repos, security_alerts
from meta_harness.run_service import code_host

from .test_planner import Events, migrated  # noqa: F401
from .test_planning import WritingTracker


def _alert(kind: str, number: int, severity: str, summary: str = "SQL built from user input") -> dict:
    return {
        "kind": kind,
        "number": number,
        "severity": severity,
        "summary": summary,
        "where": "src/app.py",
        "fix": "2.15.0" if kind == "dependency" else "",
        "url": f"https://github.com/o/r/security/{kind}/{number}",
    }


@pytest.fixture
def repo(monkeypatch, migrated):  # noqa: F811
    name = f"acme/alerts-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(repos, "registry", lambda: {name.lower(): repos.Repo(name, "github", [])})
    return name


def _serve(monkeypatch, alerts: list[dict] | Exception) -> None:
    async def security_alerts_of(name):
        if isinstance(alerts, Exception):
            raise alerts
        return alerts

    monkeypatch.setattr(code_host, "security_alerts", security_alerts_of)


def test_each_new_high_or_critical_alert_is_filed_once_and_a_lower_one_is_left(repo, monkeypatch):
    tracker, events = WritingTracker(), Events()
    _serve(
        monkeypatch,
        [
            _alert("code_scanning", 3, "high"),
            _alert("dependency", 7, "critical", "PyJWT accepts a forged key"),
            _alert("dependency", 8, "medium"),
        ],
    )

    filed = asyncio.run(security_alerts.file_all(tracker, events))

    assert len(filed) == 2 and [i.ref for i in tracker.created] == filed
    code, dependency = tracker.created
    assert code.title == "Code scanning alert, high: SQL built from user input" and code.labels == []
    assert "- Where: `src/app.py`" in code.body and "/security/code_scanning/3" in code.body
    assert "- Fixed in: 2.15.0" in dependency.body
    stored = [p for kind, p in events.events if kind == "security_alert_filed"]
    assert [(p["kind"], p["number"], p["severity"]) for p in stored] == [
        ("code_scanning", 3, "high"),
        ("dependency", 7, "critical"),
    ]

    # The next day's read files nothing it filed before, and takes a new one.
    _serve(monkeypatch, [_alert("code_scanning", 3, "high"), _alert("code_scanning", 4, "critical")])
    again = asyncio.run(security_alerts.file_all(tracker, events))
    assert len(again) == 1 and len(tracker.created) == 3


def test_a_tracker_that_refuses_leaves_the_alert_to_be_filed_at_the_next_read(repo, monkeypatch):
    tracker, events = WritingTracker(), Events()
    tracker.refuse_at = 1
    _serve(monkeypatch, [_alert("code_scanning", 3, "high")])

    assert asyncio.run(security_alerts.file_all(tracker, events)) == []
    assert len(asyncio.run(security_alerts.file_all(tracker, events))) == 1


def test_a_repo_whose_alerts_cannot_be_read_is_logged_with_what_to_grant(repo, monkeypatch, caplog):
    _serve(monkeypatch, code_host.CodeHostError("GitHub answered 403", retryable=False, status=403))

    with caplog.at_level("WARNING", logger="meta_harness.planner"):
        assert asyncio.run(security_alerts.file_all(WritingTracker(), Events())) == []

    assert "Grant the GitHub App read access to code scanning alerts and Dependabot alerts" in caplog.text


async def test_the_code_hosts_alerts_are_read_as_one_list_and_a_kind_turned_off_gives_none(monkeypatch):
    async def github(name, permissions, method, path, **kwargs):
        if path.endswith("/code-scanning/alerts"):
            raise code_host.CodeHostError("no analysis found", retryable=False, status=404)
        return [
            {
                "number": 7,
                "html_url": "https://github.com/o/r/security/dependabot/7",
                "dependency": {"package": {"name": "pyjwt"}},
                "security_advisory": {"severity": "High", "summary": "PyJWT accepts a forged key"},
                "security_vulnerability": {"first_patched_version": {"identifier": "2.15.0"}},
            }
        ]

    monkeypatch.setattr(code_host, "_github", github)

    assert await code_host.security_alerts("o/r") == [
        {
            "kind": "dependency",
            "number": 7,
            "severity": "high",
            "summary": "PyJWT accepts a forged key",
            "where": "pyjwt",
            "fix": "2.15.0",
            "url": "https://github.com/o/r/security/dependabot/7",
        }
    ]
