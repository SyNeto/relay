"""Locates a finding's target_excerpt in its file and replaces it with the
fixed text extracted from the model's response. Fails loudly instead of
guessing if the excerpt isn't found exactly once — a shifted or
already-changed excerpt should stop the loop, not silently corrupt the file.
"""
from pathlib import Path


class ApplyFixError(Exception):
    pass


def apply_fix(file_path: Path, target_excerpt: str, fixed_text: str) -> None:
    original = file_path.read_text()
    count = original.count(target_excerpt)
    if count == 0:
        raise ApplyFixError(
            f"target_excerpt not found verbatim in {file_path} — it may have "
            f"shifted since the finding was recorded; re-check by hand"
        )
    if count > 1:
        raise ApplyFixError(
            f"target_excerpt appears {count} times in {file_path} — ambiguous, "
            f"needs a more specific excerpt to disambiguate"
        )
    file_path.write_text(original.replace(target_excerpt, fixed_text, 1))
