"""Parses the model's raw response against the spec-draft output contract
documented in CONTRACT.md's "Discover & Generate" section.
"""
import re

DRAFT_RE = re.compile(r"<<<SPEC_DRAFT>>>\n(.*?)\n<<<END_SPEC_DRAFT>>>", re.DOTALL)
INSUFFICIENT_RE = re.compile(
    r"<<<INSUFFICIENT_CONTEXT>>>\n(.*?)\n<<<END_INSUFFICIENT_CONTEXT>>>", re.DOTALL
)


class SpecExtractionError(Exception):
    pass


def extract(raw_response: str) -> str:
    """Returns the drafted document text. Raises SpecExtractionError for the
    INSUFFICIENT_CONTEXT case too, and for any response that doesn't match
    the contract — the driving agent always has to look at a failure, never
    silently no-ops."""
    draft = DRAFT_RE.search(raw_response)
    if draft:
        return draft.group(1)
    insufficient = INSUFFICIENT_RE.search(raw_response)
    if insufficient:
        raise SpecExtractionError(f"model declined to draft: {insufficient.group(1).strip()}")
    raise SpecExtractionError(
        "Response didn't match the expected output contract "
        "(no SPEC_DRAFT or INSUFFICIENT_CONTEXT markers found)"
    )
