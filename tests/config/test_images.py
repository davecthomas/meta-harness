"""The harness images the deployment builds, listed for a harness profile's image."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meta_harness.config import api as config_api
from meta_harness.config import images


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


def _image(root: Path, name: str) -> None:
    (root / name).mkdir()
    (root / name / "Dockerfile").write_text("FROM scratch\n")


def test_the_shipped_harness_image_is_listed_under_the_name_the_deployment_builds_it_as(client):
    body = client.get("/v1/config/images").json()
    assert body["default"] == images.DEFAULT_IMAGE
    assert {"name": images.DEFAULT_IMAGE, "directory": "images/harness-pi", "default": True} in body["images"]


def test_each_directory_with_a_dockerfile_is_an_image_tagged_like_the_default(client, monkeypatch, tmp_path):
    _image(tmp_path, "harness-pi")
    _image(tmp_path, "harness-other")
    (tmp_path / "notes").mkdir()
    (tmp_path / "README.md").write_text("not an image\n")
    monkeypatch.setattr(images, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(images, "DEFAULT_IMAGE", "harness-pi:dev")

    body = client.get("/v1/config/images").json()

    assert body == {
        "images": [
            {"name": "harness-other:dev", "directory": "images/harness-other", "default": False},
            {"name": "harness-pi:dev", "directory": "images/harness-pi", "default": True},
        ],
        "default": "harness-pi:dev",
    }


def test_a_default_in_a_registry_keeps_its_name_and_lends_its_tag(tmp_path):
    _image(tmp_path, "harness-pi")
    _image(tmp_path, "harness-other")

    names = [i["name"] for i in images.built(tmp_path, "registry.example:5000/mh/harness-pi:abc123")]

    assert names == ["harness-other:abc123", "registry.example:5000/mh/harness-pi:abc123"]
    assert [i["name"] for i in images.built(tmp_path, "registry.example:5000/mh/harness-pi")] == [
        "harness-other:latest",
        "registry.example:5000/mh/harness-pi",
    ]


def test_images_that_cannot_be_listed_are_a_422_that_says_why(client, monkeypatch, tmp_path):
    monkeypatch.setattr(images, "IMAGES_DIR", tmp_path / "missing")

    response = client.get("/v1/config/images")

    assert response.status_code == 422
    assert "the images directory" in response.json()["detail"]
    assert "cannot be read" in response.json()["detail"]
