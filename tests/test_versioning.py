"""The version is worked out from main's history, and no pull request sets it."""

import subprocess
from pathlib import Path

import meta_harness
from meta_harness import versioning
from meta_harness.run_service.workflows import version_kind

ROOT = Path(__file__).resolve().parents[1]


def test_a_commit_says_its_kind_in_a_version_line_or_by_its_subject():
    assert versioning.kind_of("Record each run's prompt (#184)", "Version: feature") == "feature"
    assert versioning.kind_of("Anything", "Closes #1\n\nversion: none\n") == "none"
    assert versioning.kind_of("feat/spend-report: where the money went (#221)") == "feature"
    assert versioning.kind_of("fix/review-no-rerun: the reviewer does not rerun (#220)") == "fix"
    assert versioning.kind_of("chore/version: tidy") == "none" and versioning.kind_of("docs: the design") == "none"
    assert versioning.kind_of("A subject that says nothing") == "fix", "an unmarked change counts as a fix"
    assert versioning.kind_of("fix/x: y", "Version: feature") == "feature", "the version line wins"


def test_the_version_is_the_base_bumped_once_for_each_commit_since():
    commits = [
        ("fix/a: x", ""),
        ("feat/b: y", ""),
        ("docs: z", ""),
        ("fix/c: w", ""),
        ("Merged task", "Version: feature"),
    ]
    assert versioning.after("0.49.0", commits) == "0.51.0"
    assert versioning.after("0.49.0", commits[:4]) == "0.50.1"
    assert versioning.after("1.2.3", []) == "1.2.3"


def _repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    git("init", "--quiet", "--initial-branch=main")
    (tmp_path / "VERSION").write_text("0.49.0\n")
    git("add", "VERSION")
    git("commit", "--quiet", "-m", "chore/version: the base")
    for subject, body in [("fix/a: one", ""), ("Add exports (#9)", "Version: feature"), ("docs: notes", "")]:
        (tmp_path / "file.txt").write_text(subject)
        git("add", "file.txt")
        git("commit", "--quiet", "-m", subject, "-m", body or "no version line")
    return tmp_path


def test_the_version_at_head_reads_the_commits_since_version_last_changed(tmp_path: Path):
    repo = _repo(tmp_path)
    assert versioning.current(repo) == "0.50.0", "a fix, then a feature, then docs"
    assert versioning.current(repo, "HEAD~1") == "0.50.0" and versioning.current(repo, "HEAD~2") == "0.49.1"
    assert versioning.current(repo, "HEAD~3") == "0.49.0", "the commit that set the base"


def test_a_copy_of_the_files_with_no_history_has_the_base_version(tmp_path: Path):
    (tmp_path / "VERSION").write_text("0.49.0\n")
    assert versioning.current(tmp_path) == "0.49.0"


def test_every_version_file_holds_the_base_version():
    base = versioning.base_version(ROOT)
    held = {
        path: pattern.search((ROOT / path).read_text()).group(1) for path, pattern in versioning.VERSION_LINES.items()
    }
    assert set(held.values()) == {base}, f"every version file holds {base}: {held}"
    assert meta_harness.BASE_VERSION == base


def test_the_running_version_is_the_deploys_or_the_base(monkeypatch):
    import importlib

    monkeypatch.setenv("MH_VERSION", "0.52.3")
    assert importlib.reload(meta_harness).__version__ == "0.52.3"
    monkeypatch.delenv("MH_VERSION")
    assert importlib.reload(meta_harness).__version__ == meta_harness.BASE_VERSION


def test_a_task_merge_is_a_feature_a_fix_or_nothing_by_its_type():
    assert [version_kind(t) for t in ("feature", "bug", "refactor", "security", "docs", "test", "")] == [
        "feature",
        "fix",
        "fix",
        "fix",
        "none",
        "none",
        "",
    ]


def test_the_deploy_gives_the_version_to_the_edge_and_the_vm():
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "fetch-depth: 0" in deploy and "python3 src/meta_harness/versioning.py" in deploy
    assert '--build-arg "MH_VERSION=$version"' in deploy
    assert "MH_VERSION=$MH_VERSION" in (ROOT / "deploy/edge.Dockerfile").read_text()
    assert 'echo "MH_VERSION=$(python3 "$MH/src/meta_harness/versioning.py"' in (ROOT / "deploy/vm/boot.sh").read_text()
    ci = (ROOT / ".github/workflows/control-plane.yml").read_text()
    assert 'versioning.py --check "origin/${{ github.base_ref }}"' in ci
