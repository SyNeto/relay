from pathlib import Path

from relay.providers.rate_limiter import RpmLimiter, default_log_path


def test_providers_have_isolated_logs(tmp_path: Path):
    nim = RpmLimiter("r1", provider="nim", path=tmp_path / "nim.jsonl")
    other = RpmLimiter("r1", provider="opencode-go", path=tmp_path / "opencode-go.jsonl")

    nim.record()
    nim.record()
    other.record()

    assert nim.current_rpm() == 2
    assert other.current_rpm() == 1


def test_default_log_path_includes_provider_name(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path))

    nim_path = default_log_path("nim")
    other_path = default_log_path("opencode-go")

    assert nim_path != other_path
    assert "nim" in nim_path.name
    assert "opencode-go" in other_path.name
