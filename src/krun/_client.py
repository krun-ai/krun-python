"""`Krun` (sync) and `AsyncKrun` (async) clients.

Retry policy (deliberately conservative — the API already retries its model backend):

* `decide()` and `models()`: up to `max_retries` extra attempts (default 1) after a connection error or an HTTP
  502/503/504. The wait honours `Retry-After` (capped at 10 s), otherwise 0.5 s, 1 s, 2 s, ... A decision is
  read-only apart from usage accounting, so a retry can at worst count one extra decision.
* `feedback()` writes a row and the API has no idempotency key, so it is never retried. For the same reason
  `assets.create()` (an upload creates a new asset) and `assets.delete()` are never retried; `assets.get()` is a
  read and follows the `decide()`/`models()` policy.
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
from pathlib import Path
from types import TracebackType
from typing import IO, Any
from urllib.parse import quote

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
from ._models import (
    decide_body,
    feedback_body,
    parse_asset,
    parse_decision,
    parse_deleted_asset,
    parse_feedback,
    parse_models,
)
from ._version import __version__
from .types import Asset, ContextParam, DecisionResult, DeletedAsset, ExpectedParam, Feedback, Model, QuestionsParam

__all__ = ["AssetFile", "Assets", "AsyncAssets", "AsyncKrun", "Krun"]

AssetFile = bytes | bytearray | memoryview | str | os.PathLike[str] | IO[bytes]
"""What `assets.create()` accepts: raw bytes, a filesystem path (`str` or `pathlib.Path`) or a binary file object."""

# Extension → MIME type for the formats the API accepts (Krun One V1). Used only when `mime_type` is omitted.
_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}


def _read_asset_file(file: AssetFile, mime_type: str | None) -> tuple[bytes, str]:
    """Bytes to upload and their MIME type. Size and type limits are enforced by the API."""
    name: str | None = None
    if isinstance(file, (bytes, bytearray, memoryview)):
        data = bytes(file)
    elif isinstance(file, (str, os.PathLike)):
        path = Path(file)
        name = path.name
        data = path.read_bytes()
    elif hasattr(file, "read"):
        data = file.read()
        if not isinstance(data, bytes):
            raise TypeError("file objects must be opened in binary mode ('rb')")
        raw_name = getattr(file, "name", None)
        name = raw_name if isinstance(raw_name, str) else None
    else:
        raise TypeError(f"file must be bytes, a path or a binary file object, got {type(file).__name__}")
    if mime_type is None:
        mime_type = _MIME_BY_EXTENSION.get(os.path.splitext(name)[1].lower()) if name else None
        if mime_type is None:
            raise ValueError("mime_type is required (it could not be inferred from a file name extension)")
    elif not isinstance(mime_type, str) or not mime_type:
        raise TypeError("mime_type must be a non-empty str, e.g. 'image/png'")
    return data, mime_type


def _asset_path(asset_id: str) -> str:
    if not isinstance(asset_id, str) or not asset_id:
        raise TypeError("asset_id must be a non-empty str (Asset.id)")
    return "/v1/assets/" + quote(asset_id, safe="")


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
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        request_id: str | None,
        timeout: float | None,
        raw: tuple[bytes, str] | None = None,
    ) -> tuple[str, str, dict[str, str], bytes | None, httpx.Timeout]:
        headers = self._headers(request_id)
        content = None
        if raw is not None:
            content, headers["Content-Type"] = raw
        elif body is not None:
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
        self.assets = Assets(self)
        """Media uploads for multimodal contexts (Krun One V1, upcoming — not yet available on api.krun.ai)."""

    # ------------------------------------------------------------------------------------------------- public API

    def decide(
        self,
        *,
        context: ContextParam,
        questions: QuestionsParam,
        model: str | None = None,
        request_id: str | None = None,
        timeout: float | None = None,
    ) -> DecisionResult:
        """Answer one or more questions about `context` in a single call.

        Args:
            context: The text to decide on (1–8,000 characters). Krun One V1 (upcoming — not yet available on
                api.krun.ai): or a list of 1–16 content parts, e.g.
                `[TextPart("Is this invoice paid?"), DocumentPart(asset.id)]` or the equivalent dicts.
            questions: Question id → question (1–16: `choice`, `noul`, `score`, or `multi` in Krun One V1), e.g.
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
        expected: ExpectedParam | Mapping[str, Any] | None = None,
        expected_decision: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Feedback:
        """Report whether the answer to `question_id` of decision `request_id` was correct. Never retried.

        `expected` is the correct answer, typed like the question: `{"type": "choice", "value": "billing"}`,
        `{"type": "noul", "value": True}` or `{"type": "score", "value": 2}`. `expected_decision` (choice only)
        is kept for compatibility.
        """
        body = feedback_body(request_id, question_id, correct, expected_decision, metadata, expected)
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
        raw: tuple[bytes, str] | None = None,
    ) -> tuple[Any, str]:
        method, url, headers, content, http_timeout = self._build(method, path, body, request_id, timeout, raw)
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
        self.assets = AsyncAssets(self)
        """Async `Krun.assets`."""

    async def decide(
        self,
        *,
        context: ContextParam,
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
        expected: ExpectedParam | Mapping[str, Any] | None = None,
        expected_decision: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Feedback:
        """Async `Krun.feedback()`. Never retried."""
        body = feedback_body(request_id, question_id, correct, expected_decision, metadata, expected)
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
        raw: tuple[bytes, str] | None = None,
    ) -> tuple[Any, str]:
        method, url, headers, content, http_timeout = self._build(method, path, body, request_id, timeout, raw)
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


