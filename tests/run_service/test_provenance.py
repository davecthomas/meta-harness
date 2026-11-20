"""Provenance headers on the files a run creates: what config/provenance.json allows, where each header goes, which
files take one, and the publish that adds them before it commits."""

import copy
import json
import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.planner import client as planner_client
from meta_harness.run_service import activities, code_host, provenance
from meta_harness.run_service.shared import PublishRequest

CONTEXT = provenance.Context(run_id="task-t-1:run-1", candidate="harness:pi-lean/x:y", task_id="t-1", repo="o/r")
CONFIG = {
    "version": "test",
    "enabled": True,
    "text": "Made by run $run_id.",
    "repos": {},
    "skip": ["*.min.js", "vendor/*"],
    "styles": {
        "#": [".py", ".sh", ".yaml", "Dockerfile"],
        "//": [".ts", ".js", ".go"],
        "<!-- -->": [".md", ".html", ".xml"],
        "/* */": [".css"],
    },
}
PY = "# Made by run task-t-1:run-1."


def _config(**changes) -> dict:
    return {**copy.deepcopy(CONFIG), **changes}


def _stamp(content: str, path: str, text: str = "Made by run task-t-1:run-1.") -> str | None:
    style = provenance.style_for(path, CONFIG)
    assert style is not None
    return provenance.stamped(content, path, provenance.header_lines(text, style))


# --- The configuration ----------------------------------------------------------------------------------


def test_the_shipped_configuration_is_usable():
    shipped = json.loads(provenance.CONFIG_PATH.read_text(encoding="utf-8"))
    provenance.check_settings(shipped)
    assert provenance.style_for("src/app.py", shipped) == ("#", "")
    assert provenance.style_for("web/a.tsx", shipped) == ("//", "")
    assert provenance.style_for("docs/a.md", shipped) == ("<!--", "-->")
    assert provenance.style_for("data.json", shipped) is None, "JSON has no comments"
    assert provenance.style_for("dist/app.min.js", shipped) is None


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"enabled": "yes"}, "enabled must be true or false"),
        ({"text": "  "}, "text must be"),
        ({"text": "x" * 501}, "longer than 500"),
        ({"text": "run $run, on $candidate"}, "text names $run"),
        ({"text": "costs $5"}, "write $$ for a dollar sign"),
        ({"text": "ends */ early"}, "would end a '/* */' comment early"),
        ({"repos": {"o/r": "off"}}, "repos must map"),
        ({"skip": [""]}, "skip must be"),
        ({"styles": {}}, "styles must map"),
        ({"styles": {"a b c": [".x"]}}, "one line marker or an opening and closing pair"),
        ({"styles": {"#": [".py"], "//": [".PY"]}}, ".PY is in both"),
    ],
)
def test_a_bad_setting_is_named(changes, named):
    with pytest.raises(RuntimeError, match=named.replace("$", r"\$").replace("*", r"\*")):
        provenance.check_settings(_config(**changes))


def test_the_goal_outranks_the_repo_which_outranks_the_default():
    config = _config(repos={"o/off": False, "o/on": True})
    assert provenance.wanted("o/r", None, config) is True
    assert provenance.wanted("o/off", None, config) is False
    assert provenance.wanted("o/off", True, config) is True
    assert provenance.wanted("o/r", False, config) is False
    assert provenance.wanted("o/on", None, _config(enabled=False, repos={"o/on": True})) is True
    assert provenance.wanted("o/r", None, _config(enabled=False)) is False


def test_the_header_names_what_the_run_has_and_none_for_what_it_has_not():
    config = _config(text="Run $run_id for task $task_id of goal $goal_id ($candidate, $repo).")
    assert provenance.render(CONTEXT, config) == (
        "Run task-t-1:run-1 for task t-1 of goal none (harness:pi-lean/x:y, o/r)."
    )


# --- Where the header goes --------------------------------------------------------------------------------


def test_a_header_goes_on_top_with_a_blank_line_after_it():
    assert _stamp("print('hi')\n", "a.py") == f"{PY}\n\nprint('hi')\n"
    assert _stamp("export const a = 1;\n", "a.ts") == "// Made by run task-t-1:run-1.\n\nexport const a = 1;\n"
    assert _stamp("body {}\n", "a.css") == "/* Made by run task-t-1:run-1. */\n\nbody {}\n"
    assert _stamp("", "empty.py") == f"{PY}\n", "an empty file gets the header alone"
    assert _stamp("FROM python:3.12\n", "images/Dockerfile") == f"{PY}\n\nFROM python:3.12\n"


