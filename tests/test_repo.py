import subprocess
from pathlib import Path

import pytest

from relay.engine.repo import (
    RepoError,
    branch_exists,
    build_commit_message,
    checkout_or_create_branch,
    create_pull_request,
    current_branch,
    diff_against,
    build_pr_body,
    dirty_files,
    fetch_branch,
    is_dirty,
    push_branch,
    push_force_with_lease,
    rebase_onto,
    require_branch_matches_remote,
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


def _bare_remote(tmp_path: Path, name: str = "remote.git") -> Path:
    remote = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True, text=True)
    return remote


def _clone(remote: Path, tmp_path: Path, name: str) -> Path:
    dest = tmp_path / name
    result = subprocess.run(["git", "clone", "-q", str(remote), str(dest)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    _git(dest, "config", "user.email", "relay-test@example.com")
    _git(dest, "config", "user.name", "relay tests")
    return dest


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


def test_checkout_or_create_branch_uses_base_branch_content(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "feature-a")
    (repo / "a.md").write_text("changed on feature-a\n")
    stage_and_commit(repo, ["a.md"], "feature-a commit")
    checkout_or_create_branch(repo, base)

    result = checkout_or_create_branch(repo, "feature-b", base_branch="feature-a")

    assert result == "created"
    assert (repo / "a.md").read_text() == "changed on feature-a\n"


def test_checkout_or_create_branch_ignores_base_branch_when_already_exists(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "relay/r1")
    checkout_or_create_branch(repo, base)
    (repo / "a.md").write_text("new base content\n")
    stage_and_commit(repo, ["a.md"], "advance base")

    result = checkout_or_create_branch(repo, "relay/r1", base_branch=base)

    assert result == "checked-out"
    assert (repo / "a.md").read_text() == "original a\n"


def test_fetch_branch_updates_remote_tracking_ref_only(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    seed = _clone(remote, tmp_path, "seed")
    (seed / "a.md").write_text("seed\n")
    _git(seed, "add", "a.md")
    _git(seed, "commit", "-q", "-m", "seed commit")
    _git(seed, "push", "-q", "origin", "HEAD:dev")

    local = _clone(remote, tmp_path, "local")
    fetch_branch(local, "origin", "dev")

    assert _git(local, "rev-parse", "origin/dev").strip() == _git(seed, "rev-parse", "HEAD").strip()
    assert not branch_exists(local, "dev")


def test_fetch_branch_raises_on_unknown_branch(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    local = _clone(remote, tmp_path, "local")

    with pytest.raises(RepoError):
        fetch_branch(local, "origin", "does-not-exist")


def test_fetch_branch_raises_on_unknown_remote(tmp_path: Path):
    repo = _git_repo(tmp_path)

    with pytest.raises(RepoError):
        fetch_branch(repo, "does-not-exist-remote", "main")


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


def test_select_files_to_commit_also_files_union_when_dirty():
    result = select_files_to_commit({"a"}, {"a", "CHANGELOG.md"}, also_files={"CHANGELOG.md"})

    assert result == {"a", "CHANGELOG.md"}


def test_select_files_to_commit_also_files_alone_with_no_finding_files():
    result = select_files_to_commit(set(), {"CHANGELOG.md"}, also_files={"CHANGELOG.md"})

    assert result == {"CHANGELOG.md"}


def test_select_files_to_commit_also_files_not_dirty_raises():
    with pytest.raises(RepoError, match="CHANGELOG.md"):
        select_files_to_commit({"a"}, {"a"}, also_files={"CHANGELOG.md"})


def test_select_files_to_commit_also_files_partial_dirty_names_only_missing():
    with pytest.raises(RepoError) as exc_info:
        select_files_to_commit({"a"}, {"a", "CHANGELOG.md"}, also_files={"CHANGELOG.md", "pyproject.toml"})

    assert "pyproject.toml" in str(exc_info.value)
    assert "CHANGELOG.md" not in str(exc_info.value)


def test_select_files_to_commit_also_files_overlap_with_finding_file():
    result = select_files_to_commit({"CHANGELOG.md"}, {"CHANGELOG.md"}, also_files={"CHANGELOG.md"})

    assert result == {"CHANGELOG.md"}


def test_select_files_to_commit_also_files_none_is_unchanged_behavior():
    assert select_files_to_commit({"a"}, {"a"}, also_files=None) == select_files_to_commit({"a"}, {"a"})


def test_build_commit_message_also_files_section():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing", "file": "a.md"}]

    message = build_commit_message(findings, also_files=["CHANGELOG.md", "pyproject.toml"])

    assert "Also committed: CHANGELOG.md, pyproject.toml" in message


def test_build_commit_message_also_files_dedup_against_finding_file():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing", "file": "CHANGELOG.md"}]

    message = build_commit_message(findings, also_files=["CHANGELOG.md"])

    assert "- f1 [HIGH] fixed the thing" in message
    assert "Also committed:" not in message


def test_build_commit_message_also_files_with_zero_findings():
    message = build_commit_message([], also_files=["CHANGELOG.md"], summary_override="release bookkeeping")

    assert message.splitlines()[0] == "release bookkeeping"
    assert "Also committed: CHANGELOG.md" in message


def test_build_commit_message_also_files_none_omits_section():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing"}]

    assert "Also committed:" not in build_commit_message(findings, also_files=None)


def test_diff_against_shows_changes_since_divergence(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "feature")
    (repo / "a.md").write_text("changed on feature\n")
    stage_and_commit(repo, ["a.md"], "feature commit")

    diff = diff_against(repo, base)

    assert "changed on feature" in diff
    assert "-original a" in diff


def test_diff_against_empty_when_no_changes(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "feature")

    assert diff_against(repo, base) == ""


def test_diff_against_ignores_unrelated_changes_on_base_after_fork(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "feature")
    (repo / "a.md").write_text("changed on feature\n")
    stage_and_commit(repo, ["a.md"], "feature commit")

    checkout_or_create_branch(repo, base)
    (repo / "unrelated.md").write_text("added on base after fork\n")
    stage_and_commit(repo, ["unrelated.md"], "unrelated base commit")
    checkout_or_create_branch(repo, "feature")

    diff = diff_against(repo, base)

    assert "changed on feature" in diff
    assert "unrelated" not in diff


def test_diff_against_raises_on_unknown_branch(tmp_path: Path):
    repo = _git_repo(tmp_path)

    with pytest.raises(RepoError):
        diff_against(repo, "does-not-exist")


def test_push_branch_first_push_sets_upstream(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    local = _clone(remote, tmp_path, "local")
    (local / "a.md").write_text("local a\n")
    _git(local, "add", "a.md")
    _git(local, "commit", "-q", "-m", "local commit")
    branch = current_branch(local)

    result = push_branch(local, "origin", branch)

    assert result == "pushed"
    assert _git(local, "rev-parse", f"origin/{branch}").strip() == _git(local, "rev-parse", "HEAD").strip()


def test_push_branch_reports_up_to_date_when_nothing_new(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    local = _clone(remote, tmp_path, "local")
    (local / "a.md").write_text("local a\n")
    _git(local, "add", "a.md")
    _git(local, "commit", "-q", "-m", "local commit")
    branch = current_branch(local)
    push_branch(local, "origin", branch)

    result = push_branch(local, "origin", branch)

    assert result == "up-to-date"


def test_push_branch_rejects_diverged_history_without_modifying_remote(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    seed = _clone(remote, tmp_path, "seed")
    (seed / "a.md").write_text("seed a\n")
    _git(seed, "add", "a.md")
    _git(seed, "commit", "-q", "-m", "seed commit")
    branch = current_branch(seed)
    push_branch(seed, "origin", branch)
    remote_tip_before = _git(seed, "rev-parse", f"origin/{branch}").strip()

    other = _clone(remote, tmp_path, "other")
    (other / "b.md").write_text("other b\n")
    _git(other, "add", "b.md")
    _git(other, "commit", "-q", "-m", "other's own commit")
    push_branch(other, "origin", branch)

    (seed / "c.md").write_text("seed's own diverging commit\n")
    _git(seed, "add", "c.md")
    _git(seed, "commit", "-q", "-m", "seed's diverging commit")

    with pytest.raises(RepoError):
        push_branch(seed, "origin", branch)

    bare_tip = subprocess.run(
        ["git", "rev-parse", f"refs/heads/{branch}"], cwd=remote, capture_output=True, text=True
    ).stdout.strip()
    assert bare_tip != remote_tip_before  # other's push landed
    assert bare_tip == _git(other, "rev-parse", "HEAD").strip()  # seed's rejected push didn't overwrite it


def test_build_pr_body_matches_build_commit_message():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing"}]

    assert build_pr_body(findings, spec_file="docs/SPEC.md") == build_commit_message(
        findings, spec_file="docs/SPEC.md"
    )


def test_build_pr_body_includes_findings_and_trailer():
    findings = [{"id": "f1", "severity": "HIGH", "summary": "fixed the thing"}]

    body = build_pr_body(findings, spec_file="docs/SPEC.md", summary_override="custom PR title")

    assert body.splitlines()[0] == "custom PR title"
    assert "- f1 [HIGH] fixed the thing" in body
    assert "Spec-File: docs/SPEC.md" in body


def test_require_branch_matches_remote_passes_when_equal(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    local = _clone(remote, tmp_path, "local")
    (local / "a.md").write_text("local a\n")
    _git(local, "add", "a.md")
    _git(local, "commit", "-q", "-m", "local commit")
    branch = current_branch(local)
    push_branch(local, "origin", branch)
    fetch_branch(local, "origin", branch)

    require_branch_matches_remote(local, "origin", branch)


def test_require_branch_matches_remote_raises_when_local_ahead(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    local = _clone(remote, tmp_path, "local")
    (local / "a.md").write_text("local a\n")
    _git(local, "add", "a.md")
    _git(local, "commit", "-q", "-m", "local commit")
    branch = current_branch(local)
    push_branch(local, "origin", branch)

    (local / "b.md").write_text("unpushed\n")
    _git(local, "add", "b.md")
    _git(local, "commit", "-q", "-m", "unpushed commit")

    with pytest.raises(RepoError):
        require_branch_matches_remote(local, "origin", branch)


def test_require_branch_matches_remote_raises_when_local_behind(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    seed = _clone(remote, tmp_path, "seed")
    (seed / "a.md").write_text("seed a\n")
    _git(seed, "add", "a.md")
    _git(seed, "commit", "-q", "-m", "seed commit")
    branch = current_branch(seed)
    push_branch(seed, "origin", branch)

    local = _clone(remote, tmp_path, "local")

    (seed / "b.md").write_text("more\n")
    _git(seed, "add", "b.md")
    _git(seed, "commit", "-q", "-m", "seed's second commit")
    push_branch(seed, "origin", branch)

    fetch_branch(local, "origin", branch)

    with pytest.raises(RepoError):
        require_branch_matches_remote(local, "origin", branch)


def test_rebase_onto_succeeds_with_linear_result(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "dev")
    (repo / "b.md").write_text("dev change\n")
    stage_and_commit(repo, ["b.md"], "dev commit")

    checkout_or_create_branch(repo, base)
    (repo / "c.md").write_text("main change\n")
    stage_and_commit(repo, ["c.md"], "main commit")

    result = rebase_onto(repo, "dev", base)

    assert result == "rebased"
    assert current_branch(repo) == "dev"
    log = _git(repo, "log", "--oneline")
    assert "main commit" in log
    assert "dev commit" in log
    assert log.index("dev commit") < log.index("main commit")


def test_rebase_onto_dirty_tree_guard(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "dev")
    (repo / "a.md").write_text("dirty\n")

    with pytest.raises(RepoError):
        rebase_onto(repo, "dev", base)


def test_rebase_onto_conflict_aborts_cleanly(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = current_branch(repo)
    checkout_or_create_branch(repo, "dev")
    (repo / "a.md").write_text("dev's conflicting change\n")
    stage_and_commit(repo, ["a.md"], "dev commit")
    dev_tip_before = _git(repo, "rev-parse", "dev").strip()

    checkout_or_create_branch(repo, base)
    (repo / "a.md").write_text("main's conflicting change\n")
    stage_and_commit(repo, ["a.md"], "main commit")

    with pytest.raises(RepoError, match="a.md"):
        rebase_onto(repo, "dev", base)

    assert not (repo / ".git" / "rebase-merge").exists()
    assert not (repo / ".git" / "rebase-apply").exists()
    assert _git(repo, "rev-parse", "dev").strip() == dev_tip_before
    assert not is_dirty(repo)


def test_push_force_with_lease_succeeds_on_fresh_clone(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    local = _clone(remote, tmp_path, "local")
    (local / "a.md").write_text("local a\n")
    _git(local, "add", "a.md")
    _git(local, "commit", "-q", "-m", "local commit")
    branch = current_branch(local)
    push_branch(local, "origin", branch)

    _git(local, "commit", "--amend", "-q", "-m", "amended commit")

    result = push_force_with_lease(local, "origin", branch)

    assert result == "pushed"


def test_push_force_with_lease_rejected_when_remote_moved_since_last_fetch(tmp_path: Path):
    remote = _bare_remote(tmp_path)
    seed = _clone(remote, tmp_path, "seed")
    (seed / "a.md").write_text("seed a\n")
    _git(seed, "add", "a.md")
    _git(seed, "commit", "-q", "-m", "seed commit")
    branch = current_branch(seed)
    push_branch(seed, "origin", branch)

    other = _clone(remote, tmp_path, "other")
    (other / "b.md").write_text("other b\n")
    _git(other, "add", "b.md")
    _git(other, "commit", "-q", "-m", "other commit")
    push_branch(other, "origin", branch)

    _git(seed, "commit", "--amend", "-q", "-m", "seed amended")

    with pytest.raises(RepoError):
        push_force_with_lease(seed, "origin", branch)


def test_repo_commit_with_findings_and_also_files_real_repo(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "a.md").write_text("changed a\n")
    (repo / "CHANGELOG.md").write_text("changed changelog\n")
    _git(repo, "add", "CHANGELOG.md")  # both staged and unstaged should still show up as dirty

    dirty = dirty_files(repo)
    files = select_files_to_commit({"a.md"}, dirty, also_files={"CHANGELOG.md"})
    message = build_commit_message(
        [{"id": "f1", "severity": "HIGH", "summary": "fixed a", "file": "a.md"}],
        also_files=["CHANGELOG.md"],
    )

    sha = stage_and_commit(repo, files, message)

    committed = _git(repo, "show", "--name-only", "--format=", sha).split()
    assert sorted(committed) == ["CHANGELOG.md", "a.md"]
    commit_message = _git(repo, "show", "-s", "--format=%B", sha)
    assert "- f1 [HIGH] fixed a" in commit_message
    assert "Also committed: CHANGELOG.md" in commit_message


def test_repo_commit_also_files_not_dirty_leaves_repo_unchanged(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "a.md").write_text("changed a\n")
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    dirty = dirty_files(repo)
    with pytest.raises(RepoError):
        select_files_to_commit({"a.md"}, dirty, also_files={"CHANGELOG.md"})  # CHANGELOG.md isn't dirty

    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert is_dirty(repo)  # a.md's change is still sitting there, untouched


def test_push_branch_missing_local_branch_raises_naming_current_branch(tmp_path: Path):
    repo = _git_repo(tmp_path)
    missing = "relay/does-not-exist"
    current = current_branch(repo)

    with pytest.raises(RepoError) as exc:
        push_branch(repo, "origin", missing)

    message = str(exc.value)
    assert missing in message
    assert current in message


def test_create_pull_request_missing_local_branch_raises_before_any_gh_call(tmp_path: Path):
    repo = _git_repo(tmp_path)
    missing = "relay/does-not-exist"
    current = current_branch(repo)

    with pytest.raises(RepoError) as exc:
        create_pull_request(repo, missing, "title", "body")

    message = str(exc.value)
    assert missing in message
    assert current in message
