"""Git (and, for pull requests, `gh`) plumbing for a run: an isolated
branch to work on, a commit scoped to exactly the files a run's fixed
findings actually touched, and publishing that work -- push, PR
creation, and post-release branch maintenance. Wraps `git`/`gh` via
subprocess directly -- no third-party git library; nothing else in this
codebase needs one either. Fails loudly via RepoError instead of
guessing at branch/merge resolution, silently switching branches over
uncommitted work, falling back to `git add -A`/`-u`, or leaving a repo
mid-rebase. Merging a pull request is deliberately never wrapped here --
see CONTRACT.md's "Repository management" section for why that line is
drawn where it is.
"""
import subprocess
from pathlib import Path


class RepoError(Exception):
    pass


def _run(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _run_gh(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=repo_root, capture_output=True, text=True)


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


def diff_against(repo_root: Path, base_branch: str) -> str:
    """`git diff <base_branch>...HEAD` -- the changes introduced by HEAD
    since diverging from base_branch (merge-base diff), not a raw
    two-way diff, which would also pick up unrelated changes made on
    base_branch after the fork point. Raises RepoError if base_branch
    doesn't exist or the diff otherwise fails."""
    result = _run(repo_root, ["diff", f"{base_branch}...HEAD"])
    if result.returncode != 0:
        raise RepoError(f"git diff {base_branch}...HEAD failed in {repo_root}: {result.stderr.strip()}")
    return result.stdout


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


def fetch_branch(repo_root: Path, remote: str, branch: str) -> None:
    """`git fetch <remote> <branch>` -- updates the remote-tracking ref
    `<remote>/<branch>` (e.g. origin/dev) without touching any local
    branch. Raises RepoError verbatim on any failure (network, unknown
    remote, unknown branch on the remote)."""
    result = _run(repo_root, ["fetch", remote, branch])
    if result.returncode != 0:
        raise RepoError(f"git fetch {remote} {branch} failed in {repo_root}: {result.stderr.strip()}")


def _require_branch_exists(repo_root: Path, branch: str, command: str) -> None:
    """Raise RepoError when *branch* is not present locally, naming the
    current branch and suggesting `--branch <current>` or
    `relay repo setup`."""
    if not branch_exists(repo_root, branch):
        current = current_branch(repo_root)
        raise RepoError(
            f"{command}: branch {branch!r} does not exist locally in {repo_root}; "
            f"current branch is {current!r}. Pass --branch {current} or run "
            "`relay repo setup` to create the expected branch."
        )


def push_branch(repo_root: Path, remote: str, branch: str) -> str:
    """`git push -u <remote> <branch>`. Never force. Returns "pushed" or
    "up-to-date" (nothing new to push -- not an error). Raises RepoError
    when the local branch is missing, or verbatim on any git failure,
    including a non-fast-forward rejection (diverged remote history) --
    relay never force-pushes a run's own branch and never guesses how to
    reconcile it; that's the driving agent's call."""
    _require_branch_exists(repo_root, branch, "relay repo push")
    before = _run(repo_root, ["rev-parse", f"{remote}/{branch}"])
    result = _run(repo_root, ["push", "-u", remote, branch])
    if result.returncode != 0:
        raise RepoError(f"git push {remote} {branch} failed in {repo_root}: {result.stderr.strip()}")
    after = _run(repo_root, ["rev-parse", f"{remote}/{branch}"])
    if before.returncode == 0 and before.stdout == after.stdout:
        return "up-to-date"
    return "pushed"


def checkout_or_create_branch(repo_root: Path, name: str, base_branch: str | None = None) -> str:
    """Idempotently ensures `name` is checked out in repo_root. Returns
    one of "up-to-date" (already on name), "checked-out" (name existed,
    switched to it), "created" (name didn't exist yet, created from
    base_branch -- or current HEAD if base_branch is None -- and
    switched). Raises RepoError if the working tree is dirty and
    repo_root is not already on `name` -- never silently carries
    uncommitted work across an unrelated branch switch. (Being dirty
    while already on `name` is fine -- that's the normal in-progress
    state during Fix/Validate.)

    base_branch only affects the create path: if `name` already exists,
    base_branch is ignored entirely -- idempotent re-invocation never
    resets or moves an existing branch to a different base. Callers
    wanting to branch from a remote ref (e.g. a git-flow `dev`) should
    fetch_branch it first and pass the fetched "<remote>/<branch>" ref
    here; this function itself never touches the network."""
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

    checkout_args = ["checkout", "-b", name] + ([base_branch] if base_branch else [])
    result = _run(repo_root, checkout_args)
    if result.returncode != 0:
        raise RepoError(f"git checkout -b {name!r} failed: {result.stderr.strip()}")
    return "created"


def select_files_to_commit(
    fixed_finding_files: set[str], dirty: set[str], also_files: set[str] | None = None
) -> set[str]:
    """The set `repo commit` actually stages: this run's fixed findings'
    files that git still reports as dirty, unioned with also_files (e.g.
    release bookkeeping -- CHANGELOG.md, a version bump -- not tied to
    any finding). Pure set logic, no subprocess -- deliberately does not
    know or care how a finding file became fixed (relay fix run, or by
    hand per Validate's guidance); only finding.status and live git
    state matter.

    also_files is unioned in, not substituted -- the finding-file
    intersection is untouched, and also_files=None (the default)
    produces byte-identical behavior to the pre-also_files signature.
    Each also_files entry must still be dirty: this is the safety net
    that keeps also_files from being a way to stage arbitrary
    non-dirty files. Raises RepoError naming every non-dirty also_files
    entry -- never silently drops one. Callers must normalize
    also_files (and dirty) to the same path convention before calling;
    this function does no path normalization itself."""
    selected = fixed_finding_files & dirty
    if also_files:
        not_dirty = also_files - dirty
        if not_dirty:
            raise RepoError(
                "--also-commit files not dirty (nothing to commit for them): "
                f"{', '.join(sorted(not_dirty))}"
            )
        selected |= also_files
    return selected


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
    also_files: list[str] | None = None,
) -> str:
    """Builds the structured message for repo commit: a headline (or
    summary_override, if given), one `id [severity] summary` line per
    finding in fixed_findings, an `Also committed: <paths>` line for
    also_files not already covered by a finding, and a trailing
    `Spec-File: <path>` line if spec_file is set. summary_override
    replaces only the headline; every other section is still generated.

    also_files must already be normalized to the same path convention
    as fixed_findings' `file` values -- this function does string
    comparison for dedup, not path resolution. A file that's both a
    fixed finding's file and named in also_files appears only in the
    per-finding list, never doubled into "Also committed"."""
    headline = summary_override or f"relay: {len(fixed_findings)} finding(s) fixed"
    lines = [headline, ""]
    lines += [f"- {f['id']} [{f['severity']}] {f['summary']}" for f in fixed_findings]
    if also_files:
        finding_files = {f["file"] for f in fixed_findings}
        also_only = [p for p in also_files if p not in finding_files]
        if also_only:
            lines += ["", f"Also committed: {', '.join(also_only)}"]
    if spec_file:
        lines += ["", f"Spec-File: {spec_file}"]
    return "\n".join(lines)


