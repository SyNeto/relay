"""relay CLI — see CONTRACT.md for the full protocol this implements."""
import argparse
import difflib
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from relay import __version__
from relay.engine.apply_fix import ApplyFixError, apply_fix
from relay.engine.build_prompt import build
from relay.engine.build_review_prompt import build as build_review
from relay.engine.build_spec_prompt import build as build_spec
from relay.engine.extract_fix import FixExtractionError, extract
from relay.engine.extract_review import ReviewExtractionError, extract as extract_review_run
from relay.engine.extract_spec import SpecExtractionError, extract as extract_spec_draft
from relay.engine.repo import (
    RepoError,
    build_commit_message,
    checkout_or_create_branch,
    dirty_files,
    select_files_to_commit,
    stage_and_commit,
)
from relay.engine.state import DEFAULT_GATE_SEVERITIES, RunState, default_state_dir
from relay.engine.verify_excerpts import verify
from relay.providers import openai_compat_client, registry


def _state_dir_arg(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="override the run-state directory (default: ./.relay/runs, or $RELAY_HOME/runs)",
    )


def _load_state(run_id: str, args) -> RunState:
    return RunState(run_id, state_dir=args.state_dir or default_state_dir())


def cmd_run_start(args):
    gate = tuple(args.gate_severities.split(",")) if args.gate_severities else DEFAULT_GATE_SEVERITIES
    state = RunState(
        args.run_id,
        max_iterations=args.max_iterations,
        gate_severities=gate,
        spec_file=args.spec_file,
        state_dir=args.state_dir or default_state_dir(),
    )
    state.start_iteration()
    print(state.render())


def cmd_run_status(args):
    state = _load_state(args.run_id, args)
    print(state.render())
    print(f"\nshould_stop(): {state.should_stop()}")


def cmd_finding_record(args):
    if args.from_json:
        raw = Path(args.from_json).read_text()
    else:
        raw = sys.stdin.read()
    payload = json.loads(raw)
    findings = payload if isinstance(payload, list) else [payload]

    state = _load_state(args.run_id, args)
    for f in [dict(x) for x in findings]:
        finding_id = f.pop("id")
        severity = f.pop("severity")
        summary = f.pop("summary")
        status = f.pop("status", "open")
        state.record_finding(finding_id, severity, summary, status=status, **f)
    print(state.render())


def cmd_finding_verify(args):
    state = _load_state(args.run_id, args)
    results = verify(state, args.target_repo_root)
    ok = bad = 0
    for r in results:
        if r.ok:
            ok += 1
            print(f"OK    {r.finding_id}")
        else:
            bad += 1
            print(f"BAD   {r.finding_id}  (found {r.count}x in {r.file}, expected 1)")
    print(f"\n{ok} ok, {bad} bad")
    if bad:
        sys.exit(1)


def cmd_finding_mark(args):
    state = _load_state(args.run_id, args)
    if state.get_finding(args.finding_id) is None:
        print(f"no finding {args.finding_id!r} in run {args.run_id!r}")
        sys.exit(1)
    state.set_phase("validate")
    state.update_finding_status(args.finding_id, args.status)
    print(state.render())


def cmd_fix_run(args):
    try:
        provider_config = registry.get_provider(args.provider)
    except registry.UnknownProviderError as e:
        print(str(e))
        sys.exit(2)

    state = _load_state(args.run_id, args)
    finding = state.get_finding(args.finding_id)
    if finding is None:
        print(f"no finding {args.finding_id!r} in run {args.run_id!r}")
        sys.exit(1)

    state.set_phase("fix")
    prompt = build(finding)
    print(f"[{args.finding_id}] calling {provider_config.name} (timeout={args.timeout}s)...")
    try:
        raw = openai_compat_client.chat(
            prompt["user"],
            run_id=args.run_id,
            config=provider_config,
            system=prompt["system"],
            timeout=args.timeout,
        )
    except openai_compat_client.ProviderTimeoutError as e:
        print(f"[{args.finding_id}] MODEL CALL FAILED: {e}")
        sys.exit(1)

    try:
        fixed_text = extract(raw)
    except FixExtractionError as e:
        print(f"[{args.finding_id}] EXTRACTION FAILED: {e}")
        sys.exit(1)

    file_path = args.target_repo_root / finding["file"]
    try:
        apply_fix(file_path, finding["target_excerpt"], fixed_text)
    except ApplyFixError as e:
        print(f"[{args.finding_id}] APPLY FAILED: {e}")
        sys.exit(1)

    diff = difflib.unified_diff(
        finding["target_excerpt"].splitlines(keepends=True),
        fixed_text.splitlines(keepends=True),
        fromfile=f"{finding['file']} (before)",
        tofile=f"{finding['file']} (after)",
    )
    print(f"[{args.finding_id}] applied to {file_path}. Diff:\n")
    print("".join(diff))
    print("Not marked or committed — review the diff, then run 'relay finding mark'.")


