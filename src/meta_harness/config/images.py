"""The harness images the deployment builds, for the harness profile form to pick a profile's image from.

The deployment builds one harness image: mh-images.service runs `make images` in samples/temporal-run-task,
whose `build_image` (stack.sh) builds HARNESS_DIR, `images/harness-pi` unless set, and tags it HARNESS_IMAGE,
`harness-pi:dev` unless set. The VM's environment file sets neither, and the planner reads the same file, so
the planner sees the same two values the build used. Images are built on the machine and never pulled, so a
profile that names any other image fails every run on it at start (meta_harness.run_service.sandbox).

So the list is that one image, under HARNESS_IMAGE (meta_harness.run_service.routing.DEFAULT_IMAGE), the
image a profile with no image runs. Another directory under `images/` is not listed: nothing builds or
tags it.
"""

import os
from pathlib import Path
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT
from meta_harness.run_service.routing import DEFAULT_IMAGE

# What samples/temporal-run-task/Makefile builds the harness image from when HARNESS_DIR is not set.
DEFAULT_DIR = REPO_ROOT / "images" / "harness-pi"


class ImagesUnreadable(Exception):
    """The harness image's build directory cannot be read, so the built images cannot be listed."""


def harness_dir() -> Path:
    """The directory the deployment builds the harness image from: HARNESS_DIR, else `images/harness-pi`."""
    return Path(os.environ.get("HARNESS_DIR") or DEFAULT_DIR)


def _shown(directory: Path) -> str:
    """The directory relative to the repository, or as it is when it lies outside it."""
    try:
        return str(directory.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(directory)


def built(directory: Path | None = None, default: str | None = None) -> list[dict[str, Any]]:
    """Every harness image the deployment builds: the one image `make images` builds, with its name, the
    directory it is built from, and `default`, true since it is the image a profile with no image runs.

    Raises:
        ImagesUnreadable: The build directory is missing, or holds no readable Dockerfile.
    """
    directory = directory or harness_dir()
    default = default or DEFAULT_IMAGE
    dockerfile = directory / "Dockerfile"
    try:
        if not dockerfile.is_file():
            raise ImagesUnreadable(f"the harness image's build directory {directory} has no Dockerfile")
        with dockerfile.open("rb"):
            pass
    except OSError as err:
        raise ImagesUnreadable(
            f"the harness image's build directory {directory} cannot be read: {err.strerror or err}"
        ) from err
    return [{"name": default, "directory": _shown(directory), "default": True}]
