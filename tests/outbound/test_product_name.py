# Created by the meta-harness in run t-24b1dc29b6dc:run-4 on harness:pi-careful/claude:claude-opus-5-5.

"""The console says "meta-harness" only in its tagline.

The product is Muxxy. Its tagline, "The Open Meta-Harness for Coding at Scale", is the one place the
console may say "meta-harness". This test reads the console's strings (console/src, test files left out) and
every string under src/, since a service's string may reach a console page: a hold's reason, an error's
remedy, a task's status note. It reuses the extraction in test_fixed_strings.py, which leaves out docstrings,
SQL, migrations and the text only a model reads.

A string fails when it holds the name in any spelling ("Meta-Harness", "meta harness", "metaharness") outside
an allowed case. Each allowed case below names its reason. Every other hit fails with its file and line.
"""

import re

from .test_fixed_strings import console_strings, python_strings

PRODUCT_NAME = re.compile(r"meta[\s_-]*harness", re.IGNORECASE)
TAGLINE = "The Open Meta-Harness for Coding at Scale"

# Parts of any string that hold the name and are not the product's name in a person's sentence. Each is cut
# out of the string before the name is looked for, so the rest of the string is still checked.
ALLOWED_PARTS: list[tuple[str, re.Pattern[str]]] = [
    ("the tagline, the one place the console says the name", re.compile(re.escape(TAGLINE))),
    (
        "a repository address keeps the repository's name",
        re.compile(r"(?:https?://)?github\.com/[\w.-]+/meta-harness[\w./#?=&-]*", re.IGNORECASE),
    ),
    (
        "the Python package's identifier: a module, a logger name, a source path or an MH_-style setting",
        re.compile(r"\bmeta_harness\b|\bMETA_HARNESS_\w*", re.IGNORECASE),
    ),
    (
        "a tracker label the code puts on issues and pull requests and finds them by",
        re.compile(r"\bai:meta-harness\b|\bmeta-harness:[\w{}-]+"),
    ),
    (
        "a commit trailer key the code writes and reads back from main's history",
        re.compile(r"\bMeta-Harness-[A-Za-z]+"),
    ),
    (
        "a container label key the sandbox finds its containers and networks by",
        re.compile(r"\bmeta-harness\.(?:run_id|ref|sample)\b"),
    ),
    (
        "a git identity the control plane or the smoke check commits as",
        re.compile(r"\buser\.name=meta-harness$|\bmeta-harness@[\w.-]+"),
    ),
    ("a directory on the services' machine", re.compile(r"/var/lib/meta-harness/")),
]

# Strings allowed by file and the string's opening words, each with its reason.
ALLOWED_STRINGS: list[tuple[str, str, str]] = [
    # Text written to GitHub or Jira through outbound.prepare. A later spec renames it: it is read there
    # under the old name today.
    ("src/meta_harness/planner/tracker_progress.py", "The meta-harness started work", "an issue comment"),
    ("src/meta_harness/planner/tracker_replies.py", "The meta-harness has not started", "an issue comment"),
    ("src/meta_harness/run_service/activities.py", "Opened by the meta-harness.", "a pull request's body"),
    ("src/meta_harness/run_service/activities.py", "**meta-harness review, round", "a review's heading"),
    ("src/meta_harness/run_service/workflows.py", "**meta-harness review, round", "a review comment's heading"),
    ("src/meta_harness/run_service/pr_summary.py", "Summary written by the meta-harness", "a summary heading"),
    ("src/meta_harness/run_service/pr_summary.py", "The run ended without a usable summary", "a summary note"),
    ("src/meta_harness/run_service/pr_summary.py", "The meta-harness could not list", "a summary note"),
    # A label's description, written to GitHub with the label. The later spec renames it with the rest.
    ("src/meta_harness/trackers/github.py", "A meta-harness goal's issues", "a GitHub label's description"),
    # The provenance line, written into pull requests and read back by provenance.parse.
    ("src/meta_harness/run_service/provenance.py", "meta-harness X", "the provenance line's product field"),
    ("src/meta_harness/run_service/provenance.py", "^Provenance:", "the pattern that reads the line back"),
    ("src/meta_harness/run_service/provenance.py", "^(Meta-Harness-", "the pattern that reads the trailers back"),
]

