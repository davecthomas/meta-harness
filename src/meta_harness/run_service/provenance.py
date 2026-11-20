"""Provenance headers: a comment at the top of each file a run created, saying which run made it.

The publish activity calls `apply` on the host, after the run has ended and
before it commits, with the files the run added to the repository (not the
ones it changed, renamed or deleted). `config/provenance.json` decides what
the header says and which files take one:

- `text` is the header, with `$run_id`, `$task_id`, `$goal_id`, `$candidate`
  and `$repo` filled in (`none` for one the run does not have). It may span
  lines.
- `styles` maps a comment syntax to the file names and extensions written in
  it: one marker (`#`) is a line comment, two (`<!-- -->`) open and close a
  block. A file in no style gets no header: JSON, for one, has no comments.
  MDX takes `{/* */}`, since it does not accept an HTML comment.
- `skip` lists glob patterns of files that never take one, such as minified
  or generated files.
- `enabled` is the default; `repos` turns it on or off for one repo, and a
  goal's own `provenance` setting, when set, outranks both (`wanted`).

A header goes after what must stay first: a `#!` line, a Python coding line,
an XML declaration, an HTML doctype, a Markdown file's front matter, or a
Dockerfile's parser directives (`# syntax=`, `# escape=`, `# check=`), which
Docker reads only ahead of every ordinary comment. A
blank line follows it. A file that already starts with the header keeps it as
it is, so a retried publish adds nothing twice. Binary files, files that are
not UTF-8, links and files over MAX_BYTES are left as they are.
"""

import json
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cache
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "provenance.json"
PLACEHOLDERS = ("run_id", "task_id", "goal_id", "candidate", "repo")
# A file larger than this is left without a header: the host does not rewrite large files.
MAX_BYTES = 1_000_000
MAX_TEXT_CHARS = 500
# What a placeholder reads as when the run has no such thing, such as a task id for a run started by hand.
MISSING = "none"
MARKDOWN = {".md", ".mdx", ".markdown"}
CODING_LINE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
DOCKERFILES = {"Dockerfile", "Containerfile"}
# A Dockerfile parser directive Docker knows; an unknown one is an ordinary comment, which ends the directives.
DOCKER_DIRECTIVE = re.compile(r"^#[ \t]*(syntax|escape|check)[ \t]*=", re.IGNORECASE)


@dataclass(frozen=True)
class Context:
    """What a header may name about the run that created the file."""

    run_id: str
    candidate: str
    task_id: str = ""
    goal_id: str = ""
    repo: str = ""


@cache
def settings() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def check_settings(config: dict[str, Any] | None = None) -> None:
    """Raises RuntimeError naming what is wrong with config/provenance.json."""
    name = CONFIG_PATH.name
    if config is None:
        try:
            config = settings()
        except (OSError, ValueError) as err:
            raise RuntimeError(f"{name}: not readable ({err})") from err
    if not isinstance(config, dict):
        raise RuntimeError(f"{name}: must be an object")
    if not isinstance(config.get("enabled"), bool):
        raise RuntimeError(f"{name}: enabled must be true or false")
    text = config.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"{name}: text must be the header's words")
    if len(text) > MAX_TEXT_CHARS:
        raise RuntimeError(f"{name}: text is longer than {MAX_TEXT_CHARS} characters")
    template = Template(text)
    if not template.is_valid():
        raise RuntimeError(f"{name}: text has a $ that names no placeholder; write $$ for a dollar sign")
    unknown = [key for key in template.get_identifiers() if key not in PLACEHOLDERS]
    if unknown:
        raise RuntimeError(f"{name}: text names {', '.join('$' + key for key in unknown)}; it may name {PLACEHOLDERS}")
    repos = config.get("repos", {})
    if not isinstance(repos, dict) or not all(isinstance(on, bool) for on in repos.values()):
        raise RuntimeError(f"{name}: repos must map each repo to true or false")
    skip = config.get("skip", [])
    if not isinstance(skip, list) or not all(isinstance(pattern, str) and pattern for pattern in skip):
        raise RuntimeError(f"{name}: skip must be a list of glob patterns")
    styles = config.get("styles")
    if not isinstance(styles, dict) or not styles:
        raise RuntimeError(f"{name}: styles must map each comment syntax to its files")
    seen: dict[str, str] = {}
    for syntax, files in styles.items():
        markers = syntax.split()
        if len(markers) not in (1, 2):
            raise RuntimeError(f"{name}: style {syntax!r} must be one line marker or an opening and closing pair")
        if len(markers) == 2 and markers[1] in text:
            raise RuntimeError(f"{name}: text holds {markers[1]!r}, which would end a {syntax!r} comment early")
        if not isinstance(files, list) or not all(isinstance(f, str) and f for f in files):
            raise RuntimeError(f"{name}: style {syntax!r} must list file names or extensions")
        for kind in files:
            key = kind.lower() if kind.startswith(".") else kind
            if key in seen:
                raise RuntimeError(f"{name}: {kind} is in both the {seen[key]!r} and the {syntax!r} styles")
            seen[key] = syntax


