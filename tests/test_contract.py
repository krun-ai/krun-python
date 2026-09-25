"""Offline contract tests: the SDK's hand-written models agree with the committed OpenAPI snapshot.

When the API changes: refresh `openapi/openapi.json` (`python scripts/check_openapi.py --update`), update
`OPENAPI_SHA256`/`OPENAPI_VERSION` in `src/krun/_constants.py`, then fix whatever these tests report.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import typing
from pathlib import Path

import httpx
import pytest

from krun import ChoiceAnswer, DecisionResult, Feedback, Krun, Model, NoulAnswer, ScoreAnswer, Usage
from krun._constants import OPENAPI_SHA256, OPENAPI_VERSION
from krun._exceptions import _CODE_TO_CLASS
from krun._models import _ANSWER_PARSERS, decide_body, feedback_body
from krun.types import (
    AbstentionStatus,
    ChoiceQuestionParam,
    NoulQuestionParam,
    QuestionType,
    ScoreQuestionParam,
    TaskType,
)

from .mock_server import OPENAPI, schema_validator

SNAPSHOT = Path(__file__).resolve().parents[1] / "openapi" / "openapi.json"
SCHEMAS = OPENAPI["components"]["schemas"]
_DECIDE = OPENAPI["paths"]["/v1/decide"]["post"]
REQUEST_EXAMPLES = _DECIDE["requestBody"]["content"]["application/json"]["examples"]
RESPONSE_EXAMPLES = _DECIDE["responses"]["200"]["content"]["application/json"]["examples"]


def canonical_sha256(doc: object) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fields(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_snapshot_matches_pinned_hash_and_version() -> None:
    assert canonical_sha256(json.loads(SNAPSHOT.read_text())) == OPENAPI_SHA256
    assert OPENAPI["info"]["version"] == OPENAPI_VERSION


def test_endpoints_used_by_the_sdk_exist() -> None:
    paths = OPENAPI["paths"]
    assert "post" in paths["/v1/decide"]
    assert "post" in paths["/v1/feedback"]
    assert "get" in paths["/v1/models"]
    decide_params = {p["name"] for p in paths["/v1/decide"]["post"].get("parameters", [])}
    assert "X-Request-ID" in decide_params


def test_error_codes_are_all_mapped() -> None:
    assert set(SCHEMAS["ErrorCode"]["enum"]) == set(_CODE_TO_CLASS)
    assert set(SCHEMAS["ErrorDetail"]["properties"]) == {"code", "message", "request_id"}


def _variant(union: str, tag: str) -> str:
    """Name of the component schema of `union` for discriminator value `tag`."""
    ref: str = SCHEMAS[union]["discriminator"]["mapping"][tag]
    name = ref.rsplit("/", 1)[1]
    assert {"$ref": ref} in SCHEMAS[union]["oneOf"]
    assert SCHEMAS[name]["properties"]["type"]["enum"] == [tag] and "type" in SCHEMAS[name]["required"]
    return name


def test_answer_models() -> None:
    tags = typing.get_args(QuestionType)
    assert set(tags) == set(_ANSWER_PARSERS) == {"choice", "noul", "score"}
    assert {_variant("Answer", t) for t in tags} == {"ChoiceAnswer", "NoulAnswer", "ScoreAnswer"}
    for cls in (ChoiceAnswer, NoulAnswer, ScoreAnswer):
        assert set(SCHEMAS[cls.__name__]["properties"]) == fields(cls), cls
    assert set(SCHEMAS["AbstentionStatus"]["enum"]) == set(typing.get_args(AbstentionStatus))
    assert "choice" not in SCHEMAS["ChoiceAnswer"]["required"]  # nullable/abstain


def test_usage_has_only_input_tokens() -> None:
    assert set(SCHEMAS["Usage"]["properties"]) == fields(Usage) == {"input_tokens"}


def test_decide_response_model() -> None:
    assert set(SCHEMAS["DecideResponse"]["properties"]) == fields(DecisionResult) - {"request_id"}


def test_question_models() -> None:
    for tag, param in (("choice", ChoiceQuestionParam), ("noul", NoulQuestionParam), ("score", ScoreQuestionParam)):
        schema = SCHEMAS[_variant("Question", tag)]
        assert set(schema["properties"]) == set(param.__annotations__), tag
    assert set(SCHEMAS["NoulCriteria"]["properties"]) == {"true", "false"}
    assert set(SCHEMAS["TaskType"]["enum"]) == set(typing.get_args(TaskType))
    assert set(SCHEMAS["DecideRequest"]["properties"]) == {"context", "questions", "model"}


def test_feedback_models() -> None:
    assert set(SCHEMAS["FeedbackRequest"]["properties"]) == {
        "request_id", "question_id", "correct", "expected", "expected_decision", "metadata"
    }  # fmt: skip
    assert set(SCHEMAS["FeedbackResponse"]["properties"]) == fields(Feedback)
    assert set(SCHEMAS["Model"]["properties"]) == fields(Model)


def test_serialized_requests_validate_against_the_schema() -> None:
    decide = schema_validator("DecideRequest")
    body = decide_body(
        "ctx",
        {
            "a": {"type": "choice", "options": {"x": "", "y": None}},
            "b": {"type": "choice", "options": {"x": "desc", "y": "desc"}, "task_type": "tool"},
            "c": {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes"}},
            "d": {"type": "score", "instructions": "How much?", "levels": ["low", "mid", "high"]},
        },
        "krun-one-v0",
    )
    decide.validate(body)
    feedback = schema_validator("FeedbackRequest")
    feedback.validate(feedback_body("req_1", "a", False, "y", {"k": 1}))
    feedback.validate(feedback_body("req_1", "a", True, None, None))
    feedback.validate(feedback_body("req_1", "c", False, None, None, {"type": "noul", "value": True}))
    feedback.validate(feedback_body("req_1", "d", False, None, None, {"type": "score", "value": 2}))


@pytest.mark.parametrize("name", list(REQUEST_EXAMPLES))
def test_openapi_request_examples_round_trip(name: str) -> None:
    value = REQUEST_EXAMPLES[name]["value"]
    assert decide_body(value["context"], value["questions"], value.get("model")) == value


@pytest.mark.parametrize("name", list(RESPONSE_EXAMPLES))
def test_openapi_response_examples_parse(name: str) -> None:
    value = RESPONSE_EXAMPLES[name]["value"]
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=value, headers={"X-Request-ID": "req_ex"}))
    client = Krun(api_key="krun_test_x", http_client=httpx.Client(transport=transport))
    first_q = next(iter(value["answers"]))
    result = client.decide(context="x", questions={first_q: {"type": "choice", "options": {"a": "", "b": ""}}})
    assert result.model == value["model"]
    assert result.usage.input_tokens == value["usage"]["input_tokens"]
    classes = {"choice": ChoiceAnswer, "noul": NoulAnswer, "score": ScoreAnswer}
    for qid, answer in value["answers"].items():
        assert type(result.answers[qid]) is classes[answer["type"]]
        assert dataclasses.asdict(result.answers[qid]) == answer