def test_what_must_stay_first_stays_first():
    assert _stamp("#!/bin/sh\necho hi\n", "run.sh") == f"#!/bin/sh\n{PY}\n\necho hi\n"
    assert _stamp("#!/usr/bin/env python\n# -*- coding: latin-1 -*-\nx = 1\n", "a.py") == (
        f"#!/usr/bin/env python\n# -*- coding: latin-1 -*-\n{PY}\n\nx = 1\n"
    )
    assert _stamp('<?xml version="1.0"?>\n<a/>\n', "a.xml") == (
        '<?xml version="1.0"?>\n<!-- Made by run task-t-1:run-1. -->\n\n<a/>\n'
    )
    assert _stamp("<!DOCTYPE html>\n<html></html>\n", "a.html") == (
        "<!DOCTYPE html>\n<!-- Made by run task-t-1:run-1. -->\n\n<html></html>\n"
    )
    assert _stamp("---\ntitle: A\n---\n# A\n", "docs/a.md") == (
        "---\ntitle: A\n---\n<!-- Made by run task-t-1:run-1. -->\n\n# A\n"
    )
    assert _stamp("#!/bin/sh", "run.sh") == f"#!/bin/sh\n{PY}\n", "a first line with no newline gets one"


def test_line_endings_a_byte_order_mark_and_several_lines_are_kept():
    assert _stamp("a = 1\r\nb = 2\r\n", "a.py") == f"{PY}\r\n\r\na = 1\r\nb = 2\r\n"
    assert _stamp("\ufeffa = 1\n", "a.py") == f"\ufeff{PY}\n\na = 1\n"
    assert (
        _stamp("x {}\n", "a.css", "Made by a run.\nSee its task.") == "/*\nMade by a run.\nSee its task.\n*/\n\nx {}\n"
    )
    assert _stamp("a = 1\n", "a.py", "Made by a run.\nSee its task.") == "# Made by a run.\n# See its task.\n\na = 1\n"


def test_a_file_that_starts_with_the_header_is_left_as_it_is():
    once = _stamp("#!/bin/sh\necho hi\n", "run.sh")
    assert once is not None and _stamp(once, "run.sh") is None
    empty = _stamp("", "a.py")
    assert empty is not None and _stamp(empty, "a.py") is None
    front = _stamp("---\ntitle: A\n---\n# A\n", "a.md")
    assert front is not None and _stamp(front, "a.md") is None


def test_apply_skips_what_takes_no_header(tmp_path: Path):
    (tmp_path / "vendor").mkdir()
    files = {
        "a.py": b"x = 1\n",
        "b.json": b"{}\n",
        "app.min.js": b"var a;\n",
        "vendor/lib.py": b"y = 2\n",
        "image.py": b"\x00\x01binary",
        "latin.py": "caf\xe9 = 1\n".encode("latin-1"),
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("z = 3\n")
    (tmp_path / "link.py").symlink_to(outside)

    carried = provenance.apply(tmp_path, [*files, "link.py", "gone.py"], CONTEXT, CONFIG)

    assert carried == ["a.py"]
    assert (tmp_path / "a.py").read_text() == f"{PY}\n\nx = 1\n"
    for name, data in files.items():
        if name != "a.py":
            assert (tmp_path / name).read_bytes() == data, name
    assert outside.read_text() == "z = 3\n", "a link out of the workspace is not followed"
    assert provenance.apply(tmp_path, ["a.py"], CONTEXT, CONFIG) == ["a.py"]
    assert (tmp_path / "a.py").read_text() == f"{PY}\n\nx = 1\n", "a second pass adds nothing"


# --- The publish ------------------------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "--quiet", "--initial-branch=main", cwd=seed)
    (seed / "old.py").write_text("old = 1\n")
    (seed / "moved.py").write_text("moved = 1\n")
    _git("add", ".", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "seed", cwd=seed)
    bare = tmp_path / "origin.git"
    _git("clone", "--quiet", "--bare", str(seed), str(bare), cwd=tmp_path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    return bare


async def test_publish_heads_only_the_files_the_run_created(origin: Path, tmp_path: Path):
    run_dir = tmp_path / "runs" / "task" / "run-1"
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "old.py").write_text("old = 2\n")
    (workspace / "moved.py").rename(workspace / "renamed.py")
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "new.py").write_text("new = 1\n")
    (workspace / "notes.json").write_text("{}\n")
    given: list[list[str]] = []

    async def headers(created: list[str]) -> list[str]:
        given.append(created)
        return provenance.apply(workspace, created, CONTEXT, CONFIG)

    kwargs = dict(
        repo=str(origin), workspace=workspace, git_dir=git_dir, branch="mh/task", title="t", body="b",
        commit_message="m", pr_record=run_dir / "pull-request.md", headers=headers,
    )  # fmt: skip
    published = await code_host.publish(**kwargs)

    assert given == [["notes.json", "pkg/new.py"]], "a changed or moved file was not created by the run"
    assert published is not None and published.stamped == ["pkg/new.py"]
    assert _git("show", "mh/task:pkg/new.py", cwd=origin) == f"{PY}\n\nnew = 1"
    assert _git("show", "mh/task:old.py", cwd=origin) == "old = 2"
    assert _git("show", "mh/task:renamed.py", cwd=origin) == "moved = 1"

    again = await code_host.publish(**kwargs)
    assert again is not None and again.commit == published.commit and again.stamped == []
    assert given == [["notes.json", "pkg/new.py"]], "a retried publish finds nothing new to head"


