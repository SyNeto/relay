import pytest

from relay.engine.build_spec_prompt import build


def test_renders_system_and_user():
    spec_request = {
        "change_request": "add a widget",
        "context": [{"source": "a.md", "content": "existing content"}],
    }

    rendered = build(spec_request)

    assert "add a widget" in rendered["user"]
    assert "### Source: a.md" in rendered["user"]
    assert "existing content" in rendered["user"]
    assert rendered["system"]  # non-empty


def test_missing_change_request_raises():
    with pytest.raises(ValueError, match="change_request"):
        build({"context": [{"source": "a.md", "content": "x"}]})


def test_empty_context_raises():
    with pytest.raises(ValueError, match="context"):
        build({"change_request": "x", "context": []})


def test_context_item_missing_source_raises():
    with pytest.raises(ValueError, match="source"):
        build({"change_request": "x", "context": [{"content": "no source"}]})


def test_context_item_missing_content_raises():
    with pytest.raises(ValueError, match="content"):
        build({"change_request": "x", "context": [{"source": "a.md"}]})
