"""Protocol adapter for openai-completions-shaped providers (see
ProviderConfig.api in registry.py). Both providers configured today (nim,
opencode-go) use this exact shape: the `openai` SDK against an
OpenAI-compatible endpoint, plain bearer API key auth.

Not provider-specific — reads whichever ProviderConfig it's given. Never
hardcode a base_url, key path, or model name in this module; those live in
providers/defaults.json (or a user's ~/.relay/providers.json override).

Bounded retry (chat_with_retry) is transient-blip resilience, not a fix for
a persistent provider outage -- see CONTRACT.md's "Model connector" section.
It never reintroduces silent SDK-level retry (the client is still
constructed with max_retries=0); every retry is explicit, bounded, and
printed to stderr so a driving agent can tell a retried-then-succeeded call
apart from a first-attempt success.
"""
import random
import sys
import time
from pathlib import Path

import openai as openai_module
from openai import OpenAI, OpenAIError

from relay.providers.rate_limiter import RpmLimiter
from relay.providers.registry import ProviderConfig

DEFAULT_TIMEOUT = 90.0  # seconds — fail loud instead of hanging the caller
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_DELAY = 120.0  # seconds — hard cap on any single wait


class ProviderError(Exception):
    """Base for every provider-call failure. retryable=False by default --
    an unrecognized failure is never assumed transient."""

    retryable: bool = False

    def __init__(self, provider_name: str, message: str, *, status: int | None = None,
                 retry_after: float | None = None):
        self.provider_name = provider_name
        self.status = status
        self.retry_after = retry_after
        super().__init__(message)


class ProviderTimeoutError(ProviderError):
    """Request timed out -- no response within the configured timeout."""

    retryable = True


class ProviderConnectionError(ProviderError):
    """Network-level failure (connection refused, DNS, TLS) -- not a timeout."""

    retryable = True


class ProviderRateLimitError(ProviderError):
    """429 -- provider-side throttle."""

    retryable = True


class ProviderServerError(ProviderError):
    """500/502/503/504 -- transient server-side failure."""

    retryable = True


class ProviderAuthError(ProviderError):
    """401 -- authentication failed. Never succeeds on retry."""

    retryable = False


class ProviderRequestError(ProviderError):
    """400/403/404/422 -- the request itself is wrong. Retrying is pure waste."""

    retryable = False


class ProviderUnknownError(ProviderError):
    """An OpenAIError subtype this module doesn't recognize, or a 5xx status
    (501/505/511) that means "this will never work" rather than "try again."
    Distinct from a generic ProviderError so "we couldn't classify this" is
    never silently indistinguishable from any other base-class catch."""

    retryable = False


_RETRYABLE_5XX = (500, 502, 503, 504)


def _parse_retry_after(value: str | None) -> float | None:
    """Integer seconds only -- HTTP-date format is not handled (not worth
    the complexity for providers that, in practice, send integer seconds).
    Returns None if missing or not a plain integer."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def classify_openai_error(exc: OpenAIError, config: ProviderConfig) -> ProviderError:
    """Maps an openai SDK exception to relay's own typed ProviderError
    hierarchy, so callers can decide retryability without depending on SDK
    internals. Unrecognized subtypes classify as ProviderUnknownError
    (never retryable) -- consistent with "fail loudly, never guess."""
    name = config.name
    message = str(exc)

    if isinstance(exc, openai_module.APITimeoutError):
        return ProviderTimeoutError(name, message)
    if isinstance(exc, openai_module.APIConnectionError):
        return ProviderConnectionError(name, message)
    if isinstance(exc, openai_module.RateLimitError):
        retry_after = _parse_retry_after(exc.response.headers.get("retry-after"))
        return ProviderRateLimitError(name, message, status=exc.response.status_code, retry_after=retry_after)
    if isinstance(exc, openai_module.APIStatusError):
        status = exc.response.status_code
        retry_after = _parse_retry_after(exc.response.headers.get("retry-after"))
        if status == 401:
            return ProviderAuthError(name, message, status=status)
        if status in _RETRYABLE_5XX:
            return ProviderServerError(name, message, status=status, retry_after=retry_after)
        if 400 <= status < 500:
            return ProviderRequestError(name, message, status=status)
        return ProviderUnknownError(name, message, status=status)
    return ProviderUnknownError(name, message)


