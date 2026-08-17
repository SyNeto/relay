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
| **Commit** | the driving agent, via `relay repo setup`/`commit`/`push`/`pr create` or plain `git`/`gh` | Partially — mechanics (branch, staging, message, publishing) are mechanized; deciding *when* to commit or publish, and that this iteration's work is actually done, stays judgment |
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
   still open. Once the run is stopping with a clean gate (or the driving agent otherwise decides the
   branch is ready), `relay repo push` and `relay repo pr create` can publish it — see "Repository
   management" below. Neither is part of the repeating per-iteration cycle above.

## State model

`relay run status` and every mutating subcommand operate on one JSON document per run, keyed by `run_id`.
Fields:

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | |
| `max_iterations` | int | fixed at creation |
| `gate_severities` | list of severity strings | fixed at creation — which severities gate the exit condition |
| `spec_file` | string or null | optional, fixed at creation — path to the spec document (e.g. from `relay spec draft --output`) that drove this run, for traceability; not validated to exist or be readable; `repo commit`/`repo pr create` warn (stderr, non-blocking) if this path isn't git-tracked in `target_repo_root` — a signal, not a gate |
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

**Push and PR creation don't touch `phase`.** `VALID_PHASES` stays `("find", "fix", "validate", "commit")`.
`relay repo push`/`relay repo pr create` load a run's state (to derive the default branch name and, for
`pr create`, the fixed findings and `spec_file`) but never call `set_phase`. `phase` models progress through
one iteration's find→fix→validate→commit cycle; push and PR-create aren't per-iteration steps — a run's
branch might get pushed after every iteration, or only once at the end — so there's no single canonical
moment for them the way `"commit"` has Sequence step 6. Reusing the phase enum for them would strain a model
built for something else rather than fit it.

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

**Retry never applies here.** A malformed envelope is a model-judgment or prompt-formatting problem, not a
transport failure — resending the same prompt to the same model would produce the same non-conforming
result. Retry (below) operates strictly at the transport layer, before a response ever reaches this parser.

## Model connector

`relay fix run` sends its prompt to a configured **provider** — external, user-editable config (`name`,
`base_url`, `api_key_path`, `default_model`), not part of the finding schema; the response envelope above
applies uniformly regardless of which provider produced it. Providers ship with working defaults (`relay`
works zero-config); `~/.relay/providers.json` overrides or adds provider entries by name — an override
replaces that provider's whole record, not a field-level merge. Select one via
`relay fix run ... --provider NAME` (default: `nim`). `relay quota status` tracks each provider's request
volume independently — one account, one log file, one report block.

**Bounded retry on transient transport errors.** `relay fix run`, `relay spec draft`, and `relay review run`
retry a call automatically when the failure is plausibly transient: a 429 rate limit, a 5xx server error
(500/502/503/504 — not 501/505/511, which mean "this will never work," not "try again"), a connection
failure, or a timeout. A 401 auth failure or a 400/403/404/422 request error fails immediately with no
retry — retrying a request that can never succeed is pure waste. Default `--max-retries 2` (3 total
attempts), exponential backoff from a per-error-class base delay (with jitter), respecting the provider's
own `Retry-After` header when present. `--no-retry` (or `--max-retries 0`) restores the exact single-attempt
behavior; `--retry-base-delay SECONDS` overrides the per-class defaults uniformly.

