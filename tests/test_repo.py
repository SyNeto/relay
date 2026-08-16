import subprocess
from pathlib import Path

import pytest

from relay.engine.repo import (
    RepoError,
    branch_exists,
    build_commit_message,
    checkout_or_create_branch,
    current_branch,
    dirty_files,
    is_dirty,
    select_files_to_commit,
    stage_and_commit,
)


def _git(repo: Path, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "relay-test@example.com")
    _git(repo, "config", "user.name", "relay tests")
    (repo / "a.md").write_text("original a\n")
    _git(repo, "add", "a.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_checkout_or_create_branch_creates_new(tmp_path: Path):
    repo = _git_repo(tmp_path)

    result = checkout_or_create_branch(repo, "relay/r1")

    assert result == "created"
    assert current_branch(repo) == "relay/r1"


def test_checkout_or_create_branch_idempotent_noop(tmp_path: Path):
    repo = _git_repo(tmp_path)
    checkout_or_create_branch(repo, "relay/r1")

    result = checkout_or_create_branch(repo, "relay/r1")

    assert result == "up-to-date"
    assert current_branch(repo) == "relay/r1"


def test_checkout_or_create_branch_switches_to_existing(tmp_path: Path):
    repo = _git_repo(tmp_path)
    _git(repo, "branch", "relay/r1")

    result = checkout_or_create_branch(repo, "relay/r1")

    assert result == "checked-out"
    assert current_branch(repo) == "relay/r1"


def test_checkout_or_create_branch_refuses_when_dirty_and_different_branch(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "a.md").write_text("dirty change\n")

    with pytest.raises(RepoError):
        checkout_or_create_branch(repo, "relay/r1")

    assert current_branch(repo) == "main" or current_branch(repo) == "master"
    assert (repo / "a.md").read_text() == "dirty change\n"


def test_checkout_or_create_branch_allows_dirty_when_already_on_target(tmp_path: Path):
    repo = _git_repo(tmp_path)
    checkout_or_create_branch(repo, "relay/r1")
    (repo / "a.md").write_text("dirty change\n")

    result = checkout_or_create_branch(repo, "relay/r1")

    assert result == "up-to-date"


def test_dirty_files_reports_staged_unstaged_and_untracked(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "a.md").write_text("unstaged change\n")
    (repo / "staged.md").write_text("staged\n")
    _git(repo, "add", "staged.md")
    (repo / "untracked.md").write_text("untracked\n")

    files = dirty_files(repo)

    assert files == {"a.md", "staged.md", "untracked.md"}


def test_dirty_files_empty_when_clean(tmp_path: Path):
    repo = _git_repo(tmp_path)

    assert dirty_files(repo) == set()
    assert is_dirty(repo) is False


def test_branch_exists_true_and_false(tmp_path: Path):
    repo = _git_repo(tmp_path)
    _git(repo, "branch", "relay/r1")

    assert branch_exists(repo, "relay/r1") is True
    assert branch_exists(repo, "relay/does-not-exist") is False


def test_stage_and_commit_stages_only_given_files(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "a.md").write_text("changed a\n")
    (repo / "b.md").write_text("new b\n")

    stage_and_commit(repo, ["a.md"], "commit just a")

    assert dirty_files(repo) == {"b.md"}
    tracked = _git(repo, "show", "--stat", "--format=", "HEAD")
    assert "a.md" in tracked
    assert "b.md" not in tracked


def test_stage_and_commit_returns_full_sha(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "a.md").write_text("changed a\n")

    sha = stage_and_commit(repo, ["a.md"], "commit a")

    assert len(sha) == 40
    assert sha == _git(repo, "rev-parse", "HEAD").strip()


def test_stage_and_commit_raises_on_empty_files(tmp_path: Path):
    repo = _git_repo(tmp_path)

    with pytest.raises(RepoError):
        stage_and_commit(repo, [], "empty")


def test_select_files_to_commit_partial_overlap():
    assert select_files_to_commit({"a", "b"}, {"a", "c"}) == {"a"}


def test_select_files_to_commit_zero_overlap():
    assert select_files_to_commit({"a"}, {"c"}) == set()


def test_select_files_to_commit_indifferent_to_fix_provenance():
    # only sets in, set out -- no notion of relay fix run vs. hand-fixed
    assert select_files_to_commit({"hand-fixed.md"}, {"hand-fixed.md"}) == {"hand-fixed.md"}


def test_build_commit_message_lists_findings_no_trailer_when_spec_file_unset():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing"}]

    message = build_commit_message(findings)

    assert "- f1 [HIGH] fixed the thing" in message
    assert "Spec-File:" not in message


def test_build_commit_message_includes_spec_file_trailer_when_set():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing"}]

    message = build_commit_message(findings, spec_file="docs/SPEC.md")

    assert "Spec-File: docs/SPEC.md" in message


def test_build_commit_message_summary_override_replaces_only_headline():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing"}]

    message = build_commit_message(findings, spec_file="docs/SPEC.md", summary_override="custom headline")

    lines = message.splitlines()
    assert lines[0] == "custom headline"
    assert "- f1 [HIGH] fixed the thing" in message
    assert "Spec-File: docs/SPEC.md" in message
