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
from relay.engine.extract_fix import FixExtractionError, extract
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


def cmd_skill_install(args):
    if args.harness != "claude-code":
        print(f"unsupported harness {args.harness!r} — only 'claude-code' is packaged today")
        sys.exit(2)

    src = resources.files("relay") / "skills" / "claude-code" / "SKILL.md"
    target_dir = Path(args.target_dir) / ".claude" / "skills" / "relay"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / "SKILL.md"

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
    p.add_argument("--harness", required=True, choices=["claude-code"])
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
