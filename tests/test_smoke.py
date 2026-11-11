"""The deploy's smoke task: one small task through the Task API, and what the deploy makes of how it ends."""

from pathlib import Path

import pytest

from meta_harness import smoke

TIERS = {
    "tiers": [
        {"name": "standard", "models": ["harness:pi-lean/claude:cheap", "harness:pi-lean/openai:other"]},
        {"name": "complex", "models": ["harness:pi-careful/claude:dear"]},
    ]
}


class Tasks:
    """A Task API whose tasks end as scripted: each entry is the status the next task ends with."""

    def __init__(self, endings: list[str]):
        self.endings = endings
        self.created: list[dict] = []
        self.canceled: list[str] = []

    def create(self, **task):
        task = {**task, "id": f"t-{len(self.created) + 1}", "status": "queued"}
        self.created.append(task)
        return task

    def get(self, task_id):
        task = next(t for t in self.created if t["id"] == task_id)
        ending = self.endings[self.created.index(task)]
        return {**task, "status": ending, "status_reason": f"it ended {ending}"}

    def cancel(self, task_id, reason):
        self.canceled.append(task_id)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(smoke, "POLL_SECONDS", 0)


def _run(tasks: Tasks, tmp_path: Path, held: str = "") -> tuple[int, list[str]]:
    said: list[str] = []
    code = smoke.run(tasks, TIERS, tmp_path / "smoke" / "origin.git", "0123456789abcdef", said.append, held)
    return code, said


def test_a_smoke_task_that_ends_closed_passes_the_deploy(tmp_path: Path):
    tasks = Tasks(["closed"])

    code, said = _run(tasks, tmp_path)

    assert code == 0 and len(tasks.created) == 1
    task = tasks.created[0]
    assert task["task_type"] == "smoke" and task["pin"] == "harness:pi-lean/claude:cheap"
    assert task["external_ref"].startswith("smoke:0123456789ab:") and task["max_usd"] == "0.25"
    assert task["repo"] == str(tmp_path / "smoke" / "origin.git") and "goal_id" not in task
    assert any(line.startswith("passed: task t-1 ended closed") for line in said)


def test_one_failure_is_tried_again_and_two_fail_the_deploy(tmp_path: Path):
    again = Tasks(["failed", "closed"])
    code, said = _run(again, tmp_path)
    assert code == 0 and len(again.created) == 2 and "trying once more: one failure can be chance" in said
    assert again.created[0]["external_ref"] != again.created[1]["external_ref"]

    twice = Tasks(["failed", "review_unresolved"])
    code, said = _run(twice, tmp_path)
    assert code == 1 and said[-1].startswith("failed: task t-2 ended review_unresolved")


def test_a_smoke_task_that_never_ends_is_canceled_and_counts_as_failed(tmp_path: Path):
    tasks = Tasks(["running"])
    said: list[str] = []
    repo = str(smoke.scratch_repo(tmp_path / "origin.git"))

    ok, what = smoke.run_once(tasks, repo, "harness:a/b:c", "abc", said.append, wait=0.05)

    assert not ok and "was still running after 0 seconds" in what and tasks.canceled == ["t-1"]


def test_no_smoke_task_runs_while_no_task_may_start(tmp_path: Path):
    tasks = Tasks([])
    code, said = _run(tasks, tmp_path, held="the kill switch is on")
    assert code == 0 and not tasks.created
    assert said == ["no smoke task: the kill switch is on, so no task may start"]


def test_the_scratch_repo_is_made_once_with_a_commit_on_main(tmp_path: Path):
    import subprocess

    path = smoke.scratch_repo(tmp_path / "smoke" / "origin.git")
    first = subprocess.run(
        ["git", "--git-dir", str(path), "rev-parse", "main"], capture_output=True, text=True, check=True
    ).stdout
    assert smoke.scratch_repo(path) == path
    again = subprocess.run(
        ["git", "--git-dir", str(path), "rev-parse", "main"], capture_output=True, text=True, check=True
    ).stdout
    assert first == again and len(first.strip()) == 40


def test_the_cheapest_candidate_is_the_first_of_the_first_tier():
    assert smoke.cheapest_candidate(TIERS) == "harness:pi-lean/claude:cheap"
    with pytest.raises(RuntimeError, match="no candidate"):
        smoke.cheapest_candidate({"tiers": []})


def test_the_deploy_runs_the_smoke_task_after_the_vm_reports_and_shows_it_in_a_failure_issue():
    root = Path(__file__).resolve().parents[1]
    deploy = (root / ".github/workflows/deploy.yml").read_text()
    booted, smoked = (
        deploy.index("wait for the VM to boot the commit"),
        deploy.index("run a smoke task through the deployed system"),
    )
    assert booted < smoked < deploy.index("open an issue for the failed deploy")
    assert "deploy/vm/smoke.sh ${{ github.sha }}" in deploy and "tee smoke.txt" in deploy
    script = (root / "deploy/vm/smoke.sh").read_text()
    assert "mh deploy smoke" in script and "source /etc/meta-harness/env" in script
