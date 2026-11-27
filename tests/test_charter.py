"""The charter: one text, read from config/charter.txt, that ends every deciding prompt."""

import asyncio
import re
from pathlib import Path

import pytest

from meta_harness import charter
from meta_harness.outbound import rules as writing_rules
from meta_harness.run_service import activities
from meta_harness.run_service.shared import LoopPromptRequest


def test_the_shipped_charter_passes_its_startup_check_and_states_the_merge_test():
    charter.check_settings()

    text = charter.block()

    assert "merged work that makes the main branch better" in text
    assert "better with it than without it" in text
    assert "Ask a person only for what a person alone can give" in text


def test_a_prompt_gets_the_charter_after_it():
    assert charter.after("Review this.") == f"Review this.\n\n{charter.block()}"


@pytest.mark.parametrize("content", [None, "  \n"])
def test_a_missing_or_empty_charter_stops_a_service_and_names_the_file(monkeypatch, tmp_path, content):
    path = tmp_path / "charter.txt"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr(charter, "CHARTER_PATH", path)

    with pytest.raises(RuntimeError, match=r"charter\.txt.*config/charter\.txt"):
        charter.check_settings()
    assert charter.after("Review this.") == "Review this."


SRC = Path(__file__).resolve().parents[1] / "src" / "meta_harness"
# The control-plane model calls that decide something: a plan, a task's size, a split, a plan's fix, why a
# task failed. Writing an update or a name decides nothing, and carries only the writing rules.
DECIDERS = ("drafting.py", "sizing.py", "splits.py", "suggestions.py", "diagnose.py")


def test_each_model_call_that_decides_something_carries_the_charter_before_the_writing_rules():
    for name in DECIDERS:
        (path,) = [p for p in SRC.rglob(name) if "/v1/structured" in p.read_text(encoding="utf-8")]
        sent = re.findall(r'"system_prompt": (.*),\n', path.read_text(encoding="utf-8"))
        assert sent and all(v.startswith("writing_rules.after(charter.after(") for v in sent), (
            f"{name} decides something without the charter: wrap its system prompt in `charter.after(...)`"
        )


@pytest.mark.parametrize("kind", ["review", "fix"])
def test_a_review_and_a_fix_run_close_with_the_charter_then_the_writing_rules(kind):
    request = LoopPromptRequest(
        kind=kind, repo="o/r", title="A fix", description="Fix it.", checks=[], base="main", branch="mh/t-1"
    )

    prompt = asyncio.run(activities.loop_prompt(request))

    assert prompt.endswith(f"{charter.block()}\n\n{writing_rules.block()}")


def test_the_review_prompt_sorts_findings_into_blocking_and_follow_up():
    template = (activities.PROMPTS_DIR / "review-v6.txt").read_text(encoding="utf-8")

    assert "is the main branch better with it merged as it is than without it" in template
    assert "set blocking to false" in template
    assert "Use verdict changes only when at least one finding blocks" in template
