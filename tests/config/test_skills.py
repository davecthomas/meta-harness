"""Skills: packaged instructions a harness profile's runs load, kept as a configuration kind."""

import subprocess
from pathlib import Path

import pytest

from meta_harness.config import kinds, skills
from meta_harness.run_service import activities

SKILL_MD = '---\nname: tidy-notes\ndescription: "Tidy a file of notes: \\"one idea per line\\"."\n---\n\nRead the notes, then tidy them.\n'


def _skill(**files: str) -> dict:
    return {
        "description": "Tidy a file of notes.",
        "source": {"kind": "upload"},
        "files": {"SKILL.md": SKILL_MD, **files},
    }


def test_a_skill_is_read_from_its_files_by_its_frontmatter():
    name, skill = skills.from_files({"SKILL.md": SKILL_MD, "scripts/tidy.py": "print('tidy')\n"}, {"kind": "upload"})
    assert name == "tidy-notes" and skill["description"] == 'Tidy a file of notes: "one idea per line".'
    assert sorted(skill["files"]) == ["SKILL.md", "scripts/tidy.py"] and skill["source"] == {"kind": "upload"}
    with pytest.raises(skills.SkillError, match="no SKILL.md"):
        skills.from_files({"README.md": "x"}, {"kind": "upload"})
    with pytest.raises(skills.SkillError, match="names no skill"):
        skills.from_files({"SKILL.md": "Just text."}, {"kind": "upload"})


def test_a_skills_document_is_refused_with_every_reason():
    assert skills.check_skills({"skills": {"tidy-notes": _skill()}}, {}) == []
    assert skills.check_skills({"skills": []}, {}) == ["skills must be an object of skills, by name"]
    reasons = skills.check_skills(
        {
            "skills": {
                "Tidy Notes": _skill(),
                "other-name": _skill(),
                "tidy-notes": _skill(**{"../escape.sh": "x", "big.txt": "x" * (skills.MAX_FILE_BYTES + 1)}),
            }
        },
        {},
    )
    assert "Tidy Notes: a skill's name is lowercase letters, digits and hyphens, at most 63" in reasons
    assert "other-name: its SKILL.md names it 'tidy-notes'; the two must match" in reasons
    assert "tidy-notes: a file's path is relative and stays inside the skill: ../escape.sh" in reasons
    assert "tidy-notes: files over 256 KB: big.txt" in reasons


def test_a_skill_a_profile_names_cannot_be_removed_and_a_profile_names_only_known_skills():
    profile = {"tools": ["read", "bash"], "max_turns": 10, "max_minutes": 10, "skills": ["tidy-notes"]}
    refused = skills.check_skills({"skills": {}}, {"profiles": {"profiles": {"pi-lean": profile}}})
    assert refused == ["harness profile pi-lean names skill tidy-notes: remove it from the profile first"]

    known = {"skills": {"skills": {"tidy-notes": _skill()}}, "tiers": {}}
    assert kinds.check_profiles({"profiles": {"pi-lean": profile}}, known) == []
    unknown = kinds.check_profiles({"profiles": {"pi-lean": {**profile, "skills": ["nope"]}}}, known)
    assert unknown == ["pi-lean: no skill named nope; import it on the Skills page first"]
    blind = kinds.check_profiles({"profiles": {"pi-lean": {**profile, "tools": ["bash"]}}}, known)
    assert blind == ["pi-lean: a profile with skills needs the read tool, which is how a run opens one"]


def test_the_shipped_skills_seed_the_kind_and_no_ai_slop_is_one_of_them(tmp_path: Path, monkeypatch):
    shipped = skills.seed_skills()["skills"]
    assert "no-ai-slop" in shipped and shipped["no-ai-slop"]["source"] == {"kind": "shipped"}
    assert skills.check_skills({"skills": shipped}, {}) == []
    assert "MIT License" in shipped["no-ai-slop"]["files"]["LICENSE"]

    (tmp_path / "tidy-notes").mkdir()
    (tmp_path / "tidy-notes" / "SKILL.md").write_text(SKILL_MD)
    (tmp_path / "not-a-skill").mkdir()
    monkeypatch.setenv("MH_SKILLS_DIR", str(tmp_path))
    assert list(skills.seed_skills()["skills"]) == ["tidy-notes"]


def test_a_skill_is_imported_from_a_git_repository_at_a_ref_and_says_the_commit(tmp_path: Path, monkeypatch):
    origin = tmp_path / "origin"
    (origin / "skills" / "tidy-notes" / "scripts").mkdir(parents=True)
    (origin / "skills" / "tidy-notes" / "SKILL.md").write_text(SKILL_MD)
    (origin / "skills" / "tidy-notes" / "scripts" / "tidy.py").write_text("print('tidy')\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(origin)]
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(origin)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "a skill"], check=True)
    commit = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    # The import takes only https URLs on public hosts; the clone itself is pointed at the local repository.
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[:2] == ["git", "clone"]:
            command = [origin.as_uri() if part == "https://github.com/acme/skills" else part for part in command]
        return real_run(command, **kwargs)

    monkeypatch.setattr(skills.subprocess, "run", run)

    name, skill = skills.from_git("https://github.com/acme/skills", "main", "skills/tidy-notes")

    assert name == "tidy-notes" and sorted(skill["files"]) == ["SKILL.md", "scripts/tidy.py"]
    assert skill["source"] == {
        "kind": "git",
        "url": "https://github.com/acme/skills",
        "ref": "main",
        "path": "skills/tidy-notes",
        "commit": commit,
    }
    with pytest.raises(skills.SkillError, match="holds no SKILL.md"):
        skills.from_git("https://github.com/acme/skills", "main", "skills")


@pytest.mark.parametrize(
    "url", ["git@github.com:acme/skills.git", "file:///etc", "http://github.com/a/b", "https://10.0.0.5/a", "https://localhost/a/b", "https://metadata.google.internal/x"]
)  # fmt: skip
def test_a_skill_is_imported_only_from_an_https_url_on_a_public_host(url: str):
    with pytest.raises(skills.SkillError, match="https:// git URL on a public host"):
        skills.from_git(url, None, None)


def test_a_run_is_sent_the_skills_its_profile_names_with_their_files(monkeypatch):
    document = {"tidy-notes": _skill(**{"scripts/tidy.py": "print('tidy')\n"}), "unused": _skill()}
    monkeypatch.setattr(activities.routing, "skills", lambda: document)

    sent = activities.run_skills({"name": "pi-lean", "skills": ["tidy-notes"]})

    assert [s["name"] for s in sent] == ["tidy-notes"] and sorted(sent[0]["files"]) == ["SKILL.md", "scripts/tidy.py"]
    assert activities.run_skills({"name": "pi-lean"}) == [], "a profile that names none reads nothing"

    def unreadable():
        raise RuntimeError("no database")

    monkeypatch.setattr(activities.routing, "skills", unreadable)
    assert activities.run_skills({"name": "pi-lean", "skills": ["tidy-notes"]}) == [], "the run goes on without them"
