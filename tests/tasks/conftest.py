import pytest


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    """The example repo registry, whatever the machine has: a caller's key names a repo only from the registry,
    and CI and the sandbox have no config/repos.local.json."""
    from meta_harness.planner import repos
    from meta_harness.run_service.paths import REPO_ROOT

    monkeypatch.setenv("MH_REPOS_CONFIG", str(REPO_ROOT / "config" / "repos.example.json"))
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    repos._file_registry.cache_clear()
    yield
    repos._file_registry.cache_clear()
