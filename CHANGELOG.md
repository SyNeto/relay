# Changelog

## 0.9.4 — Actionable errors on `repo push`/`repo pr create` when the default branch doesn't exist

Fixes [#4](https://github.com/SyNeto/relay/issues/4): a worktree with a plain feature-branch name (this
project's own dev convention), `repo setup` skipped, and `relay repo push` failing with git's raw `src
refspec relay/<run_id> does not match any` — because push defaulted to `relay/<run_id>`, never created.

Went through the same propose → review → adjust process, and the review reframed the fix's shape, not just
its details: the failure happened *with the relevant documentation already in place* — more docs in the
same location wouldn't obviously have prevented it. The review also caught that the proposal's own optional
follow-up (a richer `repo setup` no-op message) was mis-aimed — `repo setup` was *skipped* in the actual
failure, so a message on a command that was never called couldn't have helped. The real root cause: `repo
setup`'s `relay/<run_id>` default is prescriptive ("create this if missing"); `repo push`/`repo pr
create`'s same default is descriptive ("push this, assuming it exists") — reusing one default value across
two different default semantics is a code property, not a documentation gap. `nim` hit a third persistent
429 this session during the review call; `opencode-go` picked it up.

New `_require_branch_exists()` in `engine/repo.py`, called first thing in both `push_branch` and
`create_pull_request`: raises a clear, actionable `RepoError` naming the actual current branch and
suggesting `--branch <current>` (or `relay repo setup`) when the target/head branch doesn't exist locally —
instead of a raw git/gh error surfacing verbatim. Deliberately **not** falling back to the current branch
automatically — that would be `relay` guessing which branch the caller meant, the exact thing "never guess,
fail loudly" exists to prevent. CONTRACT.md's warning is placed where an agent invoking the *failing*
commands will actually read it (`repo push`/`repo pr create` subsections), not only under `repo setup`,
which an agent following the "skip it" path would never open. `repo setup`'s own subsection is now
opinionated for relay-run worktrees specifically: name them `relay/<run_id>` (real traceability value a
short feature name doesn't have) so the naming converges and `repo setup` becomes a harmless no-op — verified
directly that `repo setup`'s implementation does nothing beyond `git checkout`/`git checkout -b`, no hidden
side state, so this claim holds. Two more edge cases named explicitly rather than left implicit: `--base-branch`
is silently inert once the worktree's branch already exists, and `repo commit`'s branch-name-agnostic design
(confirmed useful in practice — issue #2's own fix was committed on a plain, non-`relay/`-prefixed branch)
means a wrong `run_id` on the right branch would silently misattribute a commit, a known, accepted limitation.

Implemented end to end through `relay`'s own Find→Fix→Validate loop this time, not hand-written — 11
findings (the code fix, five CONTRACT.md subsections, both skill files' Start step, and two new test cases)
all went through `relay fix run` via `opencode-go`, validated individually (one CONTRACT.md diff needed a
hand rewrap after insertion broke the file's line-width convention, one skill file needed a hand fix for a
missed backtick), and committed in one shot via `relay repo commit` — the first fully findings-driven commit
in this project's own dogfooding history, no `--also-commit` needed since every changed file traced to a
finding.

## 0.9.3 — `repo commit --also-commit` for release bookkeeping

Fixes [#2](https://github.com/SyNeto/relay/issues/2): `relay repo commit` only ever staged files tied to a
run's `fixed` findings, so release bookkeeping (`pyproject.toml`, `src/relay/__init__.py`, `CHANGELOG.md`)
always fell outside its mechanized path — not hypothetical, it hit every release this project has shipped,
including finishing 0.9.1's own fix and resolving the 0.9.1/0.9.2 version-number collision, both requiring
manual `git add`/`git commit` entirely outside `relay`.

Went through the full propose → review → adjust process again: `relay spec draft` recommended an opt-in
flag; `relay review run` gave it an independent critique that changed two real design decisions —
repeatable `--also-commit PATH [--also-commit PATH ...]` instead of comma-separated (file paths can contain
commas, unlike this codebase's other comma-separated flags, which are all short enum tokens that can't),
and explicitly naming `repo pr create`'s existing `if not fixed: sys.exit(1)` guard as a real, deliberate
scope boundary rather than an implicit trap — a pure-bookkeeping run can now `repo commit` through relay
but still needs manual `gh pr create`. Along the way, `nim` hit another real, persistent 429 during the
review call — 0.9.1's retry logic correctly retried twice with backoff before failing loudly, a good live
confirmation that release actually works; fell back to `opencode-go` for the review itself.

`select_files_to_commit` and `build_commit_message` both gain an `also_files` parameter — unioned into the
existing fixed-finding intersection, never substituted for it; `also_files=None` (the default) is
byte-identical to prior behavior. `--also-commit` files must still be dirty — fails loudly naming every
non-dirty one, same "never guess" discipline as everything else in this module — but no longer need to be
tied to any finding, so a run with zero fixed findings and only `--also-commit` files now produces a valid
commit (this is the common case for pure bookkeeping). New CLI-layer path normalization + containment check
(`--also-commit ../../etc/passwd`-style paths fail loudly before any git command runs) keeps
`select_files_to_commit` itself pure set logic with no filesystem access, matching its existing contract.
Commit messages gain an `Also committed: <paths>` line, deduplicated against the per-finding list by
(normalized) file path.

Validated live doing exactly what issue #3's fix and the version collision needed by hand: a scratch repo,
zero recorded findings, `repo commit --also-commit CHANGELOG.md --also-commit VERSION.txt` — succeeded
where it would have failed before, commit contains exactly those two files. Both new failure paths
(escaping path, non-dirty `--also-commit` file) verified live, failing loudly before any git mutation.

## 0.9.2 — Skill coverage for push, PR creation, and dev sync

The fast-follow deferred at the end of 0.9.0, same split as 0.6.0/0.6.1: `SKILL.md`/`AGENT.md` shipped
untouched with 0.9.0 since nothing in them became actively false, only incomplete. Closes that gap.

Produced end to end via `relay` on itself, for the first time letting `relay fix run` generate the actual
skill-file edits rather than hand-editing them (as 0.5.1/0.6.1/0.6.0's surgical fix all were): `relay spec
draft` produced an itemized edit spec from `CONTRACT.md` + the 0.6.1 precedent as context; 8 findings (4 per
file — capabilities-table row, playbook Execute-step mention, a new Notes bullet for `repo sync-dev`, and
the stale `v0.8.0` version stamp) went through `relay fix run` via `--provider opencode-go`. One diff
(the capabilities-table row, in both files) came back accurate but nearly 2x the length of every other row
in the table — a real scannability regression in a file meant to stay terse — caught in Validate and
tightened by hand rather than re-running the model against the same prompt.

Both files: capabilities table's repo row extended to `repo setup` / `repo commit` / `repo push` / `repo pr
create` (one row, not four — they're the same local-git-plumbing-then-publish family); the "fuzzy idea"
playbook's Execute step now mentions `repo push`/`repo pr create` once a branch is ready; Notes gains a
`repo sync-dev` bullet (placed there, not in the per-run walkthrough, since it's repo-level maintenance, not
part of any single run's lifecycle); version stamp bumped `v0.8.0` → `v0.9.2` (landed after 0.9.1's retry/
backoff fix merged first — see that entry below). Frontmatter `description` fields deliberately not
broadened, same reasoning 0.6.1 already applied to `repo setup`/`repo commit`: these are sub-steps of the
already-matched find/fix/validate flow, not an independently-triggered user intent.

Also validated live, further along the lifecycle than any prior release: `repo setup` (no `--base-branch`,
since `relay`'s own repo is main-only, not git-flow), `repo commit`, `repo push`, and `repo pr create` all
run for real against `relay`'s own GitHub repo — the first time this project's own dev loop went through a
real PR instead of a direct `merge --no-ff` to `main`. `gh pr merge` stays unwrapped by design (see 0.9.0);
the PR is reviewed and merged by hand.

## 0.9.1 — Bounded retry/backoff on transient provider failures

Fixes [#3](https://github.com/SyNeto/relay/issues/3), filed after a real incident during the skill-coverage
fast-follow dogfood run (PR #1): a 429 from `nim` (confirmed provider-side — `relay quota status` showed
near-zero local usage), a manual retry that also 429'd, then a fallback to `opencode-go` that hit a plain
timeout. The closing check was silently skipped.

Went through the full propose → review → adjust process: `relay spec draft` produced an initial design,
`relay review run` gave it an independent critique, and a few things it caught changed the final shape —
most importantly, the incident's 429 was **persistent**, so the proposal's original defaults (retrying with
the same 15s wait the incident already showed failing) would have automated a strategy already proven
insufficient. This ships as **transient-blip resilience + visibility, not an incident fix** — a genuinely
down provider will still exhaust the retry budget and fail; that's provider fallback's job, deliberately
deferred as a separate, later issue.

`openai_compat_client.py` gains a typed `ProviderError` hierarchy (`ProviderTimeoutError`,
`ProviderConnectionError`, `ProviderRateLimitError`, `ProviderServerError`, `ProviderAuthError`,
`ProviderRequestError`, `ProviderUnknownError`), each with a `retryable` class attribute, and
`classify_openai_error()` mapping real openai SDK exceptions onto it — 429/5xx(500/502/503/504)/timeout/
connection are retryable, 401/4xx are not (retrying a request that can never succeed is pure waste), and an
unrecognized SDK exception type classifies as `ProviderUnknownError` (never retried — "fail loudly, never
guess" extended to error classification itself). `Retry-After` is respected when present (from both 429 and
any 5xx that carries it), clamped to `--retry-base-delay`'s `max_delay`; if a provider asks for longer than
that, `should_retry` fails fast rather than burning a retry attempt that will likely just 429 again.

New `chat_with_retry()`, sibling to `chat()` (not routed through `registry.chat_for()` — verified `cli.py`
calls `chat()` directly today, so the retry wrapper stays in the same module rather than widening the
provider-dispatch seam). `relay fix run`/`spec draft`/`review run` gain `--max-retries N` (default 2),
`--no-retry` (mutually exclusive with `--max-retries`), and `--retry-base-delay SECONDS`. Every retry
prints to stderr with a `[relay] attempt N/M` prefix — a first-attempt success prints nothing, so silence on
stderr means no retry happened. Default policy's worst-case wall clock before giving up: roughly
3 × 90s timeout + ~45s backoff ≈ 6 minutes — documented explicitly in CONTRACT.md rather than left for a
driving agent to discover by waiting.

New `tests/test_openai_compat_client.py` (no file existed for this module before): the full classification
mapping tested with **real** SDK exceptions built on real `httpx2` `Request`/`Response` objects (`httpx2` is
a genuine, independently pip-installed package required by `openai`, not something needing mocking — this
was verified directly, resolving what the initial design proposal had flagged as an open question), plus the
pure `should_retry`/`compute_backoff` decision functions. No mocking anywhere, matching the codebase's
existing convention exactly.

## 0.9.0 — Push, PR creation, and post-release dev sync

The "glue" work deferred since 0.6.0. Meaningfully higher-stakes than every prior release: this touches a
remote (push), creates artifacts visible to a whole team (PRs), and one operation — rebasing `dev` onto
`main` after a release — rewrites history other collaborators may already have pulled locally. Every new
`engine/repo.py` addition gets the same fail-loudly `RepoError` discipline as everything since 0.6.0,
extended to cover what happens when that discipline matters most — a shared branch, force-pushed.

**`relay repo setup`** gains `--base-branch NAME [--remote NAME]`: fetches `<remote>/<base-branch>` (e.g.
`origin/dev`) and branches from that ref instead of current HEAD — for git-flow-style repos where new work
should start from a known integration branch, not wherever HEAD happens to be. Ignored once the run's
branch already exists — idempotent re-invocation never resets an existing branch to a different base.
Backward compatible: omitted, behavior is unchanged.

**`relay repo push`** — plain `git push -u <remote> <branch>`. Never force; a non-fast-forward rejection
surfaces as-is.

**`relay repo pr create`** — opens a PR via `gh pr create` (no new dependency — shelled out to exactly like
`git`). Title/body mirror `repo commit`'s message shape via a new `build_pr_body`, which delegates directly
to `build_commit_message` rather than reimplementing the formatting. Requires the branch already pushed —
does not push as a side effect, so a push failure never hides behind a confusing `gh` error; push and
PR-create are each their own explicit, remote-visible action. `--base` has no relay-side default (hardcoding
`dev` would assume every target repo follows git-flow).

**`relay repo sync-dev`** — the highest-stakes addition: rebases `dev` onto `main` and force-with-lease-
pushes the result. Deliberately **not run-scoped** (no `run_id`, no `--state-dir`) — this is repo-level
maintenance after a release, not part of any single run's lifecycle. Safety sequence: refuses before any git
command runs at all without an explicit `--i-understand-this-rewrites-dev-history` flag (deliberately not
reusing this codebase's existing `--force`/`--yes`, which already mean something much lower-stakes
elsewhere); fetches both branches fresh; a new `require_branch_matches_remote` guard refuses if local `dev`
is stale or diverged from the remote *before* the operation starts (`--force-with-lease` alone only protects
against the remote moving *during* the operation); refuses on a dirty tree; on a rebase conflict, captures
the conflicting files and runs `git rebase --abort` immediately rather than ever leaving the repo mid-rebase
for `relay` to babysit; only then force-with-lease-pushes, never bare `--force`.

**`gh pr merge` is deliberately not wrapped.** Applying the same test this contract has used everywhere else
(Find/Validate never automated; Review's "no decline form"; the Evidence convention's "invoking X *is* the
explicit action"): setup/commit/push/pr-create all mechanize a decision already made earlier in the same
agent-supervised loop. Merging is different in kind — it's the point where an *external* review is supposed
to happen, and that reviewer often isn't the same agent that ran the loop. A thin pass-through would compute
nothing relay-specific and would make the one step meant to require an external check look, in an agent's
tool-call history, exactly as routine as `repo commit` does. Merge by whatever process the target repo
already uses, directly via `gh pr merge` or the GitHub UI.

`VALID_PHASES` deliberately **not** extended with a `"push"`/`"pr"` value — `phase` models per-iteration
find→fix→validate→commit progress, and push/PR-create aren't per-iteration steps.

CONTRACT.md's "Repository management" deferral language resolved for push/PR-create/dev-sync (now
documented, not deferred); narrowed to keep deferring merge specifically, with the reasoning above stated
plainly instead of lumped in as generic "later glue." Evidence convention extended to match. README's Status
paragraph updated — it previously said push/PR/merge were "still entirely manual," which this release makes
false for everything except merge.

**Skill files (`SKILL.md`/`AGENT.md`) intentionally not touched this release**, same split as 0.6.0/0.6.1:
nothing in them becomes actively wrong by this shipping (the `repo setup`/`repo commit` capabilities-table
row's "local only, no push/PR" stays true about those two specific commands), it's a coverage gap, not an
inaccuracy — deferred to a 0.9.2 fast-follow.

Validated live end to end against a real, disposable private GitHub-hosted scratch repo — the first release
where a local-only scratch repo wasn't enough, since `gh pr create` needs an actual hosted repo. Confirmed:
`repo setup --base-branch dev` branching correctly from the fetched ref; a real PR created with the expected
auto-generated title/body/base/head; `sync-dev`'s conflict-abort path against a genuine conflicting change
(named the right file, left `dev` untouched); `sync-dev`'s clean path (rebase + force-with-lease succeeded);
and, with a second clone standing in for a collaborator, that its local `dev` was left diverged from the
rewritten remote — not fast-forwardable, needing a manual reset — the concrete consequence the opt-in flag
exists to make deliberate. `push_force_with_lease`'s rejection semantics and `rebase_onto`'s conflict-abort
path are additionally unit-tested locally (two-clone bare-remote setups, a constructed same-line conflict)
— no mocking, matching this codebase's existing convention.

## 0.8.0 — `relay review run --diff-from-branch`

Makes the "closing check" pattern (already described in the skill files' fuzzy-idea playbook — re-run
`review run` after a fix loop's clean gate, asking whether the implementation still matches the spec) less
manual: `--diff-from-branch <branch> --target-repo-root <path>` gathers `git diff <branch>...HEAD` (the
merge-base diff — only what changed on the current branch, not unrelated changes made on `<branch>` after
the fork point) as one context item automatically, instead of the driving agent hand-assembling a summary
of what changed.

New `engine.repo.diff_against(repo_root, base_branch) -> str`, same fail-loudly `RepoError` discipline as
the rest of that module. `--context-file` is no longer `required=True` at the argparse level — `cmd_review_run`
now requires at least one of `--context-file`/`--diff-from-branch`, checked after both are gathered so the
error message covers either path; the two may be combined (e.g. the spec file plus the diff). Empty diffs
don't silently produce an empty context item — a note is printed and the context item reads `(no changes)`,
since `build_review_prompt.build()` already rejects context items with empty content.

CONTRACT.md's "Review" section gains the flag's mechanics and a new closing-check example under "What makes
a good subject for review"; README and both skill files' closing-check step now show the concrete command
instead of the previous vague "with a summary of what actually changed."

Validated live end to end: a scratch repo with a real feature-branch diff, `review run --diff-from-branch`
against it, confirmed the review's content specifically engaged with the diff's actual change (not just
generic advice) — plus both new CLI error paths (missing context entirely, `--diff-from-branch` without
`--target-repo-root`) fail before any provider lookup or model call.

## 0.7.0 — `relay run list`

Visibility across multiple in-flight runs: `relay run list [--state-dir PATH]` enumerates every `run_id`
with a state file under `state_dir` and prints one summary line each (iteration, phase, gate status,
`spec_file` if set) — previously the only way to find a run's `run_id` to resume it, or see what's in
flight on a machine, was remembering it.

New `engine.state.list_run_ids(state_dir=None) -> list[str]`: scans `state_dir` for `*/state.json`,
returns sorted run_ids (alphabetical sort is also chronological, given the `YYYY-MM-DD-runN` convention).
New `RunState.summary_line()`, the condensed one-line counterpart to the existing multi-line `render()`.
CONTRACT.md's State model section gains a short "Discovering runs" note; CLI surface gains the bullet.

## 0.6.1 — Skill coverage for repo management

Fast-follow explicitly deferred at the end of 0.6.0: that release's skill-file change was a surgical fix to
one already-wrong step (Commit), not the fuller treatment 0.5.1 gave `spec draft`/`review run`. This closes
it. Both `SKILL.md`/`AGENT.md` gain: a fourth row in the capabilities table for `relay repo setup`/`relay
repo commit`; a mention in the "fuzzy idea" playbook's Execute step that `--spec-file` and `repo setup`
belong there, not just in the mechanical per-iteration walkthrough where 0.6.0 left them; and `--spec-file`
now shown as an example flag on `run start` in "Per iteration," where it was previously undocumented
entirely (0.6.0 added the flag to the CLI and to CONTRACT.md but never actually showed it in either skill
file's own Start step).

Deliberately **not** done, unlike 0.5.1's precedent: broadening the frontmatter `description` fields.
`spec draft`/`review run` earned that because they're independently-triggered user intents ("draft a spec
for X", "review this decision") a driving agent needs the skill to match on. `repo setup`/`repo commit`
aren't that — they're sub-steps of the already-matched find/fix/validate flow, not a request shape a user
states on their own, so there's no new trigger phrase for the description to catch.

## 0.6.0 — Run→spec traceability and local repo management

Two independently-scoped, deliberately small features: linking a run back to the spec document that drove
it, and mechanizing the local git plumbing (branch isolation, scoped commits) around the Commit role —
pushing, pull requests, and any remote interaction stay explicitly out of scope, deferred to a later "glue"
design.

`RunState` gains an optional `spec_file` field, fixed at creation like `max_iterations`/`gate_severities`
already are: `relay run start --spec-file PATH` (not validated to exist — it's a provenance string, may
point outside the target repo entirely), rendered as a `Spec:` line in `relay run status`. New CONTRACT.md
State model row; back-compat handled so a pre-existing `state.json` without the field reloads as
`spec_file: None` rather than raising.

New `src/relay/engine/repo.py`, wrapping `git` via `subprocess` directly (no new dependency — nothing else
in this codebase uses a git library either), one `RepoError`, same fail-loudly discipline as
`apply_fix.py`. `relay repo setup <run_id> <target_repo_root> [--branch NAME]` idempotently ensures a
dedicated branch (default `relay/<run_id>`) exists and is checked out, refusing loudly if the tree is dirty
on a different branch — meant to run right after Start, before Find/Fix touch anything. `relay repo commit
<run_id> <target_repo_root> [-m TEXT]` stages and commits exactly the **intersection** of this run's
`fixed`-finding files and whatever git currently reports as dirty — deliberately not the finding's
`iteration` field, which `record_finding` stamps once at Find time and never updates, so it can't answer
"what changed this iteration" for a finding fixed in a later iteration than it was recorded. Scoping by
status + live dirty-state instead self-corrects with no new bookkeeping (already-committed files drop out
because they're no longer dirty) and is indifferent to *how* a finding was fixed — `relay fix run` or by
hand, both look identical to the algorithm. Never `git add -A`/`-u`; refuses loudly on an empty
intersection; notes any leftover dirty files outside the committed set rather than dropping them silently.

CONTRACT.md: Roles table's Commit row reworded (partially mechanized — mechanics vs. judgment split); new
"Repository management" section (placed after Review, before CLI surface); Evidence convention rewritten —
it previously said committing is "not a `relay` subcommand," which this release makes literally false, so
the section now explains why `repo commit` doesn't violate the guarantee that sentence protected (`relay`
still never commits without the driving agent's explicit action; invoking `repo commit` *is* that action,
same as invoking `fix run` is the action that applies a fix).

Skill files: `SKILL.md`/`AGENT.md`'s existing "5. Commit." step described the now-superseded plain-git-only
path as the only option — actively wrong, not just silent, once this shipped — so it got a surgical fix
pointing at `repo setup`/`repo commit` in this same release. Everything else (capabilities-table row,
playbook integration) stays a deferred fast-follow, same precedent 0.5.1 set for `spec draft`/`review run`.

## 0.5.1 — Skill coverage for spec draft and review run

Fast-follow on a gap left open by both 0.3.0 (Discover & Generate) and 0.5.0 (Review): the packaged
`SKILL.md`/`AGENT.md` templates only ever taught the driving agent about the find -> fix -> validate loop.
An agent starting cold from `relay skill install` had no way to discover `relay spec draft` or
`relay review run` existed, let alone how to sequence them — so "supervise a spec from ideation through
execution" wasn't actually something the skill enabled, regardless of what the CLI could do.

Both templates gain: a capabilities table (all three commands, stateful or not, one-liner each); a
"when the user brings you a fuzzy idea" playbook — clarify -> optional decision review (different
`--provider` recommended, advisory only) -> spec draft (reviewed before saving) -> decompose into findings
-> execute the existing loop -> optional closing review comparing the finished diff back against the
original spec for larger work; and a Notes entry naming all three response envelopes (`FIXED_EXCERPT`/
`CANNOT_FIX`, `SPEC_DRAFT`/`INSUFFICIENT_CONTEXT`, `REVIEW`) so a hard-fail on a malformed response reads as
intentional, not a bug to route around. Both skill frontmatter `description` fields were broadened to match
— skill/subagent auto-invocation is description-driven, so a driving agent asked to "get an independent
review of this decision" or "draft a spec for X" wouldn't have surfaced this skill under the old, fix-loop-
only description. No CLI or `CONTRACT.md` changes — the underlying capabilities already existed; this
closes the gap in what a driving agent starting from the skill alone can discover about them. Also caught
and fixed: both templates' own version stamps were still `v0.4.0`, one release behind.

## 0.5.0 — Review (independent critique)

Fourth capability, alongside Fix (excerpt correction) and Generate (document drafting): `relay review run
--decision TEXT --context-file PATH [--context-file PATH ...]` produces an independent critique of an
architectural or technical decision. Stateless, same as `spec draft` — no `run_id`, no `RunState`. New
strict envelope (`<<<REVIEW>>>`/`<<<END_REVIEW>>>`, `src/relay/prompts/review_system.md` +
`review_prompt.md`, `engine/build_review_prompt.py` + `engine/extract_review.py`) with one deliberate
difference from every other envelope in this project: **no decline form.** `CANNOT_FIX` and
`INSUFFICIENT_CONTEXT` exist because those tasks can legitimately fail on thin input; a review's whole job
is reasoning productively about thin or contested input, so giving it an escape hatch would just make
"insufficient context" a socially acceptable way to dodge taking a critical stance.

The prompt contract inverts `spec_system.md`'s core grounding rule on purpose. `spec_system.md` says: assert
nothing the given context doesn't support. A review that followed that rule would only restate its input —
useless. So `review_system.md` says the opposite: the review's value is surfacing risks, unstated
assumptions, and alternative framings the context does *not* already contain — but every added claim must
still show its inferential step from something stated ("if X is true, then Y follows"), never assert an
unverified fact about the world as settled truth. Inference from stated premises: encouraged. Fabricated
evidence: never acceptable. The prompt is also explicit that this is advisory input, never a verdict —
gating language ("approved", "rejected", "blocked") is out; hedged language ("recommend", "lean toward") is
in. This mirrors how `CONTRACT.md` already treats Find/Validate: the decision stays with the driving agent
or the human it serves, never with `relay` or the model provider. `CONTRACT.md` gains a "Review (independent
critique)" section documenting all of this, including a recommendation (not enforced) to run the review
through a different `--provider` than whatever produced the reasoning under review.

Validated manually before this prompt existed: a real, still-open decision (should `relay` support the
`openai-codex` provider?) was sent through the unmodified `spec draft` machinery as a stand-in, with a
deliberately different provider than the original reasoning. It worked — surfaced real risks not in the
input (account-suspension risk, a "mission drift" argument, a challenge to the stated economic premise)
rather than just restating what it was given — which is what confirmed the need for this capability. But it
was a misuse of a prompt whose core rule is backwards for this task, which is exactly why `review run` gets
its own contract instead of reusing `spec draft`'s.

## 0.4.0 — OpenCode harness adapter

Second concrete `CONTRACT.md` implementation, alongside the Claude Code skill:
`relay skill install --harness opencode` installs `src/relay/skills/opencode/AGENT.md` — an OpenCode
subagent (`mode: subagent`, `permission: {edit: deny, bash: deny}`) — to
`<project>/.opencode/agents/relay.md`. `cli.py`'s `cmd_skill_install` moved from a hardcoded
claude-code-only check to a `_HARNESS_INSTALL` dispatch table keyed by harness name; `--harness` now
accepts `claude-code` or `opencode`.

Produced end to end using relay on itself: `relay spec draft` generated the adapter content and the
`cli.py` change as a reviewed spec (0.3.0's dogfood run — see below); the two `cli.py` findings from that
spec (the hardcoded harness check, the argparse `choices` list) were then landed for real via
`relay fix run` against relay's own repo, reviewed, and marked fixed through the same Find/Fix/Validate
loop this tool runs for any other target. Two defects the dogfood run's Validate step had already caught
in the draft (a stray malformed trailing artifact, a stale hardcoded version stamp copied from the
reference file) were corrected by hand before the file was saved — see the 0.3.0 entry for what those
were; neither reached this release.

Not verified: the OpenCode frontmatter schema and `permission.bash: deny` behavior against a real
`opencode` install (no `opencode` binary available in this environment) — flagged explicitly in the
shipped file itself, not silently assumed correct.

## 0.3.0 — Phase 3

Discover & Generate: spec authorship, not just fixing an existing finding. New `relay spec draft
--request TEXT --context-file PATH [--context-file PATH ...]` — the driving agent (Discover, judgment,
never automated) gathers context from one or more files, the configured provider (Generate, mechanized)
drafts a whole document via a new strict envelope (`<<<SPEC_DRAFT>>>`/`<<<INSUFFICIENT_CONTEXT>>>`,
`src/relay/prompts/spec_system.md` + `spec_prompt.md`, `engine/build_spec_prompt.py` +
`engine/extract_spec.py` — mirrors the existing fix envelope's hard-fail-on-malformed-response discipline,
adapted for whole-document generation instead of excerpt correction). Stateless by design — no `run_id`,
no `RunState`; doesn't share Find/Fix/Validate's iterate-until-gate-clean shape. Prints to stdout by
default; `--output PATH` writes to disk, refusing to overwrite without `--force` (same precedent as
`relay skill install`). CONTRACT.md gains a "Discover & Generate" section resolving the forward-reference
the Model-response contract section has carried since Phase 1.

Dogfooded against relay's own repo: generated a spec for an OpenCode harness adapter (a second concrete
CONTRACT.md implementation, analogous to the Claude Code skill), using real research on OpenCode's actual
subagent/rules/command conventions (`.opencode/agents/`, `AGENTS.md`, `.opencode/commands/` — sourced from
opencode.ai's docs and cross-checked against the raw doc source in `sst/opencode`) as context alongside
CONTRACT.md and the existing skill. Result: the draft passed all 7 acceptance criteria structurally (real
CLI commands only, correctly preserved "Fix and Validate must never be the same actor", cited real sourced
OpenCode conventions rather than inventing syntax, correctly flagged 4 items as needing verification
against a live OpenCode install rather than asserting them as fact, described the real `cli.py` change
needed) — but Validate caught 2 real defects before it would be trusted: a stray malformed fragment
(`<![SPEC_DRAFT>>>`) leaked into the document as its last line, immediately before the real closing
envelope tag (the extractor correctly stripped the real tag; the model's own output had the artifact,
not a `relay` bug), and a hardcoded skill-version stamp copied verbatim from the reference file (`v0.1.0`)
without updating it to the current version. Neither defect was subtle to catch by reading the draft, which
is exactly the point — this dogfood run is the strongest evidence yet for the project's core discipline:
Fix/Generate and Validate must never collapse into the same, unreviewed step. The draft itself was not
committed into the tracked tree — reviewing and landing an actual OpenCode adapter is separate, later work.

## 0.2.0 — Phase 2

Pluggable model connector. `providers/nim_client.py` is gone — replaced by
`providers/openai_compat_client.py` (a protocol adapter parameterized by `ProviderConfig`, not tied to any
one provider) and `providers/registry.py` (loads bundled defaults from `providers/defaults.json`, merges in
`~/.relay/providers.json` overrides by whole provider record). Ships two working providers out of the box:
`nim` (unchanged endpoint/key/model) and `opencode-go` (OpenCode Zen, Go tier — same `openai-completions`
protocol shape, validated against the real account, including running the identical finding through both
providers and confirming both fixed it correctly).

`relay fix run` gains `--provider NAME` (default `nim` — zero flags behaves exactly like Phase 1).
`relay quota status` gains `--provider NAME` and, with no flag, now prints one report block per known
provider instead of a single implicitly-NIM block — this is a default-output-shape change, not a flag
default change, since there was no way to represent "which provider" before this release.

Rate/quota tracking is now scoped per provider (`~/.relay/rpm_log.<provider>.jsonl`, was one shared
`~/.relay/rpm_log.jsonl`) — two providers are two independent accounts with two independent quotas; the old
Phase-1 global log is superseded, not migrated.

Not done: the `openai-codex`/ChatGPT-backend protocol shape (structurally different from
`openai-completions`) — no key configured, deliberately out of scope. `registry.chat_for()` is the seam
where a second protocol would branch in.

## 0.1.0 — Phase 1

Initial release: harness-neutral contract (`CONTRACT.md`), the `relay` CLI, and the first concrete
harness implementation (a Claude Code skill via `relay skill install --harness claude-code`).

Ported from the `tiling-spec-review-loop` proof of concept (43 findings, 41 fixed, across two real runs
against a multi-file technical spec). Model connector (`providers/nim_client.py`) is a straight relocation,
not yet abstracted across providers — that's Phase 2. Business logic is still spec-review/fix specific,
not yet generalized to spec *generation* — that's Phase 3.
