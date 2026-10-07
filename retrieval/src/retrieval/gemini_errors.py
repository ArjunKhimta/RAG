"""Gemini API failures, turned into one error type that callers can act on without SDK types.

Embedding and answer generation fail in the same ways, so both raise a subclass of
`GeminiRequestError`, carrying the HTTP status, any retry delay the API suggests, and the IDs of any
quotas that were exceeded.

A 429 can mean the per-minute limits, which free up within a minute, or the daily limit, which
does not free up until the daily reset. The quota IDs tell them apart: daily ones contain
`PerDay`, as in `EmbedContentRequestsPerDayPerProjectPerModel-FreeTier`. A daily-quota error is
never worth retrying.

A call that times out or loses its connection never reaches Gemini's error handling: the HTTP
library underneath (httpx) raises its own error. Such a failure becomes a `GeminiRequestError`
too, with no HTTP status, and counts as temporary, like a busy reply.

A retryable failure waits for the delay the API suggests, or else backs off exponentially from
2 seconds, capped at 60, with jitter. Every failure carries a short `reason`, such as
`HTTP 503 UNAVAILABLE` or `timed out after 30 s`, for log lines that never include a reply body.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from google.genai import errors

RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})

RETRY_INFO_TYPE_SUFFIX = "google.rpc.RetryInfo"

QUOTA_FAILURE_TYPE_SUFFIX = "google.rpc.QuotaFailure"

DAILY_QUOTA_ID_MARKER = "PerDay"

SECONDS_SUFFIX = "s"

BASE_RETRY_DELAY_SECONDS = 2.0

MAX_RETRY_DELAY_SECONDS = 60.0

MINIMUM_JITTER_FRACTION = 0.5


class GeminiRequestError(RuntimeError):
    """Raised when a Gemini request fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None,
        retry_after_seconds: float | None,
        exceeded_quota_ids: tuple[str, ...] = (),
        reason: str | None = None,
        is_transport_failure: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.exceeded_quota_ids = exceeded_quota_ids
        self.reason = reason or message
        self.is_transport_failure = is_transport_failure

    @property
    def is_daily_quota_exhausted(self) -> bool:
        return any(DAILY_QUOTA_ID_MARKER in quota_id for quota_id in self.exceeded_quota_ids)

    @property
    def is_retryable(self) -> bool:
        if self.is_transport_failure:
            return True
        return self.status_code in RETRYABLE_STATUS_CODES and not self.is_daily_quota_exhausted


def gemini_request_error[ErrorType: GeminiRequestError](
    error: errors.APIError, error_class: type[ErrorType]
) -> ErrorType:
    exceeded_quota_ids = _exceeded_quota_ids(error.details)
    message = f"Gemini request failed with HTTP {error.code} {error.status}: {error.message}"
    if exceeded_quota_ids:
        message += f" [quota: {', '.join(exceeded_quota_ids)}]"
    return error_class(
        message,
        status_code=error.code,
        retry_after_seconds=_suggested_retry_seconds(error.details),
        exceeded_quota_ids=exceeded_quota_ids,
        reason=f"HTTP {error.code} {error.status}",
    )


def transport_request_error[ErrorType: GeminiRequestError](
    error: httpx.TransportError, error_class: type[ErrorType], timeout_seconds: float
) -> ErrorType:
    """Turn a timed-out or failed connection into a temporary Gemini failure."""
    if isinstance(error, httpx.TimeoutException):
        reason = f"timed out after {timeout_seconds:g} s"
    else:
        reason = f"connection failed ({type(error).__name__})"
    return error_class(
        f"Gemini request {reason}",
        status_code=None,
        retry_after_seconds=None,
        reason=reason,
        is_transport_failure=True,
    )


def retry_delay_seconds(
    error: GeminiRequestError, attempt_number: int, jitter_fraction: Callable[[], float]
) -> float:
    """Use the API's suggested delay if given; otherwise back off exponentially with jitter.

    Jitter spreads retries between half and all of the backoff, so parallel clients that failed
    together do not all retry at the same moment.
    """
    if error.retry_after_seconds is not None:
        return error.retry_after_seconds
    backoff_seconds = min(
        MAX_RETRY_DELAY_SECONDS, BASE_RETRY_DELAY_SECONDS * 2 ** (attempt_number - 1)
    )
    jitter_range = 1.0 - MINIMUM_JITTER_FRACTION
    return backoff_seconds * (MINIMUM_JITTER_FRACTION + jitter_range * jitter_fraction())


def _error_details(response_json: Any) -> list[dict[str, Any]]:
    if not isinstance(response_json, dict):
        return []
    error_json = response_json.get("error", response_json)
    details = error_json.get("details") or []
    return [detail for detail in details if isinstance(detail, dict)]


def _exceeded_quota_ids(response_json: Any) -> tuple[str, ...]:
    """Read quota IDs from `google.rpc.QuotaFailure` entries in the error response."""
    quota_ids: list[str] = []
    for detail in _error_details(response_json):
        if not str(detail.get("@type", "")).endswith(QUOTA_FAILURE_TYPE_SUFFIX):
            continue
        for violation in detail.get("violations") or []:
            quota_id = violation.get("quotaId") if isinstance(violation, dict) else None
            if isinstance(quota_id, str):
                quota_ids.append(quota_id)
    return tuple(quota_ids)


def _suggested_retry_seconds(response_json: Any) -> float | None:
    """Read the delay from a `google.rpc.RetryInfo` entry, such as `"retryDelay": "17s"`."""
    for detail in _error_details(response_json):
        is_retry_info = str(detail.get("@type", "")).endswith(RETRY_INFO_TYPE_SUFFIX)
        retry_delay = detail.get("retryDelay")
        if is_retry_info and isinstance(retry_delay, str):
            try:
                return float(retry_delay.removesuffix(SECONDS_SUFFIX))
            except ValueError:
                return None
    return None
