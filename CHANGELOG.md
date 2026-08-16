# Changelog

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
