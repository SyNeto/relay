# relay

A harness-neutral find → fix → validate quality loop. `relay` is the engine (state machine + fixer
integration); it doesn't know or care which coding agent is driving it. See [`CONTRACT.md`](CONTRACT.md)
for the full protocol — roles, state model, finding schema, CLI surface — written to be reconstructible by
any agent from that document alone.

Today's fixer model is NVIDIA NIM (`z-ai/glm-5.2`) — that's a placeholder, not a design commitment; the
model connector gets abstracted across providers in a later phase.

## Install

```bash
pipx install git+https://github.com/<org>/relay.git
```

(`pipx` gives one global per-user install independent of any target project's own stack — target repos are
not assumed to be Python projects.)

For local development:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

API key for the model provider lives at `~/.secrets/.nvidia-api-key` (chmod 600) — never in the repo or in
code.

## Quickstart

```bash
relay run start my-run-1 --max-iterations 3 --gate-severities CRITICAL,HIGH
echo '{"id": "f1", "severity": "HIGH", "summary": "...", "file": "docs/x.md",
       "target_excerpt": "...", "failure_scenario": "..."}' | relay finding record my-run-1
relay finding verify my-run-1 /path/to/target-repo
relay fix run my-run-1 f1 /path/to/target-repo
# review the printed diff, then:
relay finding mark my-run-1 f1 fixed
relay run status my-run-1
```

## Use from Claude Code

```bash
relay skill install --harness claude-code --target-dir /path/to/your/project
```

Installs a skill at `<project>/.claude/skills/relay/SKILL.md` that teaches Claude how to drive the loop
above — Find and Validate stay agent judgment calls, `relay` handles state, gating, and the fixer-model
call.

## Layout

- `CONTRACT.md` — the harness-neutral protocol spec (read this first)
- `src/relay/engine/` — business logic: state machine, prompt building, fix application, verification
- `src/relay/providers/` — model connector(s) + rate/quota tracking
- `src/relay/prompts/` — fixer-model prompt templates
- `src/relay/skills/` — per-harness integration templates (`relay skill install` copies from here)
- `src/relay/cli.py` — the `relay` command

## Status

Phase 1 (this release): harness-neutral contract + Claude Code skill, ported from a validated POC.
Not yet done: model-provider abstraction (still NIM-only), spec-*generation* specialization (still
fix-existing-findings only). See `CHANGELOG.md`.
