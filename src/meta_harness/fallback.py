"""A setting with a safe default that is set badly: the service runs on the default and says so.

A bad setting used to stop its service at startup, naming it. For a setting whose default does no harm, such as
how often a loop runs, the owner chose otherwise on 2026-11-24 (VL-14520): the service uses the default, logs an
error naming the setting, the value and the rule, and records it in its startup event (`settings_checked`), which
the planner shows in what needs the operator until the setting is fixed and the service restarted. A setting
whose default could cost money, widen access or hide a mistake still stops its service: those are not read here.
"""

import logging
import math
import os
from typing import Any

log = logging.getLogger("meta_harness.settings")

# This process's settings that fell back to their default, by name: what its startup event reports.
DEFAULTED: dict[str, dict[str, str]] = {}


def defaulted(name: str, raw: str, default: Any, rule: str) -> Any:
    """Record that a setting fell back, log it as an error, and return the default."""
    fix = f"Set {name} to {rule}, or remove it to keep {default}, then restart the service"
    log.error("%s is %r, which is not %s: using %s. %s", name, raw, rule, default, fix)
    DEFAULTED[name] = {"setting": name, "value": raw[:200], "default": str(default), "fix": fix}
    return default


def seconds(name: str, default: float) -> float:
    """A number of seconds from the environment, 0 or more: the default when it is left out, and when it is not
    such a number (with the fallback recorded)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if math.isnan(value) or math.isinf(value) or value < 0:
        return defaulted(name, raw, default, "a number of seconds, 0 or more")
    return value


def checked(service: str) -> dict[str, Any]:
    """The payload of the service's `settings_checked` startup event: which settings fell back, if any."""
    return {"service": service, "defaulted": list(DEFAULTED.values())}
