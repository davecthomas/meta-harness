"""Says which type errors a change adds: the ones its type check reports and the base's does not.

The repo has several hundred type errors from before it was checked, so a check that fails on any would fail
every pull request, and one that never fails says nothing. This compares two reports of the same checker
(`pyright --outputjson`), the base's and the change's, and fails only for an error the change adds. An error
is matched by its file, its rule and its message, never its line, which moves when lines above it change.

    python3 deploy/type_ratchet.py base.json head.json [--strip <path prefix of the base's files>]

Prints the errors added, and exits 1 when there is one.
"""

import json
import sys
from collections import Counter
from pathlib import Path


def errors(report: dict, strip: str = "") -> list[tuple[str, str, str, int]]:
    """Each error of a pyright report as its file, rule, message and line. strip: a prefix of the report's
    paths to drop, so two checkouts' files compare by their place in the repo."""
    found = []
    for item in report.get("generalDiagnostics") or []:
        if item.get("severity") != "error":
            continue
        path = str(item.get("file") or "")
        for prefix in (strip, str(Path.cwd()) + "/"):
            if prefix and path.startswith(prefix):
                path = path[len(prefix) :]
        line = int(((item.get("range") or {}).get("start") or {}).get("line") or 0) + 1
        found.append((path.lstrip("/"), str(item.get("rule") or ""), str(item.get("message") or ""), line))
    return found


def added(
    base: list[tuple[str, str, str, int]], head: list[tuple[str, str, str, int]]
) -> list[tuple[str, str, str, int]]:
    """The head's errors past how many of the same the base has: an error the base has twice and the head
    three times is added once."""
    left = Counter((path, rule, message) for path, rule, message, _ in base)
    new = []
    for path, rule, message, line in head:
        if left[(path, rule, message)] > 0:
            left[(path, rule, message)] -= 1
        else:
            new.append((path, rule, message, line))
    return new


def main(argv: list[str]) -> int:
    strip = argv[argv.index("--strip") + 1] if "--strip" in argv else ""
    base = errors(json.loads(Path(argv[0]).read_text(encoding="utf-8")), strip)
    head = errors(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    new = added(base, head)
    print(f"type errors: {len(base)} on the base, {len(head)} with this change, {len(new)} added")
    for path, rule, message, line in new:
        first = message.splitlines()[0] if message else ""
        print(f"  {path}:{line}: {first} ({rule})")
    if new:
        print("Fix each error this change adds, or annotate the code so the checker can follow it.")
    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
