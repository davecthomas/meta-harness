"""The outbound pipeline: every text written for people goes through its stages, in order, or is refused."""

import asyncio
import re
from pathlib import Path

import pytest

from meta_harness import outbound
from meta_harness.outbound import credentials, stages
from meta_harness.run_service import code_host, provenance, writing
from meta_harness.trackers import GitHubTracker, OwnTracker
from meta_harness.trackers.jira import JiraTracker

SRC = Path(__file__).resolve().parents[2] / "src" / "meta_harness"
GH_TOKEN = "ghp_" + "a1B2" * 9


def _prepare(text: str, **context) -> outbound.Checked:
    fields = {"destination": outbound.GITHUB, "kind": outbound.COMMENT, "source": outbound.FIXED, **context}
    return asyncio.run(outbound.prepare(text, outbound.Context(**fields)))


@pytest.fixture(autouse=True)
def redaction_on(monkeypatch):
    """Credential redaction turned on, as a deployment's task policy turns it on (test_credentials.py has
    the switch itself)."""
    monkeypatch.setattr(credentials, "enabled", lambda: True)


@pytest.fixture
def no_pass(monkeypatch):
    """The writing pass, recorded and not run: each text it is given, returned as it was."""
    seen: list[str] = []

    async def check_all(texts, transport=None):
        seen.extend(texts)
        return [writing.Outcome(text=t) for t in texts]

    monkeypatch.setattr(writing, "check_all", check_all)
    return seen


def test_a_secret_is_redacted_whoever_wrote_the_text_and_before_any_model_sees_it(no_pass):
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    text = f"The log shows {GH_TOKEN}, Authorization: Bearer {'x' * 30} and\n{key}\nthen it passed."

    for source in outbound.SOURCES:
        made = _prepare(text, source=source)
        assert GH_TOKEN not in made and "MIIabc" not in made and "x" * 30 not in made
        assert made.count("[redacted]") == 3 and made.endswith("then it passed.")
    assert all(GH_TOKEN not in seen for seen in no_pass), "the writing pass's model never sees the secret"
    assert outbound.redactions([_prepare(text)]) == 3
    assert _prepare("ghp_short and sk-1 are not secrets") == "ghp_short and sk-1 are not secrets"


def test_only_model_written_text_goes_through_the_writing_pass_and_never_a_title(no_pass):
    _prepare("A model wrote this.", source=outbound.GENERATED)
    _prepare("We wrote this.", source=outbound.FIXED)
    _prepare("An operator wrote this.", source=outbound.PERSON)
    _prepare("A model's title", source=outbound.GENERATED, kind=outbound.TITLE)
    assert no_pass == ["A model wrote this."]


def test_the_writing_pass_rewrite_is_what_is_posted_and_its_outcome_rides_on_the_text(monkeypatch):
    async def check_all(texts, transport=None):
        return [writing.Outcome(text="Plain words.", before=texts[0], findings=2, rewritten=True, kept=True)]

    monkeypatch.setattr(writing, "check_all", check_all)
    made = _prepare("A rich tapestry of words.", source=outbound.GENERATED)
    assert made == "Plain words."
    [outcome] = outbound.writing_outcomes([made])
    assert outcome.kept and writing.record("comment", [outcome])["kept"] == 1
    assert outbound.writing_outcomes([_prepare("Fixed.")]) == [None]


def test_a_text_is_cut_to_what_its_destination_takes_and_says_so(no_pass):
    long = "word " * 20000
    jira = _prepare(long, destination=outbound.JIRA, kind=outbound.BODY)
    assert len(jira) < 31200 and jira.endswith("characters did not fit here.]")
    assert len(_prepare(long, kind=outbound.BODY)) > len(jira), "GitHub takes more than Jira"
    title = _prepare("A title " * 60, kind=outbound.TITLE)
    assert len(title) <= 250 and title.endswith("…")
    assert _prepare("Short.") == "Short."


def test_the_provenance_line_goes_last_after_the_cut_and_never_on_a_title(no_pass):
    signed = provenance.Provenance(provenance.COMMENT, goal="g-1")
    made = _prepare("word " * 20000, destination=outbound.JIRA, provenance=signed)
    assert made.endswith(provenance.line(signed)) and "did not fit here.]\n\nProvenance:" in made
    assert [o.stage for o in made.report] == ["redact", "fit", "stamp"]
    assert "Provenance" not in _prepare("A title", kind=outbound.TITLE, provenance=signed)


def test_a_stage_that_fails_passes_the_text_on_and_a_blocker_stops_the_post(monkeypatch):
    class Broken(stages.Stage):
        name = "broken"

        async def run(self, text, context):
            raise RuntimeError("no disk")

    class Stops(stages.Stage):
        name = "review"
        blocks = True

        async def run(self, text, context):
            return outbound.Result(text, blocked="it names a production host. Take the host out, then post again")

    monkeypatch.setattr(stages, "STAGES", (Broken(), stages.Redact()))
    made = _prepare(f"keep going {GH_TOKEN}")
    assert made == "keep going [redacted]" and made.report[0].error == "RuntimeError: no disk"

    monkeypatch.setattr(stages, "STAGES", (Stops(),))
    with pytest.raises(outbound.Blocked, match="the review stage stopped the text: it names a production host"):
        _prepare("see db-prod-7")
    monkeypatch.setattr(Stops, "run", Broken.run)
    with pytest.raises(outbound.Blocked, match="could not check the text .* so it was not posted. Try again"):
        _prepare("anything")


def test_every_adapter_that_writes_to_the_outside_refuses_text_that_skipped_the_pipeline():
    said = "must come through the outbound pipeline"
    ok = outbound.trusted("fine")
    calls = [
        GitHubTracker().create_issue("o/r", "title", ok, []),
        GitHubTracker().create_issue("o/r", ok, "body", []),
        GitHubTracker().comment("github:o/r#1", "body"),
        JiraTracker().create_issue("o/r", ok, "body", []),
        JiraTracker().comment("jira:VL-1", "body"),
        OwnTracker().create_issue("o/r", ok, "body", []),
        OwnTracker().comment("mh:i-000000000000", "body"),
        code_host.comment("o/r", 1, "body"),
        code_host.comment_on_lines("o/r", 1, "sha", "a.py", 1, "body"),
        code_host.post_review("o/r", 1, "sha", "body", []),
        code_host.post_review("o/r", 1, "sha", ok, [{"path": "a.py", "line": 1, "body": "inline"}]),
        code_host.add_to_description("o/r", "https://github.com/o/r/pull/1", "Fix round 1", "text", Path("x")),
    ]
    for call in calls:
        with pytest.raises(TypeError, match=said):
            asyncio.run(call)


def test_nothing_under_src_marks_a_text_checked_except_the_pipeline_itself():
    """`Checked(...)` and `trusted(...)` are the only ways to make a text the adapters take: the first belongs
    to the pipeline, the second to tests."""
    made = re.compile(r"\b(?:outbound\.)?(?:Checked|trusted)\(")
    found = [
        f"{path.relative_to(SRC)}:{n}"
        for path in SRC.rglob("*.py")
        if path.parent.name != "outbound"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if made.search(line)
    ]
    assert not found, f"{found}: send the text through outbound.prepare instead"


def test_a_context_names_a_known_destination_kind_and_source():
    with pytest.raises(ValueError, match="destination must be one of github, jira, own"):
        outbound.Context("slack")
    assert outbound.destination_of("jira:VL-1") == outbound.JIRA
    assert outbound.destination_of("mh:i-1") == outbound.OWN and outbound.destination_of("github") == outbound.GITHUB
