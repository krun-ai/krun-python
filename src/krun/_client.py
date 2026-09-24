"""`Krun` (sync) and `AsyncKrun` (async) clients.

Retry policy (deliberately conservative — the API already retries its model backend):

* `decide()` and `models()`: up to `max_retries` extra attempts (default 1) after a connection error or an HTTP
  502/503/504. The wait honours `Retry-After` (capped at 10 s), otherwise 0.5 s, 1 s, 2 s, ... A decision is
  read-only apart from usage accounting, so a retry can at worst count one extra decision.
* `feedback()` writes a row and the API has no idempotency key, so it is never retried.
* The SDK timeout (`APITimeoutError`) is never retried: `timeout` bounds the wait for an attempt, and a request that
  already took that long is not repeated behind the caller's back.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any

import anyio
import httpx

from ._constants import API_KEY_ENV, DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
from ._exceptions import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    KrunError,
    error_from_response,
)
from ._models import decide_body, feedback_body, parse_decision, parse_feedback, parse_models
from ._version import __version__
from .types import DecisionResult, Feedback, Model, QuestionsParam

__all__ = ["AsyncKrun", "Krun"]

logger = logging.getLogger("krun")

_RETRYABLE_STATUS = frozenset({502, 503, 504})
_MAX_RETRY_AFTER = 10.0
_USER_AGENT = f"krun-python/{__version__}"


class _Secret:
    """Holds the API key; never shows it in repr/str."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "'***'"

    __str__ = __repr__


def _retry_delay(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, _MAX_RETRY_AFTER)
    return min(0.5 * 2.0**attempt, 8.0) + random.uniform(0, 0.1)


def _validate_timeout(timeout: float, name: str = "timeout") -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f"{name} must be a number of seconds")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{name} must be a positive, finite number of seconds")
    return float(timeout)


class _BaseClient:
    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
        max_retries: int,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        if not key:
            raise KrunError(f"No API key: pass api_key=... or set the {API_KEY_ENV} environment variable.")
        self._api_key = _Secret(key)
        url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if not url.startswith(("https://", "http://")):
            raise ValueError("base_url must start with https:// or http://")
        self.base_url = url
        self.timeout = _validate_timeout(timeout)
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be an integer >= 0")
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, timeout={self.timeout}, max_retries={self.max_retries})"
        )

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key.get()}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        return headers

    def _build(
        self, method: str, path: str, body: dict[str, Any] | None, request_id: str | None, timeout: float | None
    ) -> tuple[str, str, dict[str, str], bytes | None, httpx.Timeout]:
        headers = self._headers(request_id)
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        t = self.timeout if timeout is None else _validate_timeout(timeout)
        return method, self.base_url + path, headers, content, httpx.Timeout(t)

    @staticmethod
    def _decode(response: httpx.Response) -> tuple[Any, str]:
        """JSON body and request id of a successful response."""
        try:
            data = response.json()
        except ValueError as exc:
            raise APIResponseValidationError(
                f"unexpected response from the Krun API: HTTP {response.status_code} body is not JSON",
                status_code=response.status_code,
                request_id=response.headers.get("x-request-id"),
            ) from exc
        return data, response.headers.get("x-request-id", "")

    @staticmethod
    def _transport_error(exc: httpx.TransportError, url: str) -> APIConnectionError:
        if isinstance(exc, httpx.TimeoutException):
            return APITimeoutError(f"request to {url} timed out ({type(exc).__name__})")
        return APIConnectionError(f"could not reach {url}: {type(exc).__name__}: {exc}")


