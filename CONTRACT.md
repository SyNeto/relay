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
| **Commit** | the driving agent, via `relay repo setup`/`relay repo commit` or plain `git` | Partially — mechanics (branch, staging, message) are mechanized; deciding *when* to commit, and that this iteration's work is actually done, stays judgment |
| **Check** | `relay run status` | Yes — pure function of recorded finding statuses |

**Fix and Validate must never be the same actor.** A model that both proposes and accepts its own fix has
no adversarial check on it. In practice: across one validated run of 38 accepted fixes, ~11% contained a
real defect (a type mismatch, a broken test, a dangling markdown fence) that only surfaced because the
driving agent read every diff before accepting it. Skipping Validate — or delegating it back to the same
model that produced the Fix — reintroduces exactly the failure mode this loop exists to catch.

## Sequence (one iteration)

1. **Start.** `relay run start <run_id> [--max-iterations N] [--gate-severities LIST] [--spec-file PATH]`.
   `gate_severities`, `max_iterations`, and `spec_file` are fixed at first creation of a run — later calls
   with the same `run_id` just advance the iteration counter. Right after Start, while the target repo's
   tree is still clean, `relay repo setup <run_id> <target_repo_root> [--branch NAME]` can isolate this
   run's work on a dedicated branch (default `relay/<run_id>`) before Find/Fix touch anything — see
   "Repository management" below.
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
6. **Commit.** Once this iteration's fixes are validated: `relay repo commit <run_id> <target_repo_root>
   [-m TEXT]` — stages and commits exactly the files belonging to this run's `fixed` findings that are
   still dirty, refusing loudly if that set is empty. The driving agent may still do this entirely by hand
   with plain `git` instead — `repo commit` is a convenience for the common case, not a requirement. See
   "Repository management" below for exactly how the file selection and commit message are built.
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
| `spec_file` | string or null | optional, fixed at creation — path to the spec document (e.g. from `relay spec draft --output`) that drove this run, for traceability; not validated to exist or be readable |
| `iteration` | int | current iteration number, starts at 0, incremented by `run start` |
| `phase` | string | one of `find`, `fix`, `validate`, `commit` — set by the CLI subcommand currently in use, informational |
| `findings` | list of finding records | see schema below |

**Exit gate.** A finding is *resolved* if its `status` is `fixed` or `wontfix` — `wontfix` is a deliberate
decision (e.g. a finding superseded by a corrected duplicate, or determined to no longer apply), not a
euphemism for abandoned. `open` is not resolved. The gate is clean when every recorded finding whose
`severity` is in `gate_severities` is resolved. A run should stop when either `iteration >= max_iterations`
or (`iteration > 0` and the gate is clean).

**Discovering runs.** `relay run list [--state-dir PATH]` enumerates every `run_id` with a state file under
`state_dir`, one summary line each (iteration, phase, gate status, `spec_file` if set) — the way to find a
run's `run_id` to resume it, or to see what's in flight across a machine, without needing to remember it.

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

`--diff-from-branch <branch> --target-repo-root <path>` gathers one context item automatically: `git diff
<branch>...HEAD` (the merge-base diff — only what changed on the current branch since diverging from
`<branch>`, not unrelated changes made on `<branch>` afterward) from `target_repo_root`, run through
`engine/repo.py`'s `diff_against`. This exists for the closing check described in "What makes a good subject
for review" below — comparing a finished run's actual diff back against the spec that motivated it, without
the driving agent hand-assembling a summary of what changed. At least one of `--context-file`/
`--diff-from-branch` is required, and both may be combined (e.g. the spec file plus the diff).

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

One good subject is still-open even after a fix loop reaches a clean gate: whether the finished
implementation actually matches the intent that motivated it. `--diff-from-branch` exists for exactly this
closing check — the decision under review becomes "does this diff actually deliver on the spec," with the
spec (e.g. via `--context-file`) and the diff (via `--diff-from-branch`) as the supporting context.

## Repository management

`relay repo setup` and `relay repo commit` mechanize the branch-and-commit mechanics of the Commit role
(see Roles above) — deciding *when* to invoke them stays with the driving agent, same as every other
explicit `relay` subcommand. Both operate purely on local git state — no remote, no PR, no merge; the loop
hands the run's finished branch back to the driving agent (or the human it serves) to push, review, and
merge by whatever process the target repo already uses. Pushing, opening a pull request, and merging are
explicitly out of scope for this cut, deferred to a later "glue" design.

**`relay repo setup <run_id> <target_repo_root> [--branch NAME]`** — idempotent. Ensures a dedicated branch
(default `relay/<run_id>`) exists and is checked out:
- already on the target branch: no-op.
- target branch doesn't exist: create it from the current HEAD and check it out.
- target branch exists but isn't checked out: check it out.
- working tree is dirty *and* not already on the target branch: refuse — never silently switches branches
  over uncommitted work. (Dirty while already on the target branch is fine — that's the normal
  in-progress state during Fix/Validate.)

Run this right after Start, while the tree is still clean — not after Fix has already made changes, since
the dirty-tree refusal above would then correctly block it.

**If `--state-dir`'s default (`./.relay/runs`, relative to wherever `relay` is invoked from) resolves to
somewhere inside `target_repo_root`**, that directory shows up as untracked in `git status` like anything
else — the dirty-tree checks above have no special-case awareness of it. Either gitignore `.relay/` in the
target repo (the convention `relay`'s own repo already follows), or pass `--state-dir` pointing outside
`target_repo_root`, so run state itself never trips a dirty-tree refusal.

**`relay repo commit <run_id> <target_repo_root> [-m TEXT]`** — stages and commits exactly the files this
run is responsible for, nothing else:
1. The *fixed-finding file set*: the `file` of every finding in the run whose `status` is `fixed`.
2. The *dirty set*: every path git currently reports as changed (staged, unstaged, or untracked).
3. Stages and commits their **intersection** — one `git add` naming every path explicitly, never
   `git add -A`/`-u`. Files outside the intersection are left untouched.
4. Refuses loudly (non-zero exit, no commit attempted) if the intersection is empty.

This deliberately does not use the finding's `iteration` field — `record_finding` stamps it once, at Find
time, and never updates it, so a finding recorded in iteration 1 but not fixed until iteration 3 would
misreport as iteration-1 work forever. Scoping by status + live dirty-state instead means the set is always
correct for "whatever hasn't been committed yet," with no new bookkeeping field required. It also doesn't
depend on *how* a finding was fixed: a finding fixed by `relay fix run`, and one fixed entirely by hand (per
Validate's "fix it by hand if it's simple" guidance) and marked `fixed` via `relay finding mark`, are
indistinguishable to this algorithm — both are `status: fixed` findings whose `file` is dirty, so both are
picked up identically.

Commit message: an auto-generated body listing each committed finding's `id`, `severity`, and `summary`
(one line each), plus a `Spec-File: <path>` trailer if the run's `spec_file` is set. `-m TEXT` replaces only
the headline; the structured finding list and `Spec-File:` trailer are still generated and appended. After
committing, any dirty files outside the committed set (e.g. a fix that required touching a second, unlisted
file) are printed as a note — left for the driving agent to handle, not silently dropped.

## CLI surface

Every harness implementation drives the loop through exactly these subcommands — this is the contract's
enforcement point. A harness integration should never need to touch `relay`'s internals directly.

- `relay run start <run_id> [--max-iterations N] [--gate-severities LIST] [--spec-file PATH]` — `LIST` is
  comma-separated with no spaces, e.g. `--gate-severities CRITICAL,HIGH`
- `relay run status <run_id>`
- `relay run list [--state-dir PATH]`
- `relay finding record <run_id> [--from-json PATH]` (JSON object or array; also accepts stdin)
- `relay finding verify <run_id> <target_repo_root>`
- `relay finding mark <run_id> <finding_id> <status>`
- `relay fix run <run_id> <finding_id> <target_repo_root> [--timeout SECONDS] [--provider NAME]`
- `relay spec draft --request TEXT --context-file PATH [--context-file PATH ...] [--provider NAME] [--timeout SECONDS] [--output PATH] [--force]`
- `relay review run --decision TEXT [--context-file PATH ...] [--diff-from-branch BRANCH --target-repo-root PATH] [--provider NAME] [--timeout SECONDS] [--output PATH] [--force]` — at least one of `--context-file`/`--diff-from-branch` required
- `relay repo setup <run_id> <target_repo_root> [--branch NAME]`
- `relay repo commit <run_id> <target_repo_root> [-m TEXT]`
- `relay quota status [--provider NAME]`
- `relay skill install --harness <name> [--target-dir PATH]`

Run any subcommand with `--help` for exact flags. State lives under `--state-dir` (default
`./.relay/runs/`, relative to the current working directory) and per-user quota tracking lives under
`~/.relay/` — see each subcommand's `--help` for overrides.

## Evidence convention

`relay repo setup` and `relay repo commit` mechanize the branch-and-commit mechanics described in Sequence
step 6 — but only when the driving agent explicitly invokes them. Nothing else in this contract triggers a
commit as a side effect: `relay fix run`, `relay finding mark`, and every other subcommand touch only
working-tree files or run state, never git history. The guarantee this section has always stated is
unchanged: `relay` never commits on the driving agent's behalf without that agent's explicit action —
invoking `repo commit` *is* that explicit action, the same way invoking `fix run` is the explicit action
that applies a fix. The driving agent may still do this entirely by hand with plain `git` instead, at its
discretion; `relay repo commit` is a convenience for the common case, not a requirement.

One commit per iteration in the target repo, on a dedicated branch (`relay repo setup`, default branch name
`relay/<run_id>`) so the project's default branch stays untouched until the run is reviewed and merged.
`relay repo commit` stages and commits only the files belonging to this run's `fixed` findings that git
still reports as dirty — see "Repository management" above for exactly how that set is computed. Out of
scope for both commands, deferred to a later design: pushing, opening or merging a pull request, or any
interaction with a remote.
