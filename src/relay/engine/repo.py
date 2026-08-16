"""Local git plumbing for a run: an isolated branch to work on, and a
commit scoped to exactly the files a run's fixed findings actually
touched. Wraps `git` via subprocess directly -- no third-party git
library; nothing else in this codebase needs one either. Fails loudly via
RepoError instead of guessing at branch/merge resolution, silently
switching branches over uncommitted work, or falling back to `git add
-A`/`-u`. Deliberately local-only: never pushes, never touches a remote
or a pull request -- that's a later "glue" design's job, not this one's.
"""
import subprocess
from pathlib import Path


class RepoError(Exception):
    pass


def _run(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def current_branch(repo_root: Path) -> str:
    result = _run(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode != 0:
        raise RepoError(f"couldn't determine current branch in {repo_root}: {result.stderr.strip()}")
    return result.stdout.strip()


def branch_exists(repo_root: Path, name: str) -> bool:
    result = _run(repo_root, ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])
    return result.returncode == 0


def is_dirty(repo_root: Path) -> bool:
    result = _run(repo_root, ["status", "--porcelain"])
    if result.returncode != 0:
        raise RepoError(f"git status failed in {repo_root}: {result.stderr.strip()}")
    return bool(result.stdout.strip())


def dirty_files(repo_root: Path) -> set[str]:
    """Paths git currently reports as dirty (staged, unstaged, or
    untracked) in repo_root, relative to repo_root -- same convention
    finding['file'] uses. Parsed from `git status --porcelain=v1 -z` so
    paths with spaces/unusual characters never need ad-hoc unquoting."""
    result = _run(repo_root, ["status", "--porcelain=v1", "-z"])
    if result.returncode != 0:
        raise RepoError(f"git status failed in {repo_root}: {result.stderr.strip()}")

    fields = result.stdout.split("\0")
    files = set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        if not entry:
            i += 1
            continue
        status, path = entry[:2], entry[3:]
        files.add(path)
        if status[0] in ("R", "C"):  # rename/copy: an extra orig-path field follows
            i += 1
        i += 1
    return files


def checkout_or_create_branch(repo_root: Path, name: str) -> str:
    """Idempotently ensures `name` is checked out in repo_root. Returns
    one of "up-to-date" (already on name), "checked-out" (name existed,
    switched to it), "created" (name didn't exist yet, created from
    current HEAD and switched). Raises RepoError if the working tree is
    dirty and repo_root is not already on `name` -- never silently
    carries uncommitted work across an unrelated branch switch. (Being
    dirty while already on `name` is fine -- that's the normal
    in-progress state during Fix/Validate.)"""
    current = current_branch(repo_root)
    if current == name:
        return "up-to-date"

    if is_dirty(repo_root):
        raise RepoError(
            f"{repo_root} has uncommitted changes on branch {current!r} -- "
            f"commit or stash them before switching to {name!r}"
        )

    if branch_exists(repo_root, name):
        result = _run(repo_root, ["checkout", name])
        if result.returncode != 0:
            raise RepoError(f"git checkout {name!r} failed: {result.stderr.strip()}")
        return "checked-out"

    result = _run(repo_root, ["checkout", "-b", name])
    if result.returncode != 0:
        raise RepoError(f"git checkout -b {name!r} failed: {result.stderr.strip()}")
    return "created"


def select_files_to_commit(fixed_finding_files: set[str], dirty: set[str]) -> set[str]:
    """The intersection `repo commit` actually stages: this run's fixed
    findings' files that git still reports as dirty. Pure set logic, no
    subprocess -- deliberately does not know or care how a file became
    fixed (relay fix run, or by hand per Validate's guidance); only
    finding.status and live git state matter."""
    return fixed_finding_files & dirty


def stage_and_commit(repo_root: Path, files: set[str] | list[str], message: str) -> str:
    """Stages exactly `files` -- one `git add` call naming every path
    explicitly, never `-A`/`-u` -- and commits with `message`. Returns
    the new commit's full SHA. Raises RepoError if `files` is empty, or
    if `git add`/`git commit` fails for any reason; nothing outside
    `files` is ever staged."""
    files = list(files)
    if not files:
        raise RepoError("no files to commit")

    result = _run(repo_root, ["add", "--", *files])
    if result.returncode != 0:
        raise RepoError(f"git add failed: {result.stderr.strip()}")

    result = _run(repo_root, ["commit", "-m", message])
    if result.returncode != 0:
        raise RepoError(f"git commit failed: {result.stderr.strip()}")

    result = _run(repo_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        raise RepoError(f"git rev-parse HEAD failed: {result.stderr.strip()}")
    return result.stdout.strip()


def build_commit_message(
    fixed_findings: list[dict],
    spec_file: str | None = None,
    summary_override: str | None = None,
) -> str:
    """Builds the structured message for repo commit: a headline (or
    summary_override, if given), one `id [severity] summary` line per
    finding in fixed_findings, and a trailing `Spec-File: <path>` line
    if spec_file is set. summary_override replaces only the headline;
    the per-finding lines and Spec-File trailer are still generated."""
    headline = summary_override or f"relay: {len(fixed_findings)} finding(s) fixed"
    lines = [headline, ""]
    lines += [f"- {f['id']} [{f['severity']}] {f['summary']}" for f in fixed_findings]
    if spec_file:
        lines += ["", f"Spec-File: {spec_file}"]
    return "\n".join(lines)
