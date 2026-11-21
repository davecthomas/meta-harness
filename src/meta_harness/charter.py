"""The charter: what the meta-harness is for, given to every model that decides something.

The design's Goal seeking section states the first principles. A rule a model never reads does not change
what it does, so the principles are one short text, config/charter.txt, that ends the prompt of every run and
call that decides: a task's own run, a review, a fix, a plan, a failure's diagnosis. One text, so a reviewer
and the run it reviews are held to the same bar. What it says is decided by a pull request.

`check_settings` stops a service whose charter is missing or empty, at startup.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

CHARTER_PATH = Path(__file__).resolve().parents[2] / "config" / "charter.txt"


def _shipped() -> str:
    return CHARTER_PATH.read_text(encoding="utf-8").strip()


def check_settings() -> None:
    """Raises RuntimeError when the charter cannot be read or says nothing, naming the file to restore."""
    try:
        found = _shipped()
    except OSError as err:
        raise RuntimeError(
            f"{CHARTER_PATH.name}: the charter cannot be read ({err}). Restore config/{CHARTER_PATH.name}"
        ) from err
    if not found:
        raise RuntimeError(
            f"{CHARTER_PATH.name}: the charter is empty. Write the principles in config/{CHARTER_PATH.name}"
        )


def block() -> str:
    """The charter as a block for a prompt. Never raises, and is never empty once `check_settings` has passed."""
    try:
        return _shipped()
    except OSError as err:
        log.error("no charter: %s. Restore config/%s", err, CHARTER_PATH.name)
        return ""


def after(prompt: str) -> str:
    """A prompt with the charter after it, for a run or a model call that decides something."""
    charter = block()
    return f"{prompt}\n\n{charter}" if charter else prompt
