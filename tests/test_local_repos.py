import os

import pytest

from meta_harness import local_repos


def test_a_repo_under_a_listed_directory_is_allowed_and_one_outside_is_not(tmp_path, monkeypatch):
    listed, outside = tmp_path / "repos", tmp_path / "runs"
    (listed / "origin.git").mkdir(parents=True)
    (outside / "t-1" / "run-1" / "workspace" / "evil").mkdir(parents=True)
    monkeypatch.setenv("MH_LOCAL_REPO_ROOTS", str(listed))

    assert local_repos.allowed(str(listed / "origin.git"))
    assert not local_repos.allowed(str(outside / "t-1" / "run-1" / "workspace" / "evil"))
    assert not local_repos.allowed(str(listed)), "the directory itself is not a repo a task may name"
    assert not local_repos.allowed(str(listed / ".." / "runs" / "t-1")), "a .. does not step out of it"


def test_a_link_under_a_listed_directory_does_not_reach_a_repo_outside_it(tmp_path, monkeypatch):
    listed, outside = tmp_path / "repos", tmp_path / "elsewhere.git"
    listed.mkdir()
    outside.mkdir()
    (listed / "link.git").symlink_to(outside)
    monkeypatch.setenv("MH_LOCAL_REPO_ROOTS", str(listed))

    assert not local_repos.allowed(str(listed / "link.git"))


def test_with_no_directories_listed_only_the_smoke_repo_is_allowed(tmp_path, monkeypatch):
    smoke = tmp_path / "smoke" / "origin.git"
    monkeypatch.delenv("MH_LOCAL_REPO_ROOTS")
    monkeypatch.setenv("MH_SMOKE_REPO", str(smoke))

    assert local_repos.allowed(str(smoke))
    assert not local_repos.allowed(str(tmp_path / "other.git"))


@pytest.mark.parametrize("value", ["relative/dir", "/", f"/srv/repos{os.pathsep}also-relative"])
def test_a_bad_directory_stops_startup_naming_the_setting(monkeypatch, value):
    monkeypatch.setenv("MH_LOCAL_REPO_ROOTS", value)

    with pytest.raises(RuntimeError, match="MH_LOCAL_REPO_ROOTS"):
        local_repos.check_settings()


def test_a_relative_smoke_repo_stops_startup_naming_the_setting(monkeypatch):
    monkeypatch.setenv("MH_SMOKE_REPO", "smoke/origin.git")

    with pytest.raises(RuntimeError, match="MH_SMOKE_REPO"):
        local_repos.check_settings()


def test_the_refusal_says_what_to_do():
    said = local_repos.refusal("/srv/x.git")

    assert "owner/name" in said and "MH_LOCAL_REPO_ROOTS" in said
