"""A specification picked from the goal's repo: its documents listed, and one read (spec_files.py)."""

import base64

import pytest
from fastapi.testclient import TestClient

from meta_harness.github_app import GitHubAppError

from .test_planner import OPERATOR, REPO, Events, FakeTasks, FakeTracker, _no_title, migrated, registry  # noqa: F401

TREE = {
    "truncated": False,
    "tree": [
        {"path": "README.md", "type": "blob"},
        {"path": "docs", "type": "tree"},
        {"path": "docs/exports-prd.md", "type": "blob"},
        {"path": "docs/design/sync.md", "type": "blob"},
        {"path": "src/app.py", "type": "blob"},
        {"path": "node_modules/left-pad/README.md", "type": "blob"},
        {"path": "notes/todo.txt", "type": "blob"},
    ],
}


class Apps:
    """Stands in for the GitHub App: the repo's tree, and its files' contents."""

    def __init__(self):
        self.files = {"docs/exports-prd.md": "# Exports\n\nUsers can export their data.\n", "docs/empty.md": " \n"}
        self.calls: list[tuple] = []
        self.down = False

    async def call(self, repo, permissions, method, path, **kwargs):
        self.calls.append((repo, permissions, method, path))
        if self.down:
            raise GitHubAppError("GitHub answered 503", retryable=True, status=503)
        if path.endswith("/git/trees/HEAD"):
            return TREE
        name = path.split("/contents/", 1)[1]
        if name == "docs":
            return [{"path": "docs/exports-prd.md", "type": "file"}]
        if name == "docs/huge.md":
            return {"type": "file", "encoding": "none", "content": ""}
        if name not in self.files:
            raise GitHubAppError("GitHub answered 404", retryable=False, status=404)
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(self.files[name].encode()).decode()}


@pytest.fixture
def world():
    from meta_harness.planner.service import create_app

    apps = Apps()
    app = create_app(
        Events(), FakeTracker(), FakeTasks(), operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0,
        titler=_no_title, apps=apps,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client, apps


def test_a_repos_documents_are_listed_with_the_likeliest_specifications_first(world):
    client, apps = world

    found = client.get(f"/v1/repos/{REPO}/spec-files", headers=OPERATOR).json()

    assert [f["path"] for f in found["files"]] == [
        "docs/exports-prd.md",
        "docs/design/sync.md",
        "README.md",
        "notes/todo.txt",
    ]
    assert found["complete"] is True, "code, and other people's documents, are not offered"
    assert apps.calls[0][1] == {"contents": "read"}, "read with no more than the permission to read"


def test_a_picked_document_is_read_as_the_specifications_name_and_text(world):
    client, _ = world

    read = client.get(f"/v1/repos/{REPO}/spec-files/text", params={"path": "docs/exports-prd.md"}, headers=OPERATOR)

    assert read.status_code == 200, read.text
    assert (read.json()["name"], read.json()["text"]) == (
        "exports-prd.md",
        "# Exports\n\nUsers can export their data.\n",
    )


def test_a_file_that_cannot_be_a_specification_is_refused_saying_what_to_do(world):
    client, apps = world

    def refused(path: str) -> tuple[int, str]:
        answer = client.get(f"/v1/repos/{REPO}/spec-files/text", params={"path": path}, headers=OPERATOR)
        return answer.status_code, str(answer.json()["detail"])

    assert refused("src/app.py") == (422, refused("src/app.py")[1]) and "is not one" in refused("src/app.py")[1]
    assert "is not one" in refused("../secrets.md")[1]
    assert (
        refused("docs/missing.md")[0] == 404
        and "pick another file, or load the specification as a file" in refused("docs/missing.md")[1]
    )
    assert "is empty" in refused("docs/empty.md")[1]
    assert "too large to be a specification" in refused("docs/huge.md")[1]
    assert client.get("/v1/repos/nobody/unknown/spec-files", headers=OPERATOR).status_code == 404, (
        "only a registered repo"
    )
    apps.down = True
    down = client.get(f"/v1/repos/{REPO}/spec-files", headers=OPERATOR)
    assert (
        down.status_code == 502
        and "Try again in a minute, or load the specification as a file" in down.json()["detail"]
    )
