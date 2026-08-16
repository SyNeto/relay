import pytest

from relay.engine.extract_fix import FixExtractionError, extract


def test_extracts_fixed_excerpt():
    raw = "<<<FIXED_EXCERPT>>>\nsome **fixed** markdown\nwith a second line\n<<<END_FIXED_EXCERPT>>>"

    assert extract(raw) == "some **fixed** markdown\nwith a second line"


def test_cannot_fix_raises():
    raw = "<<<CANNOT_FIX>>>\nnot enough context to know the right value\n<<<END_CANNOT_FIX>>>"

    with pytest.raises(FixExtractionError, match="model declined to fix"):
        extract(raw)


def test_malformed_response_raises():
    raw = "sorry, here's your fix: ..."

    with pytest.raises(FixExtractionError, match="expected output contract"):
        extract(raw)
