"""The strings this code writes for people are held to the rules its models are held to.

The outbound pipeline's deslop stage looks only at model-written text: a fixed string in this code is posted
as it is. So the same scanner reads those strings here, in CI, where a finding fails the change that wrote
it. Every string of three words or more under src/ and console/src is scanned, each as one line, and a hit
the scanner decides by itself is a finding, as in the writing pass (run_service/writing.py).

Left out: docstrings, SQL, schema migrations, test files, and the strings written for a model to read (a
prompt, a response schema's descriptions), which MODEL_FACING names. A long sentence that tells a model
what to do is no fault of style a person meets.
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

from meta_harness.config import skills
from meta_harness.run_service import writing

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "meta_harness"
CONSOLE = ROOT / "console" / "src"
MIN_WORDS = 3
# A top-level name that holds text for a model, wherever it is: a prompt, or a response schema.
MODEL_NAME = re.compile(r"prompt|schema", re.IGNORECASE)
# The other top-level names whose strings a model reads and no person does, by file. "*" is the whole file.
MODEL_FACING: dict[str, set[str]] = {
    # The voice instructions, which end the planner's prompts.
    "planner/voice.py": {"*"},
    # The parts of a review or fix run's prompt.
    "run_service/activities.py": {"_previous", "_requested", "_checks_rule", "_findings", "_prepare_repo_task"},
    # The paths and version rules a run's prompt states.
    "run_service/code_host.py": {"VERSION_RULE", "PATHS_RULE", "paths_rule"},
    # The scripted model of the zero-cost end-to-end check.
    "run_service/fake_gateway.py": {"*"},
    # A shell script an image runs.
    "config/programs.py": {"LIST_SCRIPT"},
}
SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP|LOCK|NOTIFY|LISTEN|SET)\b")
# What a string's computed parts are scanned as: one plain word.
FILLED = "X"


def _top_level_name(node: ast.stmt) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
    return next((t.id for t in targets if isinstance(t, ast.Name)), "")


def _docstrings(tree: ast.Module) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _strings(node: ast.AST, skip: set[int]) -> list[tuple[int, str]]:
    """Each string under node with its line: a plain one as it is, an f-string whole, its computed parts
    filled in, so a sentence is scanned as the sentence a person reads."""
    if isinstance(node, ast.JoinedStr):
        parts = [str(v.value) if isinstance(v, ast.Constant) else FILLED for v in node.values]
        return [(node.lineno, "".join(parts))]
    if isinstance(node, ast.Constant):
        return [(node.lineno, node.value)] if isinstance(node.value, str) and id(node) not in skip else []
    return [found for child in ast.iter_child_nodes(node) for found in _strings(child, skip)]


def python_strings() -> list[tuple[str, int, str]]:
    """(file, line, text) for each string in src/ that a person may read."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        name = path.relative_to(SRC).as_posix()
        exempt = MODEL_FACING.get(name, set())
        if "migrations" in path.parts or "*" in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        for statement in tree.body:
            top = _top_level_name(statement)
            if top in exempt or MODEL_NAME.search(top):
                continue
            found += [(f"src/meta_harness/{name}", line, text) for line, text in _strings(statement, skip)]
    return [(file, line, text) for file, line, text in found if not SQL.match(text)]


QUOTED = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\]|\\.)*)`')
JSX_TEXT = re.compile(r">([^<>{}\n]+)<")


def console_strings() -> list[tuple[str, int, str]]:
    """(file, line, text) for each quoted string and each run of JSX text in the console's source."""
    found = []
    for path in sorted([*CONSOLE.rglob("*.ts"), *CONSOLE.rglob("*.tsx")]):
        if ".test." in path.name:
            continue
        source = path.read_text(encoding="utf-8")
        name = f"console/src/{path.relative_to(CONSOLE).as_posix()}"
        for match in [*QUOTED.finditer(source), *JSX_TEXT.finditer(source)]:
            text = next(group for group in match.groups() if group is not None)
            line = source.count("\n", 0, match.start()) + 1
            found.append((name, line, re.sub(r"\$\{[^}]*\}", FILLED, text)))
    return found


def findings(strings: list[tuple[str, int, str]]) -> list[str]:
    """The scanner's decided hits in the strings, each as a line naming its file, the words and the fix. All
    the strings go through one run of the shipped scanner, one string to a line."""
    kept = [(file, line, " ".join(text.split())) for file, line, text in strings]
    # A string that opens a code fence would mask the strings after it.
    kept = [(f, n, t) for f, n, t in kept if len(t.split()) >= MIN_WORDS and not t.startswith(("```", "~~~", "---"))]
    document = "\n\n".join(text for _, _, text in kept)
    config = writing.settings()
    scanner = skills.skills_dir() / config["skill"] / config["scanner"]
    done = subprocess.run(  # noqa: S603 - the shipped scanner, on this repository's own strings
        [sys.executable, "-I", str(scanner), "--compact"],
        input=document,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    found = []
    for hit in writing.decided(json.loads(done.stdout)):
        file, line, text = kept[(int(hit["line"]) - 1) // 2]
        fix = f" Fix: {hit['fix']}." if hit.get("fix") else ""
        found.append(f'{file}:{line}: "{hit["match"]}" ({hit.get("category_name")}) in "{text[:120]}".{fix}')
    return found


def _fail(found: list[str]) -> str:
    return (
        f"{len(found)} of the strings this code writes for people break the writing rules "
        "(config/skills/no-ai-slop2/WRITING-RULES.md). Reword each one. A string only a model reads is named "
        "in MODEL_FACING in this file instead:\n" + "\n".join(found)
    )


def test_the_strings_the_control_plane_writes_for_people_keep_the_writing_rules():
    strings = python_strings()
    assert len(strings) > 1000, "the scan found too few strings to trust: check how it reads src/"
    found = findings(strings)
    assert not found, _fail(found)


def test_the_strings_the_console_shows_keep_the_writing_rules():
    strings = console_strings()
    assert len(strings) > 1000, "the scan found too few strings to trust: check how it reads console/src"
    found = findings(strings)
    assert not found, _fail(found)


def test_the_scan_finds_a_planted_string_and_leaves_model_facing_text_and_names_alone():
    planted = [
        ("a.py", 3, "This is a robust and seamless way to save."),
        ("a.py", 9, "The meta-harness opened the pull request for you."),
        ("b.tsx", 4, "Saved the goal."),
    ]
    found = findings(planted)
    assert len(found) == 2 and all(line.startswith("a.py:3:") for line in found)
    assert '"robust"' in found[0] and '"seamless"' in found[1]

    scanned = {(file, text) for file, _, text in python_strings()}
    assert not any(file.endswith("planner/voice.py") for file, _ in scanned)
    from meta_harness.planner import updates

    assert all(updates.SYSTEM_PROMPT[:40] not in text for _, text in scanned)
    assert any("Try again" in text for _, text in scanned), "an error's remedy is a string for people"
