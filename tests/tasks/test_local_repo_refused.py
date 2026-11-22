from fastapi.testclient import TestClient

from meta_harness.tasks.service import create_app


class NoEvents:
    def emit(self, event_type, payload=None, **fields):
        raise AssertionError("a refused task writes no event")


def test_a_task_naming_an_unlisted_local_path_is_refused_whoever_submits_it(tmp_path, monkeypatch):
    """The run service would clone that path and push to it on its host, where a push runs the repo's hooks."""
    monkeypatch.setenv("MH_LOCAL_REPO_ROOTS", str(tmp_path / "repos"))
    planted = tmp_path / "runs" / "t-1" / "run-1" / "workspace" / "evil"
    planted.mkdir(parents=True)
    (planted / "HEAD").write_text("ref: refs/heads/main\n")
    # No lifespan, so no database: the refusal comes before anything is stored.
    api = TestClient(create_app(NoEvents(), caller_keys={"caller-key": "ops"}, service_keys={"service-key": "planner"}))
    body = {
        "external_ref": "ref-1",
        "repo": str(planted),
        "task_type": "bug",
        "title": "Fix the thing",
        "description": "It is broken.",
        "acceptance_checks": ["make test"],
    }

    for key in ("caller-key", "service-key"):
        answer = api.post("/v1/tasks", json=body, headers={"Authorization": f"Bearer {key}"})
        assert answer.status_code == 422
        assert "MH_LOCAL_REPO_ROOTS" in answer.json()["detail"]