def build_pr_body(
    fixed_findings: list[dict],
    spec_file: str | None = None,
    summary_override: str | None = None,
) -> str:
    """The PR body: delegates directly to build_commit_message, whose
    output shape (headline, one `id [severity] summary` line per
    finding, Spec-File trailer) is identical to what a PR body needs
    today. A distinct name, not a bare alias -- the two surfaces'
    formatting needs are only coincidentally identical and free to
    diverge later without coupling commit-message formatting to PR
    formatting."""
    return build_commit_message(fixed_findings, spec_file=spec_file, summary_override=summary_override)


def create_pull_request(repo_root: Path, head: str, title: str, body: str, base: str | None = None) -> str:
    """`gh pr create --head <head> --title <title> --body <body>` from
    repo_root, adding `--base <base>` only if given -- omitted, gh falls
    back to the GitHub repo's own configured default branch, rather than
    relay assuming every target repo follows one particular branching
    model. Requires `gh` installed and authenticated, and requires
    `head` already pushed -- this function does not push as a side
    effect (see push_branch). A missing local `head` branch is caught by
    _require_branch_exists before gh is invoked, producing a clear,
    actionable RepoError; gh's own error (auth failure, "no commits
    between X and Y") surfaces verbatim as RepoError. Returns the created
    PR's URL."""
    _require_branch_exists(repo_root, head, command="relay repo pr create")
    args = ["pr", "create", "--head", head, "--title", title, "--body", body]
    if base:
        args += ["--base", base]
    result = _run_gh(repo_root, args)
    if result.returncode != 0:
        raise RepoError(f"gh pr create failed in {repo_root}: {result.stderr.strip()}")
    return result.stdout.strip()


