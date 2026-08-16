import pytest

from relay.engine.extract_review import ReviewExtractionError, extract


def test_extracts_review():
    raw = "<<<REVIEW>>>\n## Recommendation\n\nDo X.\n<<<END_REVIEW>>>"

    assert extract(raw) == "## Recommendation\n\nDo X."


def test_malformed_response_raises():
    raw = "here's my review: ..."

    with pytest.raises(ReviewExtractionError, match="expected output contract"):
        extract(raw)
