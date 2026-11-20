"""The outbound pipeline's stages, in the order they run (outbound/__init__.py)."""

import re

from meta_harness.outbound import BODY, COMMENT, GENERATED, GITHUB, JIRA, OWN, TITLE, Context, Result
from meta_harness.run_service import provenance, writing

REDACTED = "[redacted]"
# Secrets by the shape their issuers give them. Each is specific enough that ordinary text does not match.
SECRETS = re.compile(
    "|".join(
        [
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
            r"\bgithub_pat_[A-Za-z0-9_]{60,}\b",
            r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",
            r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bAIza[0-9A-Za-z_-]{35}\b",
            r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
            r"\bATATT3[A-Za-z0-9_=-]{20,}\b",
            r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
            r"\b(?:[Aa]uthorization:\s*)?[Bb]earer\s+[A-Za-z0-9._~+/=-]{24,}",
        ]
    )
)
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
    """Secrets become [redacted], whoever wrote the text and wherever it goes."""

    name = "redact"

    async def run(self, text: str, context: Context) -> Result:
        cleaned, found = SECRETS.subn(REDACTED, text)
        return Result(cleaned, note=f"{found} secret{'' if found == 1 else 's'} redacted" if found else "")


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
