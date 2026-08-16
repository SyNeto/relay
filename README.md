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
relay run start my-run-1 --max-iterations 3 --gate-severities CRITICAL,HIGH
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
relay run status my-run-1
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

## Use from a coding agent

```bash
relay skill install --harness claude-code --target-dir /path/to/your/project
relay skill install --harness opencode --target-dir /path/to/your/project
```

Installs a harness-native integration file — `<project>/.claude/skills/relay/SKILL.md` for Claude Code, or
`<project>/.opencode/agents/relay.md` (an OpenCode subagent) for OpenCode — that teaches the agent how to
drive the loop above. Find and Validate stay agent judgment calls either way; `relay` handles state,
gating, and the fixer-model call identically regardless of which harness is driving it.

## Layout

- `CONTRACT.md` — the harness-neutral protocol spec (read this first)
- `src/relay/engine/` — business logic: state machine, prompt building, fix application, verification
- `src/relay/providers/` — model connector(s) + rate/quota tracking
- `src/relay/prompts/` — model prompt templates (fix, spec draft, review)
- `src/relay/skills/` — per-harness integration templates (`relay skill install` copies from here)
- `src/relay/cli.py` — the `relay` command

## Status

Active alpha. See `CHANGELOG.md` for release history and current phase.