def require_branch_matches_remote(repo_root: Path, remote: str, branch: str) -> None:
    """Raises RepoError unless local `branch`'s tip SHA is identical to
    `<remote>/<branch>`'s -- neither ahead (unpushed local commits) nor
    behind (remote has commits not yet fetched-and-merged here). No-op
    when they match. Callers should fetch_branch the remote ref
    immediately before calling this -- it only compares whatever refs
    already exist locally, it doesn't fetch anything itself.

    Exists because --force-with-lease alone only protects against the
    remote moving *during* an operation, not against the local branch
    already being stale or diverged *before* it started -- rebasing and
    force-with-lease-pushing from a stale local branch risks silently
    discarding remote-only commits, with no conflict ever surfacing to
    flag it."""
    local = _run(repo_root, ["rev-parse", branch])
    if local.returncode != 0:
        raise RepoError(f"couldn't resolve local branch {branch!r} in {repo_root}: {local.stderr.strip()}")
    remote_ref = _run(repo_root, ["rev-parse", f"{remote}/{branch}"])
    if remote_ref.returncode != 0:
        raise RepoError(f"couldn't resolve {remote}/{branch} in {repo_root}: {remote_ref.stderr.strip()}")
    if local.stdout != remote_ref.stdout:
        raise RepoError(
            f"{branch!r} ({local.stdout.strip()[:12]}) does not match {remote}/{branch} "
            f"({remote_ref.stdout.strip()[:12]}) -- pull or push to reconcile before syncing"
        )


def rebase_onto(repo_root: Path, branch: str, onto_ref: str) -> str:
    """Checks out `branch`, then `git rebase <onto_ref>`. Raises
    RepoError up front if the working tree is dirty, before touching
    anything -- same discipline as checkout_or_create_branch, never
    rebase over uncommitted work. On success returns "rebased".

    On conflict: captures the conflicting files (`git diff --name-only
    --diff-filter=U`) before running `git rebase --abort`, then raises
    RepoError naming them -- relay never leaves the repo mid-rebase for
    manual resolution through relay itself. This is the one place in
    this module where leaving an ambiguous state would be most
    dangerous (a shared branch, possibly no human immediately present
    to notice), so it gets the strictest version of the "fail loudly,
    never leave an ambiguous state" discipline every other function
    here already uses. If rebase fails for a reason other than a
    conflict (e.g. onto_ref doesn't exist), there's nothing to abort;
    git's own error surfaces directly."""
    if is_dirty(repo_root):
        raise RepoError(f"{repo_root} has uncommitted changes -- commit or stash before rebasing")

    checkout = _run(repo_root, ["checkout", branch])
    if checkout.returncode != 0:
        raise RepoError(f"git checkout {branch!r} failed: {checkout.stderr.strip()}")

    result = _run(repo_root, ["rebase", onto_ref])
    if result.returncode == 0:
        return "rebased"

    conflicts = _run(repo_root, ["diff", "--name-only", "--diff-filter=U"])
    conflicting_files = conflicts.stdout.split()
    if conflicting_files:
        _run(repo_root, ["rebase", "--abort"])
        raise RepoError(
            f"rebase of {branch!r} onto {onto_ref!r} conflicted on: {', '.join(conflicting_files)} "
            f"-- aborted, {branch!r} is unchanged; resolve the conflict by hand and retry"
        )
    raise RepoError(f"git rebase {onto_ref!r} failed in {repo_root}: {result.stderr.strip()}")


def push_force_with_lease(repo_root: Path, remote: str, branch: str) -> str:
    """`git push --force-with-lease <remote> <branch>` -- the only
    force-push in this module, and only ever --force-with-lease, never
    bare --force: rejected instead of overwriting if `<remote>/<branch>`
    moved since this repo's last recorded knowledge of it. Returns
    "pushed". Raises RepoError on any failure, including the lease
    rejection."""
    result = _run(repo_root, ["push", "--force-with-lease", remote, branch])
    if result.returncode != 0:
        raise RepoError(
            f"git push --force-with-lease {remote} {branch} failed in {repo_root}: {result.stderr.strip()}"
        )
    return "pushed"
