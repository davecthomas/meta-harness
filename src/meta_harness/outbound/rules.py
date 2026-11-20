"""The writing rules every prompt carries when a person will read what the model writes.

The outbound pipeline's deslop stage checks model-written text after it is written, and pays for a rewrite
when the scanner finds something. These rules come first: one short block, the same in every prompt, so the
text comes out clean and the rewrite rarely runs. The block is a file of the writing skill
(config/planner.json's `writing_pass.skill` and `writing_pass.rules`), so the rules a model is told and the
rules the scanner holds it to are kept in one place.

`block` reads the file from the active `skills` configuration, where an admin may have changed it. A
deployment whose skill was imported before the file shipped does not hold it, and a configuration that
cannot be read holds nothing: both fall back to the shipped file, config/skills/<skill>/<rules>. A prompt is
never built without the rules because a read failed.
"""

import logging

from meta_harness.config import skills as shipped_skills
from meta_harness.config import store as config_store
from meta_harness.run_service import writing

log = logging.getLogger(__name__)


def _shipped() -> str:
    config = writing.settings()
    return (shipped_skills.skills_dir() / config["skill"] / config["rules"]).read_text(encoding="utf-8").strip()


def check_settings() -> None:
    """Raises RuntimeError naming what is wrong with writing_pass.rules, or with the file it names."""
    writing.check_settings()
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
    """The rules, as a block to end a prompt with. Never raises, and is never empty once `check_settings`
    has passed: the shipped file answers when the active configuration cannot."""
    config = writing.settings()
    try:
        skills = config_store.current_document("skills").get("skills") or {}
        found = ((skills.get(config["skill"]) or {}).get("files") or {}).get(config["rules"])
        if isinstance(found, str) and found.strip():
            return found.strip()
    except Exception as err:  # noqa: BLE001 - a prompt is built whatever stops this read
        log.warning("the writing rules were read from the shipped file: the skills configuration failed: %s", err)
    try:
        return _shipped()
    except OSError as err:
        log.error("no writing rules: %s. Restore config/skills/%s/%s", err, config["skill"], config["rules"])
        return ""


def after(prompt: str) -> str:
    """A prompt with the rules after it, for a model call whose answer a person reads."""
    rules = block()
    return f"{prompt}\n\n{rules}" if rules else prompt
