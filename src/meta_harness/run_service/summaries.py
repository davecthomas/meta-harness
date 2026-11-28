"""The run's summary for its pull request, with a fallback written from what the harness recorded.

A run is told to end its last message with its summary for the pull request between a SUMMARY_OPEN line and a
SUMMARY_CLOSE line (prompts/repo-task-v3.txt, prompts/fix-v2.txt). Only the marked part is the summary, so the
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
# A generic claim about the change's scope: a change verb whose object is only "the required files" or "nothing
# outside the paths" ("I changed only the required files", "touched nothing outside the task's paths"). It holds a
# change verb but says nothing about what the change is, so it is taken out before _CHANGE is looked for.
_SCOPE_VERB = r"(?:changed|edited|modified|touched|updated|wrote|made|kept|limited|restricted)"
_SCOPE_QUAL = (
    r"(?:required|allowed|needed|necessary|relevant|listed|expected|permitted|requested|named|given|specified"
    r"|in-scope|in\s+scope)"
)
_SCOPE_NOUN = r"(?:files?|paths?|code|changes?|areas?|directories|folders?)"
_SCOPE_TAIL = (
    r"(?:\s+(?:within|inside|in|under|to)\s+(?:the\s+)?(?:task'?s?|issue'?s?|ticket'?s?|required|allowed|given)?"
    r"\s*(?:paths?|scope|areas?))?"
)
_SCOPE = re.compile(
    rf"\b(?:{_SCOPE_VERB}\s+(?:only|just)\s+(?:the\s+|those\s+)?(?:{_SCOPE_QUAL}\s+)?{_SCOPE_NOUN}"
    rf"|(?:only|just)\s+{_SCOPE_VERB}\s+(?:the\s+|those\s+)?(?:{_SCOPE_QUAL}\s+)?{_SCOPE_NOUN}"
    rf"|{_SCOPE_VERB}\s+(?:the\s+|those\s+)?{_SCOPE_QUAL}\s+{_SCOPE_NOUN}"
    rf"|{_SCOPE_VERB}\s+(?:nothing|no\s+(?:other\s+)?{_SCOPE_NOUN})\s+(?:else\s+)?(?:outside|beyond)\b[^,;]*"
    rf"|{_SCOPE_VERB}\s+(?:the\s+)?(?:changes?|changeset|edits?|work)\s+(?:within|inside|to)\b[^,;]*"
    # The same claims in the passive: "no files outside the task paths were changed", "only the required files
    # were edited", "files outside the paths were left untouched".
    rf"|(?:no|nothing|none)\s+(?:other\s+)?(?:{_SCOPE_NOUN}\s+)?(?:else\s+)?(?:outside|beyond)\b[^,;.]*?"
    rf"\b(?:was|were|got|has\s+been|have\s+been)\s+{_SCOPE_VERB}"
    rf"|(?:only|just)\s+(?:the\s+|those\s+)?(?:{_SCOPE_QUAL}\s+)?{_SCOPE_NOUN}\s+"
    rf"(?:were|was|got|have\s+been|has\s+been)\s+{_SCOPE_VERB}"
    rf"|{_SCOPE_NOUN}\s+(?:outside|beyond)\b[^,;.]*?\b(?:was|were|have\s+been|has\s+been|stayed|remained|are|is)\s+"
    rf"(?:not\s+|left\s+)?(?:{_SCOPE_VERB}|untouched|unchanged))"
    rf"{_SCOPE_TAIL}",
    re.IGNORECASE,
)
# A path a scope claim names ("all changes stay inside `src/app`", "nothing outside src/app and tests was
# touched"): where the change stays, not what it is. It becomes "the paths" before _SCOPE and _CHANGE are looked
# for, so a scope claim that names its paths is narration like one that names none.
_PATH = r"(?:`[^`\n]*`|[\w.-]*[\w-]/[\w./-]*|[\w-]+\.(?:py|ts|js|tsx|json|md|txt|toml|yaml|yml|sql|sh)\b)"
_SCOPE_PATH = re.compile(
    rf"\b(within|inside|outside|beyond|under|(?:stays?|stayed|remains?|remained|kept|limited|confined|restricted)"
    rf"\s+(?:in|to))\s+(?:the\s+)?(?:(?:task|issue|ticket)'?s?\s+)?(?:paths?\s+|directory\s+|folders?\s+)?"
    rf"{_PATH}(?:\s*(?:,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+){_PATH})*",
    re.IGNORECASE,
)
# A path as the object of a scope claim ("I changed only src/app.py", "only touched `src/app` and `tests/`"):
# the claim says where the change stays, not what it is. The path becomes "the files", so _SCOPE finds the claim.
_PATHS = rf"{_PATH}(?:\s*(?:,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+){_PATH})*"
_SCOPE_OBJECT = re.compile(
    rf"\b({_SCOPE_VERB}\s+(?:only|just)|(?:only|just)\s+{_SCOPE_VERB})\s+(?:the\s+)?(?:files?\s+)?{_PATHS}",
    re.IGNORECASE,
)
# What the run did on its way, not what the change is: running its checks, keeping its plan ("I ran the checks
# and updated the plan."). It holds a change verb ("updated") but says nothing about the change, so it is taken
# out before _CHANGE is looked for, and a sentence that holds it is narration unless something else is left.
_WORK = re.compile(
    r"\b(?:ran|run|reran|re-ran|executed|repeated)\s+(?:all\s+|both\s+|each\s+of\s+)?(?:the\s+|my\s+|its\s+)?"
    r"(?:acceptance\s+|required\s+|listed\s+|given\s+)?"
    r"(?:checks?|tests?|test\s+suite|suite|linters?|lint|typechecks?|type\s+checks?)\b"
    r"|\b(?:updated|marked|followed|completed|finished|kept|ticked\s+off|checked\s+off)\s+"
    r"(?:the\s+|my\s+|its\s+)?(?:plan|checklist|todo\s+list|todos?|steps|plan\s+steps)\b"
    r"(?!\s+(?:module|parser|function|class|file|code|model|table|schema|type|format|view|page)\b)",
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
# A check command written as code ("Both checks `uv run pytest -q tests` pass"): a code span that starts with a
# command runner. It says how the change was checked, not what it is, so it is taken out before _CHANGE is looked
# for, along with the recorded acceptance checks wherever the text names them.
_COMMAND = re.compile(
    r"`\s*(?:\$\s*)?(?:uv|uvx|npm|npx|pnpm|yarn|pytest|ruff|mypy|tsc|cd|make|python3?|node|go|cargo|poetry|pip"
    r"|bash|sh|tox|nox|just|eslint|prettier|jest|vitest|black)\b[^`]*`",
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


def usable(text: str | None, checks: list[str] | None = None) -> bool:
    """True when the text can stand as the run's summary: it says something about the change in at least
    MIN_WORDS words, not counting its sentences of narration. `checks` are the run's acceptance check commands."""
    said = [s for s in _SENTENCE.split(text or "") if s.strip() and not narration(s, checks)]
    return len(re.findall(r"\w+", " ".join(said))) >= MIN_WORDS


