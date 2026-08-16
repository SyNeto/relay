"""Parses the fixer model's raw response against the output contract
documented in CONTRACT.md's "Model-response contract" section.
"""
import re

FIXED_RE = re.compile(r"<<<FIXED_EXCERPT>>>\n(.*?)\n<<<END_FIXED_EXCERPT>>>", re.DOTALL)
CANNOT_RE = re.compile(r"<<<CANNOT_FIX>>>\n(.*?)\n<<<END_CANNOT_FIX>>>", re.DOTALL)


class FixExtractionError(Exception):
    pass


def extract(raw_response: str) -> str:
    """Returns the corrected excerpt text. Raises FixExtractionError for the
    CANNOT_FIX case too, and for any response that doesn't match the
    contract — the driving agent always has to look at a failure, never
    silently no-ops."""
    fixed = FIXED_RE.search(raw_response)
    if fixed:
        return fixed.group(1)
    cannot = CANNOT_RE.search(raw_response)
    if cannot:
        raise FixExtractionError(f"model declined to fix: {cannot.group(1).strip()}")
    raise FixExtractionError(
        "Response didn't match the expected output contract "
        "(no FIXED_EXCERPT or CANNOT_FIX markers found)"
    )