class Krun(_BaseClient):
    """Synchronous Krun API client.

    ```python
    from krun import Krun

    client = Krun()  # reads KRUN_API_KEY
    result = client.decide(context="...", questions={"department": {"type": "choice", "options": {...}}})
    ```

    Args:
        api_key: `krun_live_...` key. Defaults to the `KRUN_API_KEY` environment variable.
        base_url: API root. Defaults to `https://api.krun.ai`.
        timeout: Seconds to wait for each attempt (connect and read). Default 70.
        max_retries: Extra attempts for `decide()`/`models()` after connection errors or 502/503/504. Default 1.
        http_client: Optional pre-configured `httpx.Client` (proxies, transports, ...). The SDK does not close a
            client it did not create.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries)
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client(follow_redirects=False)

    # ------------------------------------------------------------------------------------------------- public API

    def decide(
        self,
        *,
        context: str,
        questions: QuestionsParam,
        model: str | None = None,
        request_id: str | None = None,
        timeout: float | None = None,
    ) -> DecisionResult:
        """Answer one or more questions about `context` in a single call.

        Args:
            context: The text to decide on (1–8,000 characters).
            questions: Question id → question (1–16), e.g.
                `{"department": {"type": "choice", "options": {"billing": "Payments", "sales": ""}}}`.
                Add `"task_type": "tool"` for tool/function routing.
            model: Optional model id (defaults to the API's default model).
            request_id: Optional `X-Request-ID` to send (1–128 chars of `[A-Za-z0-9._:-]`); otherwise the API
                generates one. Either way it is returned as `result.request_id`.
            timeout: Override the client timeout for this call.
        """
        body = decide_body(context, questions, model)
        data, rid = self._send("POST", "/v1/decide", body, request_id, timeout, self.max_retries)
        return parse_decision(data, rid or request_id or "")

    def feedback(
        self,
        *,
        request_id: str,
        question_id: str,
        correct: bool,
        expected_decision: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Feedback:
        """Report whether the answer to `question_id` of decision `request_id` was correct. Never retried."""
        body = feedback_body(request_id, question_id, correct, expected_decision, metadata)
        data, _ = self._send("POST", "/v1/feedback", body, None, timeout, 0)
        return parse_feedback(data)

    def models(self, *, timeout: float | None = None) -> list[Model]:
        """List the models available to this API key."""
        data, _ = self._send("GET", "/v1/models", None, None, timeout, self.max_retries)
        return parse_models(data)

    # --------------------------------------------------------------------------------------------------- plumbing

    def _send(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        request_id: str | None,
        timeout: float | None,
        max_retries: int,
    ) -> tuple[Any, str]:
        method, url, headers, content, http_timeout = self._build(method, path, body, request_id, timeout)
        attempt = 0
        while True:
            try:
                response = self._client.request(method, url, headers=headers, content=content, timeout=http_timeout)
            except httpx.TimeoutException as exc:
                raise self._transport_error(exc, url) from exc
            except httpx.TransportError as exc:
                if attempt < max_retries:
                    delay = _retry_delay(attempt, None)
                    logger.debug("krun %s %s: %s, retrying in %.2fs", method, path, type(exc).__name__, delay)
                    attempt += 1
                    time.sleep(delay)
                    continue
                raise self._transport_error(exc, url) from exc
            logger.debug(
                "krun %s %s -> %d (request_id=%s)",
                method,
                path,
                response.status_code,
                response.headers.get("x-request-id"),
            )
            if response.is_success:
                return self._decode(response)
            error = error_from_response(response)
            if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                delay = _retry_delay(attempt, error.retry_after)
                logger.debug("krun %s %s: HTTP %d, retrying in %.2fs", method, path, response.status_code, delay)
                attempt += 1
                time.sleep(delay)
                continue
            raise error

    def close(self) -> None:
        """Close the underlying HTTP connection pool (only if the SDK created it)."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Krun:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()


class AsyncKrun(_BaseClient):
    """Asynchronous Krun API client. Same arguments and methods as `Krun`, as coroutines.

    ```python
    async with AsyncKrun() as client:
        result = await client.decide(context="...", questions={...})
    ```
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries)
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.AsyncClient(follow_redirects=False)

    async def decide(
        self,
        *,
        context: str,
        questions: QuestionsParam,
        model: str | None = None,
        request_id: str | None = None,
        timeout: float | None = None,
    ) -> DecisionResult:
        """Async `Krun.decide()`."""
        body = decide_body(context, questions, model)
        data, rid = await self._send("POST", "/v1/decide", body, request_id, timeout, self.max_retries)
        return parse_decision(data, rid or request_id or "")

    async def feedback(
        self,
        *,
        request_id: str,
        question_id: str,
        correct: bool,
        expected_decision: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Feedback:
        """Async `Krun.feedback()`. Never retried."""
        body = feedback_body(request_id, question_id, correct, expected_decision, metadata)
        data, _ = await self._send("POST", "/v1/feedback", body, None, timeout, 0)
        return parse_feedback(data)

    async def models(self, *, timeout: float | None = None) -> list[Model]:
        """Async `Krun.models()`."""
        data, _ = await self._send("GET", "/v1/models", None, None, timeout, self.max_retries)
        return parse_models(data)

    async def _send(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        request_id: str | None,
        timeout: float | None,
        max_retries: int,
    ) -> tuple[Any, str]:
        method, url, headers, content, http_timeout = self._build(method, path, body, request_id, timeout)
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method, url, headers=headers, content=content, timeout=http_timeout
                )
            except httpx.TimeoutException as exc:
                raise self._transport_error(exc, url) from exc
            except httpx.TransportError as exc:
                if attempt < max_retries:
                    delay = _retry_delay(attempt, None)
                    logger.debug("krun %s %s: %s, retrying in %.2fs", method, path, type(exc).__name__, delay)
                    attempt += 1
                    await anyio.sleep(delay)
                    continue
                raise self._transport_error(exc, url) from exc
            logger.debug(
                "krun %s %s -> %d (request_id=%s)",
                method,
                path,
                response.status_code,
                response.headers.get("x-request-id"),
            )
            if response.is_success:
                return self._decode(response)
            error = error_from_response(response)
            if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                delay = _retry_delay(attempt, error.retry_after)
                logger.debug("krun %s %s: HTTP %d, retrying in %.2fs", method, path, response.status_code, delay)
                attempt += 1
                await anyio.sleep(delay)
                continue
            raise error

    async def close(self) -> None:
        """Close the underlying HTTP connection pool (only if the SDK created it)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncKrun:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()
