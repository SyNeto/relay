# The relay contract

This document specifies the find → fix → validate quality loop `relay` implements, in terms any coding
agent can act on — it names no specific harness. A harness integration (a Claude Code skill, an OpenCode
agent config, a plain shell script) is a concrete implementation of this contract; `relay` itself is the
state/orchestration engine every implementation calls into identically.

If you are an agent picking this up cold: everything you need to drive the loop correctly is in this
document. You should not need to read `cli.py`'s source to use `relay` correctly.

## Roles

The loop has five roles. **Two of them require judgment and are never automated by `relay` itself** — this
is the load-bearing design decision, not an implementation detail:

| Role | Who | Automatable? |
|---|---|---|
| **Find** | the driving agent | No — reading the target and deciding what's wrong is judgment |
| **Fix** | the configured model provider, via `relay fix run` | Yes — mechanized, one finding in, one diff out |
| **Validate** | the driving agent | No — reading the diff `relay fix run` produced and deciding accept/reject/hand-fix is judgment |
| **Commit** | the driving agent, via plain `git` | Judged action, mechanical execution |
| **Check** | `relay run status` | Yes — pure function of recorded finding statuses |

**Fix and Validate must never be the same actor.** A model that both proposes and accepts its own fix has
no adversarial check on it. In practice: across one validated run of 38 accepted fixes, ~11% contained a
real defect (a type mismatch, a broken test, a dangling markdown fence) that only surfaced because the
driving agent read every diff before accepting it. Skipping Validate — or delegating it back to the same
model that produced the Fix — reintroduces exactly the failure mode this loop exists to catch.

## Sequence (one iteration)

1. **Start.** `relay run start <run_id> [--max-iterations N] [--gate-severities LIST]`. `gate_severities`
   and `max_iterations` are fixed at first creation of a run — later calls with the same `run_id` just
   advance the iteration counter.
