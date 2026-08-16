import pytest

from relay.engine.build_review_prompt import build


def test_renders_system_and_user():
    review_request = {
        "decision": "should we adopt X?",
        "context": [{"source": "a.md", "content": "existing reasoning"}],
    }

    rendered = build(review_request)

    assert "should we adopt X?" in rendered["user"]
    assert "### Source: a.md" in rendered["user"]
    assert "existing reasoning" in rendered["user"]
    assert rendered["system"]  # non-empty


def test_missing_decision_raises():
    with pytest.raises(ValueError, match="decision"):
        build({"context": [{"source": "a.md", "content": "x"}]})


def test_empty_context_raises():
    with pytest.raises(ValueError, match="context"):
        build({"decision": "x", "context": []})


def test_context_item_missing_source_raises():
    with pytest.raises(ValueError, match="source"):
        build({"decision": "x", "context": [{"content": "no source"}]})


def test_context_item_missing_content_raises():
    with pytest.raises(ValueError, match="content"):
        build({"decision": "x", "context": [{"source": "a.md"}]})
