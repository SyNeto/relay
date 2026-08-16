"""Parses the model's raw response against the review output contract
documented in CONTRACT.md's "Review (independent critique)" section.
"""
import re

REVIEW_RE = re.compile(r"<<<REVIEW>>>\n(.*?)\n<<<END_REVIEW>>>", re.DOTALL)


class ReviewExtractionError(Exception):
    pass


def extract(raw_response: str) -> str:
    """Returns the review text. Raises ReviewExtractionError for any
    response that doesn't match the contract — there is no decline form for
    this task (see review_system.md), so anything not matching REVIEW is a
    hard error, same discipline as the fix/spec envelopes."""
    match = REVIEW_RE.search(raw_response)
    if match:
        return match.group(1)
    raise ReviewExtractionError(
        "Response didn't match the expected output contract (no REVIEW markers found)"
    )