def cmd_spec_draft(args):
    """Discover/Generate: draft a whole spec document from a change request
    + gathered context. Stateless — no run_id, no RunState. See CONTRACT.md's
    "Discover & Generate (spec authorship)" section.
    """
    try:
        provider_config = registry.get_provider(args.provider)
    except registry.UnknownProviderError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    output_path = Path(args.output) if args.output else None
    if output_path and output_path.exists() and not args.force:
        print(f"{output_path} already exists — refusing to overwrite. Use --force.", file=sys.stderr)
        sys.exit(1)

    context = [
        {"source": path, "content": Path(path).read_text()} for path in args.context_file
    ]
    prompt = build_spec({"change_request": args.request, "context": context})

    print(f"calling {provider_config.name} (timeout={args.timeout}s)...", file=sys.stderr)
    try:
        raw = openai_compat_client.chat(
            prompt["user"],
            run_id="adhoc-spec",
            config=provider_config,
            system=prompt["system"],
            timeout=args.timeout,
        )
    except openai_compat_client.ProviderTimeoutError as e:
        print(f"MODEL CALL FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        draft = extract_spec_draft(raw)
    except SpecExtractionError as e:
        print(f"EXTRACTION FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if output_path:
        output_path.write_text(draft)
        print(f"wrote draft -> {output_path}", file=sys.stderr)
    else:
        print(draft)
    print("Not committed — review before saving/committing.", file=sys.stderr)


def cmd_review_run(args):
    """Review: produce an independent critique of an architectural decision
    from stated reasoning + gathered context. Stateless — no run_id, no
    RunState. Advisory only — never a decision or a gate. See CONTRACT.md's
    "Review (independent critique)" section.
    """
    try:
        provider_config = registry.get_provider(args.provider)
    except registry.UnknownProviderError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    output_path = Path(args.output) if args.output else None
    if output_path and output_path.exists() and not args.force:
        print(f"{output_path} already exists — refusing to overwrite. Use --force.", file=sys.stderr)
        sys.exit(1)

    context = [
        {"source": path, "content": Path(path).read_text()} for path in args.context_file
    ]
    prompt = build_review({"decision": args.decision, "context": context})

    print(f"calling {provider_config.name} (timeout={args.timeout}s)...", file=sys.stderr)
    try:
        raw = openai_compat_client.chat(
            prompt["user"],
            run_id="adhoc-review",
            config=provider_config,
            system=prompt["system"],
            timeout=args.timeout,
        )
    except openai_compat_client.ProviderTimeoutError as e:
        print(f"MODEL CALL FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        review = extract_review_run(raw)
    except ReviewExtractionError as e:
        print(f"EXTRACTION FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if output_path:
        output_path.write_text(review)
        print(f"wrote review -> {output_path}", file=sys.stderr)
    else:
        print(review)
    print(
        "Advisory input only — not a decision. Weigh this alongside your own judgment before proceeding.",
        file=sys.stderr,
    )


def cmd_repo_setup(args):
    """Idempotently ensure a run's dedicated branch exists and is checked
    out in target_repo_root, before Find/Fix touch any files. See
    CONTRACT.md's "Repository management" section. Local-only — no push,
    no remote, no PR.
    """
    _load_state(args.run_id, args)  # just validates run_id exists
    branch = args.branch or f"relay/{args.run_id}"
    try:
        result = checkout_or_create_branch(args.target_repo_root, branch)
    except RepoError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(f"{branch}: {result}")


def cmd_repo_commit(args):
    """Stage and commit exactly this run's fixed-finding files that are
    still dirty. Local-only — no push, no remote, no PR; see
    CONTRACT.md's "Repository management" section for the file-selection
    algorithm (intersection of fixed-finding files and live git dirty
    state, deliberately not the finding's iteration field).
    """
    state = _load_state(args.run_id, args)
    fixed = [f for f in state.findings if f["status"] == "fixed"]
    dirty = dirty_files(args.target_repo_root)
    files = select_files_to_commit({f["file"] for f in fixed}, dirty)
    if not files:
        print("nothing to commit: no fixed finding's file is currently dirty", file=sys.stderr)
        sys.exit(1)

    committed_findings = [f for f in fixed if f["file"] in files]
    message = build_commit_message(committed_findings, spec_file=state.spec_file, summary_override=args.message)
    try:
        sha = stage_and_commit(args.target_repo_root, files, message)
    except RepoError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    state.set_phase("commit")
    print(f"committed {sha[:12]} — {len(files)} file(s): {', '.join(sorted(files))}")

    leftover = dirty - files
    if leftover:
        print(f"note: still dirty, not part of this run's fixed findings: {', '.join(sorted(leftover))}", file=sys.stderr)


def cmd_quota_status(args):
    from relay.providers.rate_limiter import RpmLimiter

    configs = registry.load_provider_configs()
    if args.provider:
        if args.provider not in configs:
            print(f"unknown provider {args.provider!r} — known providers: {', '.join(sorted(configs))}")
            sys.exit(2)
        names = [args.provider]
    else:
        names = sorted(configs)

    for i, name in enumerate(names):
        if i:
            print()
        print(RpmLimiter("cli", provider=name).report())


_HARNESS_INSTALL = {
    "claude-code": ("skills/claude-code/SKILL.md", ".claude/skills/relay", "SKILL.md"),
    "opencode": ("skills/opencode/AGENT.md", ".opencode/agents", "relay.md"),
}


def cmd_skill_install(args):
    if args.harness not in _HARNESS_INSTALL:
        print(f"unsupported harness {args.harness!r}")
        sys.exit(2)

    src_rel, target_subdir, target_filename = _HARNESS_INSTALL[args.harness]
    src = resources.files("relay") / src_rel
    target_dir = Path(args.target_dir) / target_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / target_filename

    if dest.exists() and args.force is False:
        header = dest.read_text().splitlines()[:10]
        if not any("generated by relay" in line for line in header):
            print(f"{dest} exists and doesn't look relay-generated — refusing to overwrite. Use --force.")
            sys.exit(1)

    with resources.as_file(src) as src_path:
        shutil.copyfile(src_path, dest)
    print(f"installed skill -> {dest}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relay", description="Harness-neutral find->fix->validate loop.")
    parser.add_argument("--version", action="version", version=f"relay {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="manage a run's iteration/state").add_subparsers(
        dest="run_command", required=True
    )
    p = run.add_parser("start", help="start or advance a run's iteration counter")
    p.add_argument("run_id")
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--gate-severities", help="comma-separated, e.g. CRITICAL,HIGH (default on first create)")
    p.add_argument(
        "--spec-file",
        default=None,
        help="path to the spec document that drove this run, for traceability (not validated to exist)",
    )
    _state_dir_arg(p)
    p.set_defaults(func=cmd_run_start)

    p = run.add_parser("status", help="print current run state without mutating anything")
    p.add_argument("run_id")
    _state_dir_arg(p)
    p.set_defaults(func=cmd_run_status)

    finding = sub.add_parser("finding", help="manage findings within a run").add_subparsers(
        dest="finding_command", required=True
    )
    p = finding.add_parser("record", help="record one or more findings (JSON, stdin or --from-json)")
    p.add_argument("run_id")
    p.add_argument("--from-json", help="path to a JSON finding object or array; omit to read stdin")
    _state_dir_arg(p)
    p.set_defaults(func=cmd_finding_record)

    p = finding.add_parser("verify", help="pre-flight: confirm every open finding's target_excerpt matches")
    p.add_argument("run_id")
    p.add_argument("target_repo_root", type=Path)
    _state_dir_arg(p)
    p.set_defaults(func=cmd_finding_verify)

    p = finding.add_parser("mark", help="set a finding's status after reviewing its diff")
    p.add_argument("run_id")
    p.add_argument("finding_id")
    p.add_argument("status", choices=["open", "fixed", "wontfix"])
    _state_dir_arg(p)
    p.set_defaults(func=cmd_finding_mark)

    fix = sub.add_parser("fix", help="run the fixer model").add_subparsers(dest="fix_command", required=True)
    p = fix.add_parser("run", help="fix one finding: call the model, apply, print a diff")
    p.add_argument("run_id")
    p.add_argument("finding_id")
    p.add_argument("target_repo_root", type=Path)
    p.add_argument("--provider", default="nim", help="which configured provider to fix with (default: nim)")
    p.add_argument("--timeout", type=float, default=openai_compat_client.DEFAULT_TIMEOUT)
    _state_dir_arg(p)
    p.set_defaults(func=cmd_fix_run)

    spec = sub.add_parser("spec", help="draft a whole spec document (Discover/Generate)").add_subparsers(
        dest="spec_command", required=True
    )
    p = spec.add_parser("draft", help="generate a spec document from a change request + context files")
    p.add_argument("--request", required=True, help="the change request in natural language")
    p.add_argument(
        "--context-file",
        action="append",
        required=True,
        dest="context_file",
        help="path to a context file; repeatable, at least one required",
    )
    p.add_argument("--provider", default="nim", help="which configured provider to draft with (default: nim)")
    p.add_argument("--timeout", type=float, default=openai_compat_client.DEFAULT_TIMEOUT)
    p.add_argument("--output", default=None, help="write the draft here instead of printing to stdout")
    p.add_argument("--force", action="store_true", help="overwrite --output if it already exists")
    p.set_defaults(func=cmd_spec_draft)

    review = sub.add_parser(
        "review", help="independent critique of an architectural decision (advisory, not a gate)"
    ).add_subparsers(dest="review_command", required=True)
    p = review.add_parser(
        "run", help="produce an independent review of a decision from stated reasoning + context"
    )
    p.add_argument("--decision", required=True, help="the decision under review, including its stated reasoning")
    p.add_argument(
        "--context-file",
        action="append",
        required=True,
        dest="context_file",
        help="path to a context file; repeatable, at least one required",
    )
    p.add_argument("--provider", default="nim", help="which configured provider to review with (default: nim)")
    p.add_argument("--timeout", type=float, default=openai_compat_client.DEFAULT_TIMEOUT)
    p.add_argument("--output", default=None, help="write the review here instead of printing to stdout")
    p.add_argument("--force", action="store_true", help="overwrite --output if it already exists")
    p.set_defaults(func=cmd_review_run)

    repo = sub.add_parser("repo", help="local git plumbing for a run's branch and commits").add_subparsers(
        dest="repo_command", required=True
    )
    p = repo.add_parser("setup", help="idempotently ensure a run's dedicated branch exists and is checked out")
    p.add_argument("run_id")
    p.add_argument("target_repo_root", type=Path)
    p.add_argument("--branch", default=None, help="branch name (default: relay/<run_id>)")
    _state_dir_arg(p)
    p.set_defaults(func=cmd_repo_setup)

    p = repo.add_parser("commit", help="stage and commit exactly this run's fixed-finding files, if any are dirty")
    p.add_argument("run_id")
    p.add_argument("target_repo_root", type=Path)
    p.add_argument(
        "-m", "--message", default=None, help="override the commit headline (structured body is still auto-generated)"
    )
    _state_dir_arg(p)
    p.set_defaults(func=cmd_repo_commit)

    quota = sub.add_parser("quota", help="model-provider request volume").add_subparsers(
        dest="quota_command", required=True
    )
    p = quota.add_parser("status", help="print rpm/1h/24h request counts, one block per provider")
    p.add_argument("--provider", default=None, help="show one provider only; default: all known providers")
    p.set_defaults(func=cmd_quota_status)

    skill = sub.add_parser("skill", help="harness integration bootstrap").add_subparsers(
        dest="skill_command", required=True
    )
    p = skill.add_parser("install", help="install a packaged skill/agent template into a project")
    p.add_argument("--harness", required=True, choices=["claude-code", "opencode"])
    p.add_argument("--target-dir", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_skill_install)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
