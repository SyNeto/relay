"""Protocol adapter for openai-completions-shaped providers (see
ProviderConfig.api in registry.py). Both providers configured today (nim,
opencode-go) use this exact shape: the `openai` SDK against an
OpenAI-compatible endpoint, plain bearer API key auth.

Not provider-specific — reads whichever ProviderConfig it's given. Never
hardcode a base_url, key path, or model name in this module; those live in
providers/defaults.json (or a user's ~/.relay/providers.json override).
"""
from pathlib import Path

from openai import OpenAI, OpenAIError

from relay.providers.rate_limiter import RpmLimiter
from relay.providers.registry import ProviderConfig

DEFAULT_TIMEOUT = 90.0  # seconds — fail loud instead of hanging the caller


class ProviderTimeoutError(Exception):
    pass


def load_api_key(config: ProviderConfig) -> str:
    key = config.api_key_path.read_text().strip()
    if not key:
        raise RuntimeError(f"API key file at {config.api_key_path} is empty")
    return key


def make_client(config: ProviderConfig, timeout: float = DEFAULT_TIMEOUT) -> OpenAI:
    return OpenAI(base_url=config.base_url, api_key=load_api_key(config), timeout=timeout, max_retries=0)


def chat(
    prompt: str,
    run_id: str,
    config: ProviderConfig,
    system: str | None = None,
    model: str | None = None,
    client: OpenAI | None = None,
    limiter: RpmLimiter | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs,
) -> str:
    """Non-streaming chat completion against `config`'s endpoint. Returns the
    full response text.

    Throttles against the rpm log scoped to `config.name` before sending,
    and records the request timestamp after — every call through here for
    this provider counts towards the same rpm/quota budget, regardless of
    which process makes it. Different providers never share a log (see
    rate_limiter.py) — their quotas are independent accounts.

    Hard request timeout (default 90s, max_retries=0): a stuck call raises
    ProviderTimeoutError instead of hanging — the caller decides whether to
    retry, not the SDK silently retrying into a longer hang.
    """
    client = client or make_client(config, timeout=timeout)
    limiter = limiter or RpmLimiter(run_id, provider=config.name)
    limiter.wait_if_needed()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        completion = client.chat.completions.create(
            model=model or config.default_model,
            messages=messages,
            stream=False,
            **kwargs,
        )
    except OpenAIError as e:
        raise ProviderTimeoutError(
            f"{config.name} request failed/timed out after {timeout}s: {e}"
        ) from e

    limiter.record()
    return completion.choices[0].message.content
