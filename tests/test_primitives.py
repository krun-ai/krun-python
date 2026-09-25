"""KRUN-011 decision primitives: typed requests (dicts and dataclasses) and typed answers (noul / score)."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from krun import (
    AsyncKrun,
    ChoiceAnswer,
    ChoiceQuestion,
    Krun,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from krun._exceptions import APIResponseValidationError
from krun._models import decide_body, feedback_body

from .mock_server import MockKrunAPI

KEY = "krun_test_mockkey"
CTX = "Customer says this is the third time exports failed and wants a human immediately."
LEVELS = ["Minor issue", "Feature degraded", "Blocking issue"]


@pytest.fixture
def api() -> Iterator[MockKrunAPI]:
    with MockKrunAPI(api_key=KEY) as server:
        yield server


def test_mixed_primitives_decode_to_typed_answers(api: MockKrunAPI) -> None:
    client = Krun(api_key=KEY, base_url=api.url)
    result = client.decide(
        context=CTX,
        questions={
            "department": {"type": "choice", "options": {"billing": "", "support": "", "sales": ""}},
            "needs_human": {"type": "noul", "instructions": "Is the customer asking for human assistance?"},
            "severity": {"type": "score", "instructions": "How severe is the reported issue?", "levels": LEVELS},
        },
    )
    assert list(result.answers) == ["department", "needs_human", "severity"]
    assert isinstance(result.answers["department"], ChoiceAnswer)
    needs_human = result.answers["needs_human"]
    assert isinstance(needs_human, NoulAnswer) and needs_human.type == "noul"
    assert needs_human.noul == 0.973
    severity = result.answers["severity"]
    assert isinstance(severity, ScoreAnswer)
    assert severity.legend == {"0": "Minor issue", "1": "Feature degraded", "2": "Blocking issue"}
    assert list(severity.probabilities) == ["0", "1", "2"]
    assert severity.score == pytest.approx(1.0, abs=1e-5)


def test_dataclass_questions_serialize_like_dicts() -> None:
    as_objects = decide_body(
        CTX,
        {
            "department": ChoiceQuestion(options={"billing": "", "support": None}),
            "needs_human": NoulQuestion("Is the customer asking for a human?", criteria={"true": "asks for a person"}),
            "severity": ScoreQuestion("How severe?", ("Minor", "Moderate", "Critical")),
        },
        None,
    )
    as_dicts = decide_body(
        CTX,
        {
            "department": {"type": "choice", "options": {"billing": "", "support": None}},
            "needs_human": {
                "type": "noul",
                "instructions": "Is the customer asking for a human?",
                "criteria": {"true": "asks for a person"},
            },
            "severity": {"type": "score", "instructions": "How severe?", "levels": ("Minor", "Moderate", "Critical")},
        },
        None,
    )
    assert as_objects == as_dicts
    assert as_objects["questions"]["severity"]["levels"] == ["Minor", "Moderate", "Critical"]  # order kept
    assert NoulQuestion("Is it?").to_dict() == {"type": "noul", "instructions": "Is it?"}


def test_dataclass_questions_end_to_end(api: MockKrunAPI) -> None:
    client = Krun(api_key=KEY, base_url=api.url)
    result = client.decide(
        context=CTX,
        questions={"n": NoulQuestion("Is it urgent?"), "s": ScoreQuestion("How much?", ["low", "high"])},
    )
    assert isinstance(result.answers["n"], NoulAnswer) and isinstance(result.answers["s"], ScoreAnswer)


def test_score_level_limits_are_enforced_by_the_api(api: MockKrunAPI) -> None:
    from krun import InvalidRequestError

    client = Krun(api_key=KEY, base_url=api.url)
    with pytest.raises(InvalidRequestError):
        client.decide(context=CTX, questions={"s": ScoreQuestion("How much?", ["only one"])})


def test_unknown_answer_type_asks_to_upgrade() -> None:
    body = {"model": "m", "answers": {"q": {"type": "rank", "rank": 1}}, "usage": {"input_tokens": 1}}
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=body, headers={"X-Request-ID": "req_1"}))
    client = Krun(api_key="krun_test_x", http_client=httpx.Client(transport=transport))
    with pytest.raises(APIResponseValidationError, match="upgrade"):
        client.decide(context="x", questions={"q": {"type": "noul", "instructions": "?"}})


def test_malformed_score_answer_is_rejected() -> None:
    body = {
        "model": "m",
        "answers": {
            "q": {"type": "score", "score": 1, "confidence": 0.5, "legend": {"0": 1}, "probabilities": {"0": 1.0}}
        },
        "usage": {"input_tokens": 1},
    }
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=body, headers={"X-Request-ID": "req_1"}))
    client = Krun(api_key="krun_test_x", http_client=httpx.Client(transport=transport))
    with pytest.raises(APIResponseValidationError, match="legend"):
        client.decide(context="x", questions={"q": {"type": "score", "instructions": "?", "levels": ["a", "b"]}})


def test_typed_feedback(api: MockKrunAPI) -> None:
    client = Krun(api_key=KEY, base_url=api.url)
    rid = client.decide(context=CTX, questions={"n": NoulQuestion("Is it urgent?")}).request_id
    fb = client.feedback(request_id=rid, question_id="n", correct=False, expected={"type": "noul", "value": False})
    assert fb.question_id == "n"
    assert feedback_body("r", "s", False, None, None, {"type": "score", "value": 2})["expected"] == {
        "type": "score", "value": 2}  # fmt: skip
    with pytest.raises(TypeError):
        feedback_body("r", "s", False, None, None, {"type": "rank", "value": 2})
    # legacy choice form unchanged
    assert feedback_body("r", "d", False, "billing", None) == {
        "request_id": "r", "question_id": "d", "correct": False, "expected_decision": "billing"}  # fmt: skip


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_async_client_decodes_primitives(api: MockKrunAPI) -> None:
    async with AsyncKrun(api_key=KEY, base_url=api.url) as client:
        result = await client.decide(context=CTX, questions={"s": ScoreQuestion("How severe?", LEVELS)})
    assert isinstance(result.answers["s"], ScoreAnswer)


def test_typed_accessors(api: MockKrunAPI) -> None:
    client = Krun(api_key=KEY, base_url=api.url)
    result = client.decide(
        context=CTX,
        questions={
            "n": NoulQuestion("Is it urgent?"),
            "s": ScoreQuestion("How much?", LEVELS),
            "c": ChoiceQuestion(options={"a": "", "b": ""}),
        },
    )
    assert result.noul("n").noul == 0.973
    assert result.score("s").legend["2"] == "Blocking issue"
    assert result.choice("c").choice == "a"
    with pytest.raises(TypeError, match="noul"):
        result.choice("n")


PARITY = __import__("json").loads(
    (__import__("pathlib").Path(__file__).parent / "fixtures" / "decision_primitives_parity.json").read_text()
)


def test_parity_fixture_decide_matches_typescript() -> None:
    """Same fixture as krun-typescript/test/fixtures: both SDKs must produce this exact wire body."""
    body = decide_body(
        CTX,
        {
            "department": ChoiceQuestion(
                options={"billing": "Billing and payment issues", "support": "", "sales": None}
            ),
            "tool": {
                "type": "choice",
                "task_type": "tool",
                "options": {"create_ticket": "Open a support ticket", "send_email": "Send an email"},
            },
            "needs_human": NoulQuestion(
                "Is the customer asking for human assistance?",
                criteria={"true": "Explicitly asks for a person", "false": "Does not ask for a person"},
            ),
            "retry": {"type": "noul", "instructions": "Should the export be retried automatically?"},
            "severity": ScoreQuestion(
                "How severe is the reported issue?", ["Minor issue", "Feature degraded", "Blocking issue"]
            ),
        },
        "krun-one-v0",
    )
    assert body == PARITY["wire"]
    assert list(body["questions"]) == list(PARITY["wire"]["questions"])


def test_parity_fixture_feedback_matches_typescript() -> None:
    assert [
        feedback_body("req_parity", "needs_human", False, None, None, {"type": "noul", "value": False}),
        feedback_body("req_parity", "severity", False, None, None, {"type": "score", "value": 2}),
        feedback_body("req_parity", "department", False, "billing", None),
    ] == PARITY["feedback"]
