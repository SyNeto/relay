# Design Proposal: Spec-File trailer references ephemeral paths in real PRs/commits (Issue #5)

## Problem

`relay run start --spec-file PATH` stores whatever path is given verbatim — no existence or reachability validation, by design (CONTRACT.md: "not validated to exist or be readable"). `relay repo commit` and `relay repo pr create` then surface that path as a `Spec-File:` trailer in the commit message and PR body. In the dogfood run that found this (PR #1), the spec was drafted to a session-specific path under `/tmp/...` via `relay spec draft --output`, so the resulting real PR carries a `Spec-File:` trailer pointing at a path that no longer exists and was never accessible to anyone but the creating session.

For throwaway/local runs, this doesn't matter. For a real, permanent, team-visible artifact (a merged commit's history, an open PR), a dangling reference is a misleading paper cut — it looks like a real pointer but resolves to nothing for anyone else.

---

## 1. Documentation-only vs. code-level nudge

### The issue #4 precedent

Issue #4's postmortem established that documentation-only fixes have proven insufficient when the failure happens under real usage conditions: the failing documentation was already correct and already present when the failure occurred. The real fix needed a small code-level improvement (an actionable error) alongside the docs.

This issue's evidence fits that pattern. The failure occurred during dogfooding — the agent driving the run was familiar with relay, yet drafted a spec to `/tmp/...` and referenced it via `--spec-file` without moving it into the target repo. A CONTRACT.md convention saying "commit specs into the target repo before referencing them" would be correct and useful, but there is no reason to expect it would be reliably followed at the moment a freshly-drafted spec path is in context and `run start --spec-file` is being invoked. The #4 precedent argues for a code-level nudge here too.

### The proposed check: cheap, non-invasive, warning-only

The exact shape of the failure is: `spec_file` points at a path that is NOT inside `target_repo_root` and NOT git-tracked there. That is detectable at publish time with a read-only check — no model call, no network, no state change:

1. Load run state (already done by `repo commit`/`pr create` to get `spec_file` for the trailer).
2. If `spec_file` is null: no warning.
3. Check whether the path is inside `target_repo_root` and git-tracked there.
4. If not: emit a warning to stderr. Command proceeds normally.

This respects `--spec-file`'s explicit contract (still "not validated to exist or be readable" — unchanged) and this issue's own constraint that no option proposes making it a hard failure. A warning surfaces the signal without blocking legitimate throwaway runs.

### The `target_repo_root` timing problem

The initial framing — "could `relay run start --spec-file PATH` warn if PATH is not inside `target_repo_root`?" — runs into a structural constraint: **`relay run start` does not take a `target_repo_root` argument.** Per the CLI surface and state model in CONTRACT.md, `target_repo_root` is first introduced at `repo setup` time and is a parameter of Find/Fix/Commit/Push/PR-create commands, not of `run start`. A run's state is deliberately separate from any target repo until Find/Fix touch it. Adding `target_repo_root` to `run start` would violate this separation and change the contract — too invasive for this issue.

**The check must move to a command that already has `target_repo_root`.** The candidates:

| Command | Has `target_repo_root`? | Already loads `spec_file`? | Right moment? |
|---|---|---|---|
| `repo setup` | Yes | No (loads run state for branch name only) | Too early — agent may intend to move the spec between setup and commit |
| `finding verify` | Yes | No | Mid-loop, not at publishing boundary |
| `fix run` | Yes | No | Mid-loop, not at publishing boundary |
| `repo commit` | Yes | Yes (for the `Spec-File:` trailer) | **Yes — the exact moment the trailer is written into a permanent artifact** |
| `repo push` | Yes | Yes (for branch name) | Too late — commit already made, trailer already embedded |
| `repo pr create` | Yes | Yes (for the PR body trailer) | **Yes — the exact moment the trailer becomes publicly visible** |

