"""The run's summary for its pull request, with a fallback written from what the harness recorded.

A run is told to end its last message with its summary for the pull request between a SUMMARY_OPEN line and a
SUMMARY_CLOSE line (prompts/repo-task-v2.txt, prompts/fix-v2.txt). Only the marked part is the summary, so the
narration a model writes around it ("Both checks pass.", "Here is the summary:") never reaches the pull
request. Some runs leave none: the last turn called a tool (a turn limit, an operator's stop), its text marks
no summary, or the marked part says next to nothing ("Done.") or is only narration ("Both checks pass and the
changeset stays within the required paths."). Then the summary is written here from the harness server's record
of the run (`GET /record`, images/harness-pi/server/run-record.ts): the files it wrote or edited, how each
acceptance check ended and how many commands it ran. The fallback says plainly that the run left no summary, and
holds none of the run's own words: not its last text, and not its plan, whose steps the run wrote itself through
update_plan. Publish adds the files the branch changes that the record does not name (pr_summary.section).

A harness image from before `GET /record` answers 404, and then the summary is the marked part of the run's
last text as the event stream showed it, or a fallback from the files the events named.
"""

import logging
import re
from typing import Any

import httpx

from meta_harness.run_service.pr_summary import FALLBACK_PREFIX

log = logging.getLogger(__name__)

# A summary of fewer words than this ("Done.", "All done.") says nothing about the change.
MIN_WORDS = 3
# The record's lists are cut to this many lines in the fallback, with a count of the rest.
MAX_LINES = 30

# The lines that mark the run's summary in its last message. The prompts name them as written here. Each counts
# only on a line of its own, so a summary that names a marker in its text is not cut there.
SUMMARY_OPEN = "<pr-summary>"
SUMMARY_CLOSE = "</pr-summary>"
_MARKED = re.compile(
    rf"^[ \t]*{re.escape(SUMMARY_OPEN)}[ \t]*$(.*?)(?:^[ \t]*{re.escape(SUMMARY_CLOSE)}[ \t]*$|\Z)",
    re.DOTALL | re.MULTILINE,
)

FALLBACK_LEAD = f"{FALLBACK_PREFIX}, so this section lists what the run service recorded of the run."

# A sentence of narration: what the run did or is about to do, or how its checks ended, with nothing about what
# the change is. _PROCESS is narration wherever it appears; _STATUS only in a sentence that names no change.
_PROCESS = re.compile(
    r"^\W*(done|all done|finished|complete|completed|ok|okay|great|perfect|summary|pr summary)\W*$"
    r"|\b(here is|here's|below is|let me|i will|i'll|i am going to|i'm going to|now i|next,? i|i have finished"
    r"|i'm done|i am done|(the )?(work|task) is (done|complete|finished)|everything is in place|anything else)\b",
    re.IGNORECASE,
)
_STATUS = re.compile(
    r"\b(checks?|tests?|lint|linting|typechecks?|type checks?|suite|build|ruff|pytest)\b.*"
    r"\b(pass|passes|passed|passing|green|succeed|succeeds|succeeded|clean)\b"
    r"|\bpaths?\b.*\b(stays?|stayed|within|inside|outside)\b|\b(stays?|stayed|within|inside|outside)\b.*\bpaths?\b",
    re.IGNORECASE,
)
# What marks a sentence as saying what the change is: a change verb, code, or a file path. "Change" is left out,
# since narration uses it as a noun ("The change stays inside the task's paths.").
_CHANGE = re.compile(
    r"`|\b[\w-]+(/[\w.-]+)+|\b[\w-]+\.(py|ts|js|tsx|json|md|txt|toml|yaml|yml|sql|sh)\b"
    r"|\b(add|adds|added|fix|fixes|fixed|changed|remove|removes|removed|rename|renames|renamed"
    r"|replace|replaces|replaced|update|updates|updated|rewrite|rewrites|rewrote|refactor|refactors|refactored"
    r"|move|moves|moved|introduce|introduces|introduced|implement|implements|implemented|delete|deletes|deleted"
    r"|drop|drops|dropped|split|splits)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


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
    """True when the text can stand as the run's summary: it says something about the change in at least
    MIN_WORDS words, not counting its sentences of narration."""
    said = [s for s in _SENTENCE.split(text or "") if s.strip() and not narration(s)]
    return len(re.findall(r"\w+", " ".join(said))) >= MIN_WORDS


def narration(sentence: str) -> bool:
    """True when the sentence narrates the run (what it does next, that its checks pass, that it kept to its
    paths) and says nothing about what the change is."""
    if _PROCESS.search(sentence):
        return True
    return bool(_STATUS.search(sentence)) and not _CHANGE.search(sentence)


def marked(text: str | None) -> str | None:
    """The summary the text marks: what follows its last SUMMARY_OPEN line, up to a SUMMARY_CLOSE line or the end
    of the text (a text cut short loses its closing line). None when the text marks none, or the marked part is empty."""
    found = _MARKED.findall(text or "")
    return _text(found[-1]) if found else None


def choose(answer: str | None, record: dict[str, Any] | None, files_changed: list[str]) -> tuple[str | None, str]:
    """The summary for the pull request, and where it came from: `run`, `recorded` or `none`.

    With a record, its final text holds the run's summary: the last turn's text, and only when that turn
    called no tool. Without one, `answer` does, as the event stream showed it. The summary is the part of
    that text between the markers. A text that marks none, or a marked part that is not usable (too short, or
    only narration), gives way to one written from the record, or from `files_changed` when there is no record. With nothing recorded
    either, there is no summary, and Publish writes its own from the files the branch changes.
    """
    summary = marked(_text(record.get("final_text")) if record is not None else _text(answer))
    if usable(summary):
        return summary, "run"
    fallback = _fallback(record or {"files_changed": files_changed})
    if fallback:
        return fallback, "recorded"
    return None, "none"


def _fallback(record: dict[str, Any]) -> str | None:
    """The summary written from the record, or None when the record holds nothing to write it from. It holds only
    file paths, check commands with their outcomes, and counts: never the run's plan, whose steps the run wrote."""
    files = [str(f) for f in _list(record.get("files_changed")) if isinstance(f, str) and f]
    checks = [c for c in _list(record.get("checks")) if isinstance(c, dict) and isinstance(c.get("check"), str)]
    commands = record.get("commands") if isinstance(record.get("commands"), dict) else {}
    run = _count(commands.get("run"))
    failed = _count(commands.get("failed"))
    if not (files or checks or run):
        return None

    lines = [FALLBACK_LEAD, ""]
    if files:
        lines += ["Files the run wrote or edited:", *_cut([f"- `{f}`" for f in files]), ""]
    else:
        lines += ["The run wrote or edited no file with its write or edit tools.", ""]
    if checks:
        lines += ["Acceptance checks, as the run last ran each one:", *_cut([_check_line(c) for c in checks]), ""]
    elif record.get("checks") == []:
        lines += ["The run was given no acceptance check.", ""]
    if run:
        lines.append(f"Commands run: {run}, of which {failed} failed.")
    return "\n".join(lines).rstrip()


def _check_line(check: dict[str, Any]) -> str:
    if not check.get("ran"):
        outcome = "no command the run made contained it"
    elif check.get("passed") is True:
        outcome = "passed"
    elif check.get("passed") is False:
        outcome = "failed"
    else:
        outcome = "ran, but the command's exit status does not show how it ended"
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
