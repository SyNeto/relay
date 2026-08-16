# Changelog

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