# Whole strings allowed by file and the exact string, each with its reason: code identifiers and the
# services' names, which the rebrand leaves as they are.
IDENTIFIERS: list[tuple[str, str, str]] = [
    ("src/meta_harness/run_service/code_host.py", "meta-harness", "the git author name of every commit"),
    ("src/meta_harness/trackers/own.py", "meta-harness", "the author login of an issue the planner wrote"),
    ("src/meta_harness/config/store.py", "meta-harness", "the actor saved on shipped config, as older rows hold"),
    ("src/meta_harness/edge/app.py", "meta-harness edge", "a service's name in its API metadata"),
    ("src/meta_harness/events/service.py", "meta-harness event service", "a service's name in its API metadata"),
    ("src/meta_harness/planner/service.py", "meta-harness planner", "a service's name in its API metadata"),
    ("src/meta_harness/scheduler/service.py", "meta-harness scheduler", "a service's name in its API metadata"),
    ("src/meta_harness/tasks/service.py", "meta-harness task service", "a service's name in its API metadata"),
]


def _allowed_string(file: str, text: str) -> bool:
    return any(file == where and text.startswith(start) for where, start, _ in ALLOWED_STRINGS) or any(
        file == where and text == whole for where, whole, _ in IDENTIFIERS
    )


def _rest(text: str) -> str:
    for _, part in ALLOWED_PARTS:
        text = part.sub(" ", text)
    return text


def name_hits(strings: list[tuple[str, int, str]]) -> list[str]:
    """Each string that says the old name outside an allowed case, as a line naming its file and line."""
    found = []
    for file, line, text in strings:
        if _allowed_string(file, text):
            continue
        hit = PRODUCT_NAME.search(_rest(text))
        if hit:
            shown = " ".join(text.split())
            found.append(f'{file}:{line}: "{hit.group(0)}" in "{shown[:120]}"')
    return found


def _fail(found: list[str]) -> str:
    return (
        f'{len(found)} strings say "meta-harness" outside the tagline. Name the product Muxxy, or reword '
        "the sentence so it needs no name. A code identifier or text written only to GitHub or Jira is named "
        "in ALLOWED_PARTS, ALLOWED_STRINGS or IDENTIFIERS in this file, with its reason:\n" + "\n".join(found)
    )


def test_the_console_says_the_old_name_only_in_its_tagline():
    strings = console_strings()
    assert len(strings) > 1000, "the scan found too few strings to trust: check how it reads console/src"
    assert any(TAGLINE in text for _, _, text in strings), "the tagline is gone: check how the scan reads it"
    found = name_hits(strings)
    assert not found, _fail(found)


def test_the_strings_the_services_send_the_console_do_not_say_the_old_name():
    strings = python_strings()
    assert len(strings) > 1000, "the scan found too few strings to trust: check how it reads src/"
    found = name_hits(strings)
    assert not found, _fail(found)


def test_each_allowed_string_is_still_in_its_file():
    strings = {(file, text) for file, _, text in [*python_strings(), *console_strings()]}
    stale = [
        f"{where}: {start!r} ({reason})"
        for where, start, reason in ALLOWED_STRINGS
        if not any(file == where and text.startswith(start) for file, text in strings)
    ] + [f"{where}: {whole!r} ({reason})" for where, whole, reason in IDENTIFIERS if (where, whole) not in strings]
    assert not stale, "these allowed strings are gone: remove them from this file:\n" + "\n".join(stale)


def test_the_check_finds_each_spelling_and_leaves_the_allowed_cases_alone():
    planted = [
        ("console/src/a.tsx", 3, "Assigned to the meta-harness"),
        ("console/src/a.tsx", 4, "Meta Harness"),
        ("console/src/a.tsx", 5, "The METAHARNESS console"),
        ("src/meta_harness/run_service/code_host.py", 6, "meta-harness wrote this commit"),
        ("console/src/a.tsx", 7, TAGLINE),
        ("console/src/a.tsx", 8, "https://github.com/davecthomas/meta-harness/issues"),
        ("console/src/a.tsx", 9, "Label: ai:meta-harness"),
        ("src/meta_harness/b.py", 10, "meta_harness.planner"),
        ("src/meta_harness/trackers/github.py", 11, "A meta-harness goal's issues"),
        ("src/meta_harness/c.py", 12, "A meta-harness goal's issues"),
        ("console/src/a.tsx", 13, f"{TAGLINE}, by the meta-harness"),
    ]
    found = name_hits(planted)
    assert [line.split(":")[1] for line in found] == ["3", "4", "5", "6", "12", "13"], found
    assert found[0].startswith('console/src/a.tsx:3: "meta-harness" in "Assigned to the meta-harness"')