class Assets:
    """`client.assets`: upload media for multimodal contexts (Krun One V1, upcoming — not yet available on
    api.krun.ai). Assets are usable only by the uploading project and expire after 24 h by default."""

    def __init__(self, client: Krun) -> None:
        self._client = client

    def create(self, file: AssetFile, *, mime_type: str | None = None, timeout: float | None = None) -> Asset:
        """Upload `file` (bytes, a path or a binary file object) as the raw request body. Never retried.

        `mime_type` is sent as `Content-Type` (e.g. `"image/png"`, `"application/pdf"`, `"audio/wav"`). When omitted
        it is inferred from the file name extension (path or file object with a `.name`); bytes need it explicitly.
        The API checks the content against the declared type and enforces size limits.
        """
        data, content_type = _read_asset_file(file, mime_type)
        body, _ = self._client._send("POST", "/v1/assets", None, None, timeout, 0, (data, content_type))
        return parse_asset(body)

    def get(self, asset_id: str, *, timeout: float | None = None) -> Asset:
        """Metadata of an asset (retried like `models()`)."""
        path = _asset_path(asset_id)
        body, _ = self._client._send("GET", path, None, None, timeout, self._client.max_retries)
        return parse_asset(body)

    def delete(self, asset_id: str, *, timeout: float | None = None) -> DeletedAsset:
        """Delete an asset before it expires. Never retried."""
        path = _asset_path(asset_id)
        body, _ = self._client._send("DELETE", path, None, None, timeout, 0)
        return parse_deleted_asset(body)


class AsyncAssets:
    """Async `Krun.assets`."""

    def __init__(self, client: AsyncKrun) -> None:
        self._client = client

    async def create(self, file: AssetFile, *, mime_type: str | None = None, timeout: float | None = None) -> Asset:
        """Async `Krun.assets.create()`. Never retried. Paths and file objects are read in a worker thread."""
        if isinstance(file, (bytes, bytearray, memoryview)):
            data, content_type = _read_asset_file(file, mime_type)
        else:
            data, content_type = await anyio.to_thread.run_sync(_read_asset_file, file, mime_type)
        body, _ = await self._client._send("POST", "/v1/assets", None, None, timeout, 0, (data, content_type))
        return parse_asset(body)

    async def get(self, asset_id: str, *, timeout: float | None = None) -> Asset:
        """Async `Krun.assets.get()`."""
        path = _asset_path(asset_id)
        body, _ = await self._client._send("GET", path, None, None, timeout, self._client.max_retries)
        return parse_asset(body)

    async def delete(self, asset_id: str, *, timeout: float | None = None) -> DeletedAsset:
        """Async `Krun.assets.delete()`. Never retried."""
        path = _asset_path(asset_id)
        body, _ = await self._client._send("DELETE", path, None, None, timeout, 0)
        return parse_deleted_asset(body)
