import pytest

from relay.engine.extract_spec import SpecExtractionError, extract


def test_extracts_spec_draft():
    raw = "<<<SPEC_DRAFT>>>\n# A Document\n\nBody text.\n<<<END_SPEC_DRAFT>>>"

    assert extract(raw) == "# A Document\n\nBody text."


def test_insufficient_context_raises():
    raw = "<<<INSUFFICIENT_CONTEXT>>>\nneed the actual API surface, not just a description\n<<<END_INSUFFICIENT_CONTEXT>>>"

    with pytest.raises(SpecExtractionError, match="model declined to draft"):
        extract(raw)


def test_malformed_response_raises():
    raw = "here's a great spec for you: ..."

    with pytest.raises(SpecExtractionError, match="expected output contract"):
        extract(raw)
