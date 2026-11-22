"""A goal's specification picked from its repo: the documents a repo holds, and one's text.

    GET /v1/repos/{owner}/{name}/spec-files            the repo's Markdown and text files, by path
    GET /v1/repos/{owner}/{name}/spec-files/text?path= one file's text

A specification is most often a document already in the repo: a PRD or a design document under docs/. The New
goal page lists the repo's documents for the operator to pick from, reads the one picked, and sends its text as
the goal's specification (specs.py), as it does a file loaded from disk. So the goal keeps the text it was
planned from, whatever the file becomes later.

Both calls read the repo's default branch through the owner's GitHub App with `contents: read`, and only a
repo in the registry. A file is offered by its extension, and one too long to be a specification is refused
with what to do. Folders of other people's code are left out of the list, since a specification is never there.
"""

import base64
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query

from meta_harness import local_repos
from meta_harness.github_app import GitHubAppError
from meta_harness.planner import specs

READ_CONTENTS = {"contents": "read"}
EXTENSIONS = (".md", ".markdown", ".mdx", ".txt")
# Folders whose documents are someone else's, or built: never a goal's specification.
SKIPPED = {"node_modules", "vendor", "dist", "build", ".git", ".venv", "site-packages", "third_party"}
# The most files offered. A repo with more says so, and its file is still read by its typed path.
MAX_FILES = 2000


def offered(path: str) -> bool:
    """Whether a path is a document a specification could be: by its extension, outside the skipped folders."""
    parts = path.split("/")
    return path.lower().endswith(EXTENSIONS) and not any(part in SKIPPED for part in parts[:-1])


def _order(path: str) -> tuple[int, int, str]:
    """Documents under docs/ first, then the shallowest: where a specification most often is."""
    return (0 if path.lower().startswith(("docs/", "doc/", "specs/", "spec/")) else 1, path.count("/"), path.lower())


def register(
    app: FastAPI, operator: Callable[..., str], registered: Callable[[list[str]], list[str]], apps: Any
) -> None:
    """Adds the routes. registered(names) gives the registry's names, or answers 404; apps makes the calls."""

    def repo_of(owner: str, name: str) -> str:
        [repo] = registered([f"{owner}/{name}"])
        if local_repos.is_local(repo):
            raise HTTPException(
                status_code=422,
                detail=f"{repo} is a folder on this machine, not a repo on GitHub: load the specification as a file",
            )
        return repo

    def failed(repo: str, what: str, err: GitHubAppError) -> HTTPException:
        if err.status == 404:
            return HTTPException(
                status_code=404,
                detail=f"{repo} has no {what} on its default branch: pick another file, or load the specification as a file",
            )
        return HTTPException(
            status_code=502,
            detail=f"{what} of {repo} could not be read from GitHub ({err}). Try again in a minute, or load the specification as a file",
        )

    @app.get("/v1/repos/{owner}/{name}/spec-files")
    async def spec_files(owner: str, name: str, who: str = Depends(operator)) -> dict:
        """The repo's Markdown and text files on its default branch, the likeliest specifications first."""
        repo = repo_of(owner, name)
        try:
            tree = await apps.call(
                repo, READ_CONTENTS, "GET", f"/repos/{repo}/git/trees/HEAD", params={"recursive": "1"}
            )
        except GitHubAppError as err:
            raise failed(repo, "file list", err) from err
        paths = sorted(
            (
                str(entry["path"])
                for entry in (tree or {}).get("tree") or []
                if entry.get("type") == "blob" and offered(str(entry.get("path") or ""))
            ),
            key=_order,
        )
        return {
            "repo": repo,
            "files": [{"path": path} for path in paths[:MAX_FILES]],
            # GitHub cut its answer short, or the repo holds more than are offered: the rest are typed by path.
            "complete": not (tree or {}).get("truncated") and len(paths) <= MAX_FILES,
        }

    @app.get("/v1/repos/{owner}/{name}/spec-files/text")
    async def spec_file_text(
        owner: str, name: str, path: str = Query(min_length=1, max_length=500), who: str = Depends(operator)
    ) -> dict:
        """One document's text, to be the goal's specification."""
        repo = repo_of(owner, name)
        path = path.strip().lstrip("/")
        if ".." in path.split("/") or not path.lower().endswith(EXTENSIONS):
            raise HTTPException(
                status_code=422,
                detail=f"a specification is a Markdown or text file ({', '.join(EXTENSIONS)}), by its path in the repo: {path!r} is not one",
            )
        try:
            found = await apps.call(repo, READ_CONTENTS, "GET", f"/repos/{repo}/contents/{quote(path)}")
        except GitHubAppError as err:
            raise failed(repo, path, err) from err
        if not isinstance(found, dict) or found.get("type") != "file":
            raise HTTPException(status_code=422, detail=f"{path} in {repo} is a folder, not a file: pick a file in it")
        if found.get("encoding") != "base64" or not found.get("content"):
            # GitHub leaves the content out of a file over a megabyte.
            raise HTTPException(
                status_code=422,
                detail=f"{path} is too large to be a specification ({specs.MAX_CHARS:,} characters at most): split it into one file per goal",
            )
        try:
            text = base64.b64decode(found["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as err:
            raise HTTPException(
                status_code=422,
                detail=f"{path} is not a text file: pick the Markdown or text file that holds the specification",
            ) from err
        if not text.strip():
            raise HTTPException(status_code=422, detail=f"{path} is empty: pick the file that holds the specification")
        if len(text) > specs.MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"{path} is {len(text):,} characters, and a specification holds {specs.MAX_CHARS:,} at most: split it into one file per goal",
            )
        return {"repo": repo, "path": path, "name": path.rsplit("/", 1)[-1], "text": text}
