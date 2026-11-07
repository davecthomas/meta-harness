"""What a per-run sandbox can reach on the host: its workspace, and its git directory read-only."""

from pathlib import Path

from meta_harness.run_service import sandbox


def _args(tmp_path: Path, git_dir: Path) -> list[str]:
    return sandbox.run_args(
        name="mh-sandbox-x",
        network="mh-run-x",
        run_id="task:run-1",
        ref="task",
        workspace=tmp_path / "workspace",
        git_dir=git_dir,
        image="harness-pi:dev",
    )


def _volumes(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "--volume"]


def test_the_git_directory_is_mounted_read_only_at_its_own_path(tmp_path: Path):
    git_dir = tmp_path / "git"
    git_dir.mkdir()

    volumes = _volumes(_args(tmp_path, git_dir))

    assert volumes == [f"{tmp_path / 'workspace'}:/workspace", f"{git_dir}:{git_dir}:ro"]


def test_a_run_without_a_clone_mounts_only_the_workspace(tmp_path: Path):
    volumes = _volumes(_args(tmp_path, tmp_path / "git"))

    assert volumes == [f"{tmp_path / 'workspace'}:/workspace"]


def test_the_git_directory_sits_beside_the_workspace_not_inside_it():
    workspace = sandbox.workspace_for("task", "task:run-1")
    git_dir = sandbox.git_dir_for("task", "task:run-1")

    assert git_dir.parent == workspace.parent
    assert workspace not in git_dir.parents


def test_the_sandbox_runs_with_no_capabilities_and_no_new_privileges(tmp_path: Path):
    args = _args(tmp_path, tmp_path / "git")

    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert args[args.index("--pull") + 1] == "never"
