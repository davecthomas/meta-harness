"""The harness images the deployment builds, listed for a harness profile's image."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meta_harness.config import api as config_api
from meta_harness.config import images
from meta_harness.run_service.paths import REPO_ROOT

SAMPLE = REPO_ROOT / "samples" / "temporal-run-task"


class Events:
    def emit(self, event_type: str, payload: dict | None = None, **fields: object) -> str:
        return "id"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The configuration API with no database: listing the images reads none."""
    app = FastAPI()
    app.include_router(config_api.router(lambda: None, lambda: "op@example.com", Events()))
    with TestClient(app) as test_client:
        yield test_client


def _image(root: Path, name: str) -> Path:
    (root / name).mkdir()
    (root / name / "Dockerfile").write_text("FROM scratch\n")
    return root / name


def _function(script: str, name: str) -> str:
    """The body of a shell function in a script."""
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", script, re.S | re.M)
    assert match, f"stack.sh has no {name}"
    return match.group(1)


def test_the_deployment_builds_one_harness_image_from_harness_dir_tagged_harness_image():
    """The contract the listing rests on: `make images`, which mh-images.service runs, builds HARNESS_DIR and
    tags it HARNESS_IMAGE, with the same defaults as the listing, and builds no other harness image."""
    unit = (REPO_ROOT / "deploy" / "vm" / "units" / "mh-images.service").read_text()
    assert "make images" in unit
    makefile = (SAMPLE / "Makefile").read_text()
    assert re.search(r"^images:\n\t@\./stack\.sh images$", makefile, re.M)
    assert re.search(r"^HARNESS_DIR \?= \$\(abspath \$\(CURDIR\)/\.\./\.\./images/harness-pi\)$", makefile, re.M)
    image_default = re.search(r"^HARNESS_IMAGE \?= (\S+)$", makefile, re.M)
    assert image_default and image_default.group(1) == "harness-pi:dev"

    script = (SAMPLE / "stack.sh").read_text()
    assert re.search(r"^  images\) cmd_images ;;$", script, re.M)
    assert _function(script, "cmd_images").split() == ["check_docker", "build_image"]
    builds = re.findall(r"docker build .*?--tag \"(\$\w+)\" (\S+)", _function(script, "build_image"))
    assert builds == [("$HARNESS_IMAGE", '"$HARNESS_DIR"'), ("$EGRESS_IMAGE", "egress-proxy")]

    assert images.DEFAULT_DIR == REPO_ROOT / "images" / "harness-pi"


def test_the_shipped_harness_image_is_listed_under_the_name_the_deployment_builds_it_as(client, monkeypatch):
    monkeypatch.delenv("HARNESS_DIR", raising=False)

    body = client.get("/v1/config/images").json()

    assert body == {
        "images": [{"name": images.DEFAULT_IMAGE, "directory": "images/harness-pi", "default": True}],
        "default": images.DEFAULT_IMAGE,
    }


def test_another_directory_under_images_is_not_listed_since_nothing_builds_it(client, monkeypatch, tmp_path):
    built_dir = _image(tmp_path, "harness-pi")
    _image(tmp_path, "harness-other")
    monkeypatch.setenv("HARNESS_DIR", str(built_dir))
    monkeypatch.setattr(images, "DEFAULT_IMAGE", "harness-pi:dev")

    body = client.get("/v1/config/images").json()

    assert [i["name"] for i in body["images"]] == ["harness-pi:dev"]
    assert body["images"][0]["directory"] == str(built_dir)
    assert body["default"] == "harness-pi:dev"


def test_the_listed_name_is_harness_image_as_set(tmp_path):
    built_dir = _image(tmp_path, "custom")

    listed = images.built(built_dir, "registry.example:5000/mh/harness-pi:abc123")

    assert listed == [
        {"name": "registry.example:5000/mh/harness-pi:abc123", "directory": str(built_dir), "default": True}
    ]


def test_a_build_directory_that_cannot_be_read_is_a_422_that_says_why(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_DIR", str(tmp_path / "missing"))

    response = client.get("/v1/config/images")

    assert response.status_code == 422
    assert "the harness image's build directory" in response.json()["detail"]
    assert "has no Dockerfile" in response.json()["detail"]
