"""Exception hierarchy.

    KrunError
    ├── APIError                    the API answered with an error status
    │   ├── InvalidRequestError     400/413  INVALID_REQUEST, INVALID_OPTIONS, PAYLOAD_TOO_LARGE
    │   ├── AuthenticationError     401      UNAUTHORIZED
    │   ├── NotFoundError           404      NOT_FOUND
    │   ├── RateLimitError          429      RATE_LIMITED
    │   ├── QuotaExceededError      429      QUOTA_EXCEEDED
    │   ├── InferenceFailedError    502      INFERENCE_FAILED
    │   ├── ServiceUnavailableError 503      UPSTREAM_UNAVAILABLE
    │   ├── UpstreamTimeoutError    504      UPSTREAM_TIMEOUT
    │   └── InternalServerError     500      INTERNAL_ERROR
    ├── APIConnectionError          no HTTP response (DNS, refused, reset, TLS, ...)
    │   └── APITimeoutError         the SDK timeout elapsed
    └── APIResponseValidationError  a 2xx response did not match the contract

Messages never contain the API key or request content.
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APIError",
    "APIResponseValidationError",
    "APITimeoutError",
    "AuthenticationError",
    "InferenceFailedError",
    "InternalServerError",
    "InvalidRequestError",
    "KrunError",
    "NotFoundError",
    "QuotaExceededError",
    "RateLimitError",
    "ServiceUnavailableError",
    "UpstreamTimeoutError",
]


class KrunError(Exception):
    """Base class of every error raised by the SDK."""

    message: str
    request_id: str | None
    status_code: int | None
    error_code: str | None

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id
        self.status_code = status_code
        self.error_code = error_code

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_code:
            parts.append(f"code={self.error_code}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return parts[0] if len(parts) == 1 else f"{parts[0]} ({', '.join(parts[1:])})"


class APIError(KrunError):
    """The API returned an error response. `status_code` is always set; `error_code` is the API's stable code
    (e.g. `INVALID_OPTIONS`) when the body had one."""

    status_code: int
    body: Any
    """Parsed JSON error body, or the raw text when it was not JSON."""
    retry_after: float | None
    """Seconds from the `Retry-After` header, when present (rate limits, upstream unavailable)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None = None,
        error_code: str | None = None,
        body: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, request_id=request_id, status_code=status_code, error_code=error_code)
        self.body = body
        self.retry_after = retry_after


class InvalidRequestError(APIError):
    """The request was rejected before any inference (bad fields, 1 option, too many questions, body too big)."""


class AuthenticationError(APIError):
    """Missing, invalid or revoked API key."""


class NotFoundError(APIError):
    """Unknown resource, e.g. feedback for a `request_id` this project never decided."""


class RateLimitError(APIError):
    """Per-key requests-per-minute limit reached. `retry_after` says when the window resets."""


class QuotaExceededError(APIError):
    """The project's monthly decision or input-token quota is exhausted. Retrying will not help."""


class InferenceFailedError(APIError):
    """The model backend failed to produce a valid answer."""


class ServiceUnavailableError(APIError):
    """The model backend is temporarily unavailable."""


class UpstreamTimeoutError(APIError):
    """The API's deadline for the model backend elapsed (e.g. a slow cold start)."""


class InternalServerError(APIError):
    """Unexpected error inside the API."""


class APIConnectionError(KrunError):
    """The request did not get an HTTP response."""


class APITimeoutError(APIConnectionError):
    """The SDK timeout elapsed before the response arrived."""


class APIResponseValidationError(KrunError):
    """A successful response did not have the documented shape (SDK/API version mismatch?)."""


_CODE_TO_CLASS: dict[str, type[APIError]] = {
    "INVALID_REQUEST": InvalidRequestError,
    "INVALID_OPTIONS": InvalidRequestError,
    "PAYLOAD_TOO_LARGE": InvalidRequestError,
    "UNAUTHORIZED": AuthenticationError,
    "NOT_FOUND": NotFoundError,
    "RATE_LIMITED": RateLimitError,
    "QUOTA_EXCEEDED": QuotaExceededError,
    "INFERENCE_FAILED": InferenceFailedError,
    "UPSTREAM_UNAVAILABLE": ServiceUnavailableError,
    "UPSTREAM_TIMEOUT": UpstreamTimeoutError,
    "INTERNAL_ERROR": InternalServerError,
}

# Used when the body has no (known) code, e.g. an error page from a proxy in front of the API.
_STATUS_TO_CLASS: dict[int, type[APIError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    404: NotFoundError,
    413: InvalidRequestError,
    422: InvalidRequestError,
    429: RateLimitError,
    500: InternalServerError,
    503: ServiceUnavailableError,
    504: UpstreamTimeoutError,
}


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        secs = float(value)
    except ValueError:
        return None  # HTTP-date form: the Krun API only sends seconds
    return secs if secs >= 0 else None


def error_from_response(response: httpx.Response) -> APIError:
    """Build the most specific `APIError` for an error response."""
    status = response.status_code
    header_request_id = response.headers.get("x-request-id")
    code: str | None = None
    message: str | None = None
    request_id: str | None = None
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        detail = body["error"]
        code = detail.get("code") if isinstance(detail.get("code"), str) else None
        message = detail.get("message") if isinstance(detail.get("message"), str) else None
        request_id = detail.get("request_id") if isinstance(detail.get("request_id"), str) else None
    cls = (code and _CODE_TO_CLASS.get(code)) or _STATUS_TO_CLASS.get(status, APIError)
    if not message:
        message = f"HTTP {status} {response.reason_phrase}".strip()
    return cls(
        message,
        status_code=status,
        request_id=request_id or header_request_id,
        error_code=code,
        body=body,
        retry_after=_parse_retry_after(response.headers.get("retry-after")),
    )
