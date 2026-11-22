"""The outbound pipeline's stages, in the order they run (outbound/__init__.py)."""

import logging
import re

from meta_harness.outbound import (
    BODY,
    COMMENT,
    GENERATED,
    GITHUB,
    JIRA,
    OWN,
    PERSON,
    TITLE,
    Context,
    Result,
    credentials,
)
from meta_harness.run_service import provenance, writing

log = logging.getLogger(__name__)

# The most characters each destination takes, by kind, with room left for a provenance line.
LIMITS = {
    (GITHUB, BODY): 64000,
    (GITHUB, COMMENT): 64000,
    (GITHUB, TITLE): 250,
    (JIRA, BODY): 31000,
    (JIRA, COMMENT): 31000,
    (JIRA, TITLE): 250,
    (OWN, BODY): 200000,
    (OWN, COMMENT): 200000,
    (OWN, TITLE): 300,
}


class Stage:
    name = ""
    # A blocker's failure stops the post; any other stage's failure passes the text on.
    blocks = False

    def applies(self, context: Context) -> bool:
        return True

    async def run(self, text: str, context: Context) -> Result:
        raise NotImplementedError


class Redact(Stage):
    """Credentials become [redacted], whoever wrote the text and wherever it goes: tokens, keys and passwords,
    never personal data (credentials.py). Off unless the task policy's `credential_redaction` is true."""

    name = "redact"
    # A deployment that turned redaction on is promised no credential leaves: a redaction that fails stops the
    # post, as the other stage that guards what leaves does.
    blocks = True

    def applies(self, context: Context) -> bool:
        return credentials.enabled()

    async def run(self, text: str, context: Context) -> Result:
        cleaned, found = credentials.redact(text)
        count = sum(found.values())
        if not count:
            return Result(text)
        # The kinds, never the values: whoever reads the log replaces the credential.
        kinds = ", ".join(f"{n} {kind}" for kind, n in found.items())
        log.warning(
            "redacted %d credential(s) from a %s %s for %s: %s. Replace each one: it reached text that was "
            "about to be posted",
            count,
            context.source,
            context.kind,
            context.destination,
            kinds,
        )
        return Result(cleaned, note=f"{count} credential{'' if count == 1 else 's'} redacted", extra=dict(found))


class Deslop(Stage):
    """The writing pass, for text a model wrote. A title skips it: a rewrite is checked against a text's
    headings, links and numbers, which a title has too few of to hold it to."""

    name = "deslop"

    def applies(self, context: Context) -> bool:
        return context.source == GENERATED and context.kind != TITLE

    async def run(self, text: str, context: Context) -> Result:
        # check_all keeps the pass's time limit: a text it does not finish in time goes on as it was.
        [outcome] = await writing.check_all([text])
        if outcome is None:
            return Result(text)
        note = "rewritten by the writing pass" if outcome.kept else outcome.reason
        return Result(outcome.text, note=note, detail=outcome)


# What GitHub acts on in a text. A name after @ notifies its person or team. An issue after one of these words
# is closed when the pull request that says so merges.
MENTION = re.compile(r"(?<![\w/`@])@[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9_])?")
# Put after an @ or a #, it stops GitHub from reading a mention or an issue number, and shows nothing.
ZERO_WIDTH = "\u200b"
CLOSING = re.compile(
    r"\b((?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+)"
    r"((?:[\w.-]+/[\w.-]+)?#\d+|GH-\d+|https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+)",
    re.IGNORECASE,
)
CODE = re.compile(f"{writing.FENCE.pattern}|{writing.CODE_SPAN.pattern}", writing.FENCE.flags)


class Inert(Stage):
    """Nothing in the text acts on GitHub by itself. A model writes from an issue anyone may have written, and
    an error or a log may name a package like @types/node: posted as it is, such a text notifies a stranger,
    or closes an issue its pull request has nothing to do with. So each @mention goes in a code span, where
    it reads the same and notifies no one, and so does each issue a model's text says it closes. The control
    plane's own `Closes` line is fixed text, added after, and stays. A person's own words are left alone."""

    name = "inert"

    def applies(self, context: Context) -> bool:
        return context.destination == GITHUB and context.source != PERSON

    async def run(self, text: str, context: Context) -> Result:
        counts = {"mention": 0, "closing reference": 0}
        # Whether each stretch of prose could take code spans.
        plain: list[bool] = []

        def quiet(prose: str) -> str:
            # A stray backtick in the prose (one with no partner on its line, or a span over several lines) would
            # pair with the backticks a code span adds, and leave the mention outside, live. Such prose gets a
            # zero-width space in each mention and reference instead, which pairs with nothing.
            spans = "`" not in prose
            plain.append(spans)
            mention = (lambda m: f"`{m.group(0)}`") if spans else (lambda m: f"@{ZERO_WIDTH}{m.group(0)[1:]}")
            prose, counts["mention"] = _counted(MENTION.subn(mention, prose), counts["mention"])
            if context.source == GENERATED:
                closing = (lambda m: f"{m.group(1)}`{m.group(2)}`") if spans else _unlinked
                closed = CLOSING.subn(closing, prose)
                prose, counts["closing reference"] = _counted(closed, counts["closing reference"])
            return prose

        # Code is left as it is: nothing in a code span or a code block notifies or closes.
        parts, last = [], 0
        for code in CODE.finditer(text):
            parts += [quiet(text[last : code.start()]), code.group(0)]
            last = code.end()
        made = "".join([*parts, quiet(text[last:])])
        said = [f"{n} {what}{'' if n == 1 else 's'}" for what, n in counts.items() if n]
        how = "put in code spans" if all(plain) else "made inert"
        return Result(made, note=f"{' and '.join(said)} {how}" if said else "", extra=counts)


def _unlinked(match: re.Match) -> str:
    """A closing reference GitHub no longer reads as one: a zero-width space before its number."""
    ref = match.group(2)
    cut = max(ref.rfind("#"), ref.rfind("/"), ref.rfind("-")) + 1
    return f"{match.group(1)}{ref[:cut]}{ZERO_WIDTH}{ref[cut:]}"


def _counted(done: tuple[str, int], so_far: int) -> tuple[str, int]:
    return done[0], so_far + done[1]


class Fit(Stage):
    """Cut to what the destination takes. A body or comment says that it was cut; a title is cut at a word."""

    name = "fit"

    async def run(self, text: str, context: Context) -> Result:
        limit = LIMITS[(context.destination, context.kind)]
        if len(text) <= limit:
            return Result(text)
        if context.kind == TITLE:
            return Result(text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…", note=f"cut to {limit} characters")
        over = len(text) - limit
        cut = text[:limit].rsplit("\n", 1)[0].rstrip()
        return Result(f"{cut}\n\n[Cut: the last {over} characters did not fit here.]", note=f"cut by {over} characters")


class Stamp(Stage):
    """The provenance line, last, so no stage rewrites or cuts it."""

    name = "stamp"

    def applies(self, context: Context) -> bool:
        return context.provenance is not None and context.kind != TITLE

    async def run(self, text: str, context: Context) -> Result:
        return Result(provenance.stamp(text, context.provenance))


STAGES: tuple[Stage, ...] = (Redact(), Deslop(), Inert(), Fit(), Stamp())
