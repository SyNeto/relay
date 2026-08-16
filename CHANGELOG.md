# Changelog

## 0.1.0 — Phase 1

Initial release: harness-neutral contract (`CONTRACT.md`), the `relay` CLI, and the first concrete
harness implementation (a Claude Code skill via `relay skill install --harness claude-code`).

Ported from the `tiling-spec-review-loop` proof of concept (43 findings, 41 fixed, across two real runs
against a multi-file technical spec). Model connector (`providers/nim_client.py`) is a straight relocation,
not yet abstracted across providers — that's Phase 2. Business logic is still spec-review/fix specific,
not yet generalized to spec *generation* — that's Phase 3.
