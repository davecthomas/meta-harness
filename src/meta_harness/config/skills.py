"""Skills: packaged instructions, with the scripts and references they use, that a run's agent loads.

A skill is a directory in the usual shape: `SKILL.md`, whose frontmatter names the skill and says what it is for,
and the files beside it. The meta-harness keeps skills as a configuration kind, `skills`, so they are imported
once by an admin, versioned and rolled back like the rest, and available to every goal's runs. A harness profile
names the skills its runs load (`skills: [...]`); the run service sends their content with each run, and the
harness server loads exactly those, and nothing from the task's repo.

The document:

    {"skills": {"<name>": {"description": str, "source": {...}, "files": {"<path>": "<text>"}}}}

`source` says where the skill came from (`{"kind": "git", "url", "ref", "path", "commit"}`, `{"kind": "upload"}`
or `{"kind": "shipped"}`). Its content is stored with the version, so a run gets exactly what was approved,
whatever its source does later.

This module checks a document and reads a skill from a directory or from a git repository. Nothing here saves.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# What one skill may hold. A skill is instructions and small scripts; anything bigger is not one.
MAX_FILES = 200
MAX_FILE_BYTES = 256 * 1024
MAX_SKILL_BYTES = 2 * 1024 * 1024
# Only https: a git URL may name a local path or another protocol, which the planner's process must not read.
GIT_URL = re.compile(r"^https://[A-Za-z0-9.\-]+(:[0-9]+)?/[^\s]+$")
GIT_TIMEOUT_SECONDS = 60
# Directories never taken into a skill: version control and caches.
SKIPPED = {".git", "__pycache__", "node_modules", ".venv"}


class SkillError(ValueError):
    """A skill that cannot be read or imported. The message says why."""


def skills_dir() -> Path:
    """The shipped skills, config/skills/<name>/, which seed the kind's first version."""
    return Path(os.environ.get("MH_SKILLS_DIR") or REPO_ROOT / "config" / "skills")


def frontmatter(text: str) -> dict[str, str]:
    """The SKILL.md frontmatter's top-level `key: value` lines, unquoted. Empty when there is none."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    found = {}
    for line in text[3:end].splitlines():
        key, colon, value = line.partition(":")
        if colon and key and not key.startswith((" ", "\t")):
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] == '"':
                # A double-quoted YAML string: its quotes go, and its escaped quotes are quotes.
                value = value[1:-1].replace('\\"', '"')
            found[key.strip()] = value.strip("'")
    return found


def _safe(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.startswith("/") and ".." not in parts and "\\" not in path


def skill_errors(name: str, skill: Any) -> list[str]:
    """What is wrong with one skill."""
    if not NAME.match(name):
        return [f"{name}: a skill's name is lowercase letters, digits and hyphens, at most 63"]
    if not isinstance(skill, dict):
        return [f"{name}: a skill is an object"]
    files = skill.get("files")
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return [f"{name}: files must map each path to its text"]
    reasons = []
    if "SKILL.md" not in files:
        reasons.append(f"{name}: a skill has a SKILL.md")
    else:
        head = frontmatter(files["SKILL.md"])
        if head.get("name") != name:
            reasons.append(f"{name}: its SKILL.md names it {head.get('name')!r}; the two must match")
        if not head.get("description"):
            reasons.append(f"{name}: its SKILL.md has no description, which is how an agent knows when to use it")
    if not isinstance(skill.get("description"), str) or not skill.get("description"):
        reasons.append(f"{name}: description is required")
    if len(files) > MAX_FILES:
        reasons.append(f"{name}: {len(files)} files is more than a skill may hold ({MAX_FILES})")
    unsafe = sorted(path for path in files if not _safe(path))
    if unsafe:
        reasons.append(f"{name}: a file's path is relative and stays inside the skill: {', '.join(unsafe[:5])}")
    big = sorted(path for path, text in files.items() if len(text.encode()) > MAX_FILE_BYTES)
    if big:
        reasons.append(f"{name}: files over {MAX_FILE_BYTES // 1024} KB: {', '.join(big[:5])}")
    total = sum(len(text.encode()) for text in files.values())
    if total > MAX_SKILL_BYTES:
        reasons.append(
            f"{name}: {total // 1024} KB in all is more than a skill may hold ({MAX_SKILL_BYTES // 1024} KB)"
        )
    return reasons


def check_skills(document: dict[str, Any], others: dict[str, dict[str, Any]]) -> list[str]:
    """Problems with a skills document, given the other kinds' active documents: a skill a harness profile
    names cannot go."""
    skills = document.get("skills")
    if not isinstance(skills, dict):
        return ["skills must be an object of skills, by name"]
    reasons = []
    for name, skill in skills.items():
        reasons += skill_errors(name, skill)
    profiles = (others.get("profiles") or {}).get("profiles", {})
    for profile, spec in profiles.items():
        for name in (spec or {}).get("skills") or []:
            if name not in skills:
                reasons.append(f"harness profile {profile} names skill {name}: remove it from the profile first")
    return reasons


def read_dir(root: Path) -> dict[str, str]:
    """Every text file under a skill's directory, by its path relative to it.

    Raises:
        SkillError: A file is not text, or the directory holds more than a skill may.
    """
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIPPED for part in relative.parts) or path.is_symlink() or not path.is_file():
            continue
        if len(files) >= MAX_FILES:
            raise SkillError(f"{root.name} holds more than {MAX_FILES} files, more than a skill may")
        try:
            files[relative.as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as err:
            raise SkillError(f"{relative.as_posix()} is not text: a skill holds text files only") from err
    return files


def from_files(files: dict[str, str], source: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A skill from its files: its name and its entry for the document.

    Raises:
        SkillError: There is no SKILL.md, or it names no skill.
    """
    if "SKILL.md" not in files:
        raise SkillError("no SKILL.md: a skill's directory has one at its top")
    head = frontmatter(files["SKILL.md"])
    name = head.get("name") or ""
    if not name:
        raise SkillError("its SKILL.md names no skill (a `name:` line in its frontmatter)")
    skill = {"description": head.get("description") or "", "source": source, "files": files}
    reasons = skill_errors(name, skill)
    if reasons:
        raise SkillError("; ".join(reasons))
    return name, skill


