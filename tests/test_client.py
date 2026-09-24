"""Unit tests: an `httpx.MockTransport` stands in for the network."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

import krun
from krun import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    InferenceFailedError,
    InternalServerError,
    InvalidRequestError,
    Krun,
    KrunError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    ServiceUnavailableError,
    UpstreamTimeoutError,
    Usage,
)

KEY = "krun_live_SECRETsecretSECRETsecret0123456"

DEPARTMENT = {
    "type": "choice",
    "options": {
        "shipping": "Shipping and delivery issues",
        "returns": "Returns and refunds",
        "billing": "Billing and payment issues",
    },
}

DECIDE_OK: dict[str, Any] = {
    "model": "krun-one-v0",
    "answers": {
        "department": {
            "type": "choice",
            "choice": "returns",
            "confidence": 0.9788,
            "probabilities": {"shipping": 0.0056, "returns": 0.9866, "billing": 0.0078},
            "abstain": False,
            "abstention_status": "advisory",
        }
    },
    "usage": {"input_tokens": 52},
}

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: Any) -> tuple[Krun, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    kwargs.setdefault("api_key", KEY)
    client = Krun(http_client=httpx.Client(transport=httpx.MockTransport(wrapped)), **kwargs)
    return client, seen


def ok(body: Any, status: int = 200, request_id: str = "req_abc123") -> Handler:
    return lambda _req: httpx.Response(status, json=body, headers={"X-Request-ID": request_id})


def error(status: int, code: str | None, message: str = "boom", request_id: str | None = "req_err1", **headers: str):
    detail: dict[str, Any] = {"message": message, "request_id": request_id}
    if code is not None:
        detail["code"] = code
    return lambda _req: httpx.Response(status, json={"error": detail}, headers={"X-Request-ID": "req_hdr", **headers})


# ------------------------------------------------------------------------------------------------ configuration / auth


def test_auth_header_and_default_headers() -> None:
    client, seen = make_client(ok(DECIDE_OK))
    client.decide(context="Customer wants to return an item.", questions={"department": DEPARTMENT})
    req = seen[0]
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["accept"] == "application/json"
    assert req.headers["user-agent"] == f"krun-python/{krun.__version__}"
    assert "x-request-id" not in req.headers


def test_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRUN_API_KEY", "krun_live_fromenv")
    client, seen = make_client(ok(DECIDE_OK), api_key=None)
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert seen[0].headers["authorization"] == "Bearer krun_live_fromenv"


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRUN_API_KEY", raising=False)
    with pytest.raises(KrunError, match="KRUN_API_KEY"):
        Krun()
    with pytest.raises(KrunError):
        krun.AsyncKrun()


def test_default_base_url_and_override() -> None:
    client, seen = make_client(ok(DECIDE_OK))
    assert client.base_url == "https://api.krun.ai"
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert str(seen[0].url) == "https://api.krun.ai/v1/decide"

    client, seen = make_client(ok(DECIDE_OK), base_url="http://localhost:8080/")
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert str(seen[0].url) == "http://localhost:8080/v1/decide"


def test_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        Krun(api_key=KEY, base_url="api.krun.ai")
    with pytest.raises(ValueError):
        Krun(api_key=KEY, timeout=0)
    with pytest.raises(ValueError):
        Krun(api_key=KEY, timeout=float("inf"))
    with pytest.raises(TypeError):
        Krun(api_key=KEY, timeout=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Krun(api_key=KEY, max_retries=-1)


def test_default_timeout_and_retries() -> None:
    client = Krun(api_key=KEY)
    assert client.timeout == 70.0
    assert client.max_retries == 1
    client.close()


def test_api_key_never_in_repr_or_str() -> None:
    client = Krun(api_key=KEY)
    for text in (repr(client), str(client), repr(vars(client)), repr(client._api_key)):
        assert KEY not in text
        assert "SECRET" not in text
    assert repr(client) == "Krun(base_url='https://api.krun.ai', timeout=70.0, max_retries=1)"
    client.close()


def test_api_key_never_in_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="krun")
    client, _ = make_client(error(401, "UNAUTHORIZED", "missing, invalid or revoked API key"))
    with pytest.raises(AuthenticationError) as exc_info:
        client.decide(context="secret context text", questions={"department": DEPARTMENT})
    exc = exc_info.value
    for text in (str(exc), repr(exc), repr(exc.body), caplog.text):
        assert KEY not in text
        assert "secret context text" not in text
        assert "Shipping and delivery" not in text


def test_sdk_is_silent_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    client, _ = make_client(ok(DECIDE_OK))
    client.decide(context="x", questions={"department": DEPARTMENT})
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# ---------------------------------------------------------------------------------------------------------- decide


def test_request_serialization_single_question() -> None:
    client, seen = make_client(ok(DECIDE_OK))
    client.decide(context="Customer wants to return an item.", questions={"department": DEPARTMENT})
    assert json.loads(seen[0].content) == {
        "context": "Customer wants to return an item.",
        "questions": {"department": DEPARTMENT},
    }


def test_request_serialization_dataclass_task_type_model_and_request_id() -> None:
    client, seen = make_client(ok(DECIDE_OK))
    client.decide(
        context="Find my meetings tomorrow.",
        questions={
            "tool": ChoiceQuestion(
                options={"calendar_search": "Search calendar events", "send_email": None}, task_type="tool"
            ),
            "label_only": {"type": "choice", "options": {"billing": "", "sales": ""}},
        },
        model="krun-one-v0",
        request_id="my-trace:42",
    )
    assert json.loads(seen[0].content) == {
        "context": "Find my meetings tomorrow.",
        "questions": {
            "tool": {
                "type": "choice",
                "options": {"calendar_search": "Search calendar events", "send_email": None},
                "task_type": "tool",
            },
            "label_only": {"type": "choice", "options": {"billing": "", "sales": ""}},
        },
        "model": "krun-one-v0",
    }
    assert seen[0].headers["x-request-id"] == "my-trace:42"


def test_ids_and_order_preserved_verbatim() -> None:
    options = {"transaction_charged_twice": "", "Card-Arrival.v2": "", "lost or stolen card": "", "ñandú": ""}
    questions = {"zeta": {"type": "choice", "options": options}, "Alpha_1": {"type": "choice", "options": options}}
    client, seen = make_client(ok(DECIDE_OK))
    client.decide(context="x", questions=questions)
    sent = json.loads(seen[0].content)["questions"]
    assert list(sent) == ["zeta", "Alpha_1"]
    assert list(sent["zeta"]["options"]) == list(options)
    assert "ñandú" in seen[0].content.decode("utf-8")  # sent as UTF-8, not \u-escaped


def test_response_parsing() -> None:
    client, _ = make_client(ok(DECIDE_OK, request_id="req_0b7f7c5e"))
    result = client.decide(context="x", questions={"department": DEPARTMENT})
    assert isinstance(result, DecisionResult)
    assert result.model == "krun-one-v0"
    assert result.request_id == "req_0b7f7c5e"
    assert result.usage == Usage(input_tokens=52)
    answer = result.answers["department"]
    assert answer == ChoiceAnswer(
        type="choice",
        choice="returns",
        confidence=0.9788,
        probabilities={"shipping": 0.0056, "returns": 0.9866, "billing": 0.0078},
        abstain=False,
        abstention_status="advisory",
    )
    assert list(answer.probabilities) == ["shipping", "returns", "billing"]


def test_no_output_tokens() -> None:
    body = {**DECIDE_OK, "usage": {"input_tokens": 52, "output_tokens": 7}}  # even if a server sent it
    client, _ = make_client(ok(body))
    result = client.decide(context="x", questions={"department": DEPARTMENT})
    assert not hasattr(result.usage, "output_tokens")
    assert not hasattr(result.usage, "completion_tokens")
    assert [f for f in Usage.__dataclass_fields__] == ["input_tokens"]


def test_input_tokens_may_be_null() -> None:
    client, _ = make_client(ok({**DECIDE_OK, "usage": {"input_tokens": None}}))
    assert client.decide(context="x", questions={"department": DEPARTMENT}).usage.input_tokens is None


def test_abstain_keeps_choice_none() -> None:
    body = {
        "model": "krun-one-v0",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": None,
                "confidence": 0.02,
                "probabilities": {"a": 0.49, "b": 0.47, "c": 0.04},
                "abstain": True,
                "abstention_status": "calibrated",
            }
        },
        "usage": {"input_tokens": 30},
    }
    client, _ = make_client(ok(body))
    answer = client.decide(context="x", questions={"intent": DEPARTMENT}).answers["intent"]
    assert answer.abstain is True
    assert answer.choice is None  # not replaced by the arg-max
    assert answer.abstention_status == "calibrated"


def test_multiple_questions() -> None:
    def answer(choice: str, probs: dict[str, float], status: str) -> dict[str, Any]:
        return {"type": "choice", "choice": choice, "confidence": 0.5, "probabilities": probs, "abstain": False,
                "abstention_status": status}  # fmt: skip

    body = {
        "model": "krun-one-v0",
        "answers": {
            "department": answer("returns", {"shipping": 0.1, "returns": 0.8, "billing": 0.1}, "advisory"),
            "priority": answer("normal", {"low": 0.2, "normal": 0.7, "high": 0.1}, "advisory"),
            "risk": answer("low", {"low": 0.9, "high": 0.1}, "calibrated"),
        },
        "usage": {"input_tokens": 150},
    }
    client, seen = make_client(ok(body))
    questions = {
        "department": DEPARTMENT,
        "priority": {"type": "choice", "options": {"low": "Can wait", "normal": "", "high": None}},
        "risk": {"type": "choice", "options": {"low": "", "high": ""}},
    }
    result = client.decide(context="x", questions=questions)
    assert len(seen) == 1  # one call for all questions
    assert list(json.loads(seen[0].content)["questions"]) == ["department", "priority", "risk"]
    assert list(result.answers) == ["department", "priority", "risk"]
    assert result.answers["priority"].choice == "normal"
    assert result.answers["risk"].abstention_status == "calibrated"


def test_tool_routing_advisory_is_exposed() -> None:
    body = {
        "model": "krun-one-v0",
        "answers": {
            "tool": {
                "type": "choice",
                "choice": "calendar_search",
                "confidence": 0.965866,
                "probabilities": {"calendar_search": 0.982933, "send_email": 0.017067},
                "abstain": False,
                "abstention_status": "advisory",
            }
        },
        "usage": {"input_tokens": 41},
    }
    client, seen = make_client(ok(body))
    result = client.decide(
        context="Find my meetings tomorrow.",
        questions={
            "tool": {
                "type": "choice",
                "task_type": "tool",
                "options": {"calendar_search": "Search calendar events", "send_email": "Send an email"},
            }
        },
    )
    assert json.loads(seen[0].content)["questions"]["tool"]["task_type"] == "tool"
    assert result.answers["tool"].abstention_status == "advisory"


def test_request_id_falls_back_to_sent_id() -> None:
    client, _ = make_client(lambda _r: httpx.Response(200, json=DECIDE_OK))  # no header
    assert client.decide(context="x", questions={"d": DEPARTMENT}, request_id="mine").request_id == "mine"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"answers": {}, "usage": {}},  # no model
        {**DECIDE_OK, "answers": {"d": {**DECIDE_OK["answers"]["department"], "type": "score"}}},
        {**DECIDE_OK, "answers": {"d": {**DECIDE_OK["answers"]["department"], "confidence": "high"}}},
        {**DECIDE_OK, "answers": {"d": {**DECIDE_OK["answers"]["department"], "choice": 3}}},
        {**DECIDE_OK, "usage": {"input_tokens": 1.5}},
    ],
)
def test_malformed_success_response(body: Any) -> None:
    client, _ = make_client(ok(body))
    with pytest.raises(APIResponseValidationError):
        client.decide(context="x", questions={"department": DEPARTMENT})


def test_non_json_success_response() -> None:
    client, _ = make_client(lambda _r: httpx.Response(200, text="<html>", headers={"X-Request-ID": "req_1"}))
    with pytest.raises(APIResponseValidationError) as exc_info:
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert exc_info.value.request_id == "req_1"


def test_bad_argument_types() -> None:
    client, seen = make_client(ok(DECIDE_OK))
    with pytest.raises(TypeError):
        client.decide(context=None, questions={"department": DEPARTMENT})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        client.decide(context="x", questions=[DEPARTMENT])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        client.decide(context="x", questions={"d": "billing"})  # type: ignore[dict-item]
    assert seen == []


# ------------------------------------------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        (400, "INVALID_REQUEST", InvalidRequestError),
        (400, "INVALID_OPTIONS", InvalidRequestError),
        (413, "PAYLOAD_TOO_LARGE", InvalidRequestError),
        (401, "UNAUTHORIZED", AuthenticationError),
        (404, "NOT_FOUND", NotFoundError),
        (429, "RATE_LIMITED", RateLimitError),
        (429, "QUOTA_EXCEEDED", QuotaExceededError),
        (502, "INFERENCE_FAILED", InferenceFailedError),
        (503, "UPSTREAM_UNAVAILABLE", ServiceUnavailableError),
        (504, "UPSTREAM_TIMEOUT", UpstreamTimeoutError),
        (500, "INTERNAL_ERROR", InternalServerError),
    ],
)
def test_error_mapping(status: int, code: str, cls: type[APIError]) -> None:
    client, _ = make_client(error(status, code, "details here", "req_fromBody"), max_retries=0)
    with pytest.raises(cls) as exc_info:
        client.decide(context="x", questions={"department": DEPARTMENT})
    exc = exc_info.value
    assert type(exc) is cls
    assert isinstance(exc, APIError) and isinstance(exc, KrunError)
    assert exc.status_code == status
    assert exc.error_code == code
    assert exc.message == "details here"
    assert exc.request_id == "req_fromBody"
    assert str(exc) == f"details here (code={code}, status={status}, request_id=req_fromBody)"


def test_rate_limit_and_quota_are_distinct() -> None:
    assert not issubclass(QuotaExceededError, RateLimitError)
    assert not issubclass(RateLimitError, QuotaExceededError)


def test_error_request_id_falls_back_to_header() -> None:
    client, _ = make_client(error(400, "INVALID_REQUEST", request_id=None))
    with pytest.raises(InvalidRequestError) as exc_info:
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert exc_info.value.request_id == "req_hdr"


def test_retry_after_is_parsed() -> None:
    client, _ = make_client(error(429, "RATE_LIMITED", **{"Retry-After": "17"}))
    with pytest.raises(RateLimitError) as exc_info:
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert exc_info.value.retry_after == 17.0


@pytest.mark.parametrize(
    ("status", "cls"),
    [(400, InvalidRequestError), (401, AuthenticationError), (404, NotFoundError), (429, RateLimitError),
     (500, InternalServerError), (502, APIError), (503, ServiceUnavailableError), (504, UpstreamTimeoutError),
     (418, APIError)],
)  # fmt: skip
def test_error_without_code_maps_by_status(status: int, cls: type[APIError]) -> None:
    client, _ = make_client(lambda _r: httpx.Response(status, text="<html>proxy error</html>"), max_retries=0)
    with pytest.raises(cls) as exc_info:
        client.models()
    exc = exc_info.value
    assert type(exc) is cls
    assert exc.status_code == status and exc.error_code is None
    assert exc.body == "<html>proxy error</html>"


def test_unknown_error_code_keeps_code_and_maps_by_status() -> None:
    client, _ = make_client(error(400, "SOMETHING_NEW"))
    with pytest.raises(InvalidRequestError) as exc_info:
        client.models()
    assert exc_info.value.error_code == "SOMETHING_NEW"


# ---------------------------------------------------------------------------------------------- retries and timeouts


def sequence(*handlers: Handler) -> Handler:
    queue = list(handlers)
    return lambda req: queue.pop(0)(req)


def raise_(exc: Exception) -> Handler:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("krun._client.time.sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("status", [502, 503, 504])
def test_decide_retries_once_on_5xx(status: int, no_sleep: list[float]) -> None:
    client, seen = make_client(sequence(error(status, None), ok(DECIDE_OK)))
    assert client.decide(context="x", questions={"department": DEPARTMENT}).answers["department"].choice == "returns"
    assert len(seen) == 2 and len(no_sleep) == 1


def test_decide_retry_honours_retry_after_with_cap(no_sleep: list[float]) -> None:
    client, _ = make_client(sequence(error(503, "UPSTREAM_UNAVAILABLE", **{"Retry-After": "5"}), ok(DECIDE_OK)))
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert no_sleep == [5.0]
    client, _ = make_client(sequence(error(503, "UPSTREAM_UNAVAILABLE", **{"Retry-After": "3600"}), ok(DECIDE_OK)))
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert no_sleep[-1] == 10.0


def test_decide_gives_up_after_max_retries(no_sleep: list[float]) -> None:
    client, seen = make_client(error(503, "UPSTREAM_UNAVAILABLE"), max_retries=2)
    with pytest.raises(ServiceUnavailableError):
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert len(seen) == 3


def test_decide_retries_connection_error(no_sleep: list[float]) -> None:
    client, seen = make_client(sequence(raise_(httpx.ConnectError("refused")), ok(DECIDE_OK)))
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert len(seen) == 2


def test_connection_error_raised_after_retries(no_sleep: list[float]) -> None:
    client, seen = make_client(raise_(httpx.ConnectError("refused")))
    with pytest.raises(APIConnectionError) as exc_info:
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert not isinstance(exc_info.value, APITimeoutError)
    assert len(seen) == 2


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
def test_no_retry_on_other_errors(status: int, no_sleep: list[float]) -> None:
    client, seen = make_client(error(status, None), max_retries=3)
    with pytest.raises(APIError):
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert len(seen) == 1


def test_max_retries_zero(no_sleep: list[float]) -> None:
    client, seen = make_client(error(503, None), max_retries=0)
    with pytest.raises(ServiceUnavailableError):
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert len(seen) == 1


def test_timeout_raises_and_is_not_retried(no_sleep: list[float]) -> None:
    client, seen = make_client(raise_(httpx.ReadTimeout("slow")), max_retries=3)
    with pytest.raises(APITimeoutError):
        client.decide(context="x", questions={"department": DEPARTMENT})
    assert len(seen) == 1


def test_timeout_is_sent_to_transport() -> None:
    client, seen = make_client(ok(DECIDE_OK), timeout=12.5)
    client.decide(context="x", questions={"department": DEPARTMENT})
    assert seen[0].extensions["timeout"] == {"connect": 12.5, "read": 12.5, "write": 12.5, "pool": 12.5}
    client.decide(context="x", questions={"department": DEPARTMENT}, timeout=3)
    assert seen[1].extensions["timeout"]["read"] == 3.0


def test_feedback_is_never_retried(no_sleep: list[float]) -> None:
    for handler in (error(503, "UPSTREAM_UNAVAILABLE"), raise_(httpx.ConnectError("refused"))):
        client, seen = make_client(handler, max_retries=5)
        with pytest.raises(KrunError):
            client.feedback(request_id="req_1", question_id="department", correct=True)
        assert len(seen) == 1


def test_models_retries(no_sleep: list[float]) -> None:
    body = {"object": "list", "data": [{"id": "krun-one-v0", "object": "model", "status": "available"}]}
    client, seen = make_client(sequence(error(502, None), ok(body)))
    assert [m.id for m in client.models()] == ["krun-one-v0"]
    assert len(seen) == 2


# ------------------------------------------------------------------------------------------------- feedback / models


FEEDBACK_OK = {
    "id": "fb_123",
    "object": "feedback",
    "request_id": "req_abc",
    "question_id": "department",
    "created_at": "2026-09-24T12:34:56.123456789Z",
}


def test_feedback_serialization_and_parsing() -> None:
    client, seen = make_client(ok(FEEDBACK_OK, status=201))
    fb = client.feedback(
        request_id="req_abc",
        question_id="department",
        correct=False,
        expected_decision="billing",
        metadata={"ticket": "T-1", "reviewer": {"team": "support"}},
    )
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/feedback"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content) == {
        "request_id": "req_abc",
        "question_id": "department",
        "correct": False,
        "expected_decision": "billing",
        "metadata": {"ticket": "T-1", "reviewer": {"team": "support"}},
    }
    assert fb.id == "fb_123" and fb.object == "feedback"
    assert fb.request_id == "req_abc" and fb.question_id == "department"
    assert fb.created_at == datetime(2026, 9, 24, 12, 34, 56, 123456, tzinfo=timezone.utc)


def test_feedback_minimal_body() -> None:
    client, seen = make_client(ok(FEEDBACK_OK, status=201))
    client.feedback(request_id="req_abc", question_id="department", correct=True)
    assert json.loads(seen[0].content) == {"request_id": "req_abc", "question_id": "department", "correct": True}


def test_feedback_argument_checks() -> None:
    client, seen = make_client(ok(FEEDBACK_OK, status=201))
    with pytest.raises(TypeError):
        client.feedback(request_id="", question_id="d", correct=True)
    with pytest.raises(TypeError):
        client.feedback(request_id="req", question_id="d", correct="yes")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        client.feedback(request_id="req", question_id="d", correct=True, metadata=["x"])  # type: ignore[arg-type]
    assert seen == []


def test_feedback_not_found() -> None:
    client, _ = make_client(error(404, "NOT_FOUND", "no decision with this request_id", "req_fb"))
    with pytest.raises(NotFoundError) as exc_info:
        client.feedback(request_id="req_unknown", question_id="department", correct=True)
    assert exc_info.value.request_id == "req_fb"


def test_models() -> None:
    body = {"object": "list", "data": [{"id": "krun-one-v0", "object": "model", "status": "available"}]}
    client, seen = make_client(ok(body))
    models = client.models()
    assert seen[0].method == "GET" and str(seen[0].url) == "https://api.krun.ai/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert models == [krun.Model(id="krun-one-v0", object="model", status="available")]


# ---------------------------------------------------------------------------------------------------------- lifecycle


def test_context_manager_closes_owned_client_only() -> None:
    with Krun(api_key=KEY) as client:
        inner = client._client
    assert inner.is_closed

    external = httpx.Client(transport=httpx.MockTransport(ok(DECIDE_OK)))
    with Krun(api_key=KEY, http_client=external):
        pass
    assert not external.is_closed
