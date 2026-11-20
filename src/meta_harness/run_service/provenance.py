"""Provenance: what the meta-harness says about each thing it writes on a code host or a tracker.

Every pull request description, review, comment, issue and commit the meta-harness writes carries where it
came from: the meta-harness version that wrote it, the kind of post, and as many of its goal, task, run,
model and operator as are known where it is written. A reader can tell from the post alone that a model
wrote it, which one, and which task in the console to open for the rest.

Text for people to read ends with one line, plain enough for any tracker to show as it is:

    Provenance: meta-harness 0.49.0; kind review; task t-12; model harness:pi-review/claude:claude-opus-5-5

A commit ends with git trailers instead, which `git log --format=%(trailers)` reads:

    Meta-Harness-Version: 0.49.0
    Meta-Harness-Task: t-12
    Meta-Harness-Run: run-task-t-12:run-1

The line is added after the writing pass, so the pass never rewrites it, and `parse` reads it back.
"""

import re
from dataclasses import dataclass

import meta_harness

PULL_REQUEST = "pull_request"
REVIEW = "review"
COMMENT = "comment"
ISSUE = "issue"
COMMIT = "commit"
KINDS = (PULL_REQUEST, REVIEW, COMMENT, ISSUE, COMMIT)

PREFIX = "Provenance:"
# Each field in the order the line and the trailers give it, with its trailer's name.
FIELDS = {
    "goal": "Meta-Harness-Goal",
    "task": "Meta-Harness-Task",
    "run": "Meta-Harness-Run",
    "model": "Meta-Harness-Model",
    "round": "Meta-Harness-Round",
    "by": "Meta-Harness-By",
}
VERSION_TRAILER = "Meta-Harness-Version"
LINE = re.compile(r"^Provenance:[ \t]*meta-harness[ \t]+(\S+)(.*)$", re.MULTILINE)
TRAILER = re.compile(r"^(Meta-Harness-[A-Za-z]+):[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Provenance:
    """Where one post came from. Empty fields are left out of what is written."""

    kind: str
    goal: str = ""
    task: str = ""
    run: str = ""
    model: str = ""
    round: int | None = None
    # The operator a post speaks for, such as the author of a line comment from the console.
    by: str = ""
    version: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"provenance kind must be one of {', '.join(KINDS)}, not {self.kind!r}")

    def fields(self) -> dict[str, str]:
        """The version, then each known field, in order, as text with no line breaks or semicolons."""
        values = {"version": self.version or meta_harness.__version__}
        for name in FIELDS:
            value = getattr(self, name)
            if value is not None and str(value).strip():
                values[name] = _clean(str(value))
        return values


def _clean(value: str) -> str:
    return " ".join(value.replace(";", ",").split())


def line(p: Provenance) -> str:
    """The provenance line for text people read."""
    values = p.fields()
    parts = [f"meta-harness {values.pop('version')}", f"kind {p.kind}"]
    parts += [f"{name} {value}" for name, value in values.items()]
    return f"{PREFIX} {'; '.join(parts)}"


def stamp(text: str, p: Provenance) -> str:
    """The text with its provenance line at the end. A provenance line already at its end is replaced, so a
    text stamped twice carries one."""
    body = text.rstrip()
    last = body.rsplit("\n", 1)
    if LINE.fullmatch(last[-1].strip()):
        body = last[0].rstrip() if len(last) > 1 else ""
    return f"{body}\n\n{line(p)}" if body else line(p)


def trailers(p: Provenance) -> list[str]:
    """The provenance as git trailer lines, for a commit message."""
    values = p.fields()
    found = [f"{VERSION_TRAILER}: {values.pop('version')}"]
    return found + [f"{FIELDS[name]}: {value}" for name, value in values.items()]


def commit_message(message: str, p: Provenance, *more: str) -> str:
    """The commit message with the provenance trailers in its last paragraph, after any trailer lines given
    in more (such as `Version: fix`)."""
    block = "\n".join([*more, *trailers(p)])
    message = message.strip()
    return f"{message}\n\n{block}" if message else block


def parse(text: str) -> list[dict[str, str]]:
    """Each provenance line in the text, in order, as its fields: version, kind and the rest it names."""
    found = []
    for match in LINE.finditer(text or ""):
        values = {"version": match.group(1).rstrip(";")}
        for part in match.group(2).split(";"):
            name, _, value = part.strip().partition(" ")
            if name and value.strip():
                values[name] = value.strip()
        found.append(values)
    return found


def parse_trailers(message: str) -> dict[str, str]:
    """A commit message's provenance trailers as fields: version and the rest it names. Empty when it has none."""
    names = {trailer: name for name, trailer in FIELDS.items()} | {VERSION_TRAILER: "version"}
    return {names[key]: value for key, value in TRAILER.findall(message or "") if key in names}


def payload(p: Provenance) -> dict[str, str]:
    """The provenance as an event payload's field, for the console: the kind with the fields written."""
    return {"kind": p.kind, **p.fields()}