**Recommendation: the warning fires at `relay repo commit` and `relay repo pr create` time.** `repo commit` is the primary checkpoint — it's where the trailer is first written and where the agent can act on the warning immediately (add the spec to `--also-commit`, or move it into the repo before committing). `repo pr create` is a secondary checkpoint — by then the commit is already made, but it's the last chance to notice before the trailer becomes publicly visible in the PR body.

### False positive analysis

| Scenario | Warning fires? | Acceptable? |
|---|---|---|
| Throwaway/local run, spec in `/tmp/...`, agent uses `repo commit` | Yes | Yes — non-blocking, agent ignores it |
| Spec in a separate shared repo (absolute path outside `target_repo_root`) | Yes | Yes — technically correct, the trailer IS a dangling reference within the target repo's context |
| Spec inside `target_repo_root`, tracked by git | No | N/A |
| Spec inside `target_repo_root`, not yet tracked, named in `--also-commit` | Suppressed (open question — see §2) | N/A |
| Spec inside `target_repo_root`, not tracked, not in `--also-commit` | Yes | Yes — agent should commit it or acknowledge the warning |
| `spec_file` not set | No | N/A |

The false positive rate is low and, crucially, non-blocking. This fits relay's existing discipline seen throughout CONTRACT.md: "never guess" (the warning doesn't prescribe a fix, only surfaces the signal), "never silently swallow a real signal" (the dangling reference is a real signal that currently passes through silently). The warning surfaces it; the agent decides what to do.

---

## 2. Warning design

### When it fires

At `relay repo commit <run_id> <target_repo_root>` and `relay repo pr create <run_id> <target_repo_root>`, after loading run state and before generating the commit message / PR body:

1. If `spec_file` is null or empty: no warning.
2. Resolve `spec_file` against `target_repo_root`:
   - If the path is absolute: check whether it resolves inside `target_repo_root` (both resolved to absolute form).
   - If the path is relative: resolve relative to `target_repo_root` — the most useful interpretation, since anyone reading the `Spec-File:` trailer in the repo would interpret a relative path that way.
3. If the resolved path is inside `target_repo_root`: check whether it is git-tracked there. If tracked: no warning. If not tracked: warn.
4. If the resolved path is outside `target_repo_root`: warn.
5. **Open question:** At `repo commit` time, if the spec file path is named in `--also-commit`, suppress the warning (it's about to be committed and will be tracked after this commit). This requires comparing `spec_file` against the `--also-commit` list. It avoids a false positive for the common fix-up flow, but adds implementation complexity. Flagged as a judgment call for the caller.

### Exact wording

**At `relay repo commit`:**

```
[relay] warning: Spec-File path '<path>' is not git-tracked in the target repo — the Spec-File: trailer in this commit will be a dangling reference for other readers. To fix: commit the spec into the target repo (e.g. via --also-commit <path>) before this commit, or move it under the repo first. (Warning only; --spec-file does not validate existence by design.)
```

**At `relay repo pr create`:**

```
[relay] warning: Spec-File path '<path>' is not git-tracked in the target repo — the Spec-File: trailer in this PR will be a dangling reference for other readers. If the spec was committed to the target repo in a prior commit, this warning can be ignored; otherwise, the trailer in the PR body and commit history points at a path no one else can resolve. (Warning only; --spec-file does not validate existence by design.)
```

Both warnings go to stderr, matching relay's existing convention for `[relay]`-prefixed diagnostic output (as described in CONTRACT.md's retry-notice behavior: "Every retry prints to stderr with a `[relay]` prefix"). Neither blocks the command — the commit/PR is still created, and exit code is non-zero only if the commit/PR itself fails for other reasons.

### Open questions (warning design)