This is **transient-blip resilience, not a fix for a persistent outage** — a provider that's genuinely down
will still exhaust the retry budget and fail. With the default timeout (90s) and retry policy, the
worst-case wall clock before giving up is roughly 3 × 90s + ~45s of backoff ≈ **6 minutes**. Every retry
prints to stderr with a `[relay]` prefix and an `attempt N/M` counter (never to stdout, which carries only
the model's response) — a first-attempt success prints nothing, so silence on stderr means no retry
happened. **Provider fallback** (trying a second provider when the first is persistently unavailable)
remains a known gap, not yet implemented — a driving agent that hits a persistent outage must retry
manually with a different `--provider` by hand.

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

When the spec is meant to back a real, published run — one that will be committed, pushed, or PR'd — it
should be committed into the target repo (e.g. under `docs/specs/`) before `run start --spec-file`
references it. Otherwise the `Spec-File:` trailer in the published commit or PR will point at a path no one
else can resolve. As a safety net, `repo commit` and `repo pr create` warn (but do not error) at publish
time when the referenced spec file isn't tracked in the repo.

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

`relay repo setup`, `relay repo commit`, `relay repo push`, and `relay repo pr create` mechanize the
branch/commit/publish mechanics around the Commit role (see Roles above) — deciding *when* to invoke each
stays with the driving agent, same as every other explicit `relay` subcommand. `relay repo sync-dev` is a
related but separate, repo-level maintenance operation (not run-scoped, not part of any run's lifecycle)
for git-flow-style target repos: rebasing `dev` onto `main` after a release ships.

**Pull-request merging remains explicitly out of scope, by design, not by omission.** Unlike setup, commit,
push, and pr-create — whose mechanics run entirely inside the same agent-supervised loop that already
validated every change — merging is the point where an external review (a second human's approval, CI or
branch-protection checks, whatever process the target repo already requires) is supposed to happen, and
that reviewer often isn't the same agent that ran the loop. Wrapping `gh pr merge` would make an inherently
judgment-gated step look identically mechanized to steps that genuinely are just mechanics — merge by
whatever process the target repo already uses, directly via `gh pr merge` or the GitHub UI.

**`relay repo setup <run_id> <target_repo_root> [--branch NAME] [--base-branch NAME] [--remote NAME]`** —
idempotent. Ensures a dedicated branch (default `relay/<run_id>`) exists and is checked out:
- already on the target branch: no-op.
- target branch doesn't exist: create it from `--base-branch` if given (see below), else current HEAD.
- target branch exists but isn't checked out: check it out.
- working tree is dirty *and* not already on the target branch: refuse — never silently switches branches
  over uncommitted work. (Dirty while already on the target branch is fine — that's the normal
  in-progress state during Fix/Validate.)

Run this right after Start, while the tree is still clean — not after Fix has already made changes, since
the dirty-tree refusal above would then correctly block it.

Already working in a dedicated worktree/branch (this project's own dev convention:
`git worktree add -b <name> ~/Projects/<repo>-<feature> main`)? Two paths:

1. Name the worktree's branch `relay/<run_id>` from creation for relay-run worktrees specifically.
   This gives real traceability, and `relay repo setup` then becomes a harmless idempotent no-op —
   `repo push` and `repo pr create`'s `--branch` defaults work with zero extra flags.
2. Keep a different branch name and skip `relay repo setup` entirely, always passing `--branch`
   explicitly to `repo push` and `repo pr create`. (`relay repo commit` is branch-name-agnostic and
   works either way.)

Nothing in relay relies on `relay repo setup` having been called specifically — only on the branch
existing and being checked out. The implementation does nothing beyond `git checkout`/`git checkout -b`
— no upstream tracking, no side state — so a branch made by plain `git worktree add -b` behaves
identically to one `relay repo setup` would make.

**`--base-branch NAME [--remote NAME]`** (default remote: `origin`) — for git-flow-style target repos,
branch from a freshly-fetched `<remote>/<base-branch>` (e.g. `origin/dev`) instead of current HEAD: fetches
first, then creates the run's branch from that remote-tracking ref, never from local `dev` directly (only
`sync-dev`, below, ever touches local `dev`). Only affects the *create* path — if the run's branch already
exists, `--base-branch` is ignored entirely; idempotent re-invocation never resets an existing branch to a
different base. Create the worktree from the correct base up front, since `--base-branch` cannot
retroactively fix an already-existing branch. Omit it to keep the previous behavior exactly (branch from
current HEAD, no network access at all).

**If `--state-dir`'s default (`./.relay/runs`, relative to wherever `relay` is invoked from) resolves to
somewhere inside `target_repo_root`**, that directory shows up as untracked in `git status` like anything
else — the dirty-tree checks above have no special-case awareness of it. Either gitignore `.relay/` in the
target repo (the convention `relay`'s own repo already follows), or pass `--state-dir` pointing outside
`target_repo_root`, so run state itself never trips a dirty-tree refusal.

**`relay repo commit <run_id> <target_repo_root> [-m TEXT] [--also-commit PATH ...]`** — stages and commits
exactly the files this run is responsible for, nothing else:
1. The *fixed-finding file set*: the `file` of every finding in the run whose `status` is `fixed`.
2. The *dirty set*: every path git currently reports as changed (staged, unstaged, or untracked).
3. Stages and commits their **intersection**, unioned with any `--also-commit` files (see below) — one
   `git add` naming every path explicitly, never `git add -A`/`-u`. Files outside this set are left
   untouched.
4. Refuses loudly (non-zero exit, no commit attempted) if the resulting set is empty.

This deliberately does not use the finding's `iteration` field — `record_finding` stamps it once, at Find
time, and never updates it, so a finding recorded in iteration 1 but not fixed until iteration 3 would
misreport as iteration-1 work forever. Scoping by status + live dirty-state instead means the set is always
correct for "whatever hasn't been committed yet," with no new bookkeeping field required. It also doesn't
depend on *how* a finding was fixed: a finding fixed by `relay fix run`, and one fixed entirely by hand (per
Validate's "fix it by hand if it's simple" guidance) and marked `fixed` via `relay finding mark`, are
indistinguishable to this algorithm — both are `status: fixed` findings whose `file` is dirty, so both are
picked up identically.

**`--also-commit PATH` (repeatable)** — stages and commits a file that isn't tied to any finding, e.g.
release bookkeeping (`CHANGELOG.md`, a version bump in `pyproject.toml`). Repeatable, not comma-separated —
file paths can contain commas, unlike this contract's other comma-separated list flags (`--gate-severities`),
so a single `--also-commit PATH,PATH`-style flag would silently split a path containing a comma into the
wrong files. Every `--also-commit` path is resolved relative to `target_repo_root` and checked to stay
inside it (fails loudly on an escaping path, e.g. `../../etc/passwd`, before any git command runs) — then
**must still be dirty**, exactly like a finding's file: `repo commit` fails loudly naming every non-dirty
`--also-commit` path, never silently skipping one. `--also-commit` files are **unioned into** the selected
set, not substituted for the finding-file intersection — a run with zero fixed findings and only
`--also-commit` files still produces a valid commit (this is the common case for pure release-bookkeeping
work, which has no findings loop at all).

This is a deliberate, explicit narrowing of the safety model for exactly the files named this way: a
finding's file must be *both* tied to a `fixed` finding *and* dirty (two conditions); an `--also-commit`
file only needs to be dirty (one condition) — the driving agent naming it explicitly takes the place of the
finding-membership check. `relay` still never guesses which files are "release bookkeeping" — the agent
says so, for exactly the files it names.

Commit message: an auto-generated body listing each committed finding's `id`, `severity`, and `summary`
(one line each), an `Also committed: <paths>` line listing any `--also-commit` files not already covered by
a finding (a file that's both a fixed finding's file and named via `--also-commit` appears only in the
per-finding list, not doubled into this line), and a `Spec-File: <path>` trailer if the run's `spec_file`
is set. `-m TEXT` replaces only the headline; every other section is still generated and appended. After
committing, any dirty files outside the committed set (e.g. a fix that required touching a second, unlisted
file) are printed as a note — left for the driving agent to handle, not silently dropped.

Known limitation: `relay repo commit` is branch-name-agnostic and commits to whatever branch is currently
checked out, regardless of the supplied `<run_id>`. It does not validate that the current branch matches the
run, so invoking `relay repo commit <run_id_B> ...` while checked out on run A's branch would land run B's
committed files on run A's branch without an error. This is a deliberate, already-validated property — a run
can be committed from a plain feature-named branch rather than a `relay/<run_id>` branch — so adding
branch-name validation is out of scope here.

Before committing, when the run's `spec_file` is set but that path is not git-tracked inside
`target_repo_root`, `repo commit` prints a non-blocking `warning:` (no `[relay]` prefix) to stderr. Two
cases apply: if the path lies entirely outside `target_repo_root`, move it into the repo; if it is inside
`target_repo_root` but not yet committed, pass it via `--also-commit <path>`. The warning is suppressed
when the untracked path is already named in `--also-commit`. A failure in the tracking check itself is
silently swallowed — it never blocks the commit.

When the committed file set includes `CHANGELOG.md` or `pyproject.toml` and the diff being committed
(`git diff HEAD`, checked before staging) actually adds a new version line — not merely because the file
happens to be present in the commit set — `repo commit` prints a non-blocking reminder to stderr naming
`relay repo check-integration` as the next step. The check is diff-based rather than presence-based
specifically to avoid false positives on unrelated changes (a dependency bump in `pyproject.toml`, a typo
fix in an old `CHANGELOG.md` entry) that would otherwise train the driving agent to ignore the reminder.

**`relay repo pr create` does not yet support `--also-commit`.** A pure-bookkeeping run (zero fixed
findings, only `--also-commit` files) can `repo commit` through relay but cannot `repo pr create` — that
command still requires at least one fixed finding to describe in the PR body, and refuses otherwise. The
agent needs a manual `gh pr create` for that step. This is a deliberate scope boundary for this capability,
not an oversight — extending `pr create` to describe `--also-commit` files is a separate, later design
(does the PR body list them? where does that information persist between the commit and the PR-create
call?), named explicitly here rather than discovered by surprise.

**`relay repo push <run_id> <target_repo_root> [--branch NAME] [--remote NAME]`** — pushes the run's branch
(default `relay/<run_id>`) to `--remote` (default `origin`), setting upstream on first push. **Never
force.** Nothing to push is reported, not an error. A non-fast-forward rejection surfaces verbatim as a
failure — `relay` never guesses how to reconcile diverged history. If the branch being pushed — the
`--branch` value, or the `relay/<run_id>` default — does not exist locally, `repo push` fails loudly,
naming the actual current branch and suggesting `--branch`, instead of letting git's raw
`src refspec ... does not match any` surface.

**`relay repo pr create <run_id> <target_repo_root> [--base BRANCH] [--branch NAME] [--title TEXT]`** —
opens a PR via `gh pr create` (requires `gh` installed and authenticated; shelled out to exactly like `git`,
no new dependency). Title/body mirror `repo commit`'s message shape (headline, one `- id [severity] summary`
line per this run's `fixed` findings, `Spec-File:` trailer if set). **Requires the branch already pushed** —
does not push as a side effect; run `relay repo push` first. This is deliberate: push and PR-create are each
their own explicit, remote-visible action, and auto-chaining a push would blur which invocation published
what and could mask a push failure behind a confusing `gh` error. If the head branch — the `--branch` value,
or `relay/<run_id>` when omitted — doesn't exist locally, the command fails loudly before ever calling `gh`,
naming the actual current branch and suggesting `--branch`. `--base` has no relay-side default
(omitted → `gh` falls back to the repo's configured default branch) — hardcoding `dev` would assume every
target repo follows git-flow, which nothing else here assumes. Prints the created PR's URL.

Like `repo commit`, `repo pr create` prints a non-blocking `warning:` to stderr when the run's `spec_file`
is set but not git-tracked inside `target_repo_root` — the same two cases (entirely outside the repo, or
inside but not yet committed) apply. There is no `--also-commit` suppression here: `repo pr create` doesn't
support that flag at all (per this section's boundary note above), so by the time this warning could fire,
there's nothing left for the caller to name.

**Post-release dev sync.** `relay repo sync-dev <target_repo_root> [--main-branch main] [--dev-branch dev]
[--remote NAME] --i-understand-this-rewrites-dev-history` — after a release ships from `main`, rebases `dev`
onto `main` and force-pushes the result. **Not run-scoped** — no `run_id`, no `--state-dir`.

This is the highest-risk operation `relay` performs: the rebase rewrites `dev`'s history, and the push
updates the shared remote to match — anyone with `dev` already checked out locally will need to reset
afterward. Safety design, in execution order:

1. **`--i-understand-this-rewrites-dev-history` required before anything else runs** — not even a fetch
   happens without it. A deliberately long, specific flag name, not this codebase's existing `--force`/
   `--yes` (already used elsewhere for the much lower-stakes "overwrite a local output file"), so it can't
   be typed from habit. A confirmation prompt was considered and rejected — `relay` is meant to be driven
   headlessly.
2. **Fetch both `main` and `dev` fresh** — never rebase onto a stale local `main`; fetching `dev`
   immediately before the operation gives the final force-with-lease push a fresh "expected remote state,"
   narrowing the fetch-to-push race window.
3. **Refuse if local `dev` doesn't exactly match `<remote>/dev`** after the fetch — behind or ahead, either
   way rebasing from a stale/diverged `dev` and force-with-lease-pushing the result risks silently
   discarding commits that only exist on the remote, with no conflict ever surfacing to flag it.
   `--force-with-lease` alone only protects against the remote moving *during* this operation, not against
   it having already moved before the operation started.
4. **Refuse if the working tree is dirty** — same discipline as `checkout_or_create_branch`.
5. **Rebase `dev` onto `<remote>/main`.** On conflict, `relay` runs `git rebase --abort` immediately and
   raises, naming every conflicting file — it never leaves the repo mid-rebase for manual resolution
   through `relay`. Same "fail loudly, never leave an ambiguous state" discipline as every other failure
   path in this module, applied to the one operation where an ambiguous state would be most dangerous to
   leave behind.
6. **`git push --force-with-lease <remote> dev`** — never bare `--force`; rejected if `<remote>/dev` moved
   since step 2's fetch, rather than overwriting it.

The only step that touches the remote is the final force-with-lease push, reached only after every guard
above has passed — the one truly irreversible action in this command is also the last thing that can
happen, and it's the one git itself refuses to perform blindly.

### Cross-PR version-collision check.

`relay repo check-integration <target_repo_root> [--base main] [--remote origin] [--fail-on-collision]` is
not run-scoped — it takes a target repo root and inspects it directly, the same shape as `sync-dev`. It
lists every open PR against that repo's remote via `gh pr list`, then reads each PR's diff via `gh pr
diff`, which resolves fork PRs correctly where a plain `git fetch` of the branch name would not. From each
diff it extracts only the ADDED lines, looking for a newly-added `## X.Y.Z` CHANGELOG heading or a changed
`pyproject.toml` version line, and excludes a version that also appears on a REMOVED line in the same diff
(an in-place edit to an existing entry, e.g. a typo fix, produces exactly that shape and must not read as a
new version) — this is a diff scan, not a full-file scan, so it does not depend on the changelog being
newest-first. It also fetches `--remote`/`--base` fresh (same mechanism `sync-dev` uses) and checks whether
any claimed version already appears in `CHANGELOG.md` there — folding in the "this version was already
released by an already-merged PR" case with a single `git show` against the freshly-fetched ref, not the
possibly-stale local branch of the same name. If the fetch itself fails, that check is skipped with a
warning rather than blocking the rest of the run. Any version claimed by two or more sources is reported
on stderr; the
command never resolves anything itself. A successful run exits 0 whether or not a collision was found,
unless `--fail-on-collision` is passed, in which case a found collision exits 1 — the same
opt-in-changes-default-exit-code pattern as `--no-retry` and `--i-understand-this-rewrites-dev-history`,
not new territory for this project. If the tool itself fails (e.g. `gh` not authenticated), it exits 1
regardless.

Honest limits: a PR opened after this check runs is invisible to it, so re-run it close to merge time,
not just once; a PR from a fork whose author never opened it against this repo is not visible to
`gh pr list` at all; only `--base` is checked for already-released versions, so a project publishing from
multiple release lines needs one run per line; and this is the only relay-native mechanized way to catch
this — a human reading the open PR list by eye can already do a version of this, the command just
automates the reading and comparing.

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
- `relay fix run <run_id> <finding_id> <target_repo_root> [--timeout SECONDS] [--provider NAME] [--max-retries N | --no-retry] [--retry-base-delay SECONDS]`
- `relay spec draft --request TEXT --context-file PATH [--context-file PATH ...] [--provider NAME] [--timeout SECONDS] [--output PATH] [--force] [--max-retries N | --no-retry] [--retry-base-delay SECONDS]`
- `relay review run --decision TEXT [--context-file PATH ...] [--diff-from-branch BRANCH --target-repo-root PATH] [--provider NAME] [--timeout SECONDS] [--output PATH] [--force] [--max-retries N | --no-retry] [--retry-base-delay SECONDS]` — at least one of `--context-file`/`--diff-from-branch` required
- `relay repo setup <run_id> <target_repo_root> [--branch NAME] [--base-branch NAME] [--remote NAME]`
- `relay repo commit <run_id> <target_repo_root> [-m TEXT] [--also-commit PATH ...]`
- `relay repo push <run_id> <target_repo_root> [--branch NAME] [--remote NAME]`
- `relay repo pr create <run_id> <target_repo_root> [--base BRANCH] [--branch NAME] [--title TEXT]`
- `relay repo sync-dev <target_repo_root> [--main-branch main] [--dev-branch dev] [--remote NAME] --i-understand-this-rewrites-dev-history`
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

The same discipline extends to `relay repo push` and `relay repo pr create`: each is its own explicit,
remote-visible action — invoking `repo push` *is* the explicit act of publishing this run's branch;
invoking `pr create` *is* the explicit act of proposing it for review. Neither happens as a side effect of
anything else `relay` does. `relay repo sync-dev` raises that bar further, given its blast radius extends to
a shared branch other collaborators build on: invocation alone is **not** treated as sufficient confirmation
the way it is for commit/push/pr-create — it additionally requires the explicit
`--i-understand-this-rewrites-dev-history` flag, refused before any git command runs at all if that flag is
absent.

One commit per iteration in the target repo, on a dedicated branch (`relay repo setup`, default branch name
`relay/<run_id>`) so the project's default branch stays untouched until the run is reviewed and merged.
`relay repo commit` stages and commits only the files belonging to this run's `fixed` findings that git
still reports as dirty, plus any `--also-commit` files the driving agent explicitly names (also required to
be dirty) — see "Repository management" above for exactly how that set is computed. Out of scope for all of
the above, by design: merging a pull request — see "Repository management" above for why merge specifically
stays deferred while push/PR-create/dev-sync do not.
