"""Contract/integration tests: real HTTP against `MockKrunAPI`, which validates requests with the OpenAPI snapshot."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import pytest

from krun import (
    APIConnectionError,
    APITimeoutError,
    AsyncKrun,
    AuthenticationError,
    ChoiceQuestion,
    InvalidRequestError,
    Krun,
    QuestionsParam,
    ServiceUnavailableError,
)

from .mock_server import MockKrunAPI, Scripted

KEY = "krun_test_mockkey"

QUESTIONS: QuestionsParam = {
    "department": {
        "type": "choice",
        "options": {"shipping": "Shipping and delivery issues", "returns": "Returns and refunds", "billing": ""},
    },
    "priority": {"type": "choice", "options": {"low": "", "normal": "", "high": ""}},
    "tool": ChoiceQuestion(options={"calendar_search": "Search calendar events", "send_email": None}, task_type="tool"),
}


@pytest.fixture
def api() -> Iterator[MockKrunAPI]:
    with MockKrunAPI(api_key=KEY) as server:
        yield server


@pytest.fixture
def client(api: MockKrunAPI) -> Iterator[Krun]:
    with Krun(api_key=KEY, base_url=api.url, timeout=5) as c:
        yield c


def test_decide_end_to_end(api: MockKrunAPI, client: Krun) -> None:
    result = client.decide(context="Customer wants to return an item.", questions=QUESTIONS)
    assert list(result.answers) == ["department", "priority", "tool"]
    assert result.answers["department"].choice == "shipping"
    assert result.answers["priority"].abstention_status == "calibrated"
    assert result.answers["tool"].abstention_status == "advisory"
    assert result.request_id.startswith("req_")
    assert isinstance(result.usage.input_tokens, int) and result.usage.input_tokens > 0
    assert api.requests[0].headers["authorization"] == f"Bearer {KEY}"


def test_abstain_end_to_end(client: Krun) -> None:
    result = client.decide(context="I am unsure what this is", questions={"intent": QUESTIONS["priority"]})
    answer = result.answers["intent"]
    assert answer.abstain is True and answer.choice is None
    assert max(answer.probabilities, key=answer.probabilities.__getitem__) == "low"  # best guess still visible


def test_client_request_id_is_echoed(client: Krun) -> None:
    result = client.decide(context="x", questions={"p": QUESTIONS["priority"]}, request_id="trace-123")
    assert result.request_id == "trace-123"


def test_decide_then_feedback(api: MockKrunAPI, client: Krun) -> None:
    result = client.decide(context="x", questions={"department": QUESTIONS["department"]})
    fb = client.feedback(
        request_id=result.request_id,
        question_id="department",
        correct=False,
        expected_decision="billing",
        metadata={"source": "test"},
    )
    assert fb.request_id == result.request_id and fb.question_id == "department"
    assert fb.created_at.year == 2026


def test_models(client: Krun) -> None:
    assert [(m.id, m.status) for m in client.models()] == [("krun-one-v0", "available")]


def test_server_side_validation_error_is_mapped(client: Krun) -> None:
    with pytest.raises(InvalidRequestError) as exc_info:
        client.decide(context="x", questions={"q": {"type": "choice", "options": {"only": ""}}})
    exc = exc_info.value
    assert exc.error_code == "INVALID_OPTIONS" and exc.status_code == 400
    assert exc.request_id and exc.request_id.startswith("req_")
    with pytest.raises(InvalidRequestError):  # unknown field rejected by the OpenAPI schema
        client.decide(context="x", questions={"q": {"type": "choice", "options": {"a": "", "b": ""}, "extra": 1}})


def test_wrong_key(api: MockKrunAPI) -> None:
    with Krun(api_key="krun_live_wrong", base_url=api.url) as c, pytest.raises(AuthenticationError) as exc_info:
        c.models()
    assert exc_info.value.status_code == 401 and exc_info.value.error_code == "UNAUTHORIZED"


def test_retry_after_503_then_success(api: MockKrunAPI, client: Krun) -> None:
    api.enqueue(
        Scripted(
            503,
            {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "try later", "request_id": "req_x"}},
            {"Retry-After": "0"},
        )
    )
    result = client.decide(context="x", questions={"p": QUESTIONS["priority"]})
    assert result.answers["p"].choice == "low"
    assert len(api.requests) == 2


def test_retry_exhausted(api: MockKrunAPI) -> None:
    err = Scripted(503, {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "down"}}, {"Retry-After": "0"})
    api.enqueue(err, err)
    with Krun(api_key=KEY, base_url=api.url) as c, pytest.raises(ServiceUnavailableError):
        c.decide(context="x", questions={"p": QUESTIONS["priority"]})
    assert len(api.requests) == 2  # 1 attempt + 1 default retry


def test_real_timeout(api: MockKrunAPI) -> None:
    api.enqueue(Scripted(200, {}, delay=1.0))
    with Krun(api_key=KEY, base_url=api.url, timeout=0.2) as c:
        start = time.monotonic()
        with pytest.raises(APITimeoutError):
            c.decide(context="x", questions={"p": QUESTIONS["priority"]})
        assert time.monotonic() - start < 0.9
    assert len(api.requests) == 1  # timeouts are not retried


def test_connection_refused() -> None:
    with socket.socket() as s:  # grab a free port, then close it so nothing listens there
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with Krun(api_key=KEY, base_url=f"http://127.0.0.1:{port}", max_retries=0) as c:
        with pytest.raises(APIConnectionError) as exc_info:
            c.models()
        assert KEY not in str(exc_info.value)


@pytest.mark.anyio
async def test_async_client(api: MockKrunAPI) -> None:
    async with AsyncKrun(api_key=KEY, base_url=api.url) as c:
        result = await c.decide(context="Customer wants to return an item.", questions=QUESTIONS)
        assert list(result.answers) == ["department", "priority", "tool"]
        fb = await c.feedback(request_id=result.request_id, question_id="tool", correct=True)
        assert fb.question_id == "tool"
        assert [m.id for m in await c.models()] == ["krun-one-v0"]
        with pytest.raises(InvalidRequestError):
            await c.decide(context="x", questions={"q": {"type": "choice", "options": {"only": ""}}})


@pytest.mark.anyio
async def test_async_retry_and_timeout(api: MockKrunAPI) -> None:
    api.enqueue(Scripted(502, {"error": {"code": "INFERENCE_FAILED", "message": "x"}}, {"Retry-After": "0"}))
    async with AsyncKrun(api_key=KEY, base_url=api.url, timeout=0.3) as c:
        assert (await c.decide(context="x", questions={"p": QUESTIONS["priority"]})).answers["p"].choice == "low"
        api.enqueue(Scripted(200, {}, delay=1.0))
        with pytest.raises(APITimeoutError):
            await c.decide(context="x", questions={"p": QUESTIONS["priority"]})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
