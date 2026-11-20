"""The summary in a pull request's description: the run's own, or a fallback the meta-harness writes.

A run is told to finish with a summary for its pull request, and the text of its last turn is that summary
(HarnessResult.answer). When it says something, it goes in the description under RUN_HEADING, after the
writing pass. When the run left none, or one too short to say anything about the change ("Done."), the
meta-harness writes the section itself from what Publish knows: the files the branch changes, with the run's
own few words quoted. A summary the run service already wrote from the harness's record of the run starts
with FALLBACK_PREFIX and is kept as it is.

Either fallback goes under FALLBACK_HEADING, and its first line says where it came from, so no reviewer reads
it as the run's words. It skips the writing pass: it holds only the meta-harness's own lines and file paths.

Where a summary came from is its source (SOURCES): Publish records it on the task as `pull_request_summary`,
so the console's task, pull request and goal views can say whose words a description holds.
"""

import re
from dataclasses import dataclass

from meta_harness.run_service import provenance

# A summary of fewer words than this ("Done.", "All done.") says nothing about the change.
MIN_WORDS = 3
# The files are cut to this many lines in the fallback, with a count of the rest.
MAX_FILES = 30
# The run's own words, quoted in the fallback when they were too few to stand alone, are cut to this length.
QUOTE_CHARS = 300

RUN_HEADING = "Summary from the run"
FALLBACK_HEADING = "Summary written by the meta-harness"
# The first words of every fallback, this module's and one the run service wrote from the harness's record.
FALLBACK_PREFIX = "The run ended without a usable summary, so the meta-harness wrote this one"
FALLBACK_LEAD = f"{FALLBACK_PREFIX} from the files the branch changes."

# Where a description's summary came from: the run's own words, a fallback the run service wrote from the
# harness's record of the run, or one this module wrote from the files the branch changes.
SOURCES = ("run", "recorded", "files")


@dataclass(frozen=True)
class Section:
    """The summary part of a description. from_run: the run's own words, which the writing pass checks first."""

    heading: str
    text: str
    from_run: bool

    @property
    def source(self) -> str:
        """Where the summary came from, one of SOURCES."""
        if self.from_run:
            return "run"
        return "files" if self.text.startswith(FALLBACK_LEAD) else "recorded"


def posted(description: str, end: str) -> Section | None:
    """The summary section a description holds, read back from it: under RUN_HEADING or FALLBACK_HEADING, up to
    end, the line that follows the summary. None when the description holds no such section before end."""
    description = description.replace("\r\n", "\n")
    stop = description.find(end)
    if stop < 0:
        return None
    starts = [
        (at, heading)
        for heading in (RUN_HEADING, FALLBACK_HEADING)
        if (at := _line_at(description[:stop], f"## {heading}\n")) >= 0
    ]
    if not starts:
        return None
    at, heading = min(starts)
    text = description[at + len(f"## {heading}\n") : stop].strip()
    return Section(heading, text, from_run=heading == RUN_HEADING)


def posted_round(description: str, heading: str) -> Section | None:
    """A fix round's summary, read back from the description: the text under heading, up to the provenance
    line that ends the section, or the description's end when it has none. None when the description holds no
    such heading. Its source comes from the text, since the round's heading does not say it."""
    description = description.replace("\r\n", "\n")
    at = _line_at(description, f"## {heading}\n")
    if at < 0:
        return None
    rest = description[at + len(f"## {heading}\n") :]
    end = provenance.LINE.search(rest)
    text = (rest[: end.start()] if end else rest).strip()
    if is_fallback(text):
        return Section(FALLBACK_HEADING, text, from_run=False)
    return Section(RUN_HEADING, text, from_run=True)


def _line_at(text: str, line: str) -> int:
    """Where line first starts a line of text, or -1."""
    if text.startswith(line):
        return 0
    at = text.find(f"\n{line}")
    return at + 1 if at >= 0 else -1


def usable(text: str | None) -> bool:
    """True when the text can stand as the run's summary: it says something in at least MIN_WORDS words."""
    return len(re.findall(r"\w+", text or "")) >= MIN_WORDS


def is_fallback(text: str | None) -> bool:
    """True when the text is a summary the meta-harness wrote, not the run's own."""
    return (text or "").lstrip().startswith(FALLBACK_PREFIX)


def section(summary: str | None, files: list[str]) -> Section:
    """The summary for a description: the run's own when usable, else a fallback from the branch's files."""
    text = (summary or "").strip()
    if is_fallback(text):
        return Section(FALLBACK_HEADING, text, from_run=False)
    if usable(text):
        return Section(RUN_HEADING, text, from_run=True)
    return Section(FALLBACK_HEADING, fallback(text, files), from_run=False)


def fallback(text: str, files: list[str]) -> str:
    """The summary the meta-harness writes when the run left none usable: its few words, if any, and the files."""
    lines = [FALLBACK_LEAD, ""]
    if text:
        quoted = text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "..."
        lines += [f"The run's last words: {' '.join(quoted.split())}", ""]
    named = [f for f in files if f]
    if named:
        listed = [f"- `{f}`" for f in named[:MAX_FILES]]
        if len(named) > MAX_FILES:
            listed.append(f"- and {len(named) - MAX_FILES} more")
        lines += ["Files the branch changes:", *listed]
    else:
        lines.append("The meta-harness could not list the files the branch changes.")
    return "\n".join(lines).rstrip()
