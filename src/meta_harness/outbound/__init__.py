"""The outbound pipeline: every text the control plane writes for people passes through here on its way out.

A tracker issue or comment, a pull request's description, a review, a comment on it: each goes through
`prepare`, which runs the stages in a fixed order and returns the text as `Checked`. The adapters that write
to the outside (the trackers, the code host) take nothing else: `require` refuses a plain string, with how to
fix it. So a new feature that writes somewhere gets every stage with nothing to remember, and one that skips
the pipeline fails the first time it runs.

    redact   secrets a run or a log may carry become [redacted]; first, so no later stage or model sees one
    deslop   model-written text goes through the writing pass (run_service/writing.py); a person's own words
             and the fixed strings in this code do not
    fit      the text is cut to what its destination takes, with a line saying so
    stamp    the provenance line goes last, where nothing rewrites it

A stage is a small class (stages.py): `applies(context)` says whether it runs for this text, and `run` returns
the text as it leaves the stage, what it did, and, for a stage that must stop a post, why it is blocked. A
stage that fails passes its text on unchanged, unless it is a blocker, whose failure blocks. `prepare` raises
`Blocked` for a blocked text, naming the stage and what to do. What each stage did rides on the result
(`Checked.report`), for the caller to record.

Adding a stage: write it in stages.py and place it in `STAGES`, where its order is its meaning.

Before any of this, the prompt that asks a model for such a text carries the writing rules (rules.py), so the
deslop stage's rewrite rarely has to run.
"""

from dataclasses import dataclass, field
from typing import Any

# Where a text is going, which decides its limits.
GITHUB, JIRA, OWN = "github", "jira", "own"
DESTINATIONS = (GITHUB, JIRA, OWN)
# Who wrote it. generated: a model did, so the writing pass looks at it. fixed: a string in this code, or one
# composed of parts already prepared. person: an operator's own words, which are never rewritten.
GENERATED, FIXED, PERSON = "generated", "fixed", "person"
SOURCES = (GENERATED, FIXED, PERSON)
# What it is, for its limit and its record: a body (an issue's, a pull request's, a review's), a comment, or a
# title, which carries no provenance line.
BODY, COMMENT, TITLE = "body", "comment", "title"
KINDS = (BODY, COMMENT, TITLE)


@dataclass(frozen=True)
class Context:
    """What the stages need to know of one text."""

    destination: str
    kind: str = BODY
    source: str = GENERATED
    # The provenance to end the text with (run_service/provenance.Provenance), or None for none.
    provenance: Any = None

    def __post_init__(self) -> None:
        for value, known, what in (
            (self.destination, DESTINATIONS, "destination"),
            (self.kind, KINDS, "kind"),
            (self.source, SOURCES, "source"),
        ):
            if value not in known:
                raise ValueError(f"an outbound text's {what} must be one of {', '.join(known)}, not {value!r}")


@dataclass
class StageOutcome:
    """What one stage did with a text."""

    stage: str
    changed: bool = False
    # What it did, in a few words, when it did something.
    note: str = ""
    # For the writing pass: its own outcome (writing.Outcome), which its record is made from.
    detail: Any = None
    error: str = ""


class Checked(str):
    """A text that came through the pipeline. Only `prepare` makes one (and `trusted`, for tests)."""

    report: list[StageOutcome]

    def __new__(cls, text: str, report: list[StageOutcome] | None = None) -> "Checked":
        made = super().__new__(cls, text)
        made.report = report or []
        return made


class Blocked(RuntimeError):
    """A stage stopped the text from being posted. The message names the stage and what to do."""


@dataclass
class Result:
    """A stage's answer: the text as it leaves the stage, and what was done."""

    text: str
    note: str = ""
    detail: Any = None
    # Set by a stage that must stop the post: why, and what to do about it.
    blocked: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def destination_of(ref_or_scheme: str) -> str:
    """The destination a tracker ref (`jira:VL-1`, `github:o/r#2`, `mh:i-3`) or a tracker's scheme writes to."""
    scheme = ref_or_scheme.partition(":")[0]
    return {"jira": JIRA, "mh": OWN, "own": OWN}.get(scheme, GITHUB)


async def prepare(text: str, context: Context) -> Checked:
    """The text as it may be posted: through every stage that applies, in order.

    Raises:
        Blocked: A stage stopped the post. The message says which, why, and what to do.
    """
    from meta_harness.outbound.stages import STAGES

    report: list[StageOutcome] = []
    for stage in STAGES:
        if not stage.applies(context):
            continue
        outcome = StageOutcome(stage.name)
        try:
            result = await stage.run(str(text), context)
        except Exception as err:  # noqa: BLE001 - a stage that fails passes the text on, unless it must block
            outcome.error = f"{type(err).__name__}: {err}"
            report.append(outcome)
            if stage.blocks:
                raise Blocked(
                    f"the {stage.name} stage could not check the text ({outcome.error}), so it was not posted. "
                    "Try again; if it keeps failing, read the service's log for the stage's error"
                ) from err
            continue
        if result.blocked:
            raise Blocked(f"the {stage.name} stage stopped the text: {result.blocked}")
        outcome.changed, outcome.note, outcome.detail = result.text != text, result.note, result.detail
        report.append(outcome)
        text = result.text
    return Checked(text, report)


async def for_tracker(
    text: str, where: str, kind: str = COMMENT, source: str = FIXED, provenance: Any = None
) -> Checked:
    """`prepare` for a text going to a tracker. where: the issue's ref, or the tracker's scheme."""
    return await prepare(text, Context(destination_of(where), kind, source, provenance))


def require(text: str, what: str) -> None:
    """Refuse a text that did not come through the pipeline. For the adapters that write to the outside."""
    if not isinstance(text, Checked):
        raise TypeError(
            f"{what} must come through the outbound pipeline: pass it to "
            "`await outbound.prepare(text, outbound.Context(...))` first, and post what that returns"
        )


def trusted(text: str) -> Checked:
    """A text marked as checked without the pipeline. For tests of an adapter only: a test fails any use of
    it under src/."""
    return Checked(text)


def writing_outcomes(texts: list[Checked]) -> list[Any]:
    """Each text's writing-pass outcome (writing.Outcome), or None where the pass did not look at it: what
    `writing.record` makes a `writing_checked` event from."""
    found = []
    for text in texts:
        looked = [o.detail for o in getattr(text, "report", []) if o.stage == "deslop" and o.detail is not None]
        found.append(looked[0] if looked else None)
    return found


def redactions(texts: list[Checked]) -> int:
    """How many secrets the redact stage took out of the texts."""
    return sum(
        int(o.note.split()[0]) for t in texts for o in getattr(t, "report", []) if o.stage == "redact" and o.changed
    )