2. **Find.** The driving agent reviews the target (files, code, whatever the run's domain is) and records
   what it finds — one JSON object per finding, via `relay finding record <run_id>` (stdin or
   `--from-json`). See "Finding schema" below for the required shape. This step is 100% agent judgment;
   `relay` has no opinion about what counts as a finding.
3. **Verify.** `relay finding verify <run_id> <target_repo_root>` — checks every `open` finding's
   `target_excerpt` still matches exactly once in its file. Run this before any Fix calls; it's cheap and
   catches a stale/mistyped excerpt before it burns a model call.
4. **Fix**, one finding at a time. `relay fix run <run_id> <finding_id> <target_repo_root> [--timeout N]`
   — builds the fixer prompt, calls the model, applies the result directly to the file, prints a unified
   diff. Does **not** mark the finding's status or commit anything — see Validate.
5. **Validate.** The driving agent reads the diff `fix run` printed and decides:
   - accept: `relay finding mark <run_id> <finding_id> fixed`
   - reject: revert the file (`git checkout -- <file>` in the target repo), then
     `relay finding mark <run_id> <finding_id> open` — either refine the finding and re-run step 4, or fix
     it by hand and mark `fixed` once it's actually right. Don't spend unlimited retries on the same
     finding against the same bad prompt.
   - the model may also correctly decline (see "CANNOT_FIX" below) — that's not a bug to route around by
     loosening its instructions mid-run; do the fix by hand if it's simple, or reconsider the finding.
6. **Commit.** In the target repo: one commit per iteration, on a dedicated branch/worktree so the
   project's main branch stays untouched until the run is reviewed. This is a plain `git` step — `relay`
   does not wrap it.
7. **Check.** `relay run status <run_id>` prints the current state and whether the run should stop. If not
   stopping and `iteration < max_iterations`: go to step 1 for the next iteration. If stopping: report the
   final state — either a clean pass on the gated severities, or the max-iteration cutoff with whatever's
   still open.

## State model

`relay run status` and every mutating subcommand operate on one JSON document per run, keyed by `run_id`.
Fields:

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | |
| `max_iterations` | int | fixed at creation |
| `gate_severities` | list of severity strings | fixed at creation — which severities gate the exit condition |
| `iteration` | int | current iteration number, starts at 0, incremented by `run start` |
| `phase` | string | one of `find`, `fix`, `validate`, `commit` — set by the CLI subcommand currently in use, informational |
| `findings` | list of finding records | see schema below |

**Exit gate.** A finding is *resolved* if its `status` is `fixed` or `wontfix` — `wontfix` is a deliberate
decision (e.g. a finding superseded by a corrected duplicate, or determined to no longer apply), not a
euphemism for abandoned. `open` is not resolved. The gate is clean when every recorded finding whose
`severity` is in `gate_severities` is resolved. A run should stop when either `iteration >= max_iterations`
or (`iteration > 0` and the gate is clean).

## Finding schema

One record per finding, produced during Find, consumed by Fix:

| Field | Required | Notes |
|---|---|---|
| `id` | yes | stable short slug, unique within the run |
| `severity` | yes | one of `CRITICAL`, `HIGH`, `MEDIUM`, `LOW` — closed set for this version of the contract |
| `summary` | yes | one sentence, the claim itself |
| `file` | yes | path to the file containing the issue, relative to the target repo root |
| `target_excerpt` | yes | the **exact, verbatim** current text to replace — copy it from the file, do not retype it by hand if you can slice it programmatically; this is the single source of truth for locating the edit, never a line number |
| `failure_scenario` | yes | why it's wrong / what breaks — this becomes the model's justification |
| `section` | no | human-readable location hint (heading, function name) for the model's orientation |
| `reference` | no | known-correct material elsewhere the fix must be consistent with |
| `status` | yes | `open` (default) → `fixed` (Validate accepted) or `wontfix` (deliberately not fixing, still resolved) |

**Why verbatim excerpts, never line numbers:** files shift between Find and Fix (earlier findings' fixes
change line counts). An excerpt is self-locating; a line number silently drifts. `relay finding verify`
exists specifically to catch a drifted or mistyped excerpt before it reaches the model.

## Model-response contract

`relay fix run` sends the model a system prompt (see `src/relay/prompts/fix_system.md`) that requires a
strict, parseable response shape:

```
<<<FIXED_EXCERPT>>>
(corrected content, replacing target_excerpt verbatim)
<<<END_FIXED_EXCERPT>>>
```

or, when the model cannot produce a confident fix:

```
<<<CANNOT_FIX>>>
(one-sentence reason)
<<<END_CANNOT_FIX>>>
```

Anything that matches neither shape is a hard error, not a best-effort parse — a malformed response should
never silently become a no-op or a guessed edit. `relay fix run` applies `target_excerpt` → fixed content
via **exact substring replacement**: if the excerpt now appears zero times (shifted or already changed) or
more than once (ambiguous) in the file, it fails loudly instead of guessing which occurrence to touch —
re-derive the excerpt during a later Find pass rather than forcing it through.

This envelope and the prompt templates that produce it are the current Layer 2/3 boundary. A future
specialization (e.g. spec *generation* rather than fixing an existing finding) may define a different
envelope for that task — this contract only covers the fix-an-existing-finding case.

## Model connector

`relay fix run` sends its prompt to a configured **provider** — external, user-editable config (`name`,
`base_url`, `api_key_path`, `default_model`), not part of the finding schema; the response envelope above
applies uniformly regardless of which provider produced it. Providers ship with working defaults (`relay`
works zero-config); `~/.relay/providers.json` overrides or adds provider entries by name — an override
replaces that provider's whole record, not a field-level merge. Select one via
`relay fix run ... --provider NAME` (default: `nim`). `relay quota status` tracks each provider's request
volume independently — one account, one log file, one report block.

## CLI surface

Every harness implementation drives the loop through exactly these subcommands — this is the contract's
enforcement point. A harness integration should never need to touch `relay`'s internals directly.

- `relay run start <run_id> [--max-iterations N] [--gate-severities LIST]` — `LIST` is
  comma-separated with no spaces, e.g. `--gate-severities CRITICAL,HIGH`
- `relay run status <run_id>`
- `relay finding record <run_id> [--from-json PATH]` (JSON object or array; also accepts stdin)
- `relay finding verify <run_id> <target_repo_root>`
- `relay finding mark <run_id> <finding_id> <status>`
- `relay fix run <run_id> <finding_id> <target_repo_root> [--timeout SECONDS] [--provider NAME]`
- `relay quota status [--provider NAME]`
- `relay skill install --harness <name> [--target-dir PATH]`

Run any subcommand with `--help` for exact flags. State lives under `--state-dir` (default
`./.relay/runs/`, relative to the current working directory) and per-user quota tracking lives under
`~/.relay/` — see each subcommand's `--help` for overrides.

## Evidence convention

One commit per iteration in the target repo, on a dedicated branch/worktree so the project's default branch
stays untouched until the run is reviewed and merged. This is intentionally a manual `git` step, not a
`relay` subcommand — the loop should never commit on the driving agent's behalf without that agent's
explicit action.
