"""The run's summary for its pull request, with a fallback written from what the harness recorded.

A run is told to finish with a summary, and its last turn's text is that summary. Some runs leave none: the
last turn called a tool (a turn limit, an operator's stop), the text was empty, or it says next to nothing
("Done."). Then the summary is written here from the harness server's record of the run (`GET /record`,
images/harness-pi/server/run-record.ts): the files it wrote or edited, how each acceptance check ended, its
last plan and the commands it ran. The fallback says that it was written from the record, so no one reads it
as the run's own words.

A harness image from before `GET /record` answers 404, and then the summary is the run's last text as the
event stream showed it, or a fallback from the files the events named.
"""

import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

# A summary of fewer words than this ("Done.", "All done.") says nothing about the change.
MIN_WORDS = 3
# The record's lists are cut to this many lines in the fallback, with a count of the rest.
MAX_LINES = 30
# The run's own words, quoted in the fallback when they were too few to stand alone, are cut to this length.
QUOTE_CHARS = 300

FALLBACK_LEAD = (
    "The run ended without a usable summary, so the meta-harness wrote this one from what the harness recorded."
)


async def fetch(client: httpx.AsyncClient, run_id: str) -> dict[str, Any] | None:
    """The harness server's record of the run, or None when it has none: an image from before GET /record,
    a run it does not know, or an answer that cannot be read. A summary is never worth failing the run for."""
    try:
        response = await client.get("/record", params={"run_id": run_id})
    except httpx.HTTPError as err:
        log.warning("run %s: could not read the harness server's record of the run: %s", run_id, err)
        return None
    if response.status_code != 200:
        return None
    try:
        record = response.json()
    except ValueError:
        log.warning("run %s: the harness server's record of the run is not JSON", run_id)
        return None
    return record if isinstance(record, dict) else None


def usable(text: str | None) -> bool:
    """True when the text can stand as the run's summary: it says something in at least MIN_WORDS words."""
    return bool(text) and len(re.findall(r"\w+", text or "")) >= MIN_WORDS


def choose(answer: str | None, record: dict[str, Any] | None, files_changed: list[str]) -> tuple[str | None, str]:
    """The summary for the pull request, and where it came from: `run`, `recorded` or `none`.

    With a record, its final text is the run's own summary: the whole of the last turn's text, and only when
    that turn called no tool. Without one, `answer` is, as the event stream showed it. A summary that is not
    usable gives way to one written from the record, or from `files_changed` when there is no record; with
    nothing recorded either, the run's text stands as it is.
    """
    text = _text(record.get("final_text")) if record is not None else _text(answer)
    if usable(text):
        return text, "run"
    fallback = _fallback(text, record or {"files_changed": files_changed})
    if fallback:
        return fallback, "recorded"
    return text, "none"


def _fallback(text: str | None, record: dict[str, Any]) -> str | None:
    """The summary written from the record, or None when the record holds nothing to write it from."""
    files = [str(f) for f in _list(record.get("files_changed")) if isinstance(f, str) and f]
    checks = [c for c in _list(record.get("checks")) if isinstance(c, dict) and isinstance(c.get("check"), str)]
    plan = [s for s in _list(record.get("plan")) if isinstance(s, dict) and isinstance(s.get("step"), str)]
    commands = record.get("commands") if isinstance(record.get("commands"), dict) else {}
    run = _count(commands.get("run"))
    failed = _count(commands.get("failed"))
    if not (files or checks or plan or run):
        return None

    lines = [FALLBACK_LEAD, ""]
    if text:
        quoted = text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "..."
        lines += [f"The run's last words: {' '.join(quoted.split())}", ""]
    if files:
        lines += ["Files the run wrote or edited:", *_cut([f"- `{f}`" for f in files]), ""]
    else:
        lines += ["The run wrote or edited no file with its write or edit tools.", ""]
    if checks:
        lines += ["Acceptance checks, as the run last ran each one:", *_cut([_check_line(c) for c in checks]), ""]
    if plan:
        lines += ["The run's last plan:", *_cut([f"- {s.get('status') or 'pending'}: {s['step']}" for s in plan]), ""]
    if run:
        lines.append(f"Commands run: {run}, of which {failed} failed.")
    return "\n".join(lines).rstrip()


def _check_line(check: dict[str, Any]) -> str:
    if not check.get("ran"):
        outcome = "no command the run made contained it"
    elif check.get("passed"):
        outcome = "passed"
    else:
        outcome = "failed"
    return f"- `{check['check']}`: {outcome}"


def _cut(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_LINES:
        return lines
    return [*lines[:MAX_LINES], f"- and {len(lines) - MAX_LINES} more"]


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