- **`--also-commit` suppression at `repo commit` time:** Should the warning be suppressed when the spec file is named in `--also-commit`? Recommended yes (it's about to be tracked), but this adds a comparison step against the `--also-commit` list. Flagged as a judgment call.
- **Warning at both `repo commit` and `repo pr create`, or just `repo commit`?** Both provides defense in depth (different decision points: local commit vs. public PR). But if the agent ignored the warning at `repo commit`, the `pr create` warning is redundant noise. Flagged as a judgment call — the caller may prefer one or both.
- **Relative-path resolution:** Resolving relative `spec_file` paths against `target_repo_root` is the most useful interpretation for the check, but the verbatim path in the trailer is ambiguous (it was originally relative to the CWD at `run start` time). The warning is a heuristic nudge, not a precise validator, so this ambiguity is acceptable — but the caller should be aware of it.
- **Implementation verification:** The exact internal mechanism for checking git-tracked status (e.g., which git command `relay` would shell out to) is not specified here, as that depends on implementation details in `engine/repo.py` not provided in this context. The design specifies the check's semantics (is the path inside `target_repo_root` and tracked by git there), not the specific git invocation.

---

## 3. CONTRACT.md wording (durable-spec-location convention)

Regardless of the code-level nudge, the documentation convention should be added — it tells agents proactively what to do, while the warning catches the case where the convention wasn't followed. The following are proposed additions to specific CONTRACT.md sections.

### Addition A: In "Discover & Generate (spec authorship)", after the `--output PATH` paragraph

> **Where durable specs should live.** `--output PATH` writes the draft wherever the agent points it — including ephemeral scratch directories, which is fine for a throwaway draft. But if the resulting spec is intended to back a real, published run (one that will be committed, pushed, or proposed as a PR via `relay repo commit`/`repo pr create`), the spec should be committed into the target repo before `relay run start --spec-file` references it — for example under `docs/specs/`. Otherwise the `Spec-File:` trailer in the published commit/PR will point at a path no one else can resolve. `relay repo commit` and `relay repo pr create` emit a warning (not an error) when `spec_file` is set but not git-tracked in the target repo, to surface this at publish time.

### Addition B: In the state model table, `spec_file` row, appended to existing notes

> `repo commit` and `repo pr create` warn (stderr, non-blocking) if this path is not git-tracked in `target_repo_root` — the trailer would be a dangling reference for other readers. The field is still not validated to exist or be readable; the warning is a signal, not a gate.

### Addition C: In "Repository management", `repo commit` description, after the `Spec-File: <path>` trailer description

> If `spec_file` is set but the path is not git-tracked in `target_repo_root` (either outside the repo or inside but not yet committed), `repo commit` emits a warning to stderr naming the path and suggesting `--also-commit` as the fix. This is non-blocking — the commit still proceeds — and does not change `--spec-file`'s contract (no existence validation). The warning surfaces the specific failure mode of a `Spec-File:` trailer that will be a dangling reference for other readers.

### Addition D (open question): In "Repository management", `repo pr create` description

A parallel note for `repo pr create` could be added, or the state-model note (Addition B) plus the `repo commit` note (Addition C) may be sufficient. Flagged for the caller — whether `pr create` gets its own paragraph depends on whether the warning fires at `pr create` time at all (see open question in §2).

---

## 4. Skill files: 'Draft the spec' step update

The issue reports that the fuzzy-idea playbook's 'Draft the spec' step currently tells a driving agent to draft to an `--output` path without guidance on where that path should durably live if the run is headed toward a real publish.

**I do not have the actual skill file content in the context provided for this design proposal, so I cannot verify the current wording or propose exact edits.** The skill files should be reviewed directly (likely in the harness-specific skill directory installed by `relay skill install`) before making changes.

Based on the issue's description of the current step, the 'Draft the spec' step should gain guidance along these lines:

> When drafting a spec that will back a real, published run, write it to a durable location inside the target repo (e.g. `docs/specs/<name>.md`) rather than a session-specific scratch or temp directory. If you draft to a scratch path first, move the file into the target repo before invoking `relay run start --spec-file` with its path. A spec left at an ephemeral path (e.g. under `/tmp/`) will produce a `Spec-File:` trailer in the published commit/PR that points at a path no one else can resolve.

**Open questions (skill files):**
- Whether this guidance belongs only in the fuzzy-idea playbook or should also be added to any other skill files that reference `relay spec draft --output` or `relay run start --spec-file`. This requires reviewing the full set of installed skill files, which is not available in the provided context.
- The exact insertion point and surrounding step structure depend on the playbook's current shape, which is unverified.

---

## 5. Test plan

Since a code-level change is proposed (the warning at `repo commit`/`pr create` time), the following test cases should be added:

1. **Warning fires for absolute path outside `target_repo_root`:** Run state has `spec_file` set to an absolute path outside the target repo. Invoke `repo commit`. Assert: warning message appears on stderr, commit still succeeds (exit 0 assuming no other failure).

2. **Warning fires for path inside `target_repo_root` but not tracked:** Run state has `spec_file` set to a path inside the target repo that exists on disk but is not git-tracked. Invoke `repo commit`. Assert: warning on stderr, commit succeeds.

3. **No warning when `spec_file` is null:** Run state has no `spec_file`. Invoke `repo commit`. Assert: no warning on stderr.

4. **No warning when `spec_file` is tracked:** Run state has `spec_file` set to a path inside the target repo that is git-tracked. Invoke `repo commit`. Assert: no warning on stderr.

5. **No warning when `spec_file` is in `--also-commit` (if suppression is implemented):** Run state has `spec_file` set to a path inside the target repo, not tracked, but named via `--also-commit`. Invoke `repo commit`. Assert: no warning on stderr. *(Skip this test case if the `--also-commit` suppression open question in §2 is resolved as "no suppression".)*

6. **Warning fires at `repo pr create` for untracked `spec_file` (if `pr create` warning is implemented):** Run state has `spec_file` set to an absolute path outside the target repo. Invoke `repo pr create` (after push). Assert: warning on stderr, PR still created. *(Skip if the "both vs. just `repo commit`" open question in §2 is resolved as "`repo commit` only".)*

7. **Warning goes to stderr, not stdout:** For any case where the warning fires, assert the warning text appears on stderr only, not stdout. Matches relay's existing convention for `[relay]`-prefixed diagnostic output.

8. **Warning is non-blocking:** For any case where the warning fires, assert the command exits 0 (assuming no other failure) and the commit/PR is created.

**What not to test:** The `--spec-file` contract itself (no existence validation) is unchanged — no new tests for existence validation should be added, because the behavior is intentionally the same. The warning is an additional signal, not a gate.

---

## Summary of recommendations

| Question | Recommendation | Confidence |
|---|---|---|
| Documentation-only or code-level nudge? | **Code-level nudge (warning)** at `repo commit`/`pr create` time, plus documentation convention | High — the #4 precedent directly applies; the check is cheap, non-invasive, and non-blocking |
| Where does the check fire? | `relay repo commit` (primary) and `relay repo pr create` (secondary) | High — these are the only points where `target_repo_root` is available and the trailer is being written |
| Does `run start` get the check? | **No** — it lacks `target_repo_root`, and adding it would violate the state-model separation | High — structural constraint, not a judgment call |
| CONTRACT.md convention added? | **Yes** — three additions: "Discover & Generate" section, state model notes, "Repository management" `repo commit` description | High — valuable regardless of the code change |
| Skill files updated? | **Yes, pending verification** — the 'Draft the spec' step needs durable-location guidance | Medium — actual skill file content not available in provided context |
| `--also-commit` suppression? | Recommended yes, but flagged as open question | Medium — adds implementation complexity for a real but narrow false-positive case |
| Warning at both `repo commit` and `pr create`? | Recommended both, but flagged as open question | Medium — `pr create` warning may be redundant if `commit` warning was seen and ignored |
| Relative-path resolution semantics | Resolve relative to `target_repo_root` for the check | Medium — most useful interpretation, but the verbatim trailer path is ambiguous; acceptable for a heuristic warning |