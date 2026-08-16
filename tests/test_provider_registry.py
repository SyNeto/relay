import json
from pathlib import Path

import pytest

from relay.providers import registry


def test_defaults_load_both_providers(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path))  # no providers.json here -> defaults only

    configs = registry.load_provider_configs()

    assert set(configs) == {"nim", "opencode-go"}
    assert configs["nim"].base_url == "https://integrate.api.nvidia.com/v1"
    assert configs["opencode-go"].base_url == "https://opencode.ai/zen/go/v1"


def test_override_file_replaces_whole_record(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path))
    (tmp_path / "providers.json").write_text(json.dumps({
        "nim": {
            "api": "openai-completions",
            "base_url": "https://example.test/v1",
            "api_key_path": "~/.secrets/.other-key",
            "default_model": "some-other-model",
        }
    }))

    configs = registry.load_provider_configs()

    assert configs["nim"].base_url == "https://example.test/v1"
    assert configs["nim"].default_model == "some-other-model"


def test_override_file_adds_new_provider(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path))
    (tmp_path / "providers.json").write_text(json.dumps({
        "custom": {
            "api": "openai-completions",
            "base_url": "https://custom.test/v1",
            "api_key_path": "~/.secrets/.custom-key",
            "default_model": "custom-model",
        }
    }))

    configs = registry.load_provider_configs()

    assert set(configs) == {"nim", "opencode-go", "custom"}
    assert configs["nim"].base_url == "https://integrate.api.nvidia.com/v1"  # untouched


def test_unknown_provider_raises_clear_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path))

    with pytest.raises(registry.UnknownProviderError, match="nope"):
        registry.get_provider("nope")
