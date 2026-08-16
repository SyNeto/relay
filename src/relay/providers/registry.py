"""Provider configuration: which fixer-model backends `relay fix run` can
target, and how to reach them. See CONTRACT.md's "Model connector" section.

Bundled defaults (providers/defaults.json) ship two working
openai-completions-compatible providers out of the box. A user override
file at $RELAY_HOME/providers.json can replace a provider's whole record
or add new ones by name — no code changes needed to add a third
openai-completions-shaped provider.

A structurally different protocol (e.g. a provider that isn't a plain
OpenAI chat-completions endpoint) is not something this module builds for
yet — chat_for() is the one seam where that dispatch would be added later.
"""
import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


class UnknownProviderError(Exception):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api: str
    base_url: str
    api_key_path: Path
    default_model: str


def _relay_home() -> Path:
    return Path(os.environ.get("RELAY_HOME", Path.home() / ".relay"))


def _override_path() -> Path:
    return _relay_home() / "providers.json"


def _to_config(name: str, record: dict) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        api=record["api"],
        base_url=record["base_url"],
        api_key_path=Path(record["api_key_path"]).expanduser(),
        default_model=record["default_model"],
    )


def load_provider_configs() -> dict[str, ProviderConfig]:
    defaults = json.loads(resources.files("relay.providers").joinpath("defaults.json").read_text())

    override_path = _override_path()
    overrides = json.loads(override_path.read_text()) if override_path.exists() else {}

    merged = {**defaults, **overrides}  # whole-record replacement by provider name, not a field merge
    return {name: _to_config(name, record) for name, record in merged.items()}


def get_provider(name: str) -> ProviderConfig:
    configs = load_provider_configs()
    if name not in configs:
        known = ", ".join(sorted(configs))
        raise UnknownProviderError(f"unknown provider {name!r} — known providers: {known}")
    return configs[name]


def chat_for(provider_name: str, prompt: str, run_id: str, **kwargs) -> str:
    """Dispatch seam: today every known provider is openai-completions-shaped,
    so this always calls openai_compat_client.chat(). A provider with a
    different `api` value would branch here."""
    from relay.providers import openai_compat_client

    config = get_provider(provider_name)
    return openai_compat_client.chat(prompt, run_id, config, **kwargs)
