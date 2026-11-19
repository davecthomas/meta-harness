"""The harness images the deployment builds, for the harness profile form to pick a profile's image from.

The deployment builds each directory under `images/` that holds a Dockerfile
(`make images` in samples/temporal-run-task, which mh-images.service runs on
the VM). Images are built on the machine and never pulled, so a profile that
names any other image fails every run on it at start
(meta_harness.run_service.sandbox).

Each image is listed under the name the deployment tags it with. The default
image, HARNESS_IMAGE (meta_harness.run_service.routing.DEFAULT_IMAGE, default
`harness-pi:dev`), is the build of the directory its repository is named
after; any other directory is tagged `<directory>:<the default's tag>`.
"""

from pathlib import Path
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT
from meta_harness.run_service.routing import DEFAULT_IMAGE

IMAGES_DIR = REPO_ROOT / "images"


class ImagesUnreadable(Exception):
    """The images directory cannot be read, so the built images cannot be listed."""


def _split(image: str) -> tuple[str, str]:
    """An image reference as (repository, tag); the tag is `latest` when it names none.

    A colon before the last slash is a registry's port, not a tag.
    """
    head, colon, tag = image.rpartition(":")
    if not colon or "/" in tag:
        return image, "latest"
    return head, tag


def built(root: Path | None = None, default: str | None = None) -> list[dict[str, Any]]:
    """Every harness image the deployment builds, by name: its name, the directory it is built from, and
    whether it is the default image, the one a profile with no image runs.

    Raises:
        ImagesUnreadable: The images directory is missing or cannot be listed.
    """
    root = root or IMAGES_DIR
    default = default or DEFAULT_IMAGE
    repository, tag = _split(default)
    default_dir = repository.rsplit("/", 1)[-1]
    try:
        directories = sorted(d for d in root.iterdir() if d.is_dir() and (d / "Dockerfile").is_file())
    except OSError as err:
        raise ImagesUnreadable(f"the images directory {root} cannot be read: {err.strerror or err}") from err
    found = []
    for directory in directories:
        is_default = directory.name == default_dir
        found.append(
            {
                "name": default if is_default else f"{directory.name}:{tag}",
                "directory": f"images/{directory.name}",
                "default": is_default,
            }
        )
    return sorted(found, key=lambda image: image["name"])
