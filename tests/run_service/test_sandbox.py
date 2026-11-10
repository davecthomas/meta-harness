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


def _env(args: list[str]) -> dict[str, str]:
    pairs = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
    return dict(p.split("=", 1) for p in pairs if "=" in p)


def test_a_profile_names_its_services_and_the_machine_sets_them_for_one_that_does_not(monkeypatch):
    monkeypatch.delenv("MH_SANDBOX_SERVICES", raising=False)
    assert sandbox.services_for({}) == ()
    assert sandbox.services_for({"services": ["postgres"]}) == ("postgres",)
    monkeypatch.setenv("MH_SANDBOX_SERVICES", "postgres, nonsense")
    assert sandbox.services_for({}) == ("postgres",), "the machine's default, without what is no service"
    assert sandbox.services_for({"services": []}) == (), "a profile that says none gets none"


def test_a_run_with_a_database_is_given_its_url_and_reaches_it_without_the_proxy(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MH_SANDBOX_DATABASE_ENV", raising=False)
    url = sandbox.database_url("task:run-1")
    args = sandbox.run_args(
        name="mh-sandbox-x",
        network="mh-run-x",
        run_id="task:run-1",
        ref="task",
        workspace=tmp_path / "workspace",
        git_dir=tmp_path / "none",
        image="harness-pi:dev",
        database=url,
    )
    env = _env(args)
    host = sandbox.database_name("task:run-1")
    assert url == f"postgresql://mh:mh@{host}:5432/mh"
    assert env["DATABASE_URL"] == url and env["MH_DATABASE_URL"] == url
    assert env["NO_PROXY"] == f"localhost,127.0.0.1,{host}" == env["no_proxy"]

    monkeypatch.setenv("MH_SANDBOX_DATABASE_ENV", "PGURL")
    renamed = _env(
        sandbox.run_args(
            name="x",
            network="n",
            run_id="task:run-1",
            ref="task",
            workspace=tmp_path,
            git_dir=tmp_path / "none",
            image="i",
            database=url,
        )
    )
    assert renamed["PGURL"] == url and "DATABASE_URL" not in renamed


def test_a_run_without_a_database_gets_no_database_environment(tmp_path: Path):
    env = _env(_args(tmp_path, tmp_path / "none"))
    assert "DATABASE_URL" not in env and "MH_DATABASE_URL" not in env
    assert env["NO_PROXY"] == "localhost,127.0.0.1"


def test_a_runs_database_is_on_its_own_network_in_memory_and_labeled_as_the_runs():
    args = sandbox.database_args(name="mh-sandbox-x-db", network="mh-run-x", run_id="task:run-1", ref="task")
    assert args[args.index("--network") + 1] == "mh-run-x"
    assert args[args.index("--tmpfs") + 1] == "/var/lib/postgresql/data"
    assert "meta-harness.run_id=task:run-1" in args and args[-1] == sandbox.DATABASE_IMAGE
    assert args[args.index("--pull") + 1] == "missing"
    assert "--publish" not in args and "-p" not in args, "no port on the host"
    assert args[args.index("--memory") + 1] == "512m"


def test_a_profiles_services_are_checked():
    from meta_harness.run_service import routing

    ok = {"tools": ["read"], "max_turns": 5, "max_minutes": 5}
    assert routing.profile_errors("p", {**ok, "services": ["postgres"]}) == []
    assert routing.profile_errors("p", {**ok, "services": ["redis"]}) == [
        "p: services must be a list of postgres, or left out"
    ]
