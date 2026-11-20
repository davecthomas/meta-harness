"""The outbound pipeline's stages, in the order they run (outbound/__init__.py)."""

import logging

from meta_harness.outbound import BODY, COMMENT, GENERATED, GITHUB, JIRA, OWN, TITLE, Context, Result, credentials
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


STAGES: tuple[Stage, ...] = (Redact(), Deslop(), Fit(), Stamp())
