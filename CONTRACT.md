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

## Discover & Generate (spec authorship)

This is the future specialization the Model-response contract section above anticipates: spec
*generation* rather than fixing an existing finding, with its own envelope. Two roles, following the
same judgment/mechanized split as Find/Fix:

| Role | Who | Automatable? |
|---|---|---|
| **Discover** | the driving agent | No — deciding what context is relevant to a change request is judgment, same category as Find |
| **Generate** | the configured model provider, via `relay spec draft` | Yes — mechanized, request + context in, one document draft out |
| **Validate** | the driving agent | No — same role as the loop's Validate, reused unchanged |

Input shape: `{change_request: string, context: [{source: string, content: string}, ...]}`. The driving
agent gathers `context` itself — one or more files, read in full, each labeled by its source — there's no
`target_excerpt` here because there's no existing text to locate; Generate produces a whole document.

Response envelope, sent via a system prompt (see `src/relay/prompts/spec_system.md`):

```
<<<SPEC_DRAFT>>>
(the full document, ready to save as-is)
<<<END_SPEC_DRAFT>>>
```

or, when the given context doesn't support a confident draft:

```
<<<INSUFFICIENT_CONTEXT>>>
(what's missing, one or two sentences)
<<<END_INSUFFICIENT_CONTEXT>>>
```

Same hard-error discipline as the fix envelope — anything matching neither shape is an error, never a
best-effort parse.

`relay spec draft` is **stateless** — no `run_id`, no `RunState` involved. Spec authorship doesn't have
Find/Fix/Validate's iterate-until-gate-clean shape: one request in, one draft out, reviewed by hand. It
prints the draft to stdout by default; `--output PATH` writes it to disk instead (refusing to overwrite an
existing file there without `--force` — the same precedent `relay skill install` already sets for writing
a file asset). The evidence convention is unchanged: the driving agent reviews, then commits by hand —
`relay` never commits anything on its own behalf, here or anywhere else in this contract.

## Review (independent critique)

| Role | Who | Automated? |
| --- | --- | --- |
| Review | model provider (mechanized) | Yes |
| Decide | driving agent, or the human it serves | No — judgment, never automated, same category as Find/Validate |

`relay review run` produces an independent critique of an architectural or technical decision, given the
decision under review (including its stated reasoning and constraints) and supporting context (documents,
code, prior discussion). It is a third specialization alongside Fix (correct an excerpt) and Generate
(draft a document) — structured the same way (one request in, one response out, stateless), but with an
inverted grounding rule, because a critique's job is the opposite of a document draft's.

Input shape: `{"decision": str, "context": [{"source": str, "content": str}, ...]}` — mirrors the
`spec draft` request shape field-for-field, with `decision` in place of `change_request`.

Model-response envelope:

```
<<<REVIEW>>>
(the review, in markdown)
<<<END_REVIEW>>>
```

**No decline form.** Unlike the Fix and Generate envelopes, there is no `CANNOT_REVIEW` or
`INSUFFICIENT_CONTEXT` analog. This is deliberate: those decline forms exist because Fix and Generate can
legitimately *fail* on thin input — there's nothing to fix, or not enough context to draft from. A review's
job is reasoning productively about thin or contested input; if it could decline whenever context is thin,
"insufficient context" would become a socially acceptable way to dodge taking a critical stance, defeating
the point of asking for one. A response that doesn't match `REVIEW` is still a hard error, same discipline
as every other envelope — just one shape instead of two.

Where `spec_system.md` instructs the model to assert nothing the given context doesn't support, the review
prompt inverts that rule on purpose: a review that only restates or lightly rephrases its input has failed
at the one thing it's for. Its value is surfacing risks, unstated assumptions, and alternative framings the
given context does *not* already contain. Every added claim must still show its inferential step from
something stated in the context ("if X is true, then Y follows"; "this assumes Z, which the context doesn't
establish") — inference from stated premises is encouraged, fabricated facts about the world dressed as
evidence are never acceptable.

**This is advisory input, not a gate.** The review is one input the driving agent (or the human it serves)
weighs alongside its own judgment — never a verdict, approval, or rejection. The decision stays exactly
where Find/Validate already put it in this contract: with the driving agent or the human it serves, never
with `relay` or the model provider. The review prompt is instructed to avoid decisive/gating language
("approved", "rejected", "blocked") in favor of hedged, advisory language ("recommend", "lean toward",
"would be safer to").

Recommended (not enforced — `relay` has no way to track provenance): run the review through a different
`--provider` than whatever produced the reasoning under review, for genuine independence.

**What makes a good subject for review:** a real, still-open decision with stated reasoning and constraints,
and enough surrounding material to check that reasoning's consistency. Poor subjects: an already-executed
decision (produces after-the-fact rationalization, not useful input), a too-vague question with no stated
reasoning to critique, or one-sided context that omits alternatives already considered.

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
- `relay spec draft --request TEXT --context-file PATH [--context-file PATH ...] [--provider NAME] [--timeout SECONDS] [--output PATH] [--force]`
- `relay review run --decision TEXT --context-file PATH [--context-file PATH ...] [--provider NAME] [--timeout SECONDS] [--output PATH] [--force]`
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
