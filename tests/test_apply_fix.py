from pathlib import Path

import pytest

from relay.engine.apply_fix import ApplyFixError, apply_fix


def test_single_match_replaces(tmp_path: Path):
    f = tmp_path / "sample.md"
    f.write_text("before\nOLD TEXT\nafter\n")

    apply_fix(f, "OLD TEXT", "NEW TEXT")

    assert f.read_text() == "before\nNEW TEXT\nafter\n"


def test_missing_excerpt_raises(tmp_path: Path):
    f = tmp_path / "sample.md"
    f.write_text("before\nNEW TEXT\nafter\n")

    with pytest.raises(ApplyFixError):
        apply_fix(f, "OLD TEXT", "NEW TEXT")


def test_ambiguous_excerpt_raises(tmp_path: Path):
    f = tmp_path / "sample.md"
    f.write_text("dup\ndup\n")

    with pytest.raises(ApplyFixError):
        apply_fix(f, "dup", "x")