def wanted(repo: str, goal_choice: bool | None = None, config: dict[str, Any] | None = None) -> bool:
    """Whether a run's new files in repo take a header: the goal's choice when it made one, else the repo's
    in `repos`, else `enabled`."""
    if goal_choice is not None:
        return goal_choice
    config = settings() if config is None else config
    repos = config.get("repos") or {}
    if repo in repos:
        return bool(repos[repo])
    return bool(config.get("enabled"))


def render(context: Context, config: dict[str, Any] | None = None) -> str:
    """The header's words for this run, placeholders filled in."""
    config = settings() if config is None else config
    values = {key: getattr(context, key) or MISSING for key in PLACEHOLDERS}
    return Template(config["text"]).substitute(values).strip()


def style_for(path: str, config: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """The comment markers for a file, (open, close), close empty for a line comment; None when the file
    takes no header: in no style, or matched by `skip`."""
    config = settings() if config is None else config
    posix = PurePosixPath(path)
    if any(fnmatch(path, pattern) or fnmatch(posix.name, pattern) for pattern in config.get("skip") or []):
        return None
    for syntax, files in config["styles"].items():
        names = {f for f in files if not f.startswith(".")}
        suffixes = {f.lower() for f in files if f.startswith(".")}
        if posix.name in names or (posix.suffix and posix.suffix.lower() in suffixes):
            markers = syntax.split()
            return markers[0], markers[1] if len(markers) == 2 else ""
    return None


def header_lines(text: str, style: tuple[str, str]) -> list[str]:
    """The header as comment lines in a style."""
    opening, closing = style
    lines = text.splitlines()
    if not closing:
        return [f"{opening} {line}".rstrip() for line in lines]
    if len(lines) == 1:
        return [f"{opening} {lines[0]} {closing}"]
    return [opening, *lines, closing]


def _is_dockerfile(path: PurePosixPath) -> bool:
    return path.name in DOCKERFILES or path.suffix.lower() == ".dockerfile"


def _preamble(lines: list[str], path: PurePosixPath) -> int:
    """How many of a file's first lines must stay ahead of a header."""
    suffix = path.suffix.lower()
    if _is_dockerfile(path):
        kept = 0
        while kept < len(lines) and DOCKER_DIRECTIVE.match(lines[kept]):
            kept += 1
        return kept
    kept = 0
    if lines and lines[0].startswith("#!"):
        kept = 1
    if kept < len(lines) and kept < 2 and CODING_LINE.match(lines[kept]):
        kept += 1
    if kept == 0 and lines:
        first = lines[0].lstrip().lower()
        if first.startswith("<?xml"):
            kept = 1
            if len(lines) > 1 and lines[1].lstrip().lower().startswith("<!doctype"):
                kept = 2
        elif first.startswith("<!doctype"):
            kept = 1
        elif suffix in MARKDOWN and lines[0].strip() == "---":
            closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() in ("---", "...")), None)
            if closing is not None:
                kept = closing + 1
    return kept


def stamped(content: str, path: str, header: list[str]) -> str | None:
    """content with the header added at its top, or None when it already starts with it."""
    bom = "\ufeff" if content.startswith("\ufeff") else ""
    body = content[len(bom) :]
    newline = "\r\n" if "\r\n" in body else "\n"
    lines = body.splitlines(keepends=True)
    kept = _preamble([line.rstrip("\r\n") for line in lines], PurePosixPath(path))
    head, rest = "".join(lines[:kept]), "".join(lines[kept:])
    if head and not head.endswith("\n"):
        head += newline
    block = newline.join(header) + newline
    if rest.startswith(block):
        return None
    return bom + head + block + (newline + rest if rest else "")


def apply(workspace: Path, files: list[str], context: Context, config: dict[str, Any] | None = None) -> list[str]:
    """Adds the header to each of files, paths relative to workspace, that takes one. Returns the files that
    carry it afterwards, in order, ones that already did included."""
    config = settings() if config is None else config
    text = render(context, config)
    root = workspace.resolve()
    carried: list[str] = []
    for name in files:
        style = style_for(name, config)
        if style is None:
            continue
        path = workspace / name
        try:
            if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_BYTES or b"\0" in data:
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        result = stamped(content, name, header_lines(text, style))
        if result is not None:
            path.write_bytes(result.encode("utf-8"))
        carried.append(name)
    return carried