_BASE_DELAY_BY_CLASS = {
    ProviderConnectionError: 5.0,
    ProviderServerError: 5.0,
    ProviderTimeoutError: 10.0,
    ProviderRateLimitError: 20.0,
}


def should_retry(error: ProviderError, attempt: int, max_retries: int,
                  max_delay: float = DEFAULT_MAX_DELAY) -> bool:
    """attempt is 0-indexed (number of attempts already made). Refuses to
    retry if the provider's own Retry-After exceeds max_delay -- waiting
    that long just to likely 429 again wastes a retry; fail fast instead."""
    if not error.retryable or attempt >= max_retries:
        return False
    if error.retry_after is not None and error.retry_after > max_delay:
        return False
    return True


def compute_backoff(error: ProviderError, attempt: int, base_delay: float | None = None,
                     max_delay: float = DEFAULT_MAX_DELAY) -> float:
    """Seconds to wait before the next attempt. Respects the provider's own
    Retry-After when present (clamped to [base, max_delay]); otherwise
    exponential backoff from a per-error-class base delay, ±20% jitter."""
    base = base_delay if base_delay is not None else _BASE_DELAY_BY_CLASS.get(type(error), 15.0)
    if error.retry_after is not None:
        return max(base, min(error.retry_after, max_delay))
    delay = base * (2**attempt)
    jitter = delay * 0.20 * random.uniform(-1, 1)
    return max(0.0, min(delay + jitter, max_delay))


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

    Hard request timeout (default 90s, max_retries=0): a stuck call raises a
    typed ProviderError subclass instead of hanging or the SDK silently
    retrying into a longer hang — see classify_openai_error. One attempt
    only; see chat_with_retry for bounded, explicit retry.
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
        raise classify_openai_error(e, config) from e

    limiter.record()
    return completion.choices[0].message.content


def chat_with_retry(
    prompt: str,
    run_id: str,
    config: ProviderConfig,
    system: str | None = None,
    model: str | None = None,
    client: OpenAI | None = None,
    limiter: RpmLimiter | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float | None = None,
    max_delay: float = DEFAULT_MAX_DELAY,
    **kwargs,
) -> str:
    """Wraps chat() with bounded, explicit retry on transient transport-layer
    errors only (see ProviderError.retryable) -- never on a malformed model
    response, which is a judgment/prompt concern handled by the CLI's
    envelope extraction, not a transport concern. Transient-blip resilience,
    not a fix for a persistent outage: max_retries=0 (or --no-retry at the
    CLI) restores chat()'s exact single-attempt behavior.

    Every retry prints to stderr with a `[relay]` prefix, "attempt N/M"
    throughout (M = max_retries + 1 total attempts) -- a first-attempt
    success prints nothing, so silence on stderr means no retry happened.
    """
    total_attempts = max_retries + 1
    attempt = 0
    last_error: ProviderError | None = None

    while attempt < total_attempts:
        try:
            result = chat(
                prompt, run_id, config, system=system, model=model, client=client,
                limiter=limiter, timeout=timeout, **kwargs,
            )
        except ProviderError as e:
            last_error = e
            if not should_retry(e, attempt, max_retries, max_delay):
                break
            delay = compute_backoff(e, attempt, base_delay, max_delay)
            attempt += 1
            status_part = f", status={e.status}" if e.status is not None else ""
            print(
                f"[relay] attempt {attempt + 1}/{total_attempts}: {config.name} raised "
                f"{type(e).__name__}{status_part} — waiting {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        else:
            if attempt > 0:
                print(
                    f"[relay] {config.name}: succeeded on attempt {attempt + 1}/{total_attempts}",
                    file=sys.stderr,
                )
            return result

    if attempt > 0:
        print(f"[relay] {config.name}: all {total_attempts} attempts failed — giving up", file=sys.stderr)
    raise last_error
