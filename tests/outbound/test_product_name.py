# Created by the meta-harness in run t-24b1dc29b6dc:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

"""The old product name stays out of the text the console shows.

The console shows its own strings (console/src, test files left out) and the strings the services send it.
Both are read the way test_fixed_strings.py reads them. "meta-harness" in any spelling (case, hyphen, space)
fails the test, outside the tagline and the cases ALLOWED names, each with its reason. A failure names the
file and line.
"""

import re

from .test_fixed_strings import console_strings, python_strings

NAME = re.compile(r"meta[- ]?harness", re.IGNORECASE)
TAGLINE = "The Open Meta-Harness for Coding at Scale"
# A repository address, such as github.com/davecthomas/meta-harness, names a place and not the product.
REPOSITORY = re.compile(r"github\.com/davecthomas/meta-harness", re.IGNORECASE)

# Each allowed case: (file, a pattern for the text, why the name stays). A string with no white space is a
# code identifier, package name, label or setting and is never read as text.
ALLOWED: list[tuple[str, str, str]] = [
    (
        "src/meta_harness/planner/tracker_progress.py",
        r"The meta-harness started work",
        "text written to a GitHub or Jira issue through outbound.prepare; a later spec renames it",
    ),
    (
        "src/meta_harness/planner/tracker_replies.py",
        r"The meta-harness has not started work",
        "text written to a GitHub or Jira issue through outbound.prepare; a later spec renames it",
    ),
    (
        "src/meta_harness/run_service/activities.py",
        r"Opened by the meta-harness\.|\*\*meta-harness review, round",
        "text written to a GitHub pull request through outbound.prepare; a later spec renames it",
    ),
    (
        "src/meta_harness/run_service/workflows.py",
        r"\*\*meta-harness review, round",
        "text written to a GitHub pull request through outbound.prepare; a later spec renames it",
    ),
    (
        "src/meta_harness/run_service/pr_summary.py",
        r"[Tt]he meta-harness\b",
        "text written to a GitHub pull request, and the earlier wording its reader matches; a later spec renames it",
    ),
    (
        "src/meta_harness/trackers/github.py",
        r"A meta-harness goal's issues",
        "the description of a label created on GitHub; a later spec renames it",
    ),
    (
        "src/meta_harness/run_service/provenance.py",
        r"Provenance:|Meta-Harness-|^meta-harness X$",
        "the provenance line and trailers a pull request carries, and the patterns that read them back",
    ),
    (
        "src/meta_harness/run_service/run_provenance.py",
        r"^Meta-Harness-Issue: X$",
        "a provenance trailer, written to GitHub; the name is its key",
    ),
    (
        "src/meta_harness/events/service.py",
        r"^meta-harness event service$",
        "a service's API title in its own docs; the console does not show it",
    ),
    (
        "src/meta_harness/tasks/service.py",
        r"^meta-harness task service$",
        "a service's API title in its own docs; the console does not show it",
    ),
    (
        "src/meta_harness/edge/app.py",
        r"^meta-harness edge$",
        "a service's API title in its own docs; the console does not show it",
    ),
    (
        "src/meta_harness/planner/service.py",
        r"^meta-harness planner$",
        "a service's API title in its own docs; the console does not show it",
    ),
    (
        "src/meta_harness/scheduler/service.py",
        r"^meta-harness scheduler$",
        "a service's API title in its own docs; the console does not show it",
    ),
]


def _allowed(file: str, text: str) -> bool:
    return any(file == name and re.search(pattern, text) for name, pattern, _ in ALLOWED)


def hits(strings: list[tuple[str, int, str]]) -> list[str]:
    """One line per string that holds the name: its file and line, then the text."""
    found = []
    for file, line, text in strings:
        text = " ".join(text.split())
        if not text or len(text.split()) < 2:
            continue
        if NAME.search(REPOSITORY.sub("", text.replace(TAGLINE, ""))) and not _allowed(file, text):
            found.append(f'{file}:{line}: "{text[:120]}"')
    return found


def test_the_console_text_keeps_the_old_name_out_but_for_the_tagline():
    strings = [*python_strings(), *console_strings()]
    assert len(strings) > 2000, "the scan found too few strings to trust: check how test_fixed_strings reads them"
    found = hits(strings)
    assert not found, (
        f'{len(found)} strings hold "meta-harness". Write the product name Octofusion, or add the case '
        "to ALLOWED with its reason:\n" + "\n".join(found)
    )


def test_each_allowed_case_still_matches_a_string():
    strings = [*python_strings(), *console_strings()]
    stale = [
        f"{name}: {pattern}"
        for name, pattern, _ in ALLOWED
        if not any(file == name and NAME.search(text) and re.search(pattern, text) for file, _, text in strings)
    ]
    assert not stale, "these allowed cases match nothing; remove them:\n" + "\n".join(stale)


def test_the_check_catches_each_spelling_and_leaves_the_tagline_and_addresses_alone():
    planted = [
        ("a.tsx", 1, "Welcome to Meta-Harness today"),
        ("a.tsx", 2, "Welcome to meta harness today"),
        ("a.tsx", 3, "Welcome to METAHARNESS today"),
        ("a.tsx", 4, f"{TAGLINE} for you"),
        ("a.tsx", 5, "Source at github.com/davecthomas/meta-harness today"),
        ("a.tsx", 6, "ai:meta-harness"),
    ]
    assert [line.split(":")[1] for line in hits(planted)] == ["1", "2", "3"]
