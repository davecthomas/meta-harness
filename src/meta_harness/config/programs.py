# Created by the meta-harness in run t-8c664bfa94b0:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""The programs a harness image has, for a harness profile's bash allowlist to pick from.

A profile's `bash_allow` names the programs pi's bash tool may run (images/harness-pi/extension/bash-allowlist.ts
reads the first word of each simple command, a path by its last part). A name the image lacks allows nothing,
so the allowlist is picked from what the image has: every executable file on the image's PATH, and bash's
builtins (cd, echo, test), which the bash tool runs with no file behind them.

The list is read from the image itself, as a run sees it: a container of the image, with no network, no
capabilities and the sandbox's uid, runs a shell that lists them. Images are built on the machine and never
pulled, so the container is started with `--pull never`. The list is kept per image id: an unchanged image is
read once, and a rebuilt one is read again.
"""

import logging
import os
import subprocess
import time

from meta_harness import retrying
from meta_harness.config import images

log = logging.getLogger("meta_harness.config.programs")

# Every executable file on PATH, by its name, then bash's builtins when the image has bash.
LIST_SCRIPT = """IFS=:
for d in $PATH; do
  for f in "$d"/*; do
    [ -f "$f" ] && [ -x "$f" ] && printf '%s\\n' "${f##*/}"
  done
done
if command -v bash >/dev/null 2>&1; then bash -c 'compgen -b'; fi
exit 0
"""

# A docker call is local, but the daemon can be busy or restarting: a few quick tries, then a reason.
POLICY = retrying.Policy(attempts=3, base_seconds=0.5, cap_seconds=2.0, deadline_seconds=30.0)
TIMEOUT_SECONDS = 20.0

# Named so tests can take the waiting out.
_sleep = time.sleep

# The programs of each image read so far, by image id.
_by_id: dict[str, list[str]] = {}


class ProgramsUnreadable(Exception):
    """The image's programs cannot be listed: it is not one the deployment builds, it is not built on this
    machine, or docker cannot run it."""


class _Missing(Exception):
    """docker answered that the image does not exist: trying again cannot help."""


def _docker(*args: str) -> tuple[int, str, str]:
    """Runs one docker command: its exit code, stdout and stderr."""
    try:
        done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except FileNotFoundError as err:
        raise ProgramsUnreadable("the docker command is not installed on this machine") from err
    except subprocess.TimeoutExpired:
        return 124, "", f"docker {args[0]} did not finish within {TIMEOUT_SECONDS:.0f}s"
    return done.returncode, done.stdout, done.stderr


def _tried(what: str, *args: str) -> str:
    """The stdout of a docker command, tried again with backoff when it fails, since a busy or restarting
    daemon is transient. An image docker says does not exist is not tried again."""
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        code, out, err = _docker(*args)
        if code == 0:
            if attempt > 1:
                log.info("%s succeeded on try %d", what, attempt)
            return out
        why = (err.strip().splitlines() or [f"exit code {code}"])[-1]
        if "no such image" in err.lower() or "unable to find image" in err.lower():
            raise _Missing(why)
        wait = retrying.backoff(POLICY, attempt)
        if attempt >= POLICY.attempts or time.monotonic() - started + wait >= POLICY.deadline_seconds:
            log.error("%s failed after %d tries: %s", what, attempt, why)
            raise ProgramsUnreadable(f"{what} failed: {why}")
        log.warning("%s: %s; try %d of %d failed, trying again in %.1fs", what, why, attempt, POLICY.attempts, wait)
        _sleep(wait)


def _parse(out: str) -> list[str]:
    """The program names in the listing, each once, sorted; a name with a space could never be a command's
    first word, so it is left out."""
    return sorted(
        {line.strip() for line in out.splitlines() if line.strip() and not any(c.isspace() for c in line.strip())}
    )


def listed(image: str | None = None) -> dict[str, object]:
    """The programs a harness image has, for a profile's bash allowlist: {image, programs}. image is a name the
    deployment builds (meta_harness.config.images); empty is the default image, which a profile with no image
    runs.

    Raises:
        ProgramsUnreadable: The image is not one the deployment builds, is not built here, or cannot be run.
    """
    image = (image or "").strip() or images.DEFAULT_IMAGE
    try:
        built = [i["name"] for i in images.built()]
    except images.ImagesUnreadable as err:
        raise ProgramsUnreadable(str(err)) from err
    if image not in built:
        raise ProgramsUnreadable(f"the deployment builds no harness image named {image}, so it has no programs to list")
    try:
        image_id = _tried(f"docker image inspect {image}", "image", "inspect", "--format", "{{.Id}}", image).strip()
    except _Missing as err:
        raise ProgramsUnreadable(f"the harness image {image} is not built on this machine yet: {err}") from err
    if image_id not in _by_id:
        try:
            out = _tried(
                f"listing the programs of {image}",
                "run", "--rm", "--pull", "never", "--network", "none",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--entrypoint", "sh", image, "-c", LIST_SCRIPT,
            )  # fmt: skip
        except _Missing as err:
            raise ProgramsUnreadable(f"the harness image {image} is not built on this machine yet: {err}") from err
        programs = _parse(out)
        if not programs:
            raise ProgramsUnreadable(f"the harness image {image} lists no programs on its PATH")
        _by_id[image_id] = programs
    return {"image": image, "programs": _by_id[image_id]}
