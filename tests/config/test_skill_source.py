"""What a git repository offers a skill's import: its branches and tags, and the directories that hold a SKILL.md."""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meta_harness.config import api as config_api
from meta_harness.config import skills

URL = "https://github.com/acme/skills"


def _skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name}, for tests.\n---\n\nDo it.\n"


class Events:
    def emit(self, event_type: str, payload: dict | None = None, **fields: object) -> str:
        return "id"


@pytest.fixture
def origin(tmp_path: Path, monkeypatch) -> Path:
    """A repository at URL: main holds two skills (and one under node_modules, which is not one), the tag v1
    marks main, and the branch next adds a third. Every git command for URL reads the local repository."""
    repo = tmp_path / "origin"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo)]

    def commit(files: dict[str, str], message: str) -> None:
        for path, text in files.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "--quiet", "-m", message], check=True)

    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(repo)], check=True)
    commit(
        {
            "SKILL.md": _skill_md("top"),
            "skills/tidy-notes/SKILL.md": _skill_md("tidy-notes"),
            "skills/tidy-notes/scripts/tidy.py": "print('tidy')\n",
            "more/review/SKILL.md": _skill_md("review"),
            "node_modules/pkg/SKILL.md": _skill_md("pkg"),
            "README.md": "Skills.\n",
        },
        "two skills",
    )
    subprocess.run([*git, "tag", "-a", "v1", "-m", "v1"], check=True)
    subprocess.run([*git, "checkout", "--quiet", "-b", "next"], check=True)
    commit({"skills/plan/SKILL.md": _skill_md("plan")}, "a third")
    subprocess.run([*git, "checkout", "--quiet", "main"], check=True)

    real_run = subprocess.run

    def run(command, **kwargs):
        return real_run([repo.as_uri() if part == URL else part for part in command], **kwargs)

    monkeypatch.setattr(skills.subprocess, "run", run)
    monkeypatch.setattr(skills, "_private_address", lambda host: None)
    return repo


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The configuration API with no database: listing a repository reads none."""
    app = FastAPI()
    app.include_router(config_api.router(lambda: None, lambda: "op@example.com", Events()))
    with TestClient(app) as test_client:
        yield test_client


def test_a_repository_lists_its_branches_tags_and_skill_directories_on_the_default_branch(origin, client):
    response = client.get("/v1/config/skills/source", params={"url": URL})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "branches": ["main", "next"],
        "tags": ["v1"],
        "default_branch": "main",
        "ref": "main",
        "paths": ["more/review", "skills/tidy-notes"],
    }


def test_the_skill_directories_are_those_at_the_ref_asked_for(origin, client):
    at_next = client.get("/v1/config/skills/source", params={"url": URL, "ref": "next"}).json()
    assert at_next["ref"] == "next" and at_next["paths"] == ["more/review", "skills/plan", "skills/tidy-notes"]

    at_tag = client.get("/v1/config/skills/source", params={"url": URL, "ref": "v1"}).json()
    assert at_tag["ref"] == "v1" and at_tag["paths"] == ["more/review", "skills/tidy-notes"]

    blank = client.get("/v1/config/skills/source", params={"url": URL, "ref": " "}).json()
    assert blank["ref"] == "main", "an empty ref is the default branch"


def test_every_branch_is_listed_however_many_there_are(origin, client):
    # A list cut short would leave a branch out of the picker, and the import would refuse it.
    head = subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    many = [f"b{n:04d}" for n in range(1200)]
    updates = "".join(f"create refs/heads/{name} {head.stdout.strip()}\n" for name in many)
    subprocess.run(["git", "-C", str(origin), "update-ref", "--stdin"], input=updates, check=True, text=True)

    listed = client.get("/v1/config/skills/source", params={"url": URL}).json()

    assert listed["branches"] == sorted([*many, "main", "next"])


def test_a_ref_the_repository_lacks_is_a_422_that_names_it(origin, client):
    response = client.get("/v1/config/skills/source", params={"url": URL, "ref": "nope"})

    assert response.status_code == 422
    assert response.json()["detail"] == f"{URL} has no branch or tag named nope"


def test_a_repository_that_cannot_be_read_is_a_422_that_says_why(tmp_path, monkeypatch, client):
    real_run = subprocess.run
    missing = (tmp_path / "missing").as_uri()
    monkeypatch.setattr(
        skills.subprocess, "run", lambda command, **kw: real_run([missing if p == URL else p for p in command], **kw)
    )
    monkeypatch.setattr(skills, "_private_address", lambda host: None)

    response = client.get("/v1/config/skills/source", params={"url": URL})

    assert response.status_code == 422 and response.json()["detail"].startswith(f"could not read {URL}: ")


def test_only_an_https_url_on_a_public_host_is_listed_and_git_is_never_run_for_another(monkeypatch, client):
    ran = []
    monkeypatch.setattr(skills.subprocess, "run", lambda command, **kwargs: ran.append(command))

    for url in ["file:///etc", "https://localhost/a/b", "git@github.com:acme/skills.git"]:
        response = client.get("/v1/config/skills/source", params={"url": url})
        assert response.status_code == 422
        assert response.json()["detail"] == "a skill is imported from an https:// git URL on a public host"
    assert ran == []


def test_only_an_admin_lists_a_repository(monkeypatch, client):
    monkeypatch.setattr(skills, "git_source", lambda url, ref: pytest.fail("listed for a non-admin"))

    response = client.get("/v1/config/skills/source", params={"url": URL}, headers={"X-MH-Role": "operator"})

    assert response.status_code == 403
