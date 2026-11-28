"""The type check fails a pull request only for an error it adds (deploy/type_ratchet.py)."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "type_ratchet", Path(__file__).resolve().parents[1] / "deploy" / "type_ratchet.py"
)
ratchet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ratchet)


def _report(*items: tuple[str, str, str, int]) -> dict:
    return {
        "generalDiagnostics": [
            {
                "file": path,
                "severity": "error",
                "rule": rule,
                "message": message,
                "range": {"start": {"line": line - 1}},
            }
            for path, rule, message, line in items
        ]
        + [{"file": "src/a.py", "severity": "warning", "rule": "w", "message": "a warning", "range": {}}]
    }


OLD = ("src/a.py", "reportArgumentType", "str is not int", 10)


def test_an_error_the_base_has_is_not_this_changes_even_when_its_line_moved(tmp_path, capsys):
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    base.write_text(json.dumps(_report(("/tmp/base/src/a.py", *OLD[1:]))))
    head.write_text(json.dumps(_report(("src/a.py", OLD[1], OLD[2], 42))))

    assert ratchet.main([str(base), str(head), "--strip", "/tmp/base/"]) == 0
    assert "1 on the base, 1 with this change, 0 added" in capsys.readouterr().out


def test_an_error_the_change_adds_fails_and_is_named_with_its_line(tmp_path, capsys):
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    base.write_text(json.dumps(_report(OLD)))
    head.write_text(
        json.dumps(_report(OLD, OLD, ("src/b.py", "reportOptionalSubscript", "None is not subscriptable", 7)))
    )

    assert ratchet.main([str(base), str(head)]) == 1
    said = capsys.readouterr().out
    assert "2 added" in said, "a second copy of an error the base has once is added too"
    assert "src/b.py:7: None is not subscriptable (reportOptionalSubscript)" in said
    assert "Fix each error this change adds" in said
