# relay

A harness-neutral find → fix → validate quality loop. `relay` is the engine (state machine + fixer
integration); it doesn't know or care which coding agent is driving it. See [`CONTRACT.md`](CONTRACT.md)
for the full protocol — roles, state model, finding schema, CLI surface — written to be reconstructible by
any agent from that document alone.

Two model providers are configured out of the box — NVIDIA NIM (`z-ai/glm-5.2`) and OpenCode Zen
(`glm-5.2`) — selectable per run via `--provider`. See CONTRACT.md's "Model connector" section
for how to add another `openai-completions`-compatible provider without touching code.

## Install

```bash
pipx install git+https://github.com/SyNeto/relay.git
```

(`pipx` gives one global per-user install independent of any target project's own stack — target repos are
not assumed to be Python projects.)

This is a private repo — teammates installing it need their own GitHub access to `SyNeto/relay` configured
(`gh auth login`, or SSH: `pipx install git+ssh://git@github.com/SyNeto/relay.git`).

For local development:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

API keys live outside the repo, never in code: NIM at `~/.secrets/.nvidia-api-key`, OpenCode Zen at
`~/.secrets/.opencode-api-key` (both chmod 600). Add a third provider (or override these paths) via
`~/.relay/providers.json` — see CONTRACT.md.

## Quickstart

```bash
relay run start my-run-1 --max-iterations 3 --gate-severities CRITICAL,HIGH --spec-file SPEC.md
relay repo setup my-run-1 /path/to/target-repo               # isolates work on branch relay/my-run-1
echo '[{"id": "f1", "severity": "HIGH", "summary": "...", "file": "docs/x.md",
        "target_excerpt": "...", "failure_scenario": "..."},
       {"id": "f2", "severity": "HIGH", "summary": "...", "file": "docs/y.md",
        "target_excerpt": "...", "failure_scenario": "..."}]' | relay finding record my-run-1
relay finding verify my-run-1 /path/to/target-repo
relay fix run my-run-1 f1 /path/to/target-repo              # --provider defaults to nim
relay fix run my-run-1 f2 /path/to/target-repo --provider opencode-go
# review the printed diffs, then:
relay finding mark my-run-1 f1 fixed
relay finding mark my-run-1 f2 fixed
relay repo commit my-run-1 /path/to/target-repo             # commits just f1/f2's files
relay run status my-run-1
relay repo push my-run-1 /path/to/target-repo                # never force
relay repo pr create my-run-1 /path/to/target-repo --base dev
relay run list                                               # every run under --state-dir, at a glance
```

(`--spec-file` and `repo setup`/`repo commit`/`repo push`/`repo pr create` are all optional — the loop works
the same without them, tracking a run's spec and handling git plumbing entirely by hand instead. Merging the
PR is deliberately not wrapped — see CONTRACT.md's "Repository management" section for why.)

For git-flow-style repos, post-release maintenance keeps `dev` rebased onto `main` (rewrites `dev`'s
history — requires an explicit, deliberately unmissable opt-in flag; not run-scoped):

```bash
relay repo sync-dev /path/to/target-repo --i-understand-this-rewrites-dev-history
```

Drafting a new spec document (Discover/Generate — stateless, no run needed):

```bash
relay spec draft \
  --request "describe the change you want a spec for" \
  --context-file some/existing/doc.md \
  --context-file some/relevant/code.py \
  --output DRAFT.md
# review DRAFT.md before saving/committing it anywhere
```

Getting an independent critique of an architectural decision (also stateless, advisory only — never a
gate; the decision stays with you):

```bash
relay review run \
  --decision "should we adopt X? (include your stated reasoning here)" \
  --context-file some/relevant/doc.md \
  --provider opencode-go \
  --output REVIEW.md
# recommended: use a different --provider than whatever produced the reasoning under review, for
# genuine independence
```

Or as a closing check once a run's fix loop reaches a clean gate — comparing the finished diff back against
the spec that motivated it, diff gathered automatically instead of hand-assembled:

```bash
relay review run \
  --decision "does this diff actually deliver on the spec?" \
  --context-file SPEC.md \
  --diff-from-branch main --target-repo-root /path/to/target-repo \
  --output CLOSING_REVIEW.md
```

## Use from a coding agent

```bash
relay skill install --harness claude-code --target-dir /path/to/your/project
relay skill install --harness opencode --target-dir /path/to/your/project
```

Installs a harness-native integration file — `<project>/.claude/skills/relay/SKILL.md` for Claude Code, or
`<project>/.opencode/agents/relay.md` (an OpenCode subagent) for OpenCode — that teaches the agent the full
lifecycle above: an optional decision review, drafting a spec, then driving the fix loop. Find, Validate,
and every decision stay agent (or human) judgment calls throughout; `relay` handles state, gating, and the
model calls identically regardless of which harness is driving it.

## Layout

- `CONTRACT.md` — the harness-neutral protocol spec (read this first)
- `src/relay/engine/` — business logic: state machine, prompt building, fix application, verification, local repo plumbing
- `src/relay/providers/` — model connector(s) + rate/quota tracking
- `src/relay/prompts/` — model prompt templates (fix, spec draft, review)
- `src/relay/skills/` — per-harness integration templates (`relay skill install` copies from here)
- `src/relay/cli.py` — the `relay` command

## Status

1.0.0-alpha. Run→spec traceability, local git plumbing (branch isolation, scoped commits), and now
publishing (push, PR creation, git-flow post-release `dev` sync) are all handled. Merging a pull request
stays deliberately manual — not deferred-for-now, but out of scope by design: see CONTRACT.md's
"Repository management" section. The one known gap at this milestone is provider fallback: relay retries
a transient failure on the configured provider (bounded backoff, ~6 minutes worst case) but does not
automatically switch to a different provider if one is persistently down — a driving agent hitting that
has to retry manually with a different `--provider`. See CONTRACT.md's "Model connector" section for the
full retry policy. See `CHANGELOG.md` for release history and current phase.