async def test_files_a_conflicted_merge_brought_are_not_the_run_s(origin: Path, tmp_path: Path):
    seed = tmp_path / "other"
    _git("clone", "--quiet", str(origin), str(seed), cwd=tmp_path)
    (seed / "from_base.py").write_text("base = 1\n")
    _git("add", ".", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "base", cwd=seed)
    _git("push", "--quiet", "origin", "HEAD:refs/heads/next", cwd=seed)
    run_dir = tmp_path / "runs" / "task" / "run-2"
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (git_dir / "MERGE_HEAD").write_text(_git("rev-parse", "next", cwd=origin) + "\n")
    (workspace / "from_base.py").write_text("base = 1\n")
    (workspace / "mine.py").write_text("mine = 1\n")
    await code_host._git(git_dir, workspace, "add", "--all")

    assert await code_host.created_files(git_dir, workspace) == ["mine.py"]


# --- The publish activity ---------------------------------------------------------------------------------


@pytest.fixture
def publishing(monkeypatch, tmp_path: Path):
    """code_host.publish faked to hand the activity's headers function two created files, with the goal the planner
    returns, the events emitted and what provenance.apply was given recorded."""
    seen: dict = {"goal": {"id": "g-1", "provenance": None}, "emitted": [], "applied": [], "asked": []}

    async def publish(*, headers, **kwargs):
        stamped = await headers(["a.py", "b.json"])
        return code_host.Published(
            "mh/t-1", "main", "c1", ["a.py", "b.json"], "https://x/pull/1", True, stamped=stamped
        )

    class Goals:
        def __init__(self, api_key):
            pass

        def get(self, goal_id):
            seen["asked"].append(goal_id)
            if isinstance(seen["goal"], Exception):
                raise seen["goal"]
            return seen["goal"]

    def apply(workspace, files, context):
        seen["applied"].append((files, context))
        return files[:1]

    monkeypatch.setenv("MH_SERVICE_KEY", "k")
    monkeypatch.setattr(code_host, "publish", publish)
    monkeypatch.setattr(planner_client, "GoalsClient", Goals)
    monkeypatch.setattr(provenance, "apply", apply)
    monkeypatch.setattr(provenance, "settings", lambda: _config(repos={"o/quiet": False}))
    monkeypatch.setattr(activities.events, "emit", lambda *args, **kwargs: seen["emitted"].append((args, kwargs)))
    return seen


def _request(**extra) -> PublishRequest:
    fields = dict(
        run_id="task-t-1:run-1", repo="o/r", ticket="", title="t", candidate="harness:pi-lean/x:y", summary=None,
        spent_usd="0", task_id="t-1", goal_id="g-1",
    )  # fmt: skip
    return PublishRequest(**{**fields, **extra})


async def test_the_publish_activity_heads_new_files_and_records_it_on_the_task(publishing):
    await ActivityEnvironment().run(activities.publish, _request())

    [(files, context)] = publishing["applied"]
    assert files == ["a.py", "b.json"]
    assert context == provenance.Context(
        run_id="task-t-1:run-1", candidate="harness:pi-lean/x:y", task_id="t-1", goal_id="g-1", repo="o/r"
    )
    recorded = [(args, kwargs) for args, kwargs in publishing["emitted"] if args[0] == "provenance_added"]
    [((_, payload), fields)] = recorded
    assert payload == {"run_id": "task-t-1:run-1", "count": 1, "files": ["a.py"]}
    assert fields["task_id"] == "t-1" and fields["run_id"] == "task-t-1:run-1" and fields["event_id"]


async def test_a_goal_that_turned_headers_off_gets_none_and_one_that_turned_them_on_does(publishing):
    publishing["goal"] = {"id": "g-1", "provenance": False}
    await ActivityEnvironment().run(activities.publish, _request())
    assert publishing["applied"] == [] and not [e for e in publishing["emitted"] if e[0][0] == "provenance_added"]

    publishing["goal"] = {"id": "g-1", "provenance": True}
    await ActivityEnvironment().run(activities.publish, _request(repo="o/quiet"))
    assert len(publishing["applied"]) == 1, "the goal's choice outranks the repo's"


async def test_a_repo_turned_off_gets_no_header_and_a_task_with_no_goal_asks_no_planner(publishing):
    await ActivityEnvironment().run(activities.publish, _request(repo="o/quiet", goal_id=""))
    assert publishing["applied"] == [] and publishing["asked"] == []


async def test_a_planner_that_does_not_answer_leaves_it_to_the_configuration(publishing):
    publishing["goal"] = RuntimeError("connection refused")
    await ActivityEnvironment().run(activities.publish, _request())
    assert publishing["asked"] == ["g-1"] and len(publishing["applied"]) == 1
