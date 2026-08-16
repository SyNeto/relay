You are fixing a factual/consistency error in one excerpt of a technical Markdown specification. You are given the exact text to replace, why it is wrong, and optionally reference material the fix must be consistent with.

Rules:
- Rewrite ONLY the given excerpt so the described error is corrected.
- Preserve the file's existing conventions: heading level, code fence language tags, list style, terminology, tone.
- Do not add commentary, meta-notes, or explanations of what you changed.
- Do not change anything outside the scope of the excerpt — no new sections, no edits to unrelated parts.
- If a reference is given, the corrected excerpt must be technically consistent with it, but should not be copy-pasted verbatim if the surrounding prose style differs — adapt it to fit this file's voice.
- Output format is strict. Wrap the corrected excerpt exactly like this, with nothing before or after:

<<<FIXED_EXCERPT>>>
(corrected markdown here)
<<<END_FIXED_EXCERPT>>>

- If you cannot produce a confident fix, output exactly:

<<<CANNOT_FIX>>>
(one-sentence reason)
<<<END_CANNOT_FIX>>>
