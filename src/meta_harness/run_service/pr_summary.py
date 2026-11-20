"""The summary in a pull request's description: the run's own, or a fallback the meta-harness writes.

A run is told to end with a marked summary for its pull request, and the run service keeps only the marked part
(summaries.py, HarnessResult.answer). When it says something, it goes in the description under RUN_HEADING,
after the writing pass. When the run left none, or one too short to say anything about the change ("Done."),
the meta-harness writes the section itself from what Publish knows: the files the branch changes. A summary the
run service already wrote from the harness's record of the run comes with the source `recorded`
(PublishRequest.summary_source) and is kept, with the files the branch changes that it does not name added after it: the record lists only the files the run's write
and edit tools changed, so a file a command changed, or an earlier run, is named only by the branch. The source,
not the summary's words, decides: a run's marked summary that opens with FALLBACK_PREFIX is still the run's.

Either fallback goes under FALLBACK_HEADING, and its first line says plainly that the run left no summary. It
holds none of the run's own words, and it skips the writing pass: it holds only the meta-harness's own lines
and file paths.
"""

import re
from dataclasses import dataclass

# A summary of fewer words than this ("Done.", "All done.") says nothing about the change.
MIN_WORDS = 3
# The files are cut to this many lines in the fallback, with a count of the rest.
MAX_FILES = 30

# The headings say what a reviewer finds under them: what the change does, or, with no summary, what it touches.
RUN_HEADING = "What this change does"
FALLBACK_HEADING = "What this change touches"
# The first words of every fallback, this module's and one the run service wrote from the harness's record.
FALLBACK_PREFIX = "The run left no summary of its change"
# The first words of a fallback written before FALLBACK_PREFIX, which a task under way may still carry.
EARLIER_PREFIX = "The run ended without a usable summary, so the meta-harness wrote this one"
FALLBACK_LEAD = f"{FALLBACK_PREFIX}, so this section lists the files the branch changes."


@dataclass(frozen=True)
class Section:
    """The summary part of a description. from_run: the run's own words, which the writing pass checks first."""

    heading: str
    text: str
    from_run: bool


def usable(text: str | None) -> bool:
    """True when the text can stand as the run's summary: it says something in at least MIN_WORDS words."""
    return len(re.findall(r"\w+", text or "")) >= MIN_WORDS


def is_fallback(text: str | None, source: str = "") -> bool:
    """True when the text is a summary the run service wrote, not the run's own. `source` is where it came from
    (summaries.choose): only `recorded` is the run service's. A request from before the source was carried has
    none, and then only a fallback in EARLIER_PREFIX's words, which that code wrote, counts."""
    if source:
        return source == "recorded" and bool((text or "").strip())
    return (text or "").lstrip().startswith(EARLIER_PREFIX)


def section(summary: str | None, files: list[str], source: str = "") -> Section:
    """The summary for a description: the run service's fallback when `source` says it wrote one, the run's own
    when usable, else a fallback from the branch's files."""
    text = (summary or "").strip()
    if is_fallback(text, source):
        return Section(FALLBACK_HEADING, with_branch_files(text, files), from_run=False)
    if usable(text):
        return Section(RUN_HEADING, text, from_run=True)
    return Section(FALLBACK_HEADING, fallback(files), from_run=False)


def with_branch_files(text: str, files: list[str]) -> str:
    """A fallback the run service wrote, with the files the branch changes that it does not name listed after it."""
    missing = [f for f in dict.fromkeys(files) if f and f"`{f}`" not in text]
    if not missing:
        return text
    return "\n".join([text, "", "Other files the branch changes:", *_listed(missing)])


def _listed(files: list[str]) -> list[str]:
    listed = [f"- `{f}`" for f in files[:MAX_FILES]]
    if len(files) > MAX_FILES:
        listed.append(f"- and {len(files) - MAX_FILES} more")
    return listed


def fallback(files: list[str]) -> str:
    """The summary the meta-harness writes when the run left none usable: that it left none, and the files."""
    lines = [FALLBACK_LEAD, ""]
    named = [f for f in files if f]
    if named:
        lines += ["Files the branch changes:", *_listed(named)]
    else:
        lines.append("The meta-harness could not list the files the branch changes.")
    return "\n".join(lines).rstrip()
