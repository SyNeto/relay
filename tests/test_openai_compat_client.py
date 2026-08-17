from pathlib import Path

import httpx2
import openai

from relay.providers.openai_compat_client import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderServerError,
    ProviderTimeoutError,
    ProviderUnknownError,
    classify_openai_error,
    compute_backoff,
    should_retry,
)
from relay.providers.registry import ProviderConfig

_CONFIG = ProviderConfig(
    name="test-provider", api="openai-completions", base_url="https://example.com",
    api_key_path=Path("/dev/null"), default_model="test-model",
)


def _request():
    return httpx2.Request("POST", "https://example.com/v1/chat/completions")


def _response(status, headers=None):
    return httpx2.Response(status, headers=headers or {}, request=_request())


# --- classify_openai_error: real SDK exceptions, real httpx2 objects, no mocking ---

def test_classify_timeout():
    exc = openai.APITimeoutError(request=_request())
    result = classify_openai_error(exc, _CONFIG)
    assert isinstance(result, ProviderTimeoutError)
    assert result.retryable is True


def test_classify_connection_error():
    exc = openai.APIConnectionError(request=_request())
    result = classify_openai_error(exc, _CONFIG)
    assert isinstance(result, ProviderConnectionError)
    assert result.retryable is True


def test_classify_rate_limit_extracts_retry_after():
    exc = openai.RateLimitError("rate limited", response=_response(429, {"retry-after": "30"}), body=None)
    result = classify_openai_error(exc, _CONFIG)
    assert isinstance(result, ProviderRateLimitError)
    assert result.retryable is True
    assert result.status == 429
    assert result.retry_after == 30.0


def test_classify_rate_limit_no_retry_after_header():
    exc = openai.RateLimitError("rate limited", response=_response(429), body=None)
    result = classify_openai_error(exc, _CONFIG)
    assert result.retry_after is None


def test_classify_5xx_retryable_extracts_retry_after():
    for status in (500, 502, 503, 504):
        exc = openai.APIStatusError(
            "server error", response=_response(status, {"retry-after": "10"}), body=None
        )
        result = classify_openai_error(exc, _CONFIG)
        assert isinstance(result, ProviderServerError), status
        assert result.retryable is True
        assert result.retry_after == 10.0


def test_classify_5xx_non_transient_not_retryable():
    for status in (501, 505, 511):
        exc = openai.APIStatusError("not implemented", response=_response(status), body=None)
        result = classify_openai_error(exc, _CONFIG)
        assert isinstance(result, ProviderUnknownError), status
        assert result.retryable is False


def test_classify_auth_error():
    exc = openai.AuthenticationError("bad key", response=_response(401), body=None)
    result = classify_openai_error(exc, _CONFIG)
    assert isinstance(result, ProviderAuthError)
    assert result.retryable is False


def test_classify_bad_request_not_retryable():
    exc = openai.BadRequestError("malformed", response=_response(400), body=None)
    result = classify_openai_error(exc, _CONFIG)
    assert isinstance(result, ProviderRequestError)
    assert result.retryable is False


def test_classify_unrecognized_4xx_maps_to_request_error():
    exc = openai.APIStatusError("forbidden", response=_response(403), body=None)
    result = classify_openai_error(exc, _CONFIG)
    assert isinstance(result, ProviderRequestError)


# --- should_retry: pure logic, real ProviderError instances ---

def test_should_retry_true_when_retryable_and_under_limit():
    err = ProviderRateLimitError("nim", "429", status=429)
    assert should_retry(err, attempt=0, max_retries=2) is True


def test_should_retry_false_when_at_limit():
    err = ProviderRateLimitError("nim", "429", status=429)
    assert should_retry(err, attempt=2, max_retries=2) is False


def test_should_retry_false_when_not_retryable():
    err = ProviderAuthError("nim", "401", status=401)
    assert should_retry(err, attempt=0, max_retries=2) is False


def test_should_retry_false_when_retry_after_exceeds_max_delay():
    err = ProviderRateLimitError("nim", "429", status=429, retry_after=300.0)
    assert should_retry(err, attempt=0, max_retries=2, max_delay=120.0) is False


def test_should_retry_true_when_retry_after_within_max_delay():
    err = ProviderRateLimitError("nim", "429", status=429, retry_after=60.0)
    assert should_retry(err, attempt=0, max_retries=2, max_delay=120.0) is True


# --- compute_backoff: pure arithmetic ---

def test_backoff_respects_retry_after():
    err = ProviderRateLimitError("nim", "429", status=429, retry_after=45.0)
    assert compute_backoff(err, attempt=0, base_delay=15.0) == 45.0


def test_backoff_retry_after_clamped_to_max_delay():
    err = ProviderRateLimitError("nim", "429", status=429, retry_after=500.0)
    assert compute_backoff(err, attempt=0, base_delay=15.0, max_delay=120.0) == 120.0


def test_backoff_exponential_with_jitter_bounds():
    err = ProviderTimeoutError("nim", "timeout")
    delay = compute_backoff(err, attempt=0, base_delay=10.0)
    assert 8.0 <= delay <= 12.0
    delay = compute_backoff(err, attempt=1, base_delay=10.0)
    assert 16.0 <= delay <= 24.0


def test_backoff_clamped_to_max_delay():
    err = ProviderTimeoutError("nim", "timeout")
    delay = compute_backoff(err, attempt=10, base_delay=10.0, max_delay=120.0)
    assert delay == 120.0


def test_backoff_uses_per_class_default_when_base_delay_omitted():
    conn_err = ProviderConnectionError("nim", "connection failed")
    rate_err = ProviderRateLimitError("nim", "429", status=429)
    # connection errors default to a shorter base than rate limits
    conn_delay = compute_backoff(conn_err, attempt=0)
    rate_delay = compute_backoff(rate_err, attempt=0)
    assert conn_delay < rate_delay
