"""Credential redaction: off unless the task policy turns it on, and what it takes out when it is on."""

import asyncio
import logging

import pytest

from meta_harness import outbound
from meta_harness.config import store
from meta_harness.outbound import credentials
from meta_harness.run_service import writing

GH_TOKEN = "ghp_" + "a1B2" * 9


@pytest.fixture(autouse=True)
def no_pass(monkeypatch):
    async def check_all(texts, transport=None):
        return [None for _ in texts]

    monkeypatch.setattr(writing, "check_all", check_all)
    monkeypatch.setattr(credentials, "_last", False)


def _policy(monkeypatch, document: dict) -> None:
    monkeypatch.setattr(store, "current_document", lambda kind: document)


def _prepare(text: str) -> outbound.Checked:
    where = outbound.Context(outbound.GITHUB, outbound.COMMENT, outbound.FIXED)
    return asyncio.run(outbound.prepare(text, where))


def test_redaction_is_off_until_the_task_policy_turns_it_on(monkeypatch):
    text = f"The log shows {GH_TOKEN}."
    for document in ({}, {"credential_redaction": False}, {"credential_redaction": "yes"}):
        _policy(monkeypatch, document)
        made = _prepare(text)
        assert made == text and not [o for o in made.report if o.stage == "redact"]

    _policy(monkeypatch, {"credential_redaction": True})
    made = _prepare(text)
    assert made == "The log shows [redacted]." and outbound.redactions([made]) == 1


def test_a_policy_that_cannot_be_read_keeps_the_switch_as_it_was_last_read(monkeypatch):
    def unreadable(kind):
        raise RuntimeError("no database")

    monkeypatch.setattr(store, "current_document", unreadable)
    assert credentials.enabled() is False, "never read: off"
    _policy(monkeypatch, {"credential_redaction": True})
    assert credentials.enabled() is True
    monkeypatch.setattr(store, "current_document", unreadable)
    assert credentials.enabled() is True, "a failed read lets no credential out of a deployment that turned it on"


@pytest.mark.parametrize(
    ("kind", "text", "secret"),
    [
        ("GitHub token", f"token {GH_TOKEN} here", GH_TOKEN),
        ("user name and password in a URL", "git clone https://deploy:hunter2pw@example.com/o/r.git", "hunter2pw"),
        ("user name and password in a URL", "postgres://mh:S3cr3tPass!@10.0.0.5/mh", "S3cr3tPass!"),
        ("Google OAuth token", "token ya29." + "a0AfH6SMBx7" * 4, "a0AfH6SMBx7"),
        ("Slack webhook URL", "https://hooks.slack.com/services/T00000000/B00000000/" + "X" * 24, "X" * 24),
        ("npm token", "npm_" + "a" * 36, "a" * 36),
        ("Basic authorization header", "Authorization: Basic ZGVwbG95Omh1bnRlcjI=", "ZGVwbG95"),
        ("bearer token", "Authorization: Bearer " + "x" * 30, "x" * 30),
        ("private key block", "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc"),
    ],
)
def test_each_shape_of_credential_is_replaced_and_named(kind, text, secret):
    cleaned, found = credentials.redact(text)
    assert secret not in cleaned and "[redacted]" in cleaned
    assert found == {kind: 1}


def test_this_services_own_secrets_are_replaced_by_value(monkeypatch):
    monkeypatch.setenv("MH_SERVICE_KEY", "9f8e7d6c5b4a39281706f5e4")
    monkeypatch.setenv("MH_DATABASE_URL", "postgresql://mh:longdbpassword1@db/mh")
    monkeypatch.setenv("SOME_PRIVATE_KEY", "/etc/meta-harness/app.pem")
    monkeypatch.setenv("SHORT_TOKEN", "abc")
    text = "key 9f8e7d6c5b4a39281706f5e4, password longdbpassword1, file /etc/meta-harness/app.pem, abc"
    cleaned, found = credentials.redact(text)
    assert cleaned == "key [redacted], password [redacted], file /etc/meta-harness/app.pem, abc"
    assert found == {credentials.OWN: 2}


def test_ordinary_text_and_personal_data_are_left_alone():
    text = (
        "Ada Lovelace <ada@example.com>, 555-0100, 12 Main St. See https://example.com:8080/a@b and "
        "git@github.com:o/r.git. A basic internationalization fix; ghp_short and sk-1 are not credentials."
    )
    assert credentials.redact(text) == (text, {})


def test_a_redaction_is_logged_with_its_kind_and_never_its_value(monkeypatch, caplog):
    _policy(monkeypatch, {"credential_redaction": True})
    with caplog.at_level(logging.WARNING, logger="meta_harness.outbound.stages"):
        _prepare(f"The log shows {GH_TOKEN}.")
    [said] = [record.getMessage() for record in caplog.records]
    assert "1 GitHub token" in said and "Replace each one" in said and GH_TOKEN not in said
