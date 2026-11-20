"""The writing rules every prompt carries when a person will read what the model writes.

The outbound pipeline's deslop stage checks model-written text after it is written, and pays for a rewrite
when the scanner finds something. These rules come first: one short block, the same in every prompt, so the
text comes out clean and the rewrite rarely runs. The block is a file of the writing skill
(config/planner.json's `writing_pass.skill` and `writing_pass.rules`), so the rules a model is told and the
rules the scanner holds it to are kept in one place.

`block` reads the shipped file, config/skills/<skill>/<rules>, never the stored `skills` configuration: the
rules end every prompt, so what they say is decided by a pull request. The skill is locked
(config/skills.py), and `check_settings` stops a service whose lock list leaves it out.
"""

import logging

from meta_harness.config import skills as shipped_skills
from meta_harness.run_service import writing

log = logging.getLogger(__name__)


def _shipped() -> str:
    config = writing.settings()
    return (shipped_skills.skills_dir() / config["skill"] / config["rules"]).read_text(encoding="utf-8").strip()


def check_settings() -> None:
    """Raises RuntimeError naming what is wrong with writing_pass.rules, with the file it names, or with
    the lock on the writing skill."""
    writing.check_settings()
    shipped_skills.check_locked([writing.settings()["skill"]])
    scanner = shipped_skills.skills_dir() / writing.settings()["skill"] / writing.settings()["scanner"]
    if not scanner.is_file():
        raise RuntimeError(
            f"{writing.CONFIG_PATH.name}: writing_pass.scanner names {scanner.name}, which the shipped skill "
            f"does not hold. Name a file under config/skills/{writing.settings()['skill']}/"
        )
    try:
        found = _shipped()
    except OSError as err:
        config = writing.settings()
        raise RuntimeError(
            f"{writing.CONFIG_PATH.name}: writing_pass.rules names {config['rules']}, which the shipped "
            f"{config['skill']} skill does not hold ({err}). Name a file under config/skills/{config['skill']}/"
        ) from err
    if not found:
        config = writing.settings()
        raise RuntimeError(
            f"{writing.CONFIG_PATH.name}: writing_pass.rules names {config['rules']}, which is empty. "
            "Write the rules in it, one to a line"
        )


def block() -> str:
    """The rules, as a block to end a prompt with, from the shipped file. Never raises, and is never empty
    once `check_settings` has passed."""
    config = writing.settings()
    try:
        return _shipped()
    except OSError as err:
        log.error("no writing rules: %s. Restore config/skills/%s/%s", err, config["skill"], config["rules"])
        return ""


def after(prompt: str) -> str:
    """A prompt with the rules after it, for a model call whose answer a person reads."""
    rules = block()
    return f"{prompt}\n\n{rules}" if rules else prompt
