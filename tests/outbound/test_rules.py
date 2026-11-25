"""The writing rules: one block, from the writing skill, in every prompt whose answer a person reads."""

import asyncio
import re
from pathlib import Path

import pytest

from meta_harness.config import skills
from meta_harness.config import store as config_store
from meta_harness.outbound import rules
from meta_harness.run_service import activities, writing
from meta_harness.run_service.shared import LoopPromptRequest

SRC = Path(__file__).resolve().parents[2] / "src" / "meta_harness"
SHIPPED = (skills.skills_dir() / "no-ai-slop2" / "WRITING-RULES.md").read_text(encoding="utf-8").strip()
# The run prompts that end in text for people: a pull request's summary, a review, a plan's issues.
RUN_PROMPTS = ("repo-task-v1", "fix-v1", "review-v5", "plan-v1", "plan-review-v1")


def _skills(monkeypatch, files: dict[str, str] | Exception) -> None:
    def current_document(kind: str) -> dict:
        if isinstance(files, Exception):
            raise files
        return {"skills": {"no-ai-slop2": {"files": files}}}

    monkeypatch.setattr(config_store, "current_document", current_document)


def test_the_rules_come_from_the_shipped_file_whatever_the_stored_skill_holds(monkeypatch):
    # The rules end every prompt, so a stored copy never decides them: not a changed one, not an unreadable one.
    for held in ({"WRITING-RULES.md": "An admin's rules.\n"}, {"SKILL.md": "..."}, RuntimeError("no database")):
        _skills(monkeypatch, held)
        assert rules.block() == SHIPPED
    assert rules.after("You write an update.") == f"You write an update.\n\n{SHIPPED}"
    assert SHIPPED.startswith("Writing rules") and "under 25 words" in SHIPPED


def test_a_service_does_not_start_with_the_writing_skill_unlocked(monkeypatch):
    rules.check_settings()

    def refuse(needed: list[str]) -> None:
        raise RuntimeError(f"{needed[0]} must be locked")

    monkeypatch.setattr(skills, "check_locked", refuse)
    with pytest.raises(RuntimeError, match="no-ai-slop2 must be locked"):
        rules.check_settings()


def test_the_rules_setting_is_checked_at_startup_and_a_bad_one_is_named(monkeypatch):
    rules.check_settings()
    good = dict(writing.settings())
    for bad in ("", None, "NO-SUCH-FILE.md"):
        monkeypatch.setattr(writing, "settings", lambda bad=bad: {**good, "rules": bad})
        with pytest.raises(RuntimeError, match="writing_pass.rules"):
            rules.check_settings()


def test_every_model_call_made_here_ends_its_system_prompt_with_the_rules():
    """A control-plane model call goes to the HTTP Gateway's /v1/structured, and a person reads what each
    one writes: an update, a goal, a split's parts, a name, a reason. A new one that leaves the rules out
    fails here."""
    callers = [
        path
        for path in sorted(SRC.rglob("*.py"))
        if "/v1/structured" in path.read_text(encoding="utf-8") and path.name != "fake_gateway.py"
    ]
    assert len(callers) >= 8
    for path in callers:
        text = path.read_text(encoding="utf-8")
        sent = re.findall(r'"system_prompt": (.*),\n', text)
        assert sent, f"{path.name} posts to /v1/structured with no system prompt"
        for value in sent:
            assert value.startswith("writing_rules.after("), (
                f'{path.name} sends "system_prompt": {value}, without the writing rules: wrap it in '
                "`writing_rules.after(...)` (meta_harness.outbound.rules)"
            )


def test_each_run_prompt_that_writes_for_people_carries_the_rules(monkeypatch):
    _skills(monkeypatch, {"WRITING-RULES.md": SHIPPED})
    for name in RUN_PROMPTS:
        template = (activities.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
        assert template.rstrip().endswith("$writing_rules"), f"prompts/{name}.txt does not end with $writing_rules"

    request = LoopPromptRequest(
        kind="fix", repo="o/r", title="A fix", description="Fix it.", checks=[], base="main", branch="mh/t-1"
    )
    prompt = asyncio.run(activities.loop_prompt(request))
    assert prompt.endswith(SHIPPED) and "$writing_rules" not in prompt
