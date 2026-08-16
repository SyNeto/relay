# Changelog

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