def seed_skills() -> dict[str, Any]:
    """The shipped skills, each a directory under config/skills/, as the kind's first version."""
    found: dict[str, Any] = {}
    root = skills_dir()
    if root.is_dir():
        for directory in sorted(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
            name, skill = from_files(read_dir(directory), {"kind": "shipped"})
            found[name] = skill
    return {"skills": found}


def from_git(url: str, ref: str | None, path: str | None) -> tuple[str, dict[str, Any]]:
    """A skill read from a directory of a git repository at a branch, tag or commit, as of now. The commit it
    was read at is kept in its source, so the import says exactly what was taken.

    Raises:
        SkillError: The URL is not https, the repository or ref cannot be read, or the path holds no skill.
    """
    host = url.split("/")[2].split(":")[0].lower() if GIT_URL.match(url) else ""
    # A public git host by name: never an address, this machine or a private network's name.
    private = not re.search(r"[a-z]", host) or "." not in host or host.endswith((".internal", ".local", ".localhost"))
    if not host or private:
        raise SkillError("a skill is imported from an https:// git URL on a public host")
    if path and not _safe(path):
        raise SkillError("the path is relative to the repository and stays inside it")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    with tempfile.TemporaryDirectory(prefix="mh-skill-") as work:
        clone = Path(work) / "repo"
        command = ["git", "clone", "--quiet", "--depth", "1", "--no-tags", "--single-branch"]
        command += ["--branch", ref] if ref else []
        try:
            subprocess.run(
                [*command, "--", url, str(clone)],
                check=True,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                env=env,
            )
            commit = subprocess.run(
                ["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip()
        except subprocess.TimeoutExpired as err:
            raise SkillError(f"reading {url} took longer than {GIT_TIMEOUT_SECONDS} seconds") from err
        except subprocess.CalledProcessError as err:
            reason = (err.stderr or "").strip().splitlines()[-1:] or ["git failed"]
            raise SkillError(f"could not read {url}{f' at {ref}' if ref else ''}: {reason[0]}") from err
        root = (clone / path) if path else clone
        if not (root / "SKILL.md").is_file():
            raise SkillError(f"{path or 'the repository top'} holds no SKILL.md")
        source = {"kind": "git", "url": url, "ref": ref or "", "path": path or "", "commit": commit}
        return from_files(read_dir(root), source)


def for_run(skills: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    """The skills a run loads, as the harness server takes them: each one's name and files. A name the
    document lacks is left out; the profile check keeps that from happening."""
    return [{"name": name, "files": skills[name]["files"]} for name in names if name in skills]