def narration(sentence: str, checks: list[str] | None = None) -> bool:
    """True when the sentence narrates the run (what it does next, that its checks pass, that it kept to its
    paths, that it ran its checks or kept its plan) and says nothing about what the change is. A generic scope
    claim ("I changed only the required files", "no files outside the paths were changed") does not count as
    saying what the change is, though it holds a change verb, and neither do the paths a scope claim names ("all
    changes stay inside `src/app`") or a check command: one of `checks`, or code that starts with a command
    runner."""
    if _PROCESS.search(sentence):
        return True
    # A path a scope claim names reads as "the paths", so the claim is found and taken out whether it names its
    # paths or not. A sentence that only says where code lives ("The parser lives under `src/parser`") makes no claim.
    scoped = _scoped(sentence)
    claims = any(pattern.search(scoped) for pattern in (_STATUS, _SCOPE, _WORK))
    if not claims:
        return False
    named = sorted({c.strip() for c in checks or [] if c.strip()}, key=len, reverse=True)
    rest = _COMMAND.sub(" ", sentence)
    rest = re.sub(r"`[^`]*`", lambda span: " " if span.group(0).strip("` ") in named else span.group(0), rest)
    for check in named:
        rest = rest.replace(check, " ")
    return not _CHANGE.search(_SCOPE.sub(" ", _WORK.sub(" ", _scoped(rest))))


def _scoped(sentence: str) -> str:
    """The sentence with the paths its scope claims name read as "the paths" or "the files"."""
    return _SCOPE_OBJECT.sub(r"\1 the files", _SCOPE_PATH.sub(r"\1 the paths", sentence))


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
    checks = [
        c["check"]
        for c in _list((record or {}).get("checks"))
        if isinstance(c, dict) and isinstance(c.get("check"), str)
    ]
    if usable(summary, checks):
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
