"""Thin wrapper around the NVIDIA NIM (integrate.api.nvidia.com) chat completions API.

Reads the API key from ~/.secrets/.nvidia-api-key (never hardcode it, never print it).

Uses the `openai` SDK against NIM's OpenAI-compatible endpoint. This is
today's only fixer model connector — a placeholder, not a design
commitment. Abstracting this behind a provider-neutral interface so other
OpenAI-compatible endpoints (other GLM hosts, etc.) are swappable via
config is later work; for now the engine imports this module directly.
"""
from pathlib import Path

from openai import OpenAI, OpenAIError

from relay.providers.rate_limiter import RpmLimiter

DEFAULT_KEY_PATH = Path.home() / ".secrets" / ".nvidia-api-key"
DEFAULT_MODEL = "z-ai/glm-5.2"
BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_TIMEOUT = 90.0  # seconds — fail loud instead of hanging the caller


class NimTimeoutError(Exception):
    pass


def load_api_key(key_path: Path = DEFAULT_KEY_PATH) -> str:
    key = key_path.read_text().strip()
    if not key:
        raise RuntimeError(f"NVIDIA API key file at {key_path} is empty")
    return key


def make_client(key_path: Path = DEFAULT_KEY_PATH, timeout: float = DEFAULT_TIMEOUT) -> OpenAI:
    return OpenAI(base_url=BASE_URL, api_key=load_api_key(key_path), timeout=timeout, max_retries=0)


def chat(
    prompt: str,
    run_id: str,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    client: OpenAI | None = None,
    limiter: RpmLimiter | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs,
) -> str:
    """Non-streaming chat completion. Returns the full response text.

    Throttles against the shared rpm log for `run_id` before sending, and
    records the request timestamp after — every call through here counts
    towards the same rpm budget, regardless of which process makes it.

    Hard request timeout (default 90s, max_retries=0): a stuck call raises
    NimTimeoutError instead of hanging — the caller decides whether to
    retry, not the SDK silently retrying into a longer hang.
    """
    client = client or make_client(timeout=timeout)
    limiter = limiter or RpmLimiter(run_id)
    limiter.wait_if_needed()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=False,
            **kwargs,
        )
    except OpenAIError as e:
        raise NimTimeoutError(f"NIM request failed/timed out after {timeout}s: {e}") from e

    limiter.record()
    return completion.choices[0].message.content
