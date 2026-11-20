# Created by the meta-harness in run t-8c664bfa94b0:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""The programs a harness image has, listed for a harness profile's bash allowlist. No test runs docker: the
docker command is replaced by one that answers as the daemon would."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meta_harness.config import api as config_api
from meta_harness.config import images, programs

IMAGE = "harness-pi:dev"


class Events:
    def emit(self, event_type: str, payload: dict | None = None, **fields: object) -> str:
        return "id"


class Docker:
    """Answers docker commands from a table: each command's first word to a list of answers, the last kept."""

    def __init__(self, **answers: list[tuple[int, str, str]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> tuple[int, str, str]:
        self.calls.append(args)
        queue = self.answers[args[0]]
        return queue.pop(0) if len(queue) > 1 else queue[0]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The configuration API with no database, the default image built, and no waiting between tries."""
    monkeypatch.setattr(images, "DEFAULT_IMAGE", IMAGE)
    monkeypatch.setattr(images, "built", lambda: [{"name": IMAGE, "directory": "images/harness-pi", "default": True}])
    monkeypatch.setattr(programs, "_by_id", {})
    monkeypatch.setattr(programs, "_sleep", lambda seconds: None)
    app = FastAPI()
    app.include_router(config_api.router(lambda: None, lambda: "op@example.com", Events()))
    with TestClient(app) as test_client:
        yield test_client


LISTING = "git\nls\nrg\npython3\nls\n\nweird name\ncd\necho\ntest\n"


def test_the_programs_of_the_default_image_are_listed_from_a_locked_down_container(client, monkeypatch):
    docker = Docker(image=[(0, "sha256:abc\n", "")], run=[(0, LISTING, "")])
    monkeypatch.setattr(programs, "_docker", docker)

    body = client.get("/v1/config/images/programs").json()

    assert body == {"image": IMAGE, "programs": ["cd", "echo", "git", "ls", "python3", "rg", "test"]}
    run = docker.calls[1]
    assert run[0] == "run"
    for flag in (("--pull", "never"), ("--network", "none"), ("--cap-drop", "ALL"), ("--entrypoint", "sh")):
        assert run[run.index(flag[0]) + 1] == flag[1]
    assert "--rm" in run
    assert run[run.index("sh") + 1] == IMAGE
    assert "compgen -b" in run[-1]


def test_an_unchanged_image_is_read_once_and_a_rebuilt_one_again(client, monkeypatch):
    docker = Docker(
        image=[(0, "sha256:abc\n", ""), (0, "sha256:abc\n", ""), (0, "sha256:new\n", "")], run=[(0, LISTING, "")]
    )
    monkeypatch.setattr(programs, "_docker", docker)

    for _ in range(3):
        assert client.get("/v1/config/images/programs", params={"image": IMAGE}).status_code == 200

    assert [c[0] for c in docker.calls] == ["image", "run", "image", "image", "run"]


def test_a_transient_docker_failure_is_tried_again(client, monkeypatch):
    busy = (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    docker = Docker(image=[busy, (0, "sha256:abc\n", "")], run=[busy, (0, "git\n", "")])
    monkeypatch.setattr(programs, "_docker", docker)

    assert client.get("/v1/config/images/programs").json()["programs"] == ["git"]
    assert len(docker.calls) == 4


def test_a_daemon_that_stays_down_is_a_422_that_says_why_after_its_tries(client, monkeypatch):
    docker = Docker(image=[(1, "", "Cannot connect to the Docker daemon")])
    monkeypatch.setattr(programs, "_docker", docker)

    response = client.get("/v1/config/images/programs")

    assert response.status_code == 422
    assert "Cannot connect to the Docker daemon" in response.json()["detail"]
    assert len(docker.calls) == programs.POLICY.attempts


def test_an_image_not_built_here_is_a_422_at_once(client, monkeypatch):
    docker = Docker(image=[(1, "", "Error: No such image: harness-pi:dev")])
    monkeypatch.setattr(programs, "_docker", docker)

    response = client.get("/v1/config/images/programs")

    assert response.status_code == 422
    assert "is not built on this machine" in response.json()["detail"]
    assert len(docker.calls) == 1


def test_an_image_the_deployment_does_not_build_is_never_run(client, monkeypatch):
    docker = Docker(image=[(0, "sha256:abc\n", "")], run=[(0, LISTING, "")])
    monkeypatch.setattr(programs, "_docker", docker)

    response = client.get("/v1/config/images/programs", params={"image": "postgres:16"})

    assert response.status_code == 422
    assert "builds no harness image named postgres:16" in response.json()["detail"]
    assert docker.calls == []


def test_when_the_images_cannot_be_listed_neither_can_their_programs(client, monkeypatch):
    def unreadable() -> list:
        raise images.ImagesUnreadable("the harness image's build directory /x has no Dockerfile")

    monkeypatch.setattr(images, "built", unreadable)

    response = client.get("/v1/config/images/programs")

    assert response.status_code == 422
    assert "has no Dockerfile" in response.json()["detail"]


def test_no_docker_command_is_a_reason_not_a_crash(client, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")

    response = client.get("/v1/config/images/programs")

    assert response.status_code == 422
    assert "docker command is not installed" in response.json()["detail"]
